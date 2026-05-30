"""
Single-source-of-truth feature builder for the Sicilian ML pipeline.

Until 2026-05-21 there were TWO implementations of "compute the 20-column
feature matrix for one symbol":

  • `quant_engine/ml/trainer.py::_build_feature_frame`
  • `quant_engine/strategies/sicilian_strategy.py::_build_ml_features`

They were supposed to be byte-identical but quietly drifted (trainer's `_align`
didn't dedup, strategy's did; trainer pulled delivery directly, strategy reused
market_ctx). SIC-29 was caused by exactly this kind of drift — the
`_verify_feature_alignment` runtime guard catches column-set divergence but
NOT value-computation divergence. ML audit S9.

This module owns the **pure transformation** "given OHLCV + already-fetched
market series, return the FEATURE_COLS DataFrame". Both callers delegate to
it. Data loading stays at each caller's level (trainer fetches once globally
per training run; strategy may use a precomputed `market_ctx` or load
per-symbol at inference time) — but the column construction is shared.

The module is pure / DB-independent; assertion tests in
`quant_engine/tests/test_ml_features.py` confirm trainer and strategy paths
produce identical frames on identical inputs.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Canonical feature-column order. trainer.FEATURE_COLS and predictor.FEATURE_COLS
# must stay equal to this — `predictor._verify_feature_alignment` enforces
# alignment with the pickle at load time. Kept here so the column ORDER lives
# alongside the construction code (drift-resistant).
FEATURE_COLS: tuple[str, ...] = (
    "rsi",
    "macd",
    "trend_ma",
    "bollinger",
    "volume",
    "volatility",
    "relative_strength",
    "sector_rotation",
    "vix_regime",
    "nifty_trend",
    "markov_regime",
    "delivery_score",
    "fii_fo_score",
    "overnight_gap",
    "intraday_range_ratio",
    "last_hour_momentum",
    "vwap_deviation",
    "opening_drive_vol",
    "closing_spike_vol",
    "vol_concentration",
)


def _dedup(obj: Optional[pd.Series], label: str, context: str) -> Optional[pd.Series]:
    """
    Collapse duplicate index labels so downstream reindex calls don't raise
    `ValueError("cannot reindex on an axis with duplicate labels")`.

    Logs a warning when dedup actually fires so the data source can be traced;
    silently fixing the symptom would let upstream corruption persist.
    """
    if obj is None or (hasattr(obj, "empty") and obj.empty):
        return obj
    if not obj.index.is_unique:
        n_dupes = int(obj.index.duplicated().sum())
        logger.warning(
            "build_feature_frame(%s): %s had %d duplicate-index row(s); "
            "keeping last occurrence",
            context, label, n_dupes,
        )
        obj = obj[~obj.index.duplicated(keep="last")]
    return obj


def _align(series: Optional[pd.Series], target_index: pd.Index,
           label: str, context: str) -> pd.Series:
    """
    Reindex a market-level series onto the per-symbol date index.

    Empty series → zeros (the macro signal is "unknown" / "neutral"). Filled
    with `method="ffill"` then `.fillna(0.0)` so historical bars before the
    series starts still get a defined value.
    """
    if series is None or (hasattr(series, "empty") and series.empty):
        return pd.Series(0.0, index=target_index)
    series = _dedup(series, label, context)
    return series.reindex(target_index, method="ffill").fillna(0.0)


def _align_intraday(intraday_feats: Optional[pd.DataFrame], col: str,
                    target_index: pd.Index, context: str) -> pd.Series:
    """
    Reindex an intraday feature column point-in-time (NO ffill). Missing rows
    stay NaN so the caller's `valid_mask = features.notna().all(axis=1)` drops
    them — prevents pre-2018 bars (where intraday data doesn't exist) from
    polluting trees with a zero-constant that encodes a spurious pre/post-2018
    regime split. SIC-29 lesson.
    """
    if intraday_feats is None or intraday_feats.empty or col not in intraday_feats.columns:
        return pd.Series(np.nan, index=target_index)
    deduped = _dedup(intraday_feats[col], f"intraday.{col}", context)
    return deduped.reindex(target_index)


def build_feature_frame(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    strategy,                                 # SicilianStrategy instance (provides factor scorers)
    sector_score: Optional[pd.Series],
    vix_score: Optional[pd.Series],
    nifty_trend: Optional[pd.Series],
    markov_score: Optional[pd.Series],
    delivery_score: Optional[pd.Series],
    fii_fo_score: Optional[pd.Series],
    intraday_feats: Optional[pd.DataFrame],
    context: str = "build_feature_frame",
) -> pd.DataFrame:
    """
    Compute the FEATURE_COLS feature matrix for one symbol.

    Args:
        df:               OHLCV DataFrame indexed by date.
        benchmark_df:     NIFTY 50 OHLC for relative-strength computation.
        strategy:         A `SicilianStrategy` instance — supplies the
                          `_rolling_*_score` factor methods. (Passed in rather
                          than imported so this module stays import-cycle-free.)
        sector_score:     Per-industry 20d sector-vs-benchmark rotation score.
        vix_score:        India VIX percentile → [-1 fear, +1 calm].
        nifty_trend:      NIFTY position vs SMA50/200.
        markov_score:     Markov P(bull) − P(bear).
        delivery_score:   NSE delivery_pct z-score, clipped & normalised.
        fii_fo_score:     FII F&O positioning percentile.
        intraday_feats:   DataFrame of intraday columns (`overnight_gap`,
                          `intraday_range_ratio`, …). NaN-where-missing.
        context:          Short tag included in warning logs (typically the
                          symbol or "_trainer"); helps locate bad data sources.

    Returns:
        DataFrame indexed like `df.index` with exactly `FEATURE_COLS` as its
        columns and in that order. Macro/sector/delivery features are
        ffill+0-filled; intraday features keep NaN so the caller's valid_mask
        drops them.
    """
    close = df["close"]
    volume = df["volume"]
    idx = df.index

    frame = pd.DataFrame(
        {
            # ── Per-symbol price-derived sub-scores (always available) ──────
            "rsi":               strategy._rolling_rsi_score(close),
            "macd":              strategy._rolling_macd_score(close),
            "trend_ma":          strategy._rolling_trend_score(close),
            "bollinger":         strategy._rolling_bollinger_score(close),
            "volume":            strategy._rolling_volume_score(close, volume),
            "volatility":        strategy._rolling_volatility_score(close),
            "relative_strength": strategy._rolling_relative_strength_score(close, benchmark_df),
            # ── Cross-stock / macro (date-aligned, ffilled, zero-filled) ────
            "sector_rotation":   _align(sector_score,  idx, "sector_rotation",  context),
            "vix_regime":        _align(vix_score,     idx, "vix_regime",       context),
            "nifty_trend":       _align(nifty_trend,   idx, "nifty_trend",      context),
            "markov_regime":     _align(markov_score,  idx, "markov_regime",    context),
            "delivery_score":    _align(delivery_score, idx, "delivery_score",  context),
            "fii_fo_score":      _align(fii_fo_score,  idx, "fii_fo_score",     context),
            # ── Intraday (point-in-time, NaN-where-missing) ─────────────────
            "overnight_gap":         _align_intraday(intraday_feats, "overnight_gap",        idx, context),
            "intraday_range_ratio":  _align_intraday(intraday_feats, "intraday_range_ratio", idx, context),
            "last_hour_momentum":    _align_intraday(intraday_feats, "last_hour_momentum",   idx, context),
            "vwap_deviation":        _align_intraday(intraday_feats, "vwap_deviation",       idx, context),
            "opening_drive_vol":     _align_intraday(intraday_feats, "opening_drive_vol",    idx, context),
            "closing_spike_vol":     _align_intraday(intraday_feats, "closing_spike_vol",    idx, context),
            "vol_concentration":     _align_intraday(intraday_feats, "vol_concentration",    idx, context),
        },
        index=idx,
    )
    # Enforce canonical column order — this is what _verify_feature_alignment
    # checks against the pickle's `feature_names_in_`.
    return frame[list(FEATURE_COLS)]
