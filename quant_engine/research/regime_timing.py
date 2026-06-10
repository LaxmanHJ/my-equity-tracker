"""
Regime-timing backtest — NIFTY exposure dial from the production regime score.

Motivation (2026-06-10 strategy review): the project's strongest measured
result (SIC-91: portfolio alpha vs B&H driven by REGIME CALLS, not stock
picks) was never formalized into a strategy. Meanwhile both single-stock
short-horizon experiments failed on costs/drift. Index timing has no breadth
problem and near-zero costs (one instrument, slow dial).

DECLARED RULE — zero fitted parameters, all components are the production
formulas already shipped in market_regime_loader / SicilianStrategy:

  score_t   = weighted regime score in [-1, +1], computed from data
              through close t:
                Variant A (2011→2026, "trend+markov"): 0.5·trend + 0.5·markov
                Variant B (2022-03→2026, "full"):      0.412·vix + 0.294·trend
                                                        + 0.294·markov
                (production regime weights VIX 35 / trend 25 / markov 25 /
                 FII 15 with the FII leg dropped — <600 usable rows — and
                 the remainder renormalized)
  w_t       = clip(0.5 + score_t, 0, 1)      NIFTY weight; rest in cash
  execution = primary: next-day close (w applied with a 2-bar shift on
              close-to-close returns); sensitivity: same-close (1-bar shift)
  costs     = 5 bp one-way × |Δw|  (index ETF / futures)
  cash      = reported at both 0% and 6% (liquid fund) annual

Components are all trailing-window (SMA, rolling percentile, rolling Markov
transition matrix) — no look-ahead anywhere. Because nothing is fitted, the
whole sample is out-of-sample in the overfitting sense; residual risk is
selection bias on the rule form itself (~1 declared trial).

Usage:
    python3 -m quant_engine.research.regime_timing

Output: data/regime_timing_backtest.json + console summary.
"""
import json
import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quant_engine.config import RISK_FREE_RATE
from quant_engine.data.loader import load_price_history
from quant_engine.data.market_regime_loader import load_vix_series, vix_to_score, build_markov_score_series
from quant_engine.strategies.sicilian_strategy import SicilianStrategy as _S

logger = logging.getLogger(__name__)

COST_ONE_WAY = 0.0005
EXEC_SHIFT_PRIMARY = 2     # signal at close t → trade at close t+1 → earn t+1→t+2
EXEC_SHIFT_SENS = 1        # same-close execution (MOC on signal day)
OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "regime_timing_backtest.json"

VARIANTS = {
    "trend_markov_2011": {"vix": 0.0,   "trend": 0.5,   "markov": 0.5},
    "full_2022":         {"vix": 0.412, "trend": 0.294, "markov": 0.294},
}


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def _ann_stats(daily_ret: pd.Series, rf: float) -> dict:
    n = len(daily_ret)
    if n < 100:
        return {"n_days": n}
    equity = (1 + daily_ret).cumprod()
    years = n / 252
    cagr = float(equity.iloc[-1] ** (1 / years) - 1)
    vol = float(daily_ret.std(ddof=1) * np.sqrt(252))
    sharpe = (cagr - rf) / vol if vol > 0 else float("nan")
    mdd = _max_drawdown(equity)
    return {
        "n_days": n,
        "years": round(years, 1),
        "cagr_pct": round(cagr * 100, 2),
        "ann_vol_pct": round(vol * 100, 2),
        "sharpe_rf6": round(sharpe, 2),
        "max_dd_pct": round(mdd * 100, 1),
        "calmar": round(cagr / abs(mdd), 2) if mdd < 0 else None,
    }


def _capture(strat: pd.Series, mkt: pd.Series) -> dict:
    up = mkt > 0
    down = mkt < 0
    return {
        "up_capture_pct": round(float(strat[up].mean() / mkt[up].mean()) * 100, 1),
        "down_capture_pct": round(float(strat[down].mean() / mkt[down].mean()) * 100, 1),
    }


def _jensen_alpha(strat: pd.Series, mkt: pd.Series, rf: float) -> dict:
    rf_d = rf / 252
    y = (strat - rf_d).to_numpy()
    x = (mkt - rf_d).to_numpy()
    beta, alpha_d = np.polyfit(x, y, 1)
    resid = y - (alpha_d + beta * x)
    se = resid.std(ddof=2) / (x.std(ddof=1) * np.sqrt(len(x)))
    return {
        "beta": round(float(beta), 2),
        "jensen_alpha_ann_pct": round(float(alpha_d * 252 * 100), 2),
        "alpha_t": round(float(alpha_d / (resid.std(ddof=2) / np.sqrt(len(x)))), 2),
        "_beta_se": round(float(se), 3),
    }


