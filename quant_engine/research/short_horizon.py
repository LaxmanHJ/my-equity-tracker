"""
Short-horizon cross-sectional signal experiment (audit open item H.1).

Question: is there a tradeable NEXT-DAY (1-3d) cross-sectional signal on the
NSE Nifty-200 PIT universe? The 2026-06-04 audit proved the existing factor
stack has no 1d edge (hit 50.0-50.3% across all three tracks); classic
short-term reversal was never tested.

Declared candidate set (fixed up front — 6 signals x 4 horizons = 24 trials;
expected max |t| under the null across 24 trials is ~2.9, so nothing below
|t| ~ 3 counts as evidence):

  rev1        = -ret_1d                    1-day reversal
  rev3        = -ret_3d                    3-day reversal
  rev5        = -ret_5d                    5-day reversal
  rev5_vs     = -ret_5d / vol_20d          vol-scaled 5-day reversal
  mom21_skip5 = close[t-5]/close[t-21]-1   momentum skipping reversal window
  lowvol      = -vol_20d                   short-horizon defensive

Method
------
- Full price_history per symbol (Turso), PIT row filter vs index_membership.
- Per-symbol daily-cadence mask: rows are only eligible when the trailing
  10-bar median calendar gap is <= 1.6 days (kills the legacy weekly-bar era).
- Per-date cross-sectional Spearman IC vs fwd 1/2/3/5d close-to-close
  returns, MIN_CROSS_N = 30.
- Overlap-corrected t-stat: t = (mean_ic / se) / sqrt(horizon).
- For the 1d horizon: top-minus-bottom quintile equal-weight LS portfolio,
  gross and net of one-way costs of 15bp and 30bp scaled by realized quintile
  turnover.

Usage:
    python3 -m quant_engine.research.short_horizon [--from-date 2016-01-01]

Output: data/short_horizon_experiment.json + console summary.
"""
import argparse
import json
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from quant_engine.data.loader import load_price_history, load_all_symbols, get_connection
from quant_engine.data.membership import MembershipRegistry, apply_pit_row_filter

logger = logging.getLogger(__name__)

MIN_CROSS_N = 30
HORIZONS = (1, 2, 3, 5)
SIGNALS = ("rev1", "rev3", "rev5", "rev5_vs", "mom21_skip5", "lowvol")
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "short_horizon_experiment.json"
QUINTILE = 0.2
ONE_WAY_COSTS_BP = (15, 30)


def _symbol_frame(symbol: str, registry: MembershipRegistry, from_date: str) -> "pd.DataFrame | None":
    """Build the per-symbol signal + forward-return frame, PIT- and cadence-filtered."""
    df = load_price_history(symbol, limit=None)
    if df.empty or len(df) < 60:
        return None
    df = df[df.index >= from_date]
    if len(df) < 60:
        return None

    close = df["close"].astype(float)
    ret1 = close.pct_change()

    # Daily-cadence mask — weekly-era bars make pct_change(k) mean k WEEKS.
    gaps = df.index.to_series().diff().dt.days
    cadence_ok = gaps.rolling(10, min_periods=5).median() <= 1.6

    vol20 = ret1.rolling(20, min_periods=15).std()
    out = pd.DataFrame(index=df.index)
    out["rev1"] = -ret1
    out["rev3"] = -close.pct_change(3)
    out["rev5"] = -close.pct_change(5)
    out["rev5_vs"] = out["rev5"] / vol20.replace(0, np.nan)
    out["mom21_skip5"] = close.shift(5) / close.shift(21) - 1
    out["lowvol"] = -vol20
    for h in HORIZONS:
        out[f"fwd{h}"] = close.shift(-h) / close - 1

    out = out[cadence_ok.reindex(out.index).fillna(False)]
    out = apply_pit_row_filter(out, symbol, registry)
    if out is None or len(out) == 0:
        return None
    out = out.copy()
    out["symbol"] = symbol
    return out


def build_panel(from_date: str) -> pd.DataFrame:
    conn = get_connection()
    try:
        registry = MembershipRegistry.from_turso(conn)
    finally:
        conn.close()

    symbols = sorted(set(load_all_symbols()) & registry.all_symbols())
    logger.info("Universe: %d symbols (price_history ∩ PIT membership)", len(symbols))

    frames = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_symbol_frame, s, registry, from_date): s for s in symbols}
        for fut in as_completed(futures):
            try:
                frame = fut.result()
            except Exception as exc:
                logger.warning("symbol %s failed: %s", futures[fut], exc)
                continue
            if frame is not None:
                frames.append(frame)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames)
    panel.index.name = "date"
    return panel.reset_index()


