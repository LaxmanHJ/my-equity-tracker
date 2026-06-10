"""
Entry-timing overlay event study — rev3 (3-day reversal) as WHEN, not WHAT.

Follow-up to short_horizon.py (2026-06-10): rev3 has real next-day
cross-sectional IC (+0.0195, t=6.3) but cannot pay for its own turnover as a
standalone strategy. The only cost-free way to spend it is timing entries the
20d engine was going to make anyway.

DECLARED RULE (fixed up front, no sweeping — multiple-testing budget spent):
  When a linear-LONG event fires at t0 (7-factor composite crosses >= +0.40),
  enter at the first day d in {t0+1, t0+2, t0+3} where the stock's rev3
  cross-sectional percentile >= 0.5 (short-term oversold half); if none
  qualifies, enter at t0+3 (deadline — never miss the trade). K = 3.

PAIRED COMPARISON (same trade, only timing differs):
  baseline arm: enter close(t0+1)         — mirrors signalQueueService's
                                            next-trading-day execution
  timed arm:    enter close(d) per rule
  common exit:  close(t0+21) for BOTH arms — waiting days are in cash, so
                capital deployment window is identical
  outcome:      ret_timed - ret_baseline per event

Two cohorts:
  signal  — actual linear-LONG threshold crossings (the cohort that matters;
            momentum-tail continuation risk lives here)
  uncond  — every 5th trading day per symbol (huge n; isolates the pure
            timing effect from signal conditioning)

Inference: event diffs are clustered by t0 month; t-stat computed across
monthly means (conservative vs event-level t given overlap).

Ship gate (declared): signal-cohort mean diff > 0 with |t| > 3 AND no
sign flip in the worst regime year.

Usage:
    python3 -m quant_engine.research.entry_timing [--from-date 2016-01-01]

Output: data/entry_timing_experiment.json + console summary.
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

from quant_engine.config import FACTOR_WEIGHTS
from quant_engine.data.loader import load_price_history, load_all_symbols, load_benchmark, get_connection
from quant_engine.data.membership import MembershipRegistry, apply_pit_row_filter
from quant_engine.strategies.sicilian_strategy import SicilianStrategy as _S

logger = logging.getLogger(__name__)

K_MAX_WAIT = 3            # declared: max trading days to wait for a pullback
HOLD_DAYS = 20            # exit at t0 + 1 + HOLD_DAYS for both arms
SIGNAL_THRESHOLD = 0.40   # mirrors production LONG threshold (composite scale -1..+1)
REV3_PCT_MIN = 0.5        # enter when stock is in the oversold half cross-sectionally
UNCOND_STRIDE = 5         # unconditional cohort: every 5th trading day
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "entry_timing_experiment.json"

# Production linear composite = 7 price factors at static weights (sentiment
# contributes ~0 for most of history — mirrored here by simply omitting it,
# keeping the same absolute weights so the +0.40 threshold is comparable).
PRICE_FACTOR_WEIGHTS = {k: v for k, v in FACTOR_WEIGHTS.items() if k != "sentiment"}


def _symbol_frame(symbol: str, registry: MembershipRegistry,
                  benchmark_df: pd.DataFrame, from_date: str) -> "pd.DataFrame | None":
    df = load_price_history(symbol, limit=None)
    if df.empty or len(df) < 180:
        return None
    df = df[df.index >= from_date]
    if len(df) < 180:
        return None

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    gaps = df.index.to_series().diff().dt.days
    cadence_ok = gaps.rolling(10, min_periods=5).median() <= 1.6

    scores = {
        "momentum":          _S._rolling_momentum_score(close),
        "bollinger":         _S._rolling_bollinger_score(close),
        "rsi":               _S._rolling_rsi_score(close),
        "macd":              _S._rolling_macd_score(close),
        "volatility":        _S._rolling_volatility_score(close),
        "volume":            _S._rolling_volume_score(close, volume),
        "relative_strength": _S._rolling_relative_strength_score(close, benchmark_df),
    }
    composite = sum(PRICE_FACTOR_WEIGHTS[k] * v for k, v in scores.items()).clip(-1.0, 1.0)

    out = pd.DataFrame({
        "close": close,
        "composite": composite,
        "rev3": -close.pct_change(3),
    })
    out = out[cadence_ok.reindex(out.index).fillna(False)]
    out = apply_pit_row_filter(out, symbol, registry)
    if out is None or len(out) < 60:
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
    benchmark_df = load_benchmark(limit=None)

    symbols = sorted(set(load_all_symbols()) & registry.all_symbols())
    logger.info("Universe: %d symbols", len(symbols))

    frames = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_symbol_frame, s, registry, benchmark_df, from_date): s
                   for s in symbols}
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
    panel = panel.reset_index()
    # Cross-sectional oversold percentile per date (higher = more oversold)
    panel["rev3_pct"] = panel.groupby("date")["rev3"].rank(pct=True)
    return panel


def _events_for_symbol(g: pd.DataFrame, cohort: str) -> list[dict]:
    """Run the paired comparison for one symbol's rows (date-sorted)."""
    g = g.sort_values("date").reset_index(drop=True)
    closes = g["close"].to_numpy()
    pcts = g["rev3_pct"].to_numpy()
    comps = g["composite"].to_numpy()
    n = len(g)
    need_ahead = 1 + HOLD_DAYS  # entry at t0+1 .. exit at t0+1+HOLD_DAYS

    if cohort == "signal":
        starts = [i for i in range(1, n - need_ahead)
                  if comps[i] >= SIGNAL_THRESHOLD and comps[i - 1] < SIGNAL_THRESHOLD]
    else:
        starts = list(range(30, n - need_ahead, UNCOND_STRIDE))

    out = []
    for t0 in starts:
        exit_i = t0 + 1 + HOLD_DAYS
        base_i = t0 + 1
        timed_i = None
        for d in range(t0 + 1, t0 + 1 + K_MAX_WAIT):
            if pcts[d] >= REV3_PCT_MIN:
                timed_i = d
                break
        if timed_i is None:
            timed_i = t0 + K_MAX_WAIT
        ret_base = closes[exit_i] / closes[base_i] - 1
        ret_timed = closes[exit_i] / closes[timed_i] - 1
        out.append({
            "symbol": g["symbol"].iloc[0],
            "t0": str(g["date"].iloc[t0])[:10],
            "waited": int(timed_i - base_i),
            "diff": float(ret_timed - ret_base),
            "ret_base": float(ret_base),
            "ret_timed": float(ret_timed),
        })
    return out


