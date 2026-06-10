"""
NIFTY volatility risk premium — SIC-92 phase 1 (measurement study).

The vol risk premium is structurally different from everything tested on
2026-06-10: it harvests a documented insurance premium rather than
predicting cross-sectional returns. Phase 1 asks only: does the premium
exist on Indian data, how big is it, and does a costed harvest proxy
survive its worst regime (2020 is IN sample — VIX history extended to
2011-06 via Yahoo ^INDIAVIX, cross-validated vs NSE at 0.012% median)?

DECLARED DESIGN (fixed before running):

  VRP_t        = VIX_t − RV_{t→t+21}, where RV = annualized std of the
                 NEXT 21 daily log returns of NIFTY (^NSEI), in vol points.
  Inference    = daily overlapping series; t-stat = t_daily / sqrt(21)
                 (conservative overlap correction), plus the non-overlapping
                 21d-cycle series as the primary count.
  Harvest proxy= short a 21-trading-day variance swap at strike VIX², one
                 cycle at a time, non-overlapping. P&L per unit vega
                 ≈ (VIX² − RV²) / (2·VIX) vol points (Carr–Wu vega-
                 normalized). Costs: 0.8 vol points round trip per cycle
                 (NIFTY option-strip friction estimate, declared).
  Conditioning = reported jointly by entry-VIX tercile (no selection).
  Phase-1 gate = mean VRP > 0 with overlap-adjusted |t| > 3
                 AND net harvest Sharpe > 0.5
                 AND worst cycle reported vs a capped-loss structure
                 (a defined-risk implementation caps what the variance
                 proxy loses unbounded — flag if the uncapped worst cycle
                 exceeds ~3x the mean annual harvest).

  Caveats declared upfront: the variance-swap proxy is NOT a tradeable
  retail structure (that's phase 2 — defined-risk condors via Angel chain);
  VIX is a 30-calendar-day IV while RV here is 21 trading days (~29
  calendar) — horizon-matched, but strike convexity is ignored.

Usage:
    python3 -m quant_engine.research.vol_risk_premium

Output: data/vol_risk_premium.json + console summary.
"""
import json
import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quant_engine.data.loader import load_price_history
from quant_engine.data.market_regime_loader import load_vix_series

logger = logging.getLogger(__name__)

RV_WINDOW = 21
COST_VOL_PTS = 0.8
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "vol_risk_premium.json"


def build_series() -> pd.DataFrame:
    vix = load_vix_series(limit=10000)
    vix.index = pd.to_datetime(vix.index).normalize()
    nifty = load_price_history("^NSEI", limit=None)["close"].astype(float)

    logret = np.log(nifty / nifty.shift(1))
    # Forward realized vol: std of the NEXT 21 daily log returns, annualized,
    # in vol points. shift(-RV_WINDOW) aligns the window to start at t+1.
    fwd_rv = (
        logret.rolling(RV_WINDOW).std().shift(-RV_WINDOW) * np.sqrt(252) * 100
    )

    df = pd.DataFrame({"vix": vix}).join(pd.DataFrame({"fwd_rv": fwd_rv}), how="inner")
    df = df.dropna()
    df["vrp"] = df["vix"] - df["fwd_rv"]
    return df


def overlap_adjusted_t(series: pd.Series, horizon: int) -> float:
    t_daily = series.mean() / (series.std(ddof=1) / np.sqrt(len(series)))
    return float(t_daily / np.sqrt(horizon))


def harvest_cycles(df: pd.DataFrame) -> pd.DataFrame:
    """Non-overlapping 21-trading-day short-variance cycles."""
    rows = []
    i = 0
    idx = df.index
    while i < len(df):
        r = df.iloc[i]
        # vega-normalized variance-swap P&L in vol points
        pnl_gross = (r["vix"] ** 2 - r["fwd_rv"] ** 2) / (2 * r["vix"])
        rows.append({
            "date": str(idx[i].date()),
            "vix": float(r["vix"]),
            "fwd_rv": float(r["fwd_rv"]),
            "pnl_gross": float(pnl_gross),
            "pnl_net": float(pnl_gross - COST_VOL_PTS),
        })
        i += RV_WINDOW
    return pd.DataFrame(rows)


def _stats(pnl: pd.Series) -> dict:
    cycles_per_year = 252 / RV_WINDOW
    mean, std = float(pnl.mean()), float(pnl.std(ddof=1))
    equity = pnl.cumsum()
    dd = float((equity - equity.cummax()).min())
    return {
        "mean_vol_pts_per_cycle": round(mean, 2),
        "ann_sharpe": round(mean / std * np.sqrt(cycles_per_year), 2),
        "hit_pct": round(float((pnl > 0).mean() * 100), 1),
        "worst_cycle_vol_pts": round(float(pnl.min()), 1),
        "max_dd_vol_pts": round(dd, 1),
        "ann_harvest_vol_pts": round(mean * cycles_per_year, 1),
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    df = build_series()
    logger.info("Joint VIX+RV series: %d days, %s → %s",
                len(df), df.index[0].date(), df.index[-1].date())

    cycles = harvest_cycles(df)
    terc = pd.qcut(cycles["vix"], 3, labels=["low_vix", "mid_vix", "high_vix"])

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "n_days": int(len(df)),
        "span": [str(df.index[0].date()), str(df.index[-1].date())],
        "rv_window_days": RV_WINDOW,
        "cost_vol_pts_round_trip": COST_VOL_PTS,
        "vrp_daily": {
            "mean_vol_pts": round(float(df["vrp"].mean()), 2),
            "median_vol_pts": round(float(df["vrp"].median()), 2),
            "pct_positive": round(float((df["vrp"] > 0).mean() * 100), 1),
            "t_overlap_adj": round(overlap_adjusted_t(df["vrp"], RV_WINDOW), 2),
            "by_year_mean": {str(y): round(float(g.mean()), 2)
                             for y, g in df["vrp"].groupby(df.index.year)},
        },
        "harvest_cycles": {
            "n_cycles": int(len(cycles)),
            "gross": _stats(cycles["pnl_gross"]),
            "net": _stats(cycles["pnl_net"]),
            "by_vix_tercile_net": {
                str(t): _stats(g["pnl_net"])
                for t, g in cycles.groupby(terc, observed=True)
            },
            "worst_5_cycles": cycles.nsmallest(5, "pnl_net")[
                ["date", "vix", "fwd_rv", "pnl_net"]].to_dict("records"),
        },
        "gate": "mean VRP > 0 with overlap-adj |t| > 3 AND net Sharpe > 0.5; "
                "worst cycle reported vs capped-loss feasibility",
    }

    print(json.dumps({k: v for k, v in results.items() if k != "harvest_cycles"}, indent=1))
    print("\nHARVEST (non-overlapping cycles):")
    print(" gross:", json.dumps(results["harvest_cycles"]["gross"]))
    print(" net:  ", json.dumps(results["harvest_cycles"]["net"]))
    print(" by tercile (net):")
    for t, s in results["harvest_cycles"]["by_vix_tercile_net"].items():
        print(f"  {t:9s}", json.dumps(s))
    print(" worst 5 cycles:", json.dumps(results["harvest_cycles"]["worst_5_cycles"], indent=1))

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
