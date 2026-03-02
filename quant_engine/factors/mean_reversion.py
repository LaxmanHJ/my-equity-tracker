"""
Mean Reversion Factor
Measures how far current price deviates from its 50-day simple moving average.
Overbought (Z > +2) → Short (-1), Oversold (Z < -2) → Long (+1).
"""
import numpy as np
import pandas as pd


def calculate(df: pd.DataFrame) -> dict:
    """
    Calculate mean reversion factor score using Z-score vs 50-day SMA.
    """
    close = df["close"]
    period = 50

    if len(close) < period:
        return {"sma_50": None, "z_score": 0.0, "score": 0.0}

    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    current_price = close.iloc[-1]
    current_sma = sma.iloc[-1]
    current_std = std.iloc[-1]

    if current_std == 0 or np.isnan(current_std):
        return {"sma_50": round(float(current_sma), 2), "z_score": 0.0, "score": 0.0}

    z_score = (current_price - current_sma) / current_std

    # Inverted: high Z-score (overbought) → negative score (short)
    # Low Z-score (oversold) → positive score (long)
    score = np.clip(-z_score / 2.0, -1.0, 1.0)

    return {
        "sma_50": round(float(current_sma), 2),
        "z_score": round(float(z_score), 4),
        "score": round(float(score), 4),
    }