def _summarize(events: list[dict]) -> dict:
    if len(events) < 30:
        return {"n_events": len(events), "note": "insufficient events"}
    df = pd.DataFrame(events)
    df["month"] = df["t0"].str[:7]
    df["year"] = df["t0"].str[:4]

    monthly = df.groupby("month")["diff"].mean()
    t_monthly = float(monthly.mean() / (monthly.std(ddof=1) / np.sqrt(len(monthly))))

    yearly = df.groupby("year")["diff"].agg(["mean", "count"])
    worst_year = yearly["mean"].idxmin()

    return {
        "n_events": len(df),
        "n_months": int(len(monthly)),
        "pct_events_waited": round(float((df["waited"] > 0).mean() * 100), 1),
        "avg_days_waited": round(float(df["waited"].mean()), 2),
        "mean_diff_bp": round(float(df["diff"].mean() * 1e4), 1),
        "median_diff_bp": round(float(df["diff"].median() * 1e4), 1),
        "t_monthly_clustered": round(t_monthly, 2),
        "hit_rate_improved_pct": round(float((df["diff"] > 0).mean() * 100), 1),
        "mean_ret_base_bp": round(float(df["ret_base"].mean() * 1e4), 1),
        "mean_ret_timed_bp": round(float(df["ret_timed"].mean() * 1e4), 1),
        "by_year_diff_bp": {y: round(float(r["mean"] * 1e4), 1) for y, r in yearly.iterrows()},
        "worst_year": {"year": worst_year,
                       "diff_bp": round(float(yearly.loc[worst_year, "mean"] * 1e4), 1),
                       "n": int(yearly.loc[worst_year, "count"])},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2016-01-01")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    panel = build_panel(args.from_date)
    logger.info("Panel: %d rows, %d symbols, %d dates",
                len(panel), panel["symbol"].nunique(), panel["date"].nunique())

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "from_date": args.from_date,
        "rule": {
            "k_max_wait": K_MAX_WAIT, "hold_days": HOLD_DAYS,
            "signal_threshold": SIGNAL_THRESHOLD, "rev3_pct_min": REV3_PCT_MIN,
            "common_exit": "t0+1+20 for both arms; waiting days in cash",
        },
        "cohorts": {},
        "ship_gate": "signal cohort: mean diff > 0, |t_monthly| > 3, no worst-year sign flip",
    }

    for cohort in ("signal", "uncond"):
        events = []
        for _, g in panel.groupby("symbol"):
            events.extend(_events_for_symbol(g, cohort))
        summary = _summarize(events)
        results["cohorts"][cohort] = summary
        print(f"\n── {cohort} cohort ──")
        print(json.dumps(summary, indent=1))

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
