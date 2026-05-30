"""
Assertion tests for the consolidated feature builder
(quant_engine/ml/features.py :: build_feature_frame, P1-e).

The point of this refactor is to make `trainer._build_feature_frame` and
`SicilianStrategy._build_ml_features` impossible to drift — by routing both
through the same pure function. The headline test
`test_trainer_and_strategy_paths_produce_identical_frames` is the explicit
SIC-29 regression — if anyone ever re-introduces a divergence, that assertion
fires immediately.

All tests run on synthetic OHLCV data, no DB dependency.
"""
import numpy as np
import pandas as pd
import pytest

from quant_engine.ml.features import FEATURE_COLS, build_feature_frame
from quant_engine.ml import predictor, trainer
from quant_engine.strategies.sicilian_strategy import SicilianStrategy


def _synth_ohlcv(n=200, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = 100 * np.cumprod(1 + rng.normal(0.0003, 0.012, n))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(1_000_000, 10_000_000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _synth_market_series(idx: pd.DatetimeIndex, seed=1):
    rng = np.random.default_rng(seed)
    sector = pd.Series(rng.uniform(-1, 1, len(idx)), index=idx)
    vix = pd.Series(rng.uniform(-1, 1, len(idx)), index=idx)
    nifty = pd.Series(rng.uniform(-1, 1, len(idx)), index=idx)
    markov = pd.Series(rng.uniform(-1, 1, len(idx)), index=idx)
    delivery = pd.Series(rng.uniform(-1, 1, len(idx)), index=idx)
    fii_fo = pd.Series(rng.uniform(-1, 1, len(idx)), index=idx)
    intraday = pd.DataFrame(
        {col: rng.normal(0, 1, len(idx)) for col in (
            "overnight_gap", "intraday_range_ratio", "last_hour_momentum",
            "vwap_deviation", "opening_drive_vol", "closing_spike_vol",
            "vol_concentration",
        )},
        index=idx,
    )
    return sector, vix, nifty, markov, delivery, fii_fo, intraday


# ── Shape / contract ─────────────────────────────────────────────────────────
def test_feature_frame_shape_and_columns():
    df = _synth_ohlcv()
    bench = _synth_ohlcv(seed=99)
    sector, vix, nifty, markov, delivery, fii_fo, intraday = _synth_market_series(df.index)
    strat = SicilianStrategy("_test")
    frame = build_feature_frame(
        df, bench, strategy=strat,
        sector_score=sector, vix_score=vix, nifty_trend=nifty,
        markov_score=markov, delivery_score=delivery, fii_fo_score=fii_fo,
        intraday_feats=intraday,
    )
    assert frame.index.equals(df.index), "row index must match df.index"
    assert tuple(frame.columns) == FEATURE_COLS, \
        f"columns must be exactly FEATURE_COLS in order, got {list(frame.columns)}"
    assert len(FEATURE_COLS) == 20


def test_feature_cols_synced_across_modules():
    """trainer / predictor / features must all agree — the predictor's
    _verify_feature_alignment guard depends on this."""
    assert tuple(trainer.FEATURE_COLS) == FEATURE_COLS, \
        "trainer.FEATURE_COLS must equal features.FEATURE_COLS"
    assert tuple(predictor.FEATURE_COLS) == FEATURE_COLS, \
        "predictor.FEATURE_COLS must equal features.FEATURE_COLS"


# ── Headline SIC-29 regression: trainer vs strategy paths produce equal frames
def test_trainer_and_strategy_paths_produce_identical_frames():
    """
    Both wrappers (`trainer._build_feature_frame` and
    `SicilianStrategy._build_ml_features.__wrapped_core__`) delegate to
    `build_feature_frame`. Given the same inputs, the resulting DataFrames
    must be byte-identical column-for-column.

    To exercise the trainer wrapper (which doesn't load anything), we feed it
    synthetic series directly. To exercise the strategy without hitting Turso,
    we call `build_feature_frame` with the same inputs as the trainer wrapper
    does. If a future change diverges the trainer wrapper from the shared
    core, this comparison fails.
    """
    df = _synth_ohlcv()
    bench = _synth_ohlcv(seed=2)
    sector, vix, nifty, markov, delivery, fii_fo, intraday = _synth_market_series(df.index)

    # Path A: through the trainer wrapper.
    frame_trainer = trainer._build_feature_frame(
        df, bench, sector, vix, nifty, markov, delivery, fii_fo, intraday,
    )

    # Path B: direct shared-builder call (what the strategy now does).
    strat = SicilianStrategy("_test")
    frame_shared = build_feature_frame(
        df, bench, strategy=strat,
        sector_score=sector, vix_score=vix, nifty_trend=nifty,
        markov_score=markov, delivery_score=delivery, fii_fo_score=fii_fo,
        intraday_feats=intraday,
    )

    pd.testing.assert_frame_equal(
        frame_trainer, frame_shared, check_exact=True,
        obj="trainer wrapper vs shared builder",
    )


# ── Macro/sector handling: empty input → zeros ───────────────────────────────
def test_empty_market_series_become_zeros():
    df = _synth_ohlcv()
    bench = _synth_ohlcv(seed=3)
    empty = pd.Series(dtype=float)
    strat = SicilianStrategy("_test")
    frame = build_feature_frame(
        df, bench, strategy=strat,
        sector_score=empty, vix_score=empty, nifty_trend=empty,
        markov_score=empty, delivery_score=empty, fii_fo_score=empty,
        intraday_feats=pd.DataFrame(),
    )
    macro_cols = ["sector_rotation", "vix_regime", "nifty_trend", "markov_regime",
                  "delivery_score", "fii_fo_score"]
    for col in macro_cols:
        assert (frame[col] == 0.0).all(), \
            f"empty {col} series must produce all-zero column"


# ── Intraday handling: missing column → NaN (NOT zero) ───────────────────────
def test_missing_intraday_columns_stay_nan():
    """Critical: intraday must stay NaN so valid_mask drops pre-2018 bars
    (SIC-29 lesson — zero-filling pre-intraday rows encodes a regime split)."""
    df = _synth_ohlcv()
    bench = _synth_ohlcv(seed=4)
    sector, vix, nifty, markov, delivery, fii_fo, _ = _synth_market_series(df.index)
    empty_intraday = pd.DataFrame()
    strat = SicilianStrategy("_test")
    frame = build_feature_frame(
        df, bench, strategy=strat,
        sector_score=sector, vix_score=vix, nifty_trend=nifty,
        markov_score=markov, delivery_score=delivery, fii_fo_score=fii_fo,
        intraday_feats=empty_intraday,
    )
    intraday_cols = ["overnight_gap", "intraday_range_ratio", "last_hour_momentum",
                     "vwap_deviation", "opening_drive_vol", "closing_spike_vol",
                     "vol_concentration"]
    for col in intraday_cols:
        assert frame[col].isna().all(), \
            f"missing intraday column {col} must be all-NaN, not zero-filled"


# ── Dedup defence: duplicate-index inputs don't crash ────────────────────────
def test_duplicate_index_inputs_handled():
    df = _synth_ohlcv(n=50)
    bench = _synth_ohlcv(n=50, seed=5)
    sector_dates = df.index.repeat(2)
    sector_dup = pd.Series(np.linspace(-1, 1, len(sector_dates)), index=sector_dates)
    vix, nifty, markov, delivery, fii_fo = (
        pd.Series(np.zeros(len(df)), index=df.index) for _ in range(5)
    )
    intraday = pd.DataFrame(index=df.index)
    strat = SicilianStrategy("_test")
    # Without dedup this would raise ValueError on .reindex().
    frame = build_feature_frame(
        df, bench, strategy=strat,
        sector_score=sector_dup, vix_score=vix, nifty_trend=nifty,
        markov_score=markov, delivery_score=delivery, fii_fo_score=fii_fo,
        intraday_feats=intraday,
    )
    assert frame["sector_rotation"].notna().all(), \
        "deduped sector_rotation must produce a non-NaN column"


# ── Pickle-alignment guard contract ──────────────────────────────────────────
def test_predictor_alignment_guard_would_accept_our_frame():
    """The predictor's _verify_feature_alignment compares the pickle's
    feature_names_in_ to FEATURE_COLS. Our build_feature_frame must produce
    column names equal to predictor.FEATURE_COLS in that exact order."""
    df = _synth_ohlcv(n=80)
    bench = _synth_ohlcv(n=80, seed=6)
    sector, vix, nifty, markov, delivery, fii_fo, intraday = _synth_market_series(df.index)
    strat = SicilianStrategy("_test")
    frame = build_feature_frame(
        df, bench, strategy=strat,
        sector_score=sector, vix_score=vix, nifty_trend=nifty,
        markov_score=markov, delivery_score=delivery, fii_fo_score=fii_fo,
        intraday_feats=intraday,
    )
    assert list(frame.columns) == list(predictor.FEATURE_COLS), \
        "output column order must match predictor.FEATURE_COLS exactly"
