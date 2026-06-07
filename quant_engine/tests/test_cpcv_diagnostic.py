"""
Assertion tests for the CPCV + DSR/PBO diagnostic runner
(``quant_engine/ml/cpcv_diagnostic.py``).

This runner is the ship/no-ship gate, so the portfolio-construction and
Sharpe math underneath it must be correct. The full runner needs Turso, so
these tests cover the pure, DB-free building blocks:

  • _daily_portfolio_returns: long-only vs long-short leg construction,
    quantile selection, the short-side-isolation property
  • _annualized_sharpe: scaling, degenerate-series guards
  • the long-short-beats-long-only detection on a synthetic short-loaded
    signal (the exact pathology the post-PIT diagnostic surfaced for linear)

The CPCV/DSR/PBO primitives themselves are tested in test_ml_cpcv.py and
test_ml_validation.py — here we test only the runner's glue.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_engine.ml.cpcv_diagnostic import (
    _annualized_sharpe,
    _daily_portfolio_returns,
    TRADING_DAYS,
)


# ── _annualized_sharpe ──────────────────────────────────────────────────────
def test_annualized_sharpe_scales_by_sqrt_trading_days():
    """A constant-mean / unit-vol daily series must annualize by √252."""
    rng = np.random.default_rng(0)
    daily = pd.Series(rng.normal(0.001, 0.01, 500))
    sharpe = _annualized_sharpe(daily)
    raw = daily.mean() / daily.std(ddof=1)
    assert abs(sharpe - raw * np.sqrt(TRADING_DAYS)) < 1e-9


def test_annualized_sharpe_nan_on_degenerate_series():
    assert np.isnan(_annualized_sharpe(pd.Series([0.01] * 5)))     # too short
    assert np.isnan(_annualized_sharpe(pd.Series([0.01] * 50)))    # zero variance
    assert np.isnan(_annualized_sharpe(pd.Series(dtype=float)))    # empty


def test_annualized_sharpe_positive_for_positive_drift():
    rng = np.random.default_rng(1)
    daily = pd.Series(rng.normal(0.002, 0.005, 300))  # strong positive drift
    assert _annualized_sharpe(daily) > 1.0


# ── _daily_portfolio_returns: construction ──────────────────────────────────
def _panel(n_days: int, n_per_day: int, seed: int = 0):
    """Build a (date, score, fwd) panel with n_per_day names per date."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-01-02", periods=n_days)
    dates = pd.DatetimeIndex(np.repeat(days.values, n_per_day))
    score = rng.normal(0, 1, len(dates))
    fwd = rng.normal(0, 0.02, len(dates))
    return score, fwd, dates


def test_daily_portfolio_returns_have_one_row_per_qualifying_date():
    score, fwd, dates = _panel(n_days=40, n_per_day=10)
    lo, ls = _daily_portfolio_returns(score, fwd, dates)
    assert len(lo) == 40, "one long-only return per date with enough names"
    assert len(ls) == 40, "one long-short return per date"
    assert lo.index.is_monotonic_increasing


def test_daily_portfolio_returns_skip_thin_cross_sections():
    """Dates with fewer than MIN_XS_FOR_PORTFOLIO names must be dropped."""
    # 3 names/day < MIN_XS_FOR_PORTFOLIO (5) → no rows.
    score, fwd, dates = _panel(n_days=20, n_per_day=3)
    lo, ls = _daily_portfolio_returns(score, fwd, dates)
    assert len(lo) == 0 and len(ls) == 0


def test_long_short_isolates_cross_sectional_spread():
    """Construct a date where score perfectly ranks fwd return: the top
    quantile must have higher mean fwd than the bottom, so long_short > 0
    and long_only equals the top-quantile mean."""
    # Single date, 10 names, score == fwd (perfect rank).
    dates = pd.DatetimeIndex([pd.Timestamp("2024-01-02")] * 10)
    fwd = np.array([-0.05, -0.04, -0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05])
    score = fwd.copy()  # perfect correlation
    lo, ls = _daily_portfolio_returns(score, fwd, dates, quantile=0.20)
    # top 20% of 10 = 2 names: +0.04, +0.05 → mean 0.045
    assert abs(lo.iloc[0] - 0.045) < 1e-9
    # bottom 2: -0.05, -0.04 → mean -0.045; long_short = 0.045 - (-0.045) = 0.09
    assert abs(ls.iloc[0] - 0.09) < 1e-9


