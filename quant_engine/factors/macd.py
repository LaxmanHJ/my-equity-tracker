"""
MACD Factor
Moving Average Convergence/Divergence trend and crossover detection.
Bullish crossover / positive histogram → Long (+1), Bearish → Short (-1).
"""
import numpy as np
import pandas as pd


def calculate(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> dict:
    """
    Calculate MACD and convert histogram direction to a factor score.
    """
    close = df["close"]

    if len(close) < slow + signal:
        return {"macd_line": None, "signal_line": None, "histogram": None, "score": 0.0}

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    current_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2])

    # Magnitude-based score: larger histogram = stronger conviction
    # Normalize histogram by current price to make it comparable across stocks
    current_price = float(close.iloc[-1])
    normalized_hist = current_hist / current_price * 100  # as percentage of price

    # Crossover bonus: if histogram just flipped sign, amplify the signal
    crossover_bonus = 0.0
    if prev_hist < 0 and current_hist > 0:
        crossover_bonus = 0.3  # bullish crossover
    elif prev_hist > 0 and current_hist < 0:
        crossover_bonus = -0.3  # bearish crossover

    base_score = np.clip(normalized_hist / 1.0, -0.7, 0.7)
    score = np.clip(base_score + crossover_bonus, -1.0, 1.0)

    return {
        "macd_line": round(float(macd_line.iloc[-1]), 4),
        "signal_line": round(float(signal_line.iloc[-1]), 4),
        "histogram": round(current_hist, 4),
        "histogram_pct": round(float(normalized_hist), 4),
        "crossover": "BULLISH" if crossover_bonus > 0 else ("BEARISH" if crossover_bonus < 0 else "NONE"),
        "score": round(float(score), 4),
    }
