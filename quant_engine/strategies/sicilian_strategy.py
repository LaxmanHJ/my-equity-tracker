"""
The Sicilian Strategy — Backtestable version of The Sicilian decision engine.

Replays the 8 technical sub-scores of The Sicilian engine over historical data
in a vectorized manner to generate daily BUY (1) / SELL (-1) / HOLD (0) signals.

Fundamental sub-scores (valuation, financial_health, growth) are excluded because
they are quarterly and don't change day-to-day — including them as a static bias
would distort the backtest.
"""
import logging
import numpy as np
import pandas as pd
from quant_engine.strategies.base import BaseStrategy
from quant_engine.data.loader import load_benchmark

logger = logging.getLogger(__name__)

# ── Sicilian technical weights (re-normalized from original 80% to sum to 1.0) ──
TECH_WEIGHTS = {
    "composite_factor":  0.275,   # 0.22 / 0.80
    "rsi":               0.125,   # 0.10 / 0.80
    "macd":              0.125,   # 0.10 / 0.80
    "trend_ma":          0.125,   # 0.10 / 0.80
    "bollinger":         0.100,   # 0.08 / 0.80
    "volume":            0.088,   # 0.07 / 0.80
    "volatility":        0.075,   # 0.06 / 0.80
    "relative_strength": 0.088,   # 0.07 / 0.80
}

# Verdict thresholds — lower than the real-time Sicilian (±0.35) because the
# rolling weighted average of 8 technical sub-scores is compressed vs the full
# 11-score Sicilian. ±0.15 generates meaningful trade activity.
BUY_THRESHOLD = 0.15
SELL_THRESHOLD = -0.15


