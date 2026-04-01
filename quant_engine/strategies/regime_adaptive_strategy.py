"""
Regime-Adaptive Strategy.

Switches between two signal modes depending on the current market regime:

  BULL regime  (score > +0.15) — trend-following (Sicilian linear composite)
  BEAR regime  (score < -0.15) — mean reversion  (buy oversold, exit at mean)
  NEUTRAL zone (|score| ≤ 0.15) — cash

Regime score is a weighted blend of four macro signals, each on [-1, +1]:
  VIX regime    35%  — low VIX = calm market = trend-friendly
  Nifty trend   25%  — price above SMA50/200 = bull
  Markov        25%  — hidden-state bull/bear probability
  FII flow      15%  — institutional money direction

All signal computation reuses SicilianStrategy's vectorized helpers — nothing
is duplicated. SicilianStrategy itself is completely untouched.
"""
import logging

import numpy as np
import pandas as pd

from quant_engine.strategies.sicilian_strategy import SicilianStrategy

logger = logging.getLogger(__name__)

# Regime classification thresholds
BULL_THRESHOLD = 0.15
BEAR_THRESHOLD = -0.15

# Mean reversion entry/exit z-score thresholds
MR_ENTRY_Z  = -1.5   # buy when price is 1.5 std below 20-day mean
MR_EXIT_Z   =  0.0   # exit when price reverts back to the mean


class RegimeAdaptiveStrategy(SicilianStrategy):
    """
    Regime-Adaptive Strategy — switches between trend-following and mean
    reversion based on a real-time macro regime score.

    Inherits all rolling factor calculators from SicilianStrategy.
    Only generate_signals() is overridden.
    """

    def generate_signals(self, data: pd.DataFrame, **kwargs) -> pd.Series:
        """
        Generate signals by first classifying each bar's regime, then
        delegating to the appropriate sub-strategy.

        Args:
            data:         OHLCV DataFrame with DateTimeIndex.
            **kwargs:
                benchmark_df – NIFTY OHLCV DataFrame (optional).
                symbol       – NSE ticker string (optional, passed through).

        Returns:
            pd.Series of signals: 1 (long), 0 (flat), indexed like data.
        """
        df           = data.copy()
        benchmark_df = kwargs.get("benchmark_df", pd.DataFrame())
        n            = len(df)

        if benchmark_df is None:
            benchmark_df = pd.DataFrame()

        # ── Step 1: build regime score series ────────────────────────────────
        regime_score = self._build_regime_score(df, benchmark_df)

        # ── Step 2: build both signal series ─────────────────────────────────
        trend_signals = self._linear_signals(df, benchmark_df)
        mr_signals    = self._mean_reversion_signals(df)

        # ── Step 3: combine based on regime ──────────────────────────────────
        bull = regime_score > BULL_THRESHOLD
        bear = regime_score < BEAR_THRESHOLD

        signals = pd.Series(0.0, index=df.index)
        signals[bull] = trend_signals[bull]
        signals[bear] = mr_signals[bear]
        # neutral zone stays 0 (cash)

        bull_pct    = bull.mean() * 100
        bear_pct    = bear.mean() * 100
        neutral_pct = 100 - bull_pct - bear_pct
        logger.info(
            "Regime split — Bull: %.1f%%  Bear: %.1f%%  Neutral: %.1f%%",
            bull_pct, bear_pct, neutral_pct,
        )

        return signals

    # ── Regime score ──────────────────────────────────────────────────────────

    def _build_regime_score(
        self, df: pd.DataFrame, benchmark_df: pd.DataFrame
    ) -> pd.Series:
        """
        Compute a rolling regime score for every bar in df.

        Four components, each on [-1, +1], weighted and summed:
          VIX regime  35% — low VIX = calm market = bullish regime
          Nifty trend 25% — price vs SMA50/200
          Markov      25% — hidden Markov bull/bear state
          FII flow    15% — institutional buying/selling pressure

        Missing data (e.g. VIX table empty) gracefully falls back to 0.0
        for that component, so the other signals still drive the regime.
        """
        from quant_engine.data.market_regime_loader import (
            load_vix_series, vix_to_score,
            build_markov_score_series,
            load_fii_flow_series, _flow_to_score,
        )

        def _align(series: pd.Series) -> pd.Series:
            """Reindex any date-indexed series to df's index; ffill gaps."""
            if series is None or (hasattr(series, "empty") and series.empty):
                return pd.Series(0.0, index=df.index)
            return series.reindex(df.index, method="ffill").fillna(0.0)

        # VIX regime — low VIX → +1 (calm/bullish), high VIX → -1 (fearful)
        vix_raw   = load_vix_series(limit=2000)
        vix_score = (
            vix_to_score(vix_raw) if not vix_raw.empty else pd.Series(dtype=float)
        )

        # Nifty trend — price vs SMA50/SMA200 (inherits from SicilianStrategy)
        nifty_trend = self._build_nifty_trend(benchmark_df)

        # Markov regime — bull/bear state probability from benchmark returns
        markov = (
            build_markov_score_series(benchmark_df)
            if not benchmark_df.empty
            else pd.Series(dtype=float)
        )

        # FII cash flow — rolling 10-day net buying pressure
        fii_raw = load_fii_flow_series(limit=2000)
        fii_score = (
            _flow_to_score(fii_raw.rolling(10).sum())
            if not fii_raw.empty
            else pd.Series(dtype=float)
        )

        regime = (
            0.35 * _align(vix_score)   +
            0.25 * _align(nifty_trend) +
            0.25 * _align(markov)      +
            0.15 * _align(fii_score)
        ).clip(-1.0, 1.0)

        return regime

    # ── Mean reversion sub-strategy ───────────────────────────────────────────

    def _mean_reversion_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Generate mean reversion signals from a 20-day z-score.

        Entry condition  (signal → 1): z-score < MR_ENTRY_Z  (-1.5)
            The stock is 1.5 standard deviations below its 20-day mean.
            This is statistically "oversold" — the kind of dip that tends
            to snap back in the absence of a structural breakdown.

        Exit condition (signal → 0): z-score crosses back above MR_EXIT_Z (0.0)
            The price has reverted to its mean. Take the profit and wait
            for the next oversold opportunity.

        The signal is stateful: once in a trade, stay in until the exit
        condition fires (even if z-score briefly dips again below entry).
        A simple bar-by-bar loop is used — with daily data (~1000 bars max)
        this is fast and avoids pandas gotchas with forward-fill approaches.
        """
        close = df["close"]
        sma   = close.rolling(20, min_periods=10).mean()
        std   = close.rolling(20, min_periods=10).std().replace(0, np.nan)
        z     = ((close - sma) / std).fillna(0.0)

        signals  = np.zeros(len(df), dtype=float)
        in_trade = False

        for i in range(len(df)):
            if not in_trade and z.iloc[i] < MR_ENTRY_Z:
                in_trade = True          # enter: stock is oversold
            elif in_trade and z.iloc[i] >= MR_EXIT_Z:
                in_trade = False         # exit: price reverted to mean

            signals[i] = 1.0 if in_trade else 0.0

        return pd.Series(signals, index=df.index)
