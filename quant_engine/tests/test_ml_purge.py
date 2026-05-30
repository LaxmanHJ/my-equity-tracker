"""
Assertion tests for label-horizon purge + post-test embargo
(quant_engine/ml/diagnostic.py :: _purge_train_indices, AFML Ch.10).

Deterministic, DB-independent. Builds a synthetic ordered date index and
verifies that:
  • backward purge drops exactly the train rows within `horizon` days before
    the test fold
  • forward embargo drops exactly the train rows within `embargo` days after
    the test fold (CPCV path)
  • walk-forward (train strictly before test) is unaffected by the embargo
    parameter
  • `embargo_days=0` reproduces the pre-P1-f behaviour byte-for-byte
"""
import numpy as np
import pandas as pd
import pytest

from quant_engine.ml.diagnostic import _purge_train_indices


def _setup(n_days=100, repeats_per_day=1):
    """N consecutive business days; optional `repeats_per_day` rows per date."""
    cal = pd.date_range("2020-01-01", periods=n_days, freq="B")
    if repeats_per_day == 1:
        return cal
    return pd.DatetimeIndex(np.repeat(cal, repeats_per_day))


# ── Walk-forward (train strictly before test) ────────────────────────────────
def test_backward_purge_walk_forward():
    """Train = days [0..49], test = days [60..79], horizon = 5.
    Expect train rows with date >= test_start − 5 = day 55 dropped (none here),
    train rows < day 55 kept (all 50 here). Pulls horizon to 15 to verify."""
    dates = _setup(100)
    horizon = 15
    train_idx = np.arange(0, 50)        # days 0..49
    test_idx = np.arange(60, 80)        # days 60..79; test_start = day 60
    out = _purge_train_indices(train_idx, test_idx, dates, horizon, embargo_days=0)
    # Anything with train_date >= dates[60 - 15] = dates[45] is purged.
    # Train rows are days 0..49 → days 45..49 should be dropped, days 0..44 kept.
    expected = np.arange(0, 45)
    np.testing.assert_array_equal(out, expected)


def test_walk_forward_unaffected_by_embargo():
    """When all train comes before test, embargo can't drop anything extra."""
    dates = _setup(100)
    horizon = 10
    train_idx = np.arange(0, 60)
    test_idx = np.arange(70, 90)

    out_no_emb = _purge_train_indices(train_idx, test_idx, dates, horizon, embargo_days=0)
    out_emb = _purge_train_indices(train_idx, test_idx, dates, horizon, embargo_days=20)
    np.testing.assert_array_equal(out_no_emb, out_emb), \
        "walk-forward: embargo must not change the surviving train set"


# ── Embargo (CPCV-style: train AFTER test) ───────────────────────────────────
def test_forward_embargo_drops_post_test_window():
    """Train = days [80..99] (AFTER test [60..79]); horizon=0 disables backward
    purge so the test fold ALL leaks must come from the forward embargo.
    embargo=10 ⇒ drop days 80..89, keep days 90..99."""
    dates = _setup(100)
    train_idx = np.arange(80, 100)
    test_idx = np.arange(60, 80)
    out = _purge_train_indices(train_idx, test_idx, dates, label_horizon_days=0,
                                embargo_days=10)
    # test_end = day 79, embargo release = day 79 + 10 = 89, keep > 89 → days 90..99
    expected = np.arange(90, 100)
    np.testing.assert_array_equal(out, expected)


def test_embargo_zero_matches_legacy_behaviour():
    """`embargo_days=0` must be a perfect byte-for-byte revert."""
    dates = _setup(100)
    horizon = 15
    train_idx = np.arange(0, 50)
    test_idx = np.arange(60, 80)

    # Legacy logic (pre-P1-f) reproduced inline:
    unique_sorted = sorted(set(dates))
    test_start_pos = unique_sorted.index(dates[test_idx[0]])
    purge_pos = max(0, test_start_pos - horizon)
    threshold = unique_sorted[purge_pos]
    legacy = train_idx[np.asarray(dates[train_idx] < threshold)]

    new = _purge_train_indices(train_idx, test_idx, dates, horizon, embargo_days=0)
    np.testing.assert_array_equal(new, legacy)


def test_embargo_default_equals_horizon():
    """If embargo_days is None it defaults to horizon (AFML recommendation)."""
    dates = _setup(100)
    train_idx = np.concatenate([np.arange(0, 50), np.arange(80, 100)])
    test_idx = np.arange(60, 80)
    out_default = _purge_train_indices(train_idx, test_idx, dates,
                                        label_horizon_days=10)
    out_explicit = _purge_train_indices(train_idx, test_idx, dates,
                                         label_horizon_days=10, embargo_days=10)
    np.testing.assert_array_equal(out_default, out_explicit)


# ── Combined CPCV: both sides of the test fold present ───────────────────────
def test_cpcv_purges_both_sides():
    """Train spans both sides: [0..49] (before) and [80..99] (after) the test
    fold [60..79]. Horizon=15, embargo=10:
      • Backward: drop days >= 60−15 = 45 → drop 45..49
      • Forward:  drop days <= 79+10 = 89 → drop 80..89
    Surviving train = [0..44] ∪ [90..99]."""
    dates = _setup(100)
    train_idx = np.concatenate([np.arange(0, 50), np.arange(80, 100)])
    test_idx = np.arange(60, 80)
    out = _purge_train_indices(train_idx, test_idx, dates, 15, embargo_days=10)
    expected = np.concatenate([np.arange(0, 45), np.arange(90, 100)])
    np.testing.assert_array_equal(out, expected)


# ── Edge cases ───────────────────────────────────────────────────────────────
def test_empty_inputs_passthrough():
    dates = _setup(20)
    out = _purge_train_indices(np.array([], dtype=int), np.arange(5, 10), dates, 5)
    assert out.size == 0
    out2 = _purge_train_indices(np.arange(0, 10), np.array([], dtype=int), dates, 5)
    np.testing.assert_array_equal(out2, np.arange(0, 10))


def test_duplicate_dates_handled():
    """Multiple symbols on the same date — the function must treat the
    unique-date set as the calendar, not the raw row count."""
    # 10 calendar days, 3 symbols each ⇒ index has 30 rows.
    dates = _setup(10, repeats_per_day=3)
    train_idx = np.arange(0, 12)   # first 4 days × 3 syms
    test_idx = np.arange(18, 24)   # days 6-7
    out = _purge_train_indices(train_idx, test_idx, dates, label_horizon_days=2,
                                embargo_days=0)
    # test_start_date = day 6 → purge_pos = day 4 → keep rows whose date < day 4.
    # Train rows are days 0,0,0,1,1,1,2,2,2,3,3,3 — all < day 4 → all kept.
    np.testing.assert_array_equal(out, train_idx)

    # Tighten horizon to 5 ⇒ purge_pos = day 1 ⇒ keep rows whose date < day 1
    # → only days 0,0,0 survive (indices 0,1,2 of train).
    out_tight = _purge_train_indices(train_idx, test_idx, dates, label_horizon_days=5,
                                      embargo_days=0)
    np.testing.assert_array_equal(out_tight, np.array([0, 1, 2]))


def test_returns_indices_into_original_array():
    """Sanity: result must be a subset of `train_idx` (not row positions of
    the kept rows)."""
    dates = _setup(50)
    train_idx = np.array([3, 7, 11, 22, 35, 42])
    test_idx = np.arange(45, 50)
    out = _purge_train_indices(train_idx, test_idx, dates, 5, embargo_days=0)
    assert set(out).issubset(set(train_idx)), \
        f"output must be a subset of train_idx; got {out} not in {train_idx}"
