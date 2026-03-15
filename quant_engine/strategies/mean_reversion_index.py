"""
Enhanced Mean Reversion Strategy for Market Indexes.
Uses dual-timeframe Z-scores, Bollinger Band position, and RSI confirmation
to generate oversold/overbought signals.
"""
import numpy as np
import pandas as pd


def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    """Compute RSI for the most recent data point."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()

    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else 100.0
    return 100.0 - (100.0 / (1.0 + rs))


def calculate(df: pd.DataFrame) -> dict:
    """
    Run enhanced mean reversion analysis on index price data.

    Args:
        df: DataFrame with 'close' column, indexed by date.

    Returns:
        dict with Z-scores, Bollinger position, RSI, signal, and strength.
    """
    if df.empty or len(df) < 50:
        return {
            "z_score_20": 0.0,
            "z_score_50": 0.0,
            "bollinger_pct": 0.5,
            "rsi": 50.0,
            "signal": "INSUFFICIENT_DATA",
            "strength": 0.0,
            "sma_20": None,
            "sma_50": None,
            "upper_band": None,
            "lower_band": None,
        }

    close = df["close"].astype(float)
    current_price = float(close.iloc[-1])

    # --- Z-scores vs SMAs ---
    sma_20 = close.rolling(20).mean()
    std_20 = close.rolling(20).std()
    sma_50 = close.rolling(50).mean()
    std_50 = close.rolling(50).std()

    z_20 = float((current_price - sma_20.iloc[-1]) / std_20.iloc[-1]) if std_20.iloc[-1] > 0 else 0.0
    z_50 = float((current_price - sma_50.iloc[-1]) / std_50.iloc[-1]) if std_50.iloc[-1] > 0 else 0.0

    # --- Bollinger Bands (20-day, 2σ) ---
    upper_band = float(sma_20.iloc[-1] + 2 * std_20.iloc[-1])
    lower_band = float(sma_20.iloc[-1] - 2 * std_20.iloc[-1])
    band_width = upper_band - lower_band
    bollinger_pct = (current_price - lower_band) / band_width if band_width > 0 else 0.5

    # --- RSI ---
    rsi = _compute_rsi(close)

    # --- Composite Signal ---
    # Score ranges from -1 (strong oversold/buy) to +1 (strong overbought/sell)
    z_component = (z_20 * 0.6 + z_50 * 0.4) / 2.0  # Normalized Z blend
    bb_component = (bollinger_pct - 0.5) * 2.0        # -1 to +1
    rsi_component = (rsi - 50) / 50.0                  # -1 to +1

    raw_score = z_component * 0.4 + bb_component * 0.3 + rsi_component * 0.3
    strength = float(np.clip(abs(raw_score), 0, 1))

    # Signal classification
    if raw_score < -0.3 and rsi < 35:
        signal = "OVERSOLD_BUY"
    elif raw_score > 0.3 and rsi > 65:
        signal = "OVERBOUGHT_SELL"
    elif abs(raw_score) > 0.15:
        signal = "MILD_OVERSOLD" if raw_score < 0 else "MILD_OVERBOUGHT"
    else:
        signal = "NEUTRAL"

    return {
        "z_score_20": round(z_20, 4),
        "z_score_50": round(z_50, 4),
        "bollinger_pct": round(float(bollinger_pct), 4),
        "rsi": round(float(rsi), 2),
        "signal": signal,
        "strength": round(strength, 4),
        "sma_20": round(float(sma_20.iloc[-1]), 2),
        "sma_50": round(float(sma_50.iloc[-1]), 2),
        "upper_band": round(upper_band, 2),
        "lower_band": round(lower_band, 2),
    }
