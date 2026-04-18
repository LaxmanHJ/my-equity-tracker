"""
Composite Scoring Engine
Combines all 7 factor scores into a single -100 to +100 composite score
and classifies as LONG / HOLD / SHORT.

Signal priority:
  1. ML path  — if a trained Random Forest model is available, the ML verdict
     (BUY / SELL / HOLD) is the primary signal.
  2. Linear fallback — weighted sum of the 7 factor scores when ML is unavailable.
"""
import logging
from typing import Optional, List

import numpy as np
import pandas as pd

from quant_engine.config import FACTOR_WEIGHTS, LONG_THRESHOLD, SHORT_THRESHOLD, INDUSTRY_TO_NSE_INDEX
from quant_engine.scoring.ic_weights import get_active_weights
from quant_engine.data.loader import load_price_history, load_all_symbols, load_benchmark, load_industry_map
from quant_engine.data.delivery_loader import load_circuit_status, load_delivery_series
from quant_engine.data.sector_indices_loader import load_sector_series
from quant_engine.data.market_regime_loader import (
    load_vix_score_today,
    build_markov_score_series,
    load_fii_flow_score_today,
    load_fii_fo_score_today,
    load_pcr_score_today,
)
from quant_engine.data.intraday_features import build_intraday_features
from quant_engine.factors import (
    momentum,
    bollinger,
    rsi,
    macd,
    volatility,
    volume,
    relative_strength,
)

logger = logging.getLogger(__name__)


def _build_ml_sub_scores(
    symbol: str,
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    factors: dict,
) -> dict:
    """
    Build the 18-feature dict that predictor.predict() expects, for the latest bar.

    7 features come directly from already-computed factor modules.
    `trend_ma` is computed inline (no standalone factor module exists for it).
    7 more are macro/regime scalars (VIX, Nifty trend, Markov, delivery, sector,
    FII flow/F&O, PCR). Final 3 are intraday-derived scalars (overnight_gap,
    intraday_range_ratio, last_hour_momentum) pulled from build_intraday_features.

    Args:
        symbol:       NSE ticker.
        df:           Price history DataFrame (≥50 rows recommended).
        benchmark_df: Benchmark (NIFTY) OHLCV DataFrame.
        factors:      Already-computed factor dict from score_single_stock().

    Returns:
        dict with one float per FEATURE_COL key.
    """
    # ── Technical scores (directly from factor modules) ──────────────────────
    sub = {
        "rsi":               factors["rsi"]["score"],
        "macd":              factors["macd"]["score"],
        "bollinger":         factors["bollinger"]["score"],
        "volume":            factors["volume"]["score"],
        "volatility":        factors["volatility"]["score"],
        "relative_strength": factors["relative_strength"]["score"],
    }

    # ── trend_ma — price vs SMA20/50 blend (mirrors trainer._rolling_trend_score) ──
    close  = df["close"]
    sma20  = close.rolling(20).mean()
    sma50  = close.rolling(50).mean()
    last   = close.iloc[-1]
    s20    = sma20.iloc[-1]
    s50    = sma50.iloc[-1]
    tma    = 0.0
    tma   += 0.25 if (not np.isnan(s20) and last > s20) else -0.25
    tma   += 0.25 if (not np.isnan(s50) and last > s50) else -0.25
    tma   += 0.50 if (not np.isnan(s20) and not np.isnan(s50) and s20 > s50) else -0.50
    sub["trend_ma"] = float(np.clip(tma, -1.0, 1.0))

    # ── VIX regime scalar ────────────────────────────────────────────────────
    vix_score = load_vix_score_today()
    sub["vix_regime"] = float(vix_score) if vix_score is not None else 0.0

    # ── NIFTY trend scalar (price vs SMA50 / SMA200) ─────────────────────────
    if not benchmark_df.empty and "close" in benchmark_df.columns:
        bc     = benchmark_df["close"]
        bsma50  = bc.rolling(50, min_periods=30).mean()
        bsma200 = bc.rolling(200, min_periods=100).mean()
        vs50    = ((bc - bsma50)  / bsma50.replace(0, float("nan"))  * 10).clip(-1.0, 1.0)
        vs200   = ((bc - bsma200) / bsma200.replace(0, float("nan")) * 10).clip(-1.0, 1.0)
        nifty_trend_series = (0.5 * vs50 + 0.5 * vs200).fillna(0.0)
        sub["nifty_trend"] = float(nifty_trend_series.iloc[-1])
    else:
        sub["nifty_trend"] = 0.0

    # ── Markov regime scalar ─────────────────────────────────────────────────
    if not benchmark_df.empty:
        markov_series = build_markov_score_series(benchmark_df)
        sub["markov_regime"] = float(markov_series.iloc[-1]) if not markov_series.empty else 0.0
    else:
        sub["markov_regime"] = 0.0

    # ── Delivery score scalar ────────────────────────────────────────────────
    delivery_df = load_delivery_series(symbol, limit=200)
    if not delivery_df.empty and "delivery_pct" in delivery_df.columns:
        dpct      = delivery_df["delivery_pct"]
        roll_mean = dpct.rolling(60, min_periods=10).mean()
        roll_std  = dpct.rolling(60, min_periods=10).std().replace(0, 1)
        last_dpct = float(dpct.iloc[-1])
        rm        = float(roll_mean.iloc[-1]) if not np.isnan(roll_mean.iloc[-1]) else last_dpct
        rs_       = float(roll_std.iloc[-1])  if not np.isnan(roll_std.iloc[-1])  else 1.0
        sub["delivery_score"] = float(np.clip((last_dpct - rm) / rs_ / 3, -1.0, 1.0))
    else:
        sub["delivery_score"] = 0.0

    # ── Sector rotation scalar ───────────────────────────────────────────────
    industry_map = load_industry_map()
    industry     = industry_map.get(symbol)
    nse_index    = INDUSTRY_TO_NSE_INDEX.get(industry, "Nifty 500") if industry else "Nifty 500"
    idx_close    = load_sector_series(nse_index, limit=60)
    nifty_close  = load_sector_series("Nifty 50",  limit=60)
    if not idx_close.empty and len(idx_close) >= 20:
        sector_20d = float(idx_close.pct_change(20).iloc[-1])
        if not nifty_close.empty and len(nifty_close) >= 20:
            bench_20d = float(nifty_close.pct_change(20).iloc[-1])
            excess    = sector_20d - bench_20d
        else:
            excess = sector_20d
        sub["sector_rotation"] = float(np.clip(excess / 0.20, -1.0, 1.0))
    else:
        sub["sector_rotation"] = 0.0

    # ── FII flows scalars ────────────────────────────────────────────────────
    sub["fii_flow_score"] = load_fii_flow_score_today()
    sub["fii_fo_score"]   = load_fii_fo_score_today()

    # ── PCR sentiment scalar ─────────────────────────────────────────────────
    sub["pcr_score"] = load_pcr_score_today()

    # ── Intraday-derived scalars (latest available bar from 15-min candles) ──
    intraday_feats = build_intraday_features(symbol)
    if not intraday_feats.empty:
        last = intraday_feats.iloc[-1]
        sub["overnight_gap"]        = float(last.get("overnight_gap", 0.0))
        sub["intraday_range_ratio"] = float(last.get("intraday_range_ratio", 0.0))
        sub["last_hour_momentum"]   = float(last.get("last_hour_momentum", 0.0))
    else:
        sub["overnight_gap"]        = 0.0
        sub["intraday_range_ratio"] = 0.0
        sub["last_hour_momentum"]   = 0.0

    return sub


