"""
The Sicilian — Unified Buy/Sell Decision Engine.

Aggregates 8 sub-scores from across the system into a single
BUY / SELL / HOLD verdict with next-day target prices and confidence level.
"""
import numpy as np
import pandas as pd
from typing import Optional

from quant_engine.config import FACTOR_WEIGHTS, LONG_THRESHOLD, SHORT_THRESHOLD
from quant_engine.data.loader import load_price_history, load_benchmark
from quant_engine.scoring.composite import score_single_stock

# ── Sicilian weights for sub-scores ──────────────────────────────
SICILIAN_WEIGHTS = {
    "composite_factor": 0.30,
    "rsi":              0.12,
    "macd":             0.12,
    "trend_ma":         0.12,
    "bollinger":        0.10,
    "volume":           0.08,
    "volatility":       0.08,
    "relative_strength": 0.08,
}

# Verdict thresholds on –1 to +1 scale
BUY_THRESHOLD  =  0.35
SELL_THRESHOLD = -0.35


# ── Helper: compute technical indicators from raw price series ───
def _compute_sma(close: pd.Series, period: int) -> Optional[float]:
    if len(close) < period:
        return None
    return float(close.rolling(period).mean().iloc[-1])


def _compute_ema(close: pd.Series, period: int) -> Optional[float]:
    if len(close) < period:
        return None
    return float(close.ewm(span=period, adjust=False).mean().iloc[-1])


def _compute_rsi(close: pd.Series, period: int = 14) -> Optional[float]:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    val = float(rsi_series.iloc[-1])
    return val if not np.isnan(val) else None


def _compute_macd(close: pd.Series, fast=12, slow=26, signal=9) -> dict:
    if len(close) < slow + signal:
        return {"histogram": 0.0, "crossover": "NONE"}
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    current_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2])
    crossover = "NONE"
    if prev_hist < 0 and current_hist > 0:
        crossover = "BULLISH"
    elif prev_hist > 0 and current_hist < 0:
        crossover = "BEARISH"
    return {"histogram": current_hist, "crossover": crossover}


def _compute_bollinger(close: pd.Series, period=20, std_mult=2) -> dict:
    if len(close) < period:
        return {"pct_b": 0.5, "upper": None, "lower": None, "middle": None}
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = float(sma.iloc[-1] + std_mult * std.iloc[-1])
    lower = float(sma.iloc[-1] - std_mult * std.iloc[-1])
    middle = float(sma.iloc[-1])
    current = float(close.iloc[-1])
    band_width = upper - lower
    pct_b = (current - lower) / band_width if band_width > 0 else 0.5
    return {"pct_b": pct_b, "upper": upper, "lower": lower, "middle": middle}


# ── Sub-score calculators (each returns –1 to +1) ───────────────

def _score_composite(composite_score: float) -> float:
    """Normalize composite score from –100/+100 to –1/+1."""
    return np.clip(composite_score / 100.0, -1.0, 1.0)


def _score_rsi(rsi_val: Optional[float]) -> float:
    if rsi_val is None:
        return 0.0
    # RSI 20 → +1.0 (strong buy), RSI 50 → 0.0, RSI 80 → –1.0 (strong sell)
    return float(np.clip((50 - rsi_val) / 30.0, -1.0, 1.0))


def _score_macd(macd_info: dict, current_price: float) -> float:
    hist = macd_info["histogram"]
    normalized = hist / current_price * 100 if current_price > 0 else 0.0
    crossover_bonus = 0.0
    if macd_info["crossover"] == "BULLISH":
        crossover_bonus = 0.3
    elif macd_info["crossover"] == "BEARISH":
        crossover_bonus = -0.3
    base = np.clip(normalized / 1.0, -0.7, 0.7)
    return float(np.clip(base + crossover_bonus, -1.0, 1.0))


def _score_trend(price: float, sma20: Optional[float], sma50: Optional[float]) -> float:
    if sma20 is None or sma50 is None:
        return 0.0
    score = 0.0
    # Price vs SMA20
    if price > sma20:
        score += 0.25
    else:
        score -= 0.25
    # Price vs SMA50
    if price > sma50:
        score += 0.25
    else:
        score -= 0.25
    # SMA20 vs SMA50 (golden/death cross territory)
    if sma20 > sma50:
        score += 0.5
    else:
        score -= 0.5
    return float(np.clip(score, -1.0, 1.0))


