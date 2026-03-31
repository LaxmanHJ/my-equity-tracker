"""
The Sicilian Strategy — Backtestable version of The Sicilian decision engine.

Replays the same factor scores used by the live scoring engine over historical
data in a vectorized manner to generate daily BUY (1) / SELL (-1) / HOLD (0)
signals.

Signal generation follows a two-path priority order:

  1. ML path  — if a trained Random Forest model is available and a symbol is
     provided, the model runs over every bar using the full 15-feature matrix
     (the same one built by ml/trainer.py).  This is the preferred path.

  2. Linear fallback — if no model exists or symbol is unknown, a fixed weighted
     sum of 7 rolling factor scores produces the composite signal.  Factor
     weights and thresholds are identical to the live scoring engine.

Fundamental sub-scores (valuation, financial_health, growth) are excluded
because they are quarterly and don't change day-to-day — including them as a
static bias would distort the backtest.
"""
import logging
import numpy as np
import pandas as pd
from quant_engine.strategies.base import BaseStrategy
from quant_engine.config import FACTOR_WEIGHTS
from quant_engine.data.loader import load_benchmark

logger = logging.getLogger(__name__)

# Thresholds aligned with the live engine:
# Live engine uses ±40 on a -100 to +100 scale → ±0.40 on the -1 to +1 backtest scale.
BUY_THRESHOLD = 0.40
SELL_THRESHOLD = -0.40


