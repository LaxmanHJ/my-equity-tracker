"""
The Sicilian — Unified Buy/Sell Decision Engine.

Aggregates 11 sub-scores (8 technical + 3 fundamental) from across the system
into a single BUY / SELL / HOLD verdict with next-day target prices and confidence.
"""
import numpy as np
import pandas as pd
from typing import Optional

from quant_engine.data.loader import (
    load_price_history, load_benchmark,
    load_industry_map, load_analyst_consensus,
)
from quant_engine.data.market_regime_loader import (
    load_vix_score_today, load_fii_flow_score_today, load_fii_fo_score_today,
)
from quant_engine.strategies import markov_regime as markov_regime_strategy
from quant_engine.data.fundamentals_loader import load_fundamentals
from quant_engine.scoring.composite import score_single_stock
from quant_engine.ml import predictor as ml_predictor

# ── Sicilian weights for sub-scores (15 total, sum = 1.0) ──────
SICILIAN_WEIGHTS = {
    # Technical (62%)
    "composite_factor":  0.14,
    "rsi":               0.09,
    "macd":              0.09,
    "trend_ma":          0.08,
    "bollinger":         0.07,
    "volume":            0.05,
    "volatility":        0.04,
    "relative_strength": 0.06,
    # Cross-stock / external + market regime (20%)
    "sector_rotation":   0.05,
    "analyst_consensus": 0.03,
    "vix_regime":        0.04,
    "nifty_trend":       0.04,
    "markov_regime":     0.04,
    # Fundamental (18%)
    "valuation":         0.08,
    "financial_health":  0.06,
    "growth":            0.04,
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


# ── Fundamental sub-score calculators ────────────────────────────

def _score_valuation(fund: dict) -> float:
    """
    Valuation score based on P/E, P/B, and price-to-sales.
    Lower valuations → more positive (buy signal).
    """
    if not fund:
        return 0.0
    scores = []
    pe = fund.get("pe_ratio")
    if pe is not None and pe > 0:
        # PE < 12 = very undervalued (+1), PE 20 = fair (0), PE > 35 = very overvalued (-1)
        val = np.clip((20 - pe) / 15.0, -1.0, 1.0)
        scores.append(val)
    pb = fund.get("pb_ratio")
    if pb is not None and pb > 0:
        # PB < 1 = undervalued (+0.8), PB = 3 = fair (0), PB > 5 = overvalued (-0.8)
        val = np.clip((3 - pb) / 3.0, -1.0, 1.0)
        scores.append(val)
    ps = fund.get("price_to_sales")
    if ps is not None and ps > 0:
        # P/S < 1 = great (+0.6), P/S = 3 = fair (0), P/S > 8 = expensive (-0.6)
        val = np.clip((3 - ps) / 5.0, -1.0, 1.0)
        scores.append(val)
    return float(np.mean(scores)) if scores else 0.0


def _score_financial_health(fund: dict) -> float:
    """
    Financial health score based on current ratio, debt/equity,
    interest coverage, and free cash flow.
    """
    if not fund:
        return 0.0
    scores = []
    cr = fund.get("current_ratio")
    if cr is not None:
        # CR > 2 = strong (+0.8), CR = 1.5 = fair (0), CR < 1 = weak (-0.8)
        val = np.clip((cr - 1.5) / 1.0, -1.0, 1.0)
        scores.append(val)
    de = fund.get("debt_to_equity")
    if de is not None:
        # D/E < 0.5 = great (+0.8), D/E = 1 = ok (0), D/E > 2 = risky (-0.8)
        val = np.clip((1.0 - de) / 1.0, -1.0, 1.0)
        scores.append(val)
    ic = fund.get("interest_coverage")
    if ic is not None and ic > 0:
        # IC > 5 = great (+0.8), IC = 3 = ok (0.2), IC < 1.5 = danger (-0.8)
        val = np.clip((ic - 3.0) / 4.0, -1.0, 1.0)
        scores.append(val)
    fcf = fund.get("free_cash_flow")
    if fcf is not None:
        # Positive FCF = good, negative = bad
        val = 0.5 if fcf > 0 else -0.5
        scores.append(val)
    return float(np.mean(scores)) if scores else 0.0


def _score_growth(fund: dict) -> float:
    """
    Growth score based on revenue and EPS growth rates (5Y and 3Y),
    and net profit margin trend.
    """
    if not fund:
        return 0.0
    scores = []
    rg5 = fund.get("revenue_growth_5y")
    if rg5 is not None:
        # 15%+ growth = excellent (+0.8), 5% = decent (0.2), negative = bad (-0.6)
        val = np.clip(rg5 / 15.0, -1.0, 1.0)
        scores.append(val)
    eg5 = fund.get("eps_growth_5y")
    if eg5 is not None:
        val = np.clip(eg5 / 15.0, -1.0, 1.0)
        scores.append(val)
    rg3 = fund.get("revenue_growth_3y")
    if rg3 is not None:
        val = np.clip(rg3 / 15.0, -1.0, 1.0)
        scores.append(val)
    eg3 = fund.get("eps_growth_3y")
    if eg3 is not None:
        val = np.clip(eg3 / 15.0, -1.0, 1.0)
        scores.append(val)
    # Margin trend: compare TTM vs 5Y average
    npm_ttm = fund.get("net_profit_margin_ttm")
    npm_5y = fund.get("net_profit_margin_5y_avg")
    if npm_ttm is not None and npm_5y is not None and npm_5y != 0:
        margin_trend = (npm_ttm - npm_5y) / abs(npm_5y)
        val = np.clip(margin_trend / 0.3, -1.0, 1.0)
        scores.append(val)
    return float(np.mean(scores)) if scores else 0.0


# ── Cross-stock / external sub-score calculators ────────────────

def _score_sector_rotation(symbol: str, industry: str, benchmark_df: pd.DataFrame, period: int = 20) -> float:
    """
    Average 20-day return of all stocks in the same industry, minus the
    benchmark 20-day return, normalised to [-1, +1] (±20% excess = ±1).

    The stock itself is excluded so its own price doesn't inflate the peer average.
    Peers are capped at 20 to keep live latency bounded.
    """
    if not industry:
        return 0.0

    industry_map = load_industry_map()
    peers = [s for s, ind in industry_map.items() if ind == industry and s != symbol]
    if not peers:
        return 0.0

    bench_ret: Optional[float] = None
    if not benchmark_df.empty and len(benchmark_df) >= period:
        bench_ret = float(benchmark_df["close"].iloc[-1] / benchmark_df["close"].iloc[-period] - 1)

    peer_returns: list[float] = []
    for peer in peers[:20]:
        try:
            peer_df = load_price_history(peer, limit=period + 5)
            if len(peer_df) >= period:
                peer_returns.append(
                    float(peer_df["close"].iloc[-1] / peer_df["close"].iloc[-period] - 1)
                )
        except Exception:
            pass

    if not peer_returns:
        return 0.0

    sector_ret = float(np.mean(peer_returns))
    excess = sector_ret - bench_ret if bench_ret is not None else sector_ret
    return float(np.clip(excess / 0.20, -1.0, 1.0))


def _score_vix_regime() -> float:
    """
    Returns today's VIX regime score from the market_regime table.
    Low VIX (calm) → +1, high VIX (fearful) → -1.  0 if no data available.
    """
    score = load_vix_score_today()
    return score if score is not None else 0.0


def _score_nifty_trend(benchmark_df: pd.DataFrame) -> float:
    """
    How far NIFTY sits above/below its SMA50 and SMA200.

    vs_sma50  = clip((nifty - sma50)  / sma50  × 10, -1, +1)
    vs_sma200 = clip((nifty - sma200) / sma200 × 10, -1, +1)
    score     = 0.5 × vs_sma50 + 0.5 × vs_sma200

    Positive = uptrend (reinforces BUY signals).
    Negative = downtrend (weakens BUY signals).
    """
    if benchmark_df.empty or len(benchmark_df) < 50:
        return 0.0

    close = benchmark_df["close"]
    sma50  = float(close.rolling(50,  min_periods=30).mean().iloc[-1])
    sma200 = float(close.rolling(200, min_periods=100).mean().iloc[-1]) if len(close) >= 100 else sma50
    current = float(close.iloc[-1])

    vs_sma50  = float(np.clip((current - sma50)  / sma50  * 10 if sma50  else 0, -1.0, 1.0))
    vs_sma200 = float(np.clip((current - sma200) / sma200 * 10 if sma200 else 0, -1.0, 1.0))

    return round(0.5 * vs_sma50 + 0.5 * vs_sma200, 4)


def _score_markov_regime(benchmark_df: pd.DataFrame) -> float:
    """
    Run Markov chain regime analysis on the NIFTY benchmark and return
    P(next = Bull) - P(next = Bear) from the current state.

    Positive → transition matrix predicts Bull tomorrow.
    Negative → transition matrix predicts Bear tomorrow.
    Returns 0 if insufficient benchmark data.
    """
    if benchmark_df.empty or len(benchmark_df) < 30:
        return 0.0
    result = markov_regime_strategy.calculate(benchmark_df)
    if result.get("current_regime") == "Unknown":
        return 0.0
    probs = result.get("next_day_probabilities", {})
    bull_p = probs.get("Bull", 0.0)
    bear_p = probs.get("Bear", 0.0)
    return float(np.clip(bull_p - bear_p, -1.0, 1.0))


def _score_analyst_consensus(symbol: str) -> float:
    """
    Returns the analyst consensus score for a stock in [-1, +1].

    Score = (strong_buy + buy - sell - strong_sell) / total_analysts.
    Positive = more analysts bullish than bearish; 0 if no coverage.
    """
    clean = symbol.replace(".NS", "").replace(".BO", "")
    consensus_map = load_analyst_consensus()
    return consensus_map.get(clean, 0.0)


# ── Target price calculators ────────────────────────────────────

def _compute_buy_target(current_price: float, sma20: float, bb_lower: float) -> float:
    """
    BUY target: midpoint between lower Bollinger band and SMA20,
    floored at 5% below current price.
    """
    if sma20 is None or bb_lower is None:
        return round(current_price * 0.99, 2)
    ideal_entry = (bb_lower + sma20) / 2.0
    target = min(current_price, ideal_entry)
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

    # ── Load fundamentals + external data ────────────────────────
    fund = load_fundamentals(symbol)
    industry = load_industry_map().get(symbol.replace(".NS", "").replace(".BO", ""))

    # ── Calculate all 13 sub-scores ──────────────────────────────
    sub_scores = {
        # Technical (8)
        "composite_factor": round(_score_composite(composite_score), 4),
        "rsi":              round(_score_rsi(rsi_val), 4),
        "macd":             round(_score_macd(macd_info, current_price), 4),
        "trend_ma":         round(_score_trend(current_price, sma20, sma50), 4),
        "bollinger":        round(_score_bollinger(bb["pct_b"]), 4),
        "volume":           round(_score_volume(df), 4),
        "volatility":       round(_score_volatility(df), 4),
        "relative_strength": round(_score_relative_strength(df, benchmark_df), 4),
        # Cross-stock / external + market regime (4)
        "sector_rotation":  round(_score_sector_rotation(symbol, industry, benchmark_df), 4),
        "analyst_consensus": round(_score_analyst_consensus(symbol), 4),
        "vix_regime":       round(_score_vix_regime(), 4),
        "nifty_trend":      round(_score_nifty_trend(benchmark_df), 4),
        "markov_regime":    round(_score_markov_regime(benchmark_df), 4),
        "fii_flow_score":   round(load_fii_flow_score_today(), 4),
        "fii_fo_score":     round(load_fii_fo_score_today(), 4),
        # Fundamental (3)
        "valuation":        round(_score_valuation(fund), 4),
        "financial_health": round(_score_financial_health(fund), 4),
        "growth":           round(_score_growth(fund), 4),
    }

    # ── Weighted aggregation (linear baseline) ───────────────────
    sicilian_score = 0.0
    for key, weight in SICILIAN_WEIGHTS.items():
        sicilian_score += sub_scores.get(key, 0.0) * weight
    sicilian_score = round(float(np.clip(sicilian_score, -1.0, 1.0)), 4)

    # ── Verdict + confidence ──────────────────────────────────────
    # Try the ML model first (learns non-linear interactions between indicators).
    # Falls back to the linear weighted approach if the model hasn't been trained.
    ml_result = ml_predictor.predict(sub_scores)
    scoring_method: str

    if ml_result is not None:
        verdict = ml_result["verdict"]
        confidence = ml_result["confidence"]
        ml_probabilities = ml_result["probabilities"]
        scoring_method = "ml_random_forest"
    else:
        # Linear fallback: threshold on weighted sum + naive vote-count confidence
        if sicilian_score >= BUY_THRESHOLD:
            verdict = "BUY"
        elif sicilian_score <= SELL_THRESHOLD:
            verdict = "SELL"
        else:
            verdict = "HOLD"

        if sicilian_score > 0:
            agreeing = sum(1 for v in sub_scores.values() if v > 0)
        elif sicilian_score < 0:
            agreeing = sum(1 for v in sub_scores.values() if v < 0)
        else:
            agreeing = sum(1 for v in sub_scores.values() if v == 0)
        confidence = round(agreeing / len(sub_scores) * 100, 1)
        ml_probabilities = None
        scoring_method = "linear_weighted"

    # ── Target price ─────────────────────────────────────────────
    # Z-score for buy target discount
    if verdict == "BUY":
        target_price = _compute_buy_target(current_price, sma20, bb["lower"])
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
        "scoring_method": scoring_method,
        "ml_probabilities": ml_probabilities,
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