def score_single_stock(symbol: str, benchmark_df: pd.DataFrame) -> Optional[dict]:
    """
    Calculate all factor scores and the composite for a single stock.
    Returns None if insufficient data.
    """
    df = load_price_history(symbol, limit=365)

    if df.empty or len(df) < 30:
        return None

    # Calculate each factor
    factors = {
        "momentum":          momentum.calculate(df),
        "bollinger":         bollinger.calculate(df),
        "rsi":               rsi.calculate(df),
        "macd":              macd.calculate(df),
        "volatility":        volatility.calculate(df),
        "volume":            volume.calculate(df),
        "relative_strength": relative_strength.calculate(df, benchmark_df),
    }

    # Compute weighted composite score using IC-adaptive weights
    active_weights = get_active_weights()
    composite = 0.0
    for factor_name, weight in active_weights.items():
        factor_score = factors.get(factor_name, {}).get("score", 0.0)
        composite += factor_score * weight

    # Scale to -100 to +100
    composite_score = round(composite * 100, 2)

    # ── Linear signal — always computed independently ─────────────────────────
    if composite_score >= LONG_THRESHOLD:
        linear_signal = "LONG"
    elif composite_score <= SHORT_THRESHOLD:
        linear_signal = "SHORT"
    else:
        linear_signal = "HOLD"

    # ── ML signal — primary when model is available ───────────────────────────
    ml_result  = None
    ml_signal  = None

    try:
        from quant_engine.ml import predictor as _predictor
        if _predictor.is_model_available():
            sub_scores = _build_ml_sub_scores(symbol, df, benchmark_df, factors)
            ml_result  = _predictor.predict(sub_scores)
            if ml_result is not None:
                verdict_map = {"BUY": "LONG", "SELL": "SHORT", "HOLD": "HOLD"}
                ml_signal = verdict_map.get(ml_result["verdict"], "HOLD")
                logger.debug(
                    "ML signal for %s: %s (confidence %.1f%%)",
                    symbol, ml_result["verdict"], ml_result["confidence"],
                )
    except Exception as exc:
        logger.warning("ML path failed for %s, using linear signal: %s", symbol, exc)

    # Primary signal: ML when available, linear otherwise
    signal = ml_signal if ml_signal is not None else linear_signal

    # Circuit breaker override (applies to both)
    circuit = load_circuit_status(symbol)
    if circuit == -1 and signal == "LONG":
        signal = "HOLD"
    if circuit == -1 and linear_signal == "LONG":
        linear_signal = "HOLD"

    result = {
        "symbol":          symbol,
        "composite_score": composite_score,
        "signal":          signal,
        "linear_signal":   linear_signal,
        "factors":         factors,
        "price":           round(float(df["close"].iloc[-1]), 2),
        "data_points":     len(df),
        "ml_path":         ml_result is not None,
    }
    if ml_result is not None:
        result["ml_verdict"]       = ml_result["verdict"]
        result["ml_confidence"]    = ml_result["confidence"]
        result["ml_probabilities"] = ml_result["probabilities"]

    return result


def score_all_stocks() -> List[dict]:
    """
    Score every stock in the database and return sorted by composite score.
    """
    symbols = load_all_symbols()
    benchmark_df = load_benchmark()

    results = []
    for symbol in symbols:
        result = score_single_stock(symbol, benchmark_df)
        if result:
            results.append(result)

    # Sort by composite score descending (strongest long first)
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results
