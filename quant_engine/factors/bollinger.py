"""
Bollinger Band Factor
Measures where the current price sits within its 20-day Bollinger Bands (%B).

%B < 0.2 (near/below lower band) → oversold → Long (+1)
%B > 0.8 (near/above upper band) → overbought → Short (-1)
%B = 0.5 (at midline)           → neutral (0)

Unlike the raw Z-score mean-reversion factor, Bollinger bands adapt their
width to recent volatility — so in a high-volatility period the bands widen
and price needs to move further before scoring extreme. This makes the signal
more robust than a fixed-window Z-score.

ML feature importance: 9.3% (5th highest of 15 features).
"""
import numpy as np
import pandas as pd


def calculate(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> dict:
    """
    Calculate Bollinger %B and convert to a factor score in [-1, +1].

    Args:
        df:        DataFrame with 'close' column, indexed by date.
        period:    Lookback window for SMA and rolling std (default 20).
        std_mult:  Band width multiplier (default 2.0).

    Returns:
        dict with pct_b, upper, lower, sma, and score.
    """
    close = df["close"]

    if len(close) < period:
        return {"pct_b": 0.5, "upper": None, "lower": None, "sma": None, "score": 0.0}

    sma   = close.rolling(period).mean()
    std   = close.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    bw    = upper - lower

    current_price = float(close.iloc[-1])
    current_sma   = float(sma.iloc[-1])
    current_upper = float(upper.iloc[-1])
    current_lower = float(lower.iloc[-1])
    current_bw    = float(bw.iloc[-1])

    if current_bw == 0 or not np.isfinite(current_bw):
        pct_b = 0.5
    else:
        pct_b = (current_price - current_lower) / current_bw

    # (0.5 - pct_b) * 2:
    #   pct_b = 0.0 (at/below lower band) → score = +1.0
    #   pct_b = 0.5 (at midline)          → score =  0.0
    #   pct_b = 1.0 (at/above upper band) → score = -1.0
    score = float(np.clip((0.5 - pct_b) * 2.0, -1.0, 1.0))

    return {
        "pct_b":  round(float(pct_b), 4),
        "upper":  round(current_upper, 2),
        "lower":  round(current_lower, 2),
        "sma":    round(current_sma, 2),
        "score":  round(score, 4),
    }
