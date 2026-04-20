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

import pandas as pd

from quant_engine.config import LONG_THRESHOLD, SHORT_THRESHOLD
from quant_engine.scoring.ic_weights import get_active_weights
from quant_engine.data.loader import load_price_history, load_all_symbols, load_benchmark
from quant_engine.data.delivery_loader import load_circuit_status
from quant_engine.strategies.sicilian_strategy import SicilianStrategy
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
) -> Optional[dict]:
    """
    Build the feature dict for predictor.predict() on the latest bar.

    Delegates to SicilianStrategy._build_ml_features — the same builder the
    backtest uses — then returns the last row as a dict. This guarantees that
    the ML features behind today's live signal are computed identically to
    the features driving the backtest, so backtest confidence translates
    directly into live-trading confidence.

    SIC-29 contract: only returns None when the builder yields nothing or a
    HARD_GATE (price-derived) feature is NaN. Soft NaN (macro / sector /
    intraday) is passed through — the pipeline's SimpleImputer fills them
    with training medians downstream.
    """
    from quant_engine.ml.predictor import HARD_GATE_FEATURES

    strat = SicilianStrategy("_live")
    feats = strat._build_ml_features(df, benchmark_df, symbol)
    if feats.empty:
        return None
    last_row = feats.iloc[-1]

    hard_missing = [
        c for c in HARD_GATE_FEATURES
        if c not in last_row.index or pd.isna(last_row.get(c))
    ]
    if hard_missing:
        logger.info(
            "ML skipped for %s: hard-gate features NaN on latest bar (%s)",
            symbol, hard_missing,
        )
        return None

    soft_missing = last_row[last_row.isna()].index.tolist()
    if soft_missing:
        logger.info(
            "ML imputing for %s: %d soft features NaN on latest bar (%s)",
            symbol, len(soft_missing), soft_missing,
        )

    return last_row.to_dict()


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

    ml_unavailable = False  # True means model exists but features incomplete on this bar
    try:
        from quant_engine.ml import predictor as _predictor
        if _predictor.is_model_available():
            sub_scores = _build_ml_sub_scores(symbol, df, benchmark_df)
            if sub_scores is None:
                # Feature(s) NaN on latest bar — matches backtest which takes no
                # position on such bars. Emit HOLD instead of linear so live
                # decisions stay bit-consistent with Sicilian (ML) backtest.
                ml_unavailable = True
            else:
                ml_result = _predictor.predict(sub_scores)
                if ml_result is not None:
                    verdict_map = {"BUY": "LONG", "SELL": "SHORT", "HOLD": "HOLD"}
                    ml_signal = verdict_map.get(ml_result["verdict"], "HOLD")
                    logger.debug(
                        "ML signal for %s: %s (confidence %.1f%%)",
                        symbol, ml_result["verdict"], ml_result["confidence"],
                    )
    except (ValueError, AttributeError) as exc:
        # Narrow catch for transient sklearn issues (shape/dtype). A RuntimeError
        # from _verify_feature_alignment (SIC-29) is a deployment bug and MUST
        # propagate so we notice immediately instead of silently running linear.
        logger.warning("ML path failed for %s, using linear signal: %s", symbol, exc)

    # Primary signal: ML verdict when the model ran; HOLD when model exists but
    # features were NaN on this bar (matches Sicilian (ML) backtest — no position);
    # linear signal only when the trained model isn't available at all.
    if ml_signal is not None:
        signal = ml_signal
    elif ml_unavailable:
        signal = "HOLD"
    else:
        signal = linear_signal

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