def test_long_short_negative_when_score_anticorrelated():
    """If the score is INVERSELY related to fwd (high score → low return —
    exactly the post-PIT linear pathology), long_only must be negative and
    long_short must be negative."""
    dates = pd.DatetimeIndex([pd.Timestamp("2024-01-02")] * 10)
    fwd = np.array([-0.05, -0.04, -0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05])
    score = -fwd  # high score = low future return (survivorship momentum trap)
    lo, ls = _daily_portfolio_returns(score, fwd, dates, quantile=0.20)
    # top 2 by score are the WORST fwd names (-0.05, -0.04) → lo = -0.045
    assert lo.iloc[0] < 0, "anti-correlated score must make long-only negative"
    assert ls.iloc[0] < 0, "anti-correlated score must make long-short negative"


def test_nan_scores_and_returns_dropped_per_date():
    dates = pd.DatetimeIndex([pd.Timestamp("2024-01-02")] * 10)
    fwd = np.array([0.01, 0.02, np.nan, 0.03, 0.04, 0.05, 0.06, np.nan, 0.07, 0.08])
    score = np.array([1.0, 2.0, 3.0, np.nan, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    lo, ls = _daily_portfolio_returns(score, fwd, dates)
    # 7 valid names remain (≥5) → one row produced, no NaN in output.
    assert len(lo) == 1
    assert not np.isnan(lo.iloc[0])
    assert not np.isnan(ls.iloc[0])


# ── End-to-end story: short-loaded signal across many days ──────────────────
def test_short_loaded_signal_shows_ls_beats_lo():
    """The decisive diagnostic property: a signal whose information is on the
    SHORT side (good at picking losers, useless at picking winners) must show
    long_short Sharpe meaningfully ABOVE long_only Sharpe — the runner's whole
    reason for computing both. This is the synthetic analogue of the linear
    composite's post-PIT behaviour."""
    rng = np.random.default_rng(7)
    n_days, n_per = 250, 20
    days = pd.bdate_range("2023-01-02", periods=n_days)
    dates = pd.DatetimeIndex(np.repeat(days.values, n_per))
    n = len(dates)

    # True future return = noise; the BOTTOM names are reliably bad, the TOP
    # names are pure noise. Score ranks by a latent that's informative only
    # at the bottom.
    latent = rng.normal(0, 1, n)
    fwd = np.where(
        latent < -0.8,
        rng.normal(-0.03, 0.01, n),    # genuinely bad names
        rng.normal(0.0, 0.02, n),      # everyone else: noise
    )
    score = latent  # high score = not-bad; low score = bad

    lo, ls = _daily_portfolio_returns(score, fwd, dates, quantile=0.20)
    lo_sharpe = _annualized_sharpe(lo)
    ls_sharpe = _annualized_sharpe(ls)

    assert not np.isnan(ls_sharpe) and not np.isnan(lo_sharpe)
    assert ls_sharpe > lo_sharpe, (
        f"short-loaded signal must show LS Sharpe > LO Sharpe; "
        f"got LS={ls_sharpe:.2f}, LO={lo_sharpe:.2f}"
    )


# ── Signature / contract guard ──────────────────────────────────────────────
def test_run_cpcv_diagnostic_signature():
    """The runner must expose pit_universe + the CPCV knobs with safe defaults
    so it's callable from a notebook as well as the CLI."""
    import inspect
    from quant_engine.ml.cpcv_diagnostic import run_cpcv_diagnostic
    sig = inspect.signature(run_cpcv_diagnostic)
    for p in ("pit_universe", "n_groups", "n_test_groups", "n_trials"):
        assert p in sig.parameters, f"run_cpcv_diagnostic must accept {p}"
    assert sig.parameters["pit_universe"].default is None, \
        "PIT must default off so the runner doesn't require a registry to import"
    assert sig.parameters["n_groups"].default == 6
    assert sig.parameters["n_test_groups"].default == 2
