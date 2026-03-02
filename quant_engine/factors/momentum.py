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

    # Calculate returns over different lookback windows
    ret_1m = (close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0.0
    ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) if len(close) >= 63 else 0.0
    ret_6m = (close.iloc[-1] / close.iloc[-126] - 1) if len(close) >= 126 else 0.0

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
