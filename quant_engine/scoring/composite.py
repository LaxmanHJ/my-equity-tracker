"""
Composite Scoring Engine
Combines all 7 factor scores into a single -100 to +100 composite score
and classifies as LONG / HOLD / SHORT.
"""
from typing import Optional, List
import pandas as pd
from quant_engine.config import FACTOR_WEIGHTS, LONG_THRESHOLD, SHORT_THRESHOLD
from quant_engine.data.loader import load_price_history, load_all_symbols, load_benchmark
from quant_engine.factors import (
    momentum,
    mean_reversion,
    rsi,
    macd,
    volatility,
    volume,
    relative_strength,
)


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
        "momentum": momentum.calculate(df),
        "mean_reversion": mean_reversion.calculate(df),
        "rsi": rsi.calculate(df),
        "macd": macd.calculate(df),
        "volatility": volatility.calculate(df),
        "volume": volume.calculate(df),
        "relative_strength": relative_strength.calculate(df, benchmark_df),
    }

    # Compute weighted composite score
    composite = 0.0
    for factor_name, weight in FACTOR_WEIGHTS.items():
        factor_score = factors.get(factor_name, {}).get("score", 0.0)
        composite += factor_score * weight

    # Scale to -100 to +100
    composite_score = round(composite * 100, 2)

    # Classify signal
    if composite_score >= LONG_THRESHOLD:
        signal = "LONG"
    elif composite_score <= SHORT_THRESHOLD:
        signal = "SHORT"
    else:
        signal = "HOLD"

    return {
        "symbol": symbol,
        "composite_score": composite_score,
        "signal": signal,
        "factors": factors,
        "price": round(float(df["close"].iloc[-1]), 2),
        "data_points": len(df),
    }


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
