"""
RSI Factor
Relative Strength Index extreme detection.
RSI < 30 (oversold) → Long (+1), RSI > 70 (overbought) → Short (-1).
"""
import numpy as np
import pandas as pd


def calculate(df: pd.DataFrame, period: int = 14) -> dict:
    """
    Calculate RSI and convert to a factor score.
    """
    close = df["close"]

    if len(close) < period + 1:
        return {"rsi": None, "score": 0.0}

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    current_rsi = float(rsi.iloc[-1])

    if np.isnan(current_rsi):
        return {"rsi": None, "score": 0.0}

    # Map RSI to score (trend-momentum confirmation):
    # RSI > 50 means avg gains > avg losses → trend is positive → bullish.
    # RSI 80 → +1.0 (strong uptrend), RSI 50 → 0.0 (neutral), RSI 20 → -1.0 (downtrend).
    score = np.clip((current_rsi - 50) / 30.0, -1.0, 1.0)

    return {
        "rsi": round(current_rsi, 2),
        "score": round(float(score), 4),
    }