class SicilianStrategy(BaseStrategy):
    """
    Backtesting implementation of The Sicilian decision engine.
    Computes rolling technical indicators and scores each bar using the
    same weighting system as the real-time Sicilian.
    """

    def generate_signals(self, data: pd.DataFrame, **kwargs) -> pd.Series:
        """
        Generate daily signals from Sicilian technical sub-scores.

        Args:
            data: OHLCV DataFrame with DateTimeIndex.
            **kwargs: Optional 'benchmark_df' for relative strength calculation.

        Returns:
            pd.Series of signals: 1 (long), -1 (short), 0 (flat)
        """
        df = data.copy()
        n = len(df)

        benchmark_df = kwargs.get("benchmark_df", None)
        if benchmark_df is None:
            try:
                benchmark_df = load_benchmark(limit=n + 200)
            except Exception as exc:
                logger.warning("Could not load benchmark data, relative strength scores will be 0: %s", exc)
                benchmark_df = pd.DataFrame()

        # ── Pre-compute rolling indicators ────────────────────────────
        close = df["close"]
        volume = df["volume"]

        # RSI (14-period)
        rsi_scores = self._rolling_rsi_score(close, period=14)

        # MACD (12, 26, 9)
        macd_scores = self._rolling_macd_score(close)

        # Trend: SMA20 vs SMA50
        trend_scores = self._rolling_trend_score(close)

        # Bollinger %B (20, 2σ)
        bollinger_scores = self._rolling_bollinger_score(close)

        # Volume ratio
        volume_scores = self._rolling_volume_score(close, volume)

        # Volatility ratio (20d vs 60d)
        volatility_scores = self._rolling_volatility_score(close)

        # Relative strength vs benchmark
        rs_scores = self._rolling_relative_strength_score(close, benchmark_df)

        # Composite factor: simplified momentum + mean-reversion blend
        composite_scores = self._rolling_composite_score(close)

        # ── Weighted aggregation per bar ──────────────────────────────
        sicilian_score = (
            TECH_WEIGHTS["composite_factor"]  * composite_scores +
            TECH_WEIGHTS["rsi"]               * rsi_scores +
            TECH_WEIGHTS["macd"]              * macd_scores +
            TECH_WEIGHTS["trend_ma"]          * trend_scores +
            TECH_WEIGHTS["bollinger"]         * bollinger_scores +
            TECH_WEIGHTS["volume"]            * volume_scores +
            TECH_WEIGHTS["volatility"]        * volatility_scores +
            TECH_WEIGHTS["relative_strength"] * rs_scores
        ).clip(-1.0, 1.0)

        # ── Generate signals ─────────────────────────────────────────
        signals = pd.Series(0, index=df.index, dtype=float)
        signals[sicilian_score >= BUY_THRESHOLD] = 1
        signals[sicilian_score <= SELL_THRESHOLD] = -1

        return signals

    # ── Rolling sub-score calculators (vectorized) ────────────────────

    @staticmethod
    def _rolling_rsi_score(close: pd.Series, period: int = 14) -> pd.Series:
        """RSI 20 → +1 (strong buy), RSI 50 → 0, RSI 80 → -1 (strong sell)"""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return ((50 - rsi) / 30.0).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_macd_score(close: pd.Series, fast=12, slow=26, signal=9) -> pd.Series:
        """MACD histogram normalized by price, with crossover bonus."""
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        # Normalize histogram by price
        normalized = (histogram / close * 100).fillna(0.0)
        base = (normalized / 1.0).clip(-0.7, 0.7)

        # Crossover bonus
        prev_hist = histogram.shift(1)
        crossover_bonus = pd.Series(0.0, index=close.index)
        crossover_bonus[(prev_hist < 0) & (histogram > 0)] = 0.3    # bullish
        crossover_bonus[(prev_hist > 0) & (histogram < 0)] = -0.3   # bearish

        return (base + crossover_bonus).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_trend_score(close: pd.Series) -> pd.Series:
        """Score based on price vs SMA20/SMA50 and SMA20 vs SMA50."""
        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()

        score = pd.Series(0.0, index=close.index)

        # Price vs SMA20
        score = score + np.where(close > sma20, 0.25, -0.25)
        # Price vs SMA50
        score = score + np.where(close > sma50, 0.25, -0.25)
        # SMA20 vs SMA50 (golden/death cross territory)
        score = score + np.where(sma20 > sma50, 0.5, -0.5)

        return score.clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_bollinger_score(close: pd.Series, period=20, std_mult=2) -> pd.Series:
        """%B < 0.2 → +0.8 (buy), %B > 0.8 → -0.8 (sell), %B = 0.5 → neutral."""
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + std_mult * std
        lower = sma - std_mult * std
        band_width = upper - lower
        pct_b = (close - lower) / band_width.replace(0, np.nan)
        pct_b = pct_b.fillna(0.5)
        return ((0.5 - pct_b) * 2.0).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_volume_score(close: pd.Series, volume: pd.Series, lookback=20) -> pd.Series:
        """Volume surge with price direction confirmation."""
        avg_vol = volume.rolling(lookback).mean()
        vol_ratio = (volume / avg_vol.replace(0, np.nan)).fillna(1.0)
        price_change_5d = close.pct_change(5).fillna(0.0)

        score = pd.Series(0.0, index=close.index)

        # High volume surge (> 1.5x average)
        high_surge = vol_ratio > 1.5
        score[high_surge & (price_change_5d > 0)] = (vol_ratio[high_surge & (price_change_5d > 0)] / 3.0).clip(0, 1.0)
        score[high_surge & (price_change_5d <= 0)] = -(vol_ratio[high_surge & (price_change_5d <= 0)] / 3.0).clip(0, 1.0)

        # Moderate surge (1.2x - 1.5x)
        mod_surge = (vol_ratio > 1.2) & (vol_ratio <= 1.5)
        score[mod_surge & (price_change_5d > 0)] = 0.3
        score[mod_surge & (price_change_5d <= 0)] = -0.3

        return score.fillna(0.0)

    @staticmethod
    def _rolling_volatility_score(close: pd.Series, short_w=20, long_w=60) -> pd.Series:
        """Low vol ratio = bullish (contracting risk), high = bearish."""
        returns = close.pct_change()
        vol_short = returns.rolling(short_w).std() * np.sqrt(252)
        vol_long = returns.rolling(long_w).std() * np.sqrt(252)
        vol_ratio = (vol_short / vol_long.replace(0, np.nan)).fillna(1.0)
        return ((1.0 - vol_ratio) / 0.4).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_relative_strength_score(
        close: pd.Series, benchmark_df: pd.DataFrame, period: int = 63
    ) -> pd.Series:
        """Stock return vs benchmark return over trailing window."""
        score = pd.Series(0.0, index=close.index)

        if benchmark_df.empty or "close" not in benchmark_df.columns:
            return score

        bench_close = benchmark_df["close"]

        # Align benchmark to stock dates (forward-fill for missing dates)
        bench_aligned = bench_close.reindex(close.index, method="ffill")

        if bench_aligned.isna().all():
            return score

        stock_ret = close / close.shift(period) - 1
        bench_ret = bench_aligned / bench_aligned.shift(period) - 1

        excess = stock_ret - bench_ret
        return (excess / 0.20).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_composite_score(close: pd.Series) -> pd.Series:
        """
        Simplified composite factor score: blends momentum and mean-reversion.
        - Momentum: 63-day rate of change normalized
        - Mean-reversion: 20-day Z-score (inverted — oversold = bullish)
        Combined 60/40 to match original composite's blend of factors.
        """
        # Momentum: 63-day ROC normalized to [-1, +1]
        roc_63 = close.pct_change(63)
        momentum = (roc_63 / 0.30).clip(-1.0, 1.0).fillna(0.0)

        # Mean-reversion: 20-day Z-score inverted
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        z_score = ((close - sma20) / std20.replace(0, np.nan)).fillna(0.0)
        mean_rev = (-z_score / 2.0).clip(-1.0, 1.0)

        # Blend 60% momentum, 40% mean-reversion
        composite = 0.6 * momentum + 0.4 * mean_rev
        return composite.clip(-1.0, 1.0).fillna(0.0)