def _score_bollinger(pct_b: float) -> float:
    # %B < 0.2 → strong buy (+0.8), %B > 0.8 → strong sell (–0.8)
    # %B = 0.5 → neutral (0)
    return float(np.clip((0.5 - pct_b) * 2.0, -1.0, 1.0))


def _score_volume(df: pd.DataFrame, lookback: int = 20) -> float:
    close = df["close"]
    volume = df["volume"]
    if len(close) < lookback + 1 or volume.sum() == 0:
        return 0.0
    avg_vol = float(volume.tail(lookback).mean())
    current_vol = float(volume.iloc[-1])
    if avg_vol == 0:
        return 0.0
    vol_ratio = current_vol / avg_vol
    price_change = (close.iloc[-1] / close.iloc[-5] - 1) if len(close) >= 5 else 0.0
    if vol_ratio > 1.5:
        return float(np.clip(vol_ratio / 3.0, 0, 1.0)) if price_change > 0 else float(np.clip(-vol_ratio / 3.0, -1.0, 0))
    elif vol_ratio > 1.2:
        return 0.3 if price_change > 0 else -0.3
    return 0.0


def _score_volatility(df: pd.DataFrame, short_w=20, long_w=60) -> float:
    close = df["close"]
    if len(close) < long_w + 1:
        return 0.0
    returns = close.pct_change().dropna()
    vol_short = float(returns.tail(short_w).std() * np.sqrt(252))
    vol_long = float(returns.tail(long_w).std() * np.sqrt(252))
    if vol_long == 0:
        return 0.0
    vol_ratio = vol_short / vol_long
    return float(np.clip((1.0 - vol_ratio) / 0.4, -1.0, 1.0))


def _score_relative_strength(df: pd.DataFrame, benchmark_df: pd.DataFrame, period=63) -> float:
    close = df["close"]
    if len(close) < period or benchmark_df.empty or len(benchmark_df) < period:
        return 0.0
    stock_ret = float(close.iloc[-1] / close.iloc[-period] - 1)
    bench_ret = float(benchmark_df["close"].iloc[-1] / benchmark_df["close"].iloc[-period] - 1)
    excess = stock_ret - bench_ret
    return float(np.clip(excess / 0.20, -1.0, 1.0))


# ── Target price calculators ────────────────────────────────────

def _compute_buy_target(current_price: float, sma20: float, bb_lower: float, z_score_20: float) -> float:
    """
    BUY target: blend of SMA20 with a slight discount (based on Z-score)
    and the lower Bollinger band as support.
    """
    if sma20 is None or bb_lower is None:
        return round(current_price * 0.99, 2)  # simple 1% discount fallback
    # Ideal entry: midpoint between lower band and SMA20
    ideal_entry = (bb_lower + sma20) / 2.0
    # If stock is already below SMA20, use current price area
    target = min(current_price, ideal_entry)
    # Don't suggest a target more than 5% below current price
    floor = current_price * 0.95
    return round(max(target, floor), 2)


def _compute_sell_target(current_price: float, sma20: float, bb_upper: float) -> float:
    """
    SELL target: blend of SMA20 with a premium and the upper Bollinger band.
    """
    if sma20 is None or bb_upper is None:
        return round(current_price * 1.01, 2)
    ideal_exit = (bb_upper + sma20) / 2.0
    target = max(current_price, ideal_exit)
    ceiling = current_price * 1.05
    return round(min(target, ceiling), 2)


# ── Main engine ─────────────────────────────────────────────────

