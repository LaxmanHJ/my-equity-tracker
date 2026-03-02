"""
Volume Spike Factor
Detects unusual volume activity relative to the 20-day average.
A volume spike in the direction of the trend confirms momentum.
"""
import numpy as np
import pandas as pd


def calculate(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Detect volume spikes and determine if they confirm the price trend.
    """
    close = df["close"]
    volume = df["volume"]

    if len(close) < lookback + 1 or volume.sum() == 0:
        return {"current_volume": None, "avg_volume": None, "volume_ratio": None, "score": 0.0}

    avg_vol = float(volume.tail(lookback).mean())
    current_vol = float(volume.iloc[-1])

    if avg_vol == 0:
        return {"current_volume": current_vol, "avg_volume": 0, "volume_ratio": None, "score": 0.0}

    vol_ratio = current_vol / avg_vol

    # Determine price direction (last 5 days)
    price_change_5d = (close.iloc[-1] / close.iloc[-5] - 1) if len(close) >= 5 else 0.0

    # Volume spike (> 1.5x avg) in the direction of trend → confirms signal
    if vol_ratio > 1.5:
        # Strong volume spike: amplify the trend direction
        if price_change_5d > 0:
            score = min(vol_ratio / 3.0, 1.0)   # up trend + volume = long
        else:
            score = max(-vol_ratio / 3.0, -1.0)  # down trend + volume = short
    elif vol_ratio > 1.2:
        # Mild spike: slight confirmation
        score = 0.3 if price_change_5d > 0 else -0.3
    else:
        # No unusual volume
        score = 0.0

    return {
        "current_volume": int(current_vol),
        "avg_volume_20d": int(avg_vol),
        "volume_ratio": round(vol_ratio, 2),
        "price_trend_5d": round(float(price_change_5d * 100), 2),
        "spike": vol_ratio > 1.5,
        "score": round(float(score), 4),
    }