def _ic_stats(panel: pd.DataFrame, signal: str, horizon: int) -> dict:
    ics = []
    fwd = f"fwd{horizon}"
    for _, grp in panel.groupby("date"):
        valid = grp[[signal, fwd]].dropna()
        if len(valid) < MIN_CROSS_N:
            continue
        if valid[signal].std() < 1e-12 or valid[fwd].std() < 1e-12:
            continue
        ic = spearmanr(valid[signal], valid[fwd])[0]
        if not np.isnan(ic):
            ics.append(ic)
    if len(ics) < 50:
        return {"n_dates": len(ics)}
    arr = np.asarray(ics)
    mean, std, n = arr.mean(), arr.std(ddof=1), len(arr)
    t_raw = mean / (std / np.sqrt(n))
    return {
        "mean_ic": round(float(mean), 4),
        "icir": round(float(mean / std), 3),
        "t_overlap_adj": round(float(t_raw / np.sqrt(horizon)), 2),
        "n_dates": n,
    }


def _ls_portfolio(panel: pd.DataFrame, signal: str) -> dict:
    """Daily-rebalanced top-minus-bottom quintile LS on fwd1, gross + net."""
    daily = []
    prev_long: set = set()
    prev_short: set = set()
    for dt, grp in panel.groupby("date"):
        valid = grp[[signal, "fwd1", "symbol"]].dropna()
        if len(valid) < MIN_CROSS_N:
            continue
        k = max(int(len(valid) * QUINTILE), 1)
        ranked = valid.sort_values(signal)
        short_leg, long_leg = ranked.head(k), ranked.tail(k)
        gross = long_leg["fwd1"].mean() - short_leg["fwd1"].mean()
        cur_long, cur_short = set(long_leg["symbol"]), set(short_leg["symbol"])
        if prev_long or prev_short:
            to = 1.0 - (len(cur_long & prev_long) + len(cur_short & prev_short)) / max(len(cur_long) + len(cur_short), 1)
        else:
            to = 1.0
        prev_long, prev_short = cur_long, cur_short
        daily.append((gross, to))
    if len(daily) < 100:
        return {"n_days": len(daily)}
    g = np.array([d[0] for d in daily])
    to = np.array([d[1] for d in daily])
    out = {
        "n_days": len(g),
        "avg_daily_turnover": round(float(to.mean()), 3),
        "gross_ann_sharpe": round(float(g.mean() / g.std(ddof=1) * np.sqrt(252)), 2),
        "gross_ann_ret_pct": round(float(g.mean() * 252 * 100), 1),
    }
    for bp in ONE_WAY_COSTS_BP:
        # Both legs trade `turnover` fraction of book; each trade pays one-way
        # cost twice (in + out) → daily drag = 2 legs x 2 sides x bp x turnover / 2
        # book. Simplify: drag = 2 x (bp/1e4) x turnover.
        net = g - 2 * (bp / 1e4) * to
        out[f"net{bp}bp_ann_sharpe"] = round(float(net.mean() / net.std(ddof=1) * np.sqrt(252)), 2)
        out[f"net{bp}bp_ann_ret_pct"] = round(float(net.mean() * 252 * 100), 1)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2016-01-01")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    panel = build_panel(args.from_date)
    if panel.empty:
        raise SystemExit("empty panel — check Turso connectivity / membership table")
    n_dates = panel["date"].nunique()
    n_syms = panel["symbol"].nunique()
    logger.info("Panel: %d rows, %d dates, %d symbols", len(panel), n_dates, n_syms)

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "from_date": args.from_date,
        "n_rows": len(panel),
        "n_dates": n_dates,
        "n_symbols": n_syms,
        "min_cross_n": MIN_CROSS_N,
        "n_trials": len(SIGNALS) * len(HORIZONS),
        "ic": {},
        "ls_fwd1": {},
        "notes": (
            "PIT-filtered Nifty200, daily-cadence rows only. 24 declared trials; "
            "under the null the expected max |t| is ~2.9 — require |t| > 3 AND "
            "net-of-cost Sharpe > 0 to consider a follow-up CPCV+DSR run."
        ),
    }
    print(f"\n{'signal':14s}" + "".join(f"  {h}d: IC / t      " for h in HORIZONS))
    for sig in SIGNALS:
        row = []
        results["ic"][sig] = {}
        for h in HORIZONS:
            st = _ic_stats(panel, sig, h)
            results["ic"][sig][f"{h}d"] = st
            row.append(f"{st.get('mean_ic', float('nan')):+.4f}/{st.get('t_overlap_adj', float('nan')):+.1f}"
                       if "mean_ic" in st else "  n/a  ")
        print(f"{sig:14s}  " + "   ".join(f"{c:15s}" for c in row))

    print(f"\nLong-short quintile @ fwd1 (gross / net15bp / net30bp ann. Sharpe, turnover):")
    for sig in SIGNALS:
        ls = _ls_portfolio(panel, sig)
        results["ls_fwd1"][sig] = ls
        if "gross_ann_sharpe" in ls:
            print(f"{sig:14s}  {ls['gross_ann_sharpe']:+.2f} / {ls['net15bp_ann_sharpe']:+.2f} / "
                  f"{ls['net30bp_ann_sharpe']:+.2f}   to={ls['avg_daily_turnover']:.0%}  n={ls['n_days']}")
        else:
            print(f"{sig:14s}  insufficient days ({ls.get('n_days')})")

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
