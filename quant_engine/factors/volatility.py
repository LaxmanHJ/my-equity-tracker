"""
Volatility Regime Factor
Compares current realized volatility to its historical average.
Contracting volatility (stable) → mild Long (+0.5), expanding (risky) → mild Short (-0.5).
"""
import numpy as np
import pandas as pd


def calculate(df: pd.DataFrame, short_window: int = 20, long_window: int = 60) -> dict:
    """
    Detect volatility regime by comparing short-window vol to long-window vol.
    """
    close = df["close"]

    if len(close) < long_window + 1:
        return {"vol_short": None, "vol_long": None, "vol_ratio": None, "score": 0.0}

    returns = close.pct_change().dropna()

    vol_short = float(returns.tail(short_window).std() * np.sqrt(252))
    vol_long = float(returns.tail(long_window).std() * np.sqrt(252))

    if vol_long == 0:
        return {"vol_short": round(vol_short * 100, 2), "vol_long": 0, "vol_ratio": None, "score": 0.0}

    vol_ratio = vol_short / vol_long

    # vol_ratio < 0.8 → volatility contracting → positive (calm, good for holds)
    # vol_ratio > 1.2 → volatility expanding → negative (risky, caution)
    # Map: 0.6 → +1.0, 1.0 → 0.0, 1.4 → -1.0
    score = np.clip((1.0 - vol_ratio) / 0.4, -1.0, 1.0)

    return {
        "vol_short": round(vol_short * 100, 2),
        "vol_long": round(vol_long * 100, 2),
        "vol_ratio": round(vol_ratio, 4),
        "regime": "CONTRACTING" if vol_ratio < 0.85 else ("EXPANDING" if vol_ratio > 1.15 else "STABLE"),
        "score": round(float(score), 4),
    }
