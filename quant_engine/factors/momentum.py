"""
Momentum Factor
Measures price returns over 1-month, 3-month, and 6-month windows.
Strong upward momentum → Long (+1), strong downward momentum → Short (-1).
"""
import numpy as np
import pandas as pd


def calculate(df: pd.DataFrame) -> dict:
    """
    Calculate momentum factor score.

    Args:
        df: DataFrame with 'close' column, indexed by date.

    Returns:
        dict with raw metrics and the normalized score (-1 to +1).
    """
    close = df["close"]

    # Calculate returns over different lookback windows, guarding against NaN/zero denominators
    def _safe_return(series, lookback):
        if len(series) < lookback:
            return 0.0
        denom = series.iloc[-lookback]
        if not np.isfinite(denom) or denom == 0:
            return 0.0
        val = series.iloc[-1] / denom - 1
        return float(val) if np.isfinite(val) else 0.0

    ret_1m = _safe_return(close, 21)
    ret_3m = _safe_return(close, 63)
    ret_6m = _safe_return(close, 126)

    # Weighted average of the three windows (recent momentum weighted more)
    raw_momentum = 0.2 * ret_1m + 0.4 * ret_3m + 0.4 * ret_6m

    # Normalize: clip to [-50%, +50%] range, then scale to [-1, +1]
    score = np.clip(raw_momentum / 0.50, -1.0, 1.0)

    return {
        "return_1m": round(float(ret_1m * 100), 2),
        "return_3m": round(float(ret_3m * 100), 2),
        "return_6m": round(float(ret_6m * 100), 2),
        "raw_momentum": round(float(raw_momentum * 100), 2),
        "score": round(float(score), 4),
    }