class SicilianStrategy(BaseStrategy):
    """
    Backtesting implementation of The Sicilian decision engine.

    Computes rolling technical indicators and scores each bar using the same
    weighting system as the real-time Sicilian, with an optional ML overlay
    that replaces the linear composite when a trained model is present.
    """

    def generate_signals(self, data: pd.DataFrame, **kwargs) -> pd.Series:
        """
        Generate daily signals, preferring ML predictions when available.

        Tries the ML path first (requires a trained model and a known symbol),
        then falls back to the linear weighted-factor composite.

        Args:
            data: OHLCV DataFrame with DateTimeIndex.
            **kwargs:
                benchmark_df – pre-loaded benchmark OHLCV DataFrame (optional).
                symbol       – NSE ticker string used for ML feature loading (optional).

        Returns:
            pd.Series of signals: 1 (long), -1 (short), 0 (flat), indexed like data.
        """
        df = data.copy()
        n = len(df)
        benchmark_df = kwargs.get("benchmark_df", None)
        symbol = kwargs.get("symbol", None)

        if benchmark_df is None:
            try:
                benchmark_df = load_benchmark(limit=n + 200)
            except Exception as exc:
                logger.warning("Could not load benchmark: %s", exc)
                benchmark_df = pd.DataFrame()

        # ML path (Priority 1): try ML prediction first
        if symbol:
            ml_signals = self._ml_signals(df, benchmark_df, symbol)
            if ml_signals is not None:
                logger.info("Using ML predictions for backtest signals (%d bars)", len(df))
                return ml_signals

        # Linear fallback: fixed factor composite
        logger.info("Using linear composite for backtest signals (%d bars)", len(df))
        return self._linear_signals(df, benchmark_df)

    def _linear_signals(self, df: pd.DataFrame, benchmark_df: pd.DataFrame) -> pd.Series:
        """
        Compute signals from the 7-factor weighted composite.

        Uses "bollinger" in place of "mean_reversion" to match the FACTOR_WEIGHTS
        key expected by config.py after its corresponding update.

        Args:
            df:           OHLCV DataFrame with DateTimeIndex.
            benchmark_df: Benchmark OHLCV DataFrame (may be empty).

        Returns:
            pd.Series of signals: 1 / 0 / -1, indexed like df.
        """
        close = df["close"]
        volume = df["volume"]

        factor_scores = {
            "momentum":          self._rolling_momentum_score(close),
            "bollinger":         self._rolling_bollinger_score(close),
            "rsi":               self._rolling_rsi_score(close),
            "macd":              self._rolling_macd_score(close),
            "volatility":        self._rolling_volatility_score(close),
            "volume":            self._rolling_volume_score(close, volume),
            "relative_strength": self._rolling_relative_strength_score(close, benchmark_df),
        }

        sicilian_score = sum(
            FACTOR_WEIGHTS[name] * scores
            for name, scores in factor_scores.items()
        ).clip(-1.0, 1.0)

        signals = pd.Series(0, index=df.index, dtype=float)
        signals[sicilian_score >= BUY_THRESHOLD] = 1
        signals[sicilian_score <= SELL_THRESHOLD] = -1
        return signals

    def _ml_signals(
        self, df: pd.DataFrame, benchmark_df: pd.DataFrame, symbol: str
    ) -> "pd.Series | None":
        """
        Run the trained RF model over every bar in df.

        Builds the full feature matrix, aligns valid rows to the model's
        FEATURE_COLS, calls model.predict(), and maps results back to df's index.

        Returns a pd.Series of signals (1 / 0 / -1) or None if the model is
        unavailable or feature construction fails.
        """
        try:
            from quant_engine.ml.predictor import _load_model, FEATURE_COLS as ML_FEATURES

            model = _load_model()
            if model is None:
                return None

            features = self._build_ml_features(df, benchmark_df, symbol)
            if features is None or features.empty:
                return None

            # Only predict on rows where every required feature is present.
            available_features = [c for c in ML_FEATURES if c in features.columns]
            if not available_features:
                return None

            valid = features[available_features].notna().all(axis=1)
            X = features.loc[valid, available_features].fillna(0.0)
            if X.empty:
                return None

            preds = model.predict(X).astype(float)
            signals = pd.Series(0.0, index=df.index)
            signals.loc[X.index] = preds
            return signals

        except Exception as exc:
            logger.warning("ML prediction failed, falling back to linear: %s", exc)
            return None

    def _build_ml_features(
        self, df: pd.DataFrame, benchmark_df: pd.DataFrame, symbol: str
    ) -> pd.DataFrame:
        """
        Compute all ML feature columns for every bar in df.

        Mirrors trainer._build_feature_frame for a single symbol so that the
        signals produced during backtesting are computed identically to the
        features the model was trained on.

        All imports are local to avoid circular dependencies between the strategy
        and the trainer/loader modules.

        Args:
            df:           OHLCV DataFrame with DateTimeIndex.
            benchmark_df: Benchmark OHLCV DataFrame (may be empty).
            symbol:       NSE ticker string (e.g. "RELIANCE").

        Returns:
            pd.DataFrame indexed like df with one column per ML feature.
        """
        from quant_engine.data.market_regime_loader import (
            load_vix_series, vix_to_score,
            build_markov_score_series,
            load_fii_flow_series, load_fii_fo_series, _flow_to_score,
        )
        from quant_engine.data.delivery_loader import load_delivery_series
        from quant_engine.data.sector_indices_loader import load_sector_series
        from quant_engine.data.loader import load_industry_map
        from quant_engine.config import INDUSTRY_TO_NSE_INDEX

        close  = df["close"]
        volume = df["volume"]

        def _align(series: pd.Series) -> pd.Series:
            """Reindex a market-level series to df's date index; fill gaps with 0."""
            if series is None or (hasattr(series, "empty") and series.empty):
                return pd.Series(0.0, index=df.index)
            return series.reindex(df.index, method="ffill").fillna(0.0)

        # ── Technical sub-scores ─────────────────────────────────────────────
        rsi_s  = self._rolling_rsi_score(close)
        macd_s = self._rolling_macd_score(close)
        tma_s  = self._rolling_trend_score(close)
        bol_s  = self._rolling_bollinger_score(close)
        vol_s  = self._rolling_volume_score(close, volume)
        vola_s = self._rolling_volatility_score(close)
        rs_s   = self._rolling_relative_strength_score(close, benchmark_df)

        # ── VIX regime ───────────────────────────────────────────────────────
        raw_vix   = load_vix_series(limit=2000)
        vix_score = vix_to_score(raw_vix) if not raw_vix.empty else pd.Series(dtype=float)

        # ── NIFTY trend & Markov ─────────────────────────────────────────────
        nifty_trend  = self._build_nifty_trend(benchmark_df)
        markov_score = build_markov_score_series(benchmark_df)

        # ── Delivery score ───────────────────────────────────────────────────
        delivery_df = load_delivery_series(symbol, limit=2000)
        if not delivery_df.empty and "delivery_pct" in delivery_df.columns:
            dpct      = delivery_df["delivery_pct"].reindex(df.index)
            roll_mean = dpct.rolling(60, min_periods=10).mean()
            roll_std  = dpct.rolling(60, min_periods=10).std().replace(0, 1)
            delivery_score = ((dpct - roll_mean) / roll_std).clip(-3, 3) / 3
        else:
            delivery_score = pd.Series(dtype=float)

        # ── Sector rotation ──────────────────────────────────────────────────
        industry_map = load_industry_map()
        industry     = industry_map.get(symbol)
        nse_index    = (
            INDUSTRY_TO_NSE_INDEX.get(industry, "Nifty 500")
            if industry
            else "Nifty 500"
        )
        idx_close   = load_sector_series(nse_index, limit=2000)
        nifty_close = load_sector_series("Nifty 50",  limit=2000)
        if not idx_close.empty:
            sector_20d = idx_close.pct_change(20)
            if not nifty_close.empty:
                bench_20d = nifty_close.pct_change(20).reindex(
                    sector_20d.index, method="ffill"
                )
                excess = sector_20d - bench_20d
            else:
                excess = sector_20d
            sector_series = (excess / 0.20).clip(-1.0, 1.0).fillna(0.0)
        else:
            sector_series = pd.Series(dtype=float)

        # ── FII flows ────────────────────────────────────────────────────────
        raw_fii_flow = load_fii_flow_series(limit=2000)
        if not raw_fii_flow.empty:
            fii_10d        = raw_fii_flow.rolling(10).sum()
            fii_flow_score = _flow_to_score(fii_10d)
        else:
            fii_flow_score = pd.Series(dtype=float)

        raw_fii_fo   = load_fii_fo_series(limit=2000)
        fii_fo_score = (
            _flow_to_score(raw_fii_fo) if not raw_fii_fo.empty else pd.Series(dtype=float)
        )

        return pd.DataFrame(
            {
                "rsi":               rsi_s,
                "macd":              macd_s,
                "trend_ma":          tma_s,
                "bollinger":         bol_s,
                "volume":            vol_s,
                "volatility":        vola_s,
                "relative_strength": rs_s,
                "sector_rotation":   _align(sector_series),
                "vix_regime":        _align(vix_score),
                "nifty_trend":       _align(nifty_trend),
                "markov_regime":     _align(markov_score),
                "delivery_score":    _align(delivery_score),
                "fii_flow_score":    _align(fii_flow_score),
                "fii_fo_score":      _align(fii_fo_score),
            },
            index=df.index,
        )

    @staticmethod
    def _build_nifty_trend(benchmark_df: pd.DataFrame) -> pd.Series:
        """
        Rolling NIFTY trend score in [-1, +1].

        Blends two continuous signals:
          vs_sma50  = (nifty - sma50)  / sma50  × 10
          vs_sma200 = (nifty - sma200) / sma200 × 10

        score = 0.5 × vs_sma50 + 0.5 × vs_sma200, clipped to [-1, +1].

        Mirrors trainer._build_nifty_trend_series exactly so that backtest
        features are computed identically to training features.
        """
        if benchmark_df.empty:
            return pd.Series(dtype=float)
        close  = benchmark_df["close"]
        sma50  = close.rolling(50,  min_periods=30).mean()
        sma200 = close.rolling(200, min_periods=100).mean()
        vs_sma50  = ((close - sma50)  / sma50.replace(0, float("nan"))  * 10).clip(-1.0, 1.0)
        vs_sma200 = ((close - sma200) / sma200.replace(0, float("nan")) * 10).clip(-1.0, 1.0)
        return (0.5 * vs_sma50 + 0.5 * vs_sma200).fillna(0.0)

    # ── Rolling sub-score calculators (vectorized) ────────────────────────────
    # NOTE: all _rolling_* methods below are also used directly by ml/trainer.py
    # to build the ML feature matrix. Do not rename or remove them.

    @staticmethod
    def _rolling_momentum_score(close: pd.Series) -> pd.Series:
        """Vectorized momentum: weighted blend of 21d/63d/126d returns, normalized to [-1, +1].
        Mirrors quant_engine/factors/momentum.py calculate() exactly."""
        ret_1m = close.pct_change(21)
        ret_3m = close.pct_change(63)
        ret_6m = close.pct_change(126)
        raw = 0.2 * ret_1m + 0.4 * ret_3m + 0.4 * ret_6m
        return (raw / 0.50).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_mean_reversion_score(close: pd.Series, period: int = 50) -> pd.Series:
        """Vectorized mean reversion: 50d Z-score inverted, normalized to [-1, +1].
        Mirrors quant_engine/factors/mean_reversion.py calculate() exactly."""
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        z_score = (close - sma) / std.replace(0, np.nan)
        return (-z_score / 2.0).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_rsi_score(close: pd.Series, period: int = 14) -> pd.Series:
        """RSI 80 → +1 (strong trend / bullish), RSI 50 → 0, RSI 20 → -1 (weak / bearish).

        Trend-following mapping: RSI > 50 means cumulative gains exceed losses
        over the lookback, confirming upward momentum.  For a 20-day forward
        return prediction, a high RSI indicates the trend is intact (bullish).
        """
        delta    = close.diff()
        gain     = delta.where(delta > 0, 0.0)
        loss     = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs  = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return ((rsi - 50) / 30.0).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_macd_score(close: pd.Series, fast=12, slow=26, signal=9) -> pd.Series:
        """MACD histogram normalized by price, with crossover bonus."""
        ema_fast    = close.ewm(span=fast, adjust=False).mean()
        ema_slow    = close.ewm(span=slow, adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram   = macd_line - signal_line

        # Normalize histogram by price
        normalized = (histogram / close * 100).fillna(0.0)
        base       = (normalized / 1.0).clip(-0.7, 0.7)

        # Crossover bonus
        prev_hist       = histogram.shift(1)
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
        sma        = close.rolling(period).mean()
        std        = close.rolling(period).std()
        upper      = sma + std_mult * std
        lower      = sma - std_mult * std
        band_width = upper - lower
        pct_b      = (close - lower) / band_width.replace(0, np.nan)
        pct_b      = pct_b.fillna(0.5)
        return ((0.5 - pct_b) * 2.0).clip(-1.0, 1.0).fillna(0.0)

    @staticmethod
    def _rolling_volume_score(close: pd.Series, volume: pd.Series, lookback=20) -> pd.Series:
        """Volume surge with price direction confirmation."""
        avg_vol       = volume.rolling(lookback).mean()
        vol_ratio     = (volume / avg_vol.replace(0, np.nan)).fillna(1.0)
        price_change_5d = close.pct_change(5).fillna(0.0)

        score = pd.Series(0.0, index=close.index)

        # High volume surge (> 1.5x average)
        high_surge = vol_ratio > 1.5
        score[high_surge & (price_change_5d > 0)]  = (vol_ratio[high_surge & (price_change_5d > 0)]  / 3.0).clip(0, 1.0)
        score[high_surge & (price_change_5d <= 0)] = -(vol_ratio[high_surge & (price_change_5d <= 0)] / 3.0).clip(0, 1.0)

        # Moderate surge (1.2x - 1.5x)
        mod_surge = (vol_ratio > 1.2) & (vol_ratio <= 1.5)
        score[mod_surge & (price_change_5d > 0)]  = 0.3
        score[mod_surge & (price_change_5d <= 0)] = -0.3

        return score.fillna(0.0)

    @staticmethod
    def _rolling_volatility_score(close: pd.Series, short_w=20, long_w=60) -> pd.Series:
        """Low vol ratio = bullish (contracting risk), high = bearish."""
        returns   = close.pct_change()
        vol_short = returns.rolling(short_w).std() * np.sqrt(252)
        vol_long  = returns.rolling(long_w).std()  * np.sqrt(252)
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
        roc_63   = close.pct_change(63)
        momentum = (roc_63 / 0.30).clip(-1.0, 1.0).fillna(0.0)

        # Mean-reversion: 20-day Z-score inverted
        sma20  = close.rolling(20).mean()
        std20  = close.rolling(20).std()
        z_score = ((close - sma20) / std20.replace(0, np.nan)).fillna(0.0)
        mean_rev = (-z_score / 2.0).clip(-1.0, 1.0)

        # Blend 60% momentum, 40% mean-reversion
        composite = 0.6 * momentum + 0.4 * mean_rev
        return composite.clip(-1.0, 1.0).fillna(0.0)