def run_variant(nifty: pd.DataFrame, weights: dict, exec_shift: int, cash_rate: float) -> dict:
    close = nifty["close"].astype(float)
    mkt_ret = close.pct_change()

    trend = _S._build_nifty_trend(nifty)
    markov = build_markov_score_series(nifty)

    legs = {"trend": trend, "markov": markov}
    if weights.get("vix", 0) > 0:
        raw_vix = load_vix_series(limit=5000)
        vix = vix_to_score(raw_vix).reindex(close.index, method="ffill")
        legs["vix"] = vix

    score = sum(weights[k] * legs[k].reindex(close.index).fillna(0.0) for k in legs)
    w = (0.5 + score).clip(0.0, 1.0)

    # If the VIX leg is active, restrict the sample to dates with real VIX data
    if "vix" in legs:
        first_vix = legs["vix"].first_valid_index()
        w = w[w.index >= first_vix]
        mkt_ret = mkt_ret[mkt_ret.index >= first_vix]

    w_held = w.shift(exec_shift)
    turnover = w_held.diff().abs().fillna(0.0)
    cash_d = cash_rate / 252
    strat_ret = (w_held * mkt_ret + (1 - w_held) * cash_d - COST_ONE_WAY * turnover).dropna()
    mkt_aligned = mkt_ret.reindex(strat_ret.index)

    stats = _ann_stats(strat_ret, RISK_FREE_RATE)
    bh = _ann_stats(mkt_aligned, RISK_FREE_RATE)
    out = {
        "strategy": stats,
        "buy_hold": bh,
        "avg_exposure": round(float(w_held.mean()), 2),
        "ann_turnover_x": round(float(turnover.mean() * 252), 1),
        **_capture(strat_ret, mkt_aligned),
        **_jensen_alpha(strat_ret, mkt_aligned, RISK_FREE_RATE),
    }
    yearly_s = strat_ret.groupby(strat_ret.index.year).apply(lambda r: (1 + r).prod() - 1)
    yearly_m = mkt_aligned.groupby(mkt_aligned.index.year).apply(lambda r: (1 + r).prod() - 1)
    out["by_year_pct"] = {
        str(y): {"strategy": round(float(yearly_s[y]) * 100, 1),
                 "nifty": round(float(yearly_m[y]) * 100, 1)}
        for y in yearly_s.index
    }
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    warnings.filterwarnings("ignore")

    nifty = load_price_history("^NSEI", limit=None)
    logger.info("NIFTY: %d bars %s → %s", len(nifty), nifty.index[0].date(), nifty.index[-1].date())

    results = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "rule": "w = clip(0.5 + regime_score, 0, 1); cost 5bp one-way; production score formulas",
        "variants": {},
    }
    for name, weights in VARIANTS.items():
        results["variants"][name] = {}
        for label, shift in (("nextday_exec", EXEC_SHIFT_PRIMARY), ("sameclose_exec", EXEC_SHIFT_SENS)):
            for cash_label, cash in (("cash0", 0.0), ("cash6", 0.06)):
                key = f"{label}_{cash_label}"
                results["variants"][name][key] = run_variant(nifty, weights, shift, cash)

        primary = results["variants"][name]["nextday_exec_cash6"]
        print(f"\n══ {name} (primary: next-day exec, cash @6%) ══")
        print(f"  strategy: {json.dumps(primary['strategy'])}")
        print(f"  buy&hold: {json.dumps(primary['buy_hold'])}")
        print(f"  exposure {primary['avg_exposure']}, turnover {primary['ann_turnover_x']}x/yr, "
              f"beta {primary['beta']}, Jensen α {primary['jensen_alpha_ann_pct']}%/yr (t={primary['alpha_t']})")
        print(f"  capture up {primary['up_capture_pct']}% / down {primary['down_capture_pct']}%")
        print(f"  by year: {json.dumps(primary['by_year_pct'])}")

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nSaved → {OUT_PATH}")


if __name__ == "__main__":
    main()