def run_sicilian(symbol: str) -> dict:
    """
    Run The Sicilian analysis on a single stock.

    Returns a dict with:
        - verdict: BUY / SELL / HOLD
        - sicilian_score: –1 to +1
        - confidence: 0–100%
        - target_price: next-day target
        - sub_scores: breakdown of all 8 sub-scores
        - support_resistance: key price levels
    """
    # Load data
    df = load_price_history(symbol, limit=365)
    if df.empty or len(df) < 30:
        return {
            "symbol": symbol,
            "verdict": "INSUFFICIENT_DATA",
            "sicilian_score": 0,
            "confidence": 0,
            "target_price": None,
            "target_type": None,
            "reasoning": "Not enough price data (need ≥ 30 days)",
            "sub_scores": {},
            "support_resistance": {},
        }

    benchmark_df = load_benchmark()
    close = df["close"]
    current_price = float(close.iloc[-1])

    # ── Compute the composite factor score (existing system) ─────
    composite_result = score_single_stock(symbol, benchmark_df)
    composite_score = composite_result["composite_score"] if composite_result else 0.0

    # ── Compute technical indicators ─────────────────────────────
    rsi_val = _compute_rsi(close)
    macd_info = _compute_macd(close)
    sma20 = _compute_sma(close, 20)
    sma50 = _compute_sma(close, 50)
    bb = _compute_bollinger(close)

    # ── Calculate all 8 sub-scores ───────────────────────────────
    sub_scores = {
        "composite_factor": round(_score_composite(composite_score), 4),
        "rsi":              round(_score_rsi(rsi_val), 4),
        "macd":             round(_score_macd(macd_info, current_price), 4),
        "trend_ma":         round(_score_trend(current_price, sma20, sma50), 4),
        "bollinger":        round(_score_bollinger(bb["pct_b"]), 4),
        "volume":           round(_score_volume(df), 4),
        "volatility":       round(_score_volatility(df), 4),
        "relative_strength": round(_score_relative_strength(df, benchmark_df), 4),
    }

    # ── Weighted aggregation ─────────────────────────────────────
    sicilian_score = 0.0
    for key, weight in SICILIAN_WEIGHTS.items():
        sicilian_score += sub_scores.get(key, 0.0) * weight
    sicilian_score = round(float(np.clip(sicilian_score, -1.0, 1.0)), 4)

    # ── Confidence: how many sub-scores agree in direction ───────
    if sicilian_score > 0:
        agreeing = sum(1 for v in sub_scores.values() if v > 0)
    elif sicilian_score < 0:
        agreeing = sum(1 for v in sub_scores.values() if v < 0)
    else:
        agreeing = sum(1 for v in sub_scores.values() if v == 0)
    confidence = round(agreeing / len(sub_scores) * 100, 1)

    # ── Verdict ──────────────────────────────────────────────────
    if sicilian_score >= BUY_THRESHOLD:
        verdict = "BUY"
    elif sicilian_score <= SELL_THRESHOLD:
        verdict = "SELL"
    else:
        verdict = "HOLD"

    # ── Target price ─────────────────────────────────────────────
    # Z-score for buy target discount
    z_score_20 = 0.0
    if sma20 is not None and len(close) >= 20:
        std_20 = float(close.rolling(20).std().iloc[-1])
        if std_20 > 0:
            z_score_20 = (current_price - sma20) / std_20

    if verdict == "BUY":
        target_price = _compute_buy_target(current_price, sma20, bb["lower"], z_score_20)
        target_type = "entry"
    elif verdict == "SELL":
        target_price = _compute_sell_target(current_price, sma20, bb["upper"])
        target_type = "exit"
    else:
        target_price = round(current_price, 2)
        target_type = "fair_value"

    # ── Build reasoning string ───────────────────────────────────
    bullish_factors = [k.replace("_", " ").title() for k, v in sub_scores.items() if v > 0.1]
    bearish_factors = [k.replace("_", " ").title() for k, v in sub_scores.items() if v < -0.1]
    reasoning_parts = []
    if bullish_factors:
        reasoning_parts.append(f"Bullish: {', '.join(bullish_factors)}")
    if bearish_factors:
        reasoning_parts.append(f"Bearish: {', '.join(bearish_factors)}")
    reasoning = " | ".join(reasoning_parts) if reasoning_parts else "Signals are mixed — no strong conviction"

    # ── Support & resistance levels ──────────────────────────────
    support_resistance = {
        "sma20": round(sma20, 2) if sma20 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "bollinger_upper": round(bb["upper"], 2) if bb["upper"] else None,
        "bollinger_lower": round(bb["lower"], 2) if bb["lower"] else None,
        "bollinger_middle": round(bb["middle"], 2) if bb["middle"] else None,
    }

    return {
        "symbol": symbol,
        "current_price": round(current_price, 2),
        "verdict": verdict,
        "sicilian_score": sicilian_score,
        "confidence": confidence,
        "target_price": target_price,
        "target_type": target_type,
        "reasoning": reasoning,
        "sub_scores": sub_scores,
        "weights": SICILIAN_WEIGHTS,
        "support_resistance": support_resistance,
        "indicators": {
            "rsi": round(rsi_val, 2) if rsi_val else None,
            "macd_histogram": round(macd_info["histogram"], 4),
            "macd_crossover": macd_info["crossover"],
            "bollinger_pct_b": round(bb["pct_b"], 4),
        },
        "composite_score": composite_score,
        "data_points": len(df),
    }
