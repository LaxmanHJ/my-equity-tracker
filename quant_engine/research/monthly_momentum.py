"""
Study A — classic monthly 12-1 cross-sectional momentum (fund sleeve-2 gate).

PRE-REGISTERED DECLARED RULE (fixed before first run, 2026-07-25)
=================================================================
Every prior experiment in this project tested daily/short-horizon rules and
died on costs (best: rev3 cs-IC +0.0195, t=6.3, negative net Sharpe at
35-78% daily turnover). This study tests the one cost-survivable frequency
band never examined: monthly-rebalanced Jegadeesh-Titman 12-1 momentum.

Universe   : NIFTY200 PIT membership (index_membership registry, 2018→) ∩
             price_history, daily-cadence mask as in short_horizon.py.
Signal     : mom_12_1 = close[t-21] / close[t-252] − 1
             (12-month return, skipping the most recent month — the skip
             avoids the 1-month reversal; both lags are strictly past data).
Rebalance  : last trading day of each calendar month. Positions held one
             month, close-to-close.
Portfolios : LONG-ONLY — top 15 names equal-weight (the tradeable sleeve).
             LS        — top decile minus bottom decile, equal-weight legs.
Eligibility: symbol must be a PIT member on formation date and have ≥252
             daily bars of history; ≥30 eligible names per formation date.
Costs      : 30 bp one-way. Monthly cost = 2 × 0.0030 × turnover per leg,
             where turnover = fraction of names replaced (round trip:
             exiting names are sold, entering names are bought).
Benchmark  : ^NSEI close-to-close monthly returns on the same dates.

GATES (all three required for PASS — sleeve 2 becomes self-managed):
  G1. Net-of-cost LS annualized Sharpe > 0.6            (C0 bar)
  G2. PSR(net LS) > 0.95 — single-trial Deflated Sharpe (no search, so
      DSR reduces to the Probabilistic Sharpe Ratio vs SR*=0)
  G3. Long-only net-of-cost excess return vs ^NSEI > 0 over full sample
FAIL → sleeve 2 = Nifty200 Momentum 30 index fund. Thread closed either
way; no variants, no threshold shopping, one run.

Usage:
    python3 -m quant_engine.research.monthly_momentum

Output: data/monthly_momentum_study.json + console summary.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from quant_engine.data.loader import load_price_history, load_all_symbols, get_connection
from quant_engine.data.membership import MembershipRegistry, apply_pit_row_filter

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

FROM_DATE = "2018-01-01"       # membership registry start
TOP_N_LONG = 15
DECILE = 0.10
MIN_CROSS_N = 30
MIN_BARS = 252
COST_ONE_WAY = 0.0030          # 30 bp
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "monthly_momentum_study.json"


def _symbol_closes(symbol: str, registry: MembershipRegistry):
    """Daily close series, cadence-masked and PIT-filtered (as short_horizon.py)."""
    df = load_price_history(symbol, limit=None)
    if df.empty or len(df) < MIN_BARS:
        return None
    close = df["close"].astype(float)

    gaps = df.index.to_series().diff().dt.days
    cadence_ok = gaps.rolling(10, min_periods=5).median() <= 1.6

    out = pd.DataFrame({"close": close})
    out["mom_12_1"] = close.shift(21) / close.shift(252) - 1.0
    out["bars_seen"] = np.arange(len(out))  # history depth at each row
    out = out[cadence_ok.reindex(out.index).fillna(False)]
    out = out[out.index >= FROM_DATE]
    out = apply_pit_row_filter(out, symbol, registry)
    if out is None or len(out) == 0:
        return None
    out = out.copy()
    out["symbol"] = symbol
    return out


def build_panel() -> pd.DataFrame:
    conn = get_connection()
    try:
        registry = MembershipRegistry.from_turso(conn)
    finally:
        conn.close()

    symbols = sorted(set(load_all_symbols()) & registry.all_symbols())
    logger.info("Universe: %d symbols (price_history ∩ PIT membership)", len(symbols))

    frames = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_symbol_closes, s, registry): s for s in symbols}
        for fut in as_completed(futures):
            try:
                frame = fut.result()
            except Exception as exc:
                logger.warning("symbol %s failed: %s", futures[fut], exc)
                continue
            if frame is not None:
                frames.append(frame)
    panel = pd.concat(frames)
    panel.index.name = "date"
    return panel.reset_index()


def month_end_dates(panel: pd.DataFrame) -> list:
    """Last trading date of each month present in the panel."""
    dates = pd.Series(sorted(panel["date"].unique()))
    return list(dates.groupby(dates.dt.to_period("M")).max())


def load_nifty_monthly(dates: list) -> pd.Series:
    df = load_price_history("^NSEI", limit=None)
    close = df["close"].astype(float)
    # NIFTY close on (or last before) each formation date
    px = close.reindex(pd.DatetimeIndex(dates), method="ffill")
    return px.pct_change()


def run_study():
    panel = build_panel()
    formations = month_end_dates(panel)
    logger.info("Formation dates: %d (%s → %s)", len(formations), formations[0].date(), formations[-1].date())

    by_date = {d: g for d, g in panel.groupby("date")}

    monthly = []   # per holding month: dict of returns/turnovers
    prev_long: set = set()
    prev_ls_long: set = set()
    prev_ls_short: set = set()

    for i in range(len(formations) - 1):
        t0, t1 = formations[i], formations[i + 1]
        g0, g1 = by_date.get(t0), by_date.get(t1)
        if g0 is None or g1 is None:
            continue

        # Eligible names at formation: valid signal + enough history
        elig = g0[(g0["bars_seen"] >= MIN_BARS)].dropna(subset=["mom_12_1"])
        if len(elig) < MIN_CROSS_N:
            continue

        px1 = g1.set_index("symbol")["close"]
        px0 = elig.set_index("symbol")["close"]
        common = px0.index.intersection(px1.index)  # must still trade at t1
        elig = elig[elig["symbol"].isin(common)]
        if len(elig) < MIN_CROSS_N:
            continue

        fwd = (px1[common] / px0[common] - 1.0)
        ranked = elig.sort_values("mom_12_1")

        # Long-only top-N
        long_names = set(ranked.tail(TOP_N_LONG)["symbol"])
        lo_ret = fwd[list(long_names)].mean()
        lo_to = 1.0 - (len(long_names & prev_long) / TOP_N_LONG if prev_long else 0.0)
        prev_long = long_names

        # LS deciles
        k = max(int(len(ranked) * DECILE), 5)
        ls_long = set(ranked.tail(k)["symbol"])
        ls_short = set(ranked.head(k)["symbol"])
        ls_ret = fwd[list(ls_long)].mean() - fwd[list(ls_short)].mean()
        if prev_ls_long or prev_ls_short:
            ls_to = 1.0 - (len(ls_long & prev_ls_long) + len(ls_short & prev_ls_short)) / (len(ls_long) + len(ls_short))
        else:
            ls_to = 1.0
        prev_ls_long, prev_ls_short = ls_long, ls_short

        monthly.append({
            "formation": str(t0.date()), "settle": str(t1.date()),
            "n_eligible": int(len(elig)),
            "lo_gross": float(lo_ret), "lo_turnover": float(lo_to),
            "ls_gross": float(ls_ret), "ls_turnover": float(ls_to),
        })

    m = pd.DataFrame(monthly)
    if len(m) < 24:
        raise SystemExit(f"Only {len(m)} usable months — need >= 24 for any inference")

    # Costs: round trip on replaced names, both legs for LS
    m["lo_net"] = m["lo_gross"] - 2 * COST_ONE_WAY * m["lo_turnover"]
    m["ls_net"] = m["ls_gross"] - 2 * COST_ONE_WAY * m["ls_turnover"] * 2  # two legs

    nifty = load_nifty_monthly([pd.Timestamp(d) for d in month_end_dates(panel)]).reset_index(drop=True)
    # Align: monthly row i covers formations[i] → formations[i+1]; nifty.pct_change()
    # at position i+1 covers the same window. Rebuild alignment via settle date.
    nifty_by_settle = {}
    fm = month_end_dates(panel)
    for j in range(1, len(fm)):
        nifty_by_settle[str(fm[j].date())] = float(nifty.iloc[j]) if not np.isnan(nifty.iloc[j]) else None
    m["nifty"] = m["settle"].map(nifty_by_settle)
    m = m.dropna(subset=["nifty"])
    m["lo_excess_net"] = m["lo_net"] - m["nifty"]

    def _ann_sharpe(x: pd.Series) -> float:
        return float(x.mean() / x.std(ddof=1) * np.sqrt(12))

    def _psr(x: pd.Series) -> float:
        """Probabilistic Sharpe Ratio vs SR*=0 (single declared trial → DSR ≡ PSR)."""
        n = len(x)
        sr = x.mean() / x.std(ddof=1)  # monthly SR
        skew = float(x.skew())
        kurt = float(x.kurt()) + 3.0
        denom = np.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr**2, 1e-9))
        return float(norm.cdf(sr * np.sqrt(n - 1) / denom))

    results = {
        "study": "monthly_momentum_12_1",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "declared_rule": "see module docstring — fixed before first run",
        "n_months": int(len(m)),
        "period": {"first_formation": m["formation"].iloc[0], "last_settle": m["settle"].iloc[-1]},
        "avg_eligible_names": round(float(m["n_eligible"].mean()), 1),
        "long_only_top15": {
            "gross_ann_ret_pct": round(float(m["lo_gross"].mean() * 12 * 100), 2),
            "net_ann_ret_pct": round(float(m["lo_net"].mean() * 12 * 100), 2),
            "net_ann_sharpe": round(_ann_sharpe(m["lo_net"]), 3),
            "avg_monthly_turnover": round(float(m["lo_turnover"].mean()), 3),
            "nifty_ann_ret_pct": round(float(m["nifty"].mean() * 12 * 100), 2),
            "net_excess_ann_pct": round(float(m["lo_excess_net"].mean() * 12 * 100), 2),
            "net_excess_ir": round(_ann_sharpe(m["lo_excess_net"]), 3),
            "hit_rate_vs_nifty": round(float((m["lo_excess_net"] > 0).mean()), 3),
        },
        "ls_decile": {
            "gross_ann_ret_pct": round(float(m["ls_gross"].mean() * 12 * 100), 2),
            "net_ann_ret_pct": round(float(m["ls_net"].mean() * 12 * 100), 2),
            "gross_ann_sharpe": round(_ann_sharpe(m["ls_gross"]), 3),
            "net_ann_sharpe": round(_ann_sharpe(m["ls_net"]), 3),
            "avg_monthly_turnover": round(float(m["ls_turnover"].mean()), 3),
            "psr_net": round(_psr(m["ls_net"]), 4),
        },
        "monthly_rows": monthly,
    }

    g1 = results["ls_decile"]["net_ann_sharpe"] > 0.6
    g2 = results["ls_decile"]["psr_net"] > 0.95
    g3 = results["long_only_top15"]["net_excess_ann_pct"] > 0
    results["gates"] = {
        "G1_ls_net_sharpe_gt_0.6": g1,
        "G2_psr_gt_0.95": g2,
        "G3_lo_net_excess_positive": g3,
        "verdict": "PASS" if (g1 and g2 and g3) else "FAIL",
    }

    OUT_PATH.write_text(json.dumps(results, indent=2))
    logger.info("\n%s", json.dumps({k: v for k, v in results.items() if k != "monthly_rows"}, indent=2))
    logger.info("Saved → %s", OUT_PATH)
    return results


if __name__ == "__main__":
    run_study()
