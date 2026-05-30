"""
Assertion tests for sample-uniqueness weights (quant_engine/ml/sample_weights.py).

Deterministic, DB-independent assertions on the ML output — part of the P1
testing discipline (every ML logic change ships with assertions on its
output). See wiki/concepts/ml_audit_2026_05_21.md → S4.
"""
import numpy as np
import pandas as pd
import pytest

from quant_engine.ml.labels import triple_barrier_events
from quant_engine.ml.sample_weights import (
    concurrency_count,
    uniqueness_weights,
)


def _dates(*iso_strs) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(s) for s in iso_strs])


# ── Output shape & domain ────────────────────────────────────────────────────
def test_weights_shape_and_domain():
    t0 = _dates("2020-01-01", "2020-01-02", "2020-01-03")
    t1 = _dates("2020-01-05", "2020-01-06", "2020-01-07")
    w = uniqueness_weights(t0, t1)
    assert w.shape == (3,), "one weight per label"
    assert (w > 0).all() and (w <= 1.0).all(), \
        f"weights must lie in (0, 1], got {w}"


def test_empty_input_returns_empty_array():
    w = uniqueness_weights(_dates(), _dates())
    assert isinstance(w, np.ndarray)
    assert w.shape == (0,)


# ── Reference values: isolated, fully-overlapping, non-overlapping ───────────
def test_single_label_weighs_one():
    """An isolated label has no temporal company → uniqueness = 1.0."""
    w = uniqueness_weights(_dates("2020-01-01"), _dates("2020-01-21"))
    assert w.shape == (1,)
    assert w[0] == pytest.approx(1.0), f"single-label weight must be 1.0, got {w[0]}"


def test_k_fully_coincident_labels_weigh_one_over_k():
    """K labels with identical [t0, t1] → each weight = 1/K (AFML §4.6 reference)."""
    for k in (2, 3, 5, 10):
        t0 = _dates(*(["2020-01-01"] * k))
        t1 = _dates(*(["2020-01-21"] * k))
        w = uniqueness_weights(t0, t1)
        assert w.shape == (k,)
        np.testing.assert_allclose(
            w, np.full(k, 1.0 / k), atol=1e-12,
            err_msg=f"{k} coincident labels must each weigh 1/{k}",
        )


def test_non_overlapping_labels_each_weigh_one():
    """Labels whose lifespans don't share any master-calendar date → all weigh 1."""
    t0 = _dates("2020-01-01", "2020-02-01", "2020-03-01")
    t1 = _dates("2020-01-21", "2020-02-21", "2020-03-21")
    w = uniqueness_weights(t0, t1)
    # Master calendar = {2020-01-01, 02-01, 03-01}. Label 1 covers only Jan 1,
    # label 2 only Feb 1, label 3 only Mar 1 — no overlap → all c=1 → w=1.
    np.testing.assert_allclose(w, np.array([1.0, 1.0, 1.0]), atol=1e-12)


# ── Monotonicity: more crowding → lower weight ───────────────────────────────
def test_weight_decreases_as_concurrency_grows():
    """Add labels that fully cover a target label's lifespan; target weight drops."""
    target_t0 = pd.Timestamp("2020-01-15")
    target_t1 = pd.Timestamp("2020-02-05")
    prev_w = None
    for n_neighbors in (0, 1, 3, 7):
        t0 = _dates("2020-01-01", *([str(target_t0.date())] * (n_neighbors + 1)))
        t1 = _dates("2020-01-02", *([str(target_t1.date())] * (n_neighbors + 1)))
        # Index 0 is a non-overlapping decoy (separates "isolated" from "alone").
        # Indices 1..n+1 all share the target's lifespan.
        w = uniqueness_weights(t0, t1)
        # All indices 1..n+1 are identical labels → same weight, pick the first.
        target_w = float(w[1])
        if prev_w is not None:
            assert target_w < prev_w + 1e-12, (
                f"weight must strictly decrease as concurrency grows "
                f"(n_neighbors={n_neighbors}): prev={prev_w}, now={target_w}"
            )
        prev_w = target_w


# ── concurrency_count: structural checks ─────────────────────────────────────
def test_concurrency_count_matches_manual():
    """Hand-compute concurrency on a tiny example."""
    # Three labels:
    #   A: 01-01 → 01-03   (alive on master dates {01-01, 01-02, 01-03})
    #   B: 01-02 → 01-04   (alive on              {01-02, 01-03, 01-04})
    #   C: 01-04 → 01-05   (alive on              {01-04, 01-05})
    # Master = sorted(unique(t0)) = {01-01, 01-02, 01-04}
    t0 = _dates("2020-01-01", "2020-01-02", "2020-01-04")
    t1 = _dates("2020-01-03", "2020-01-04", "2020-01-05")
    cc = concurrency_count(t0, t1)
    # On 01-01: only A → 1
    # On 01-02: A and B → 2
    # On 01-04: B and C → 2
    expected = pd.Series([1, 2, 2], index=_dates("2020-01-01", "2020-01-02", "2020-01-04"))
    pd.testing.assert_series_equal(cc.astype(int), expected.astype(int), check_names=False)


def test_concurrency_count_validates_input():
    with pytest.raises(ValueError):
        concurrency_count(_dates("2020-01-01"), _dates("2020-01-01", "2020-01-02"))
    with pytest.raises(ValueError):
        # t1 < t0
        concurrency_count(_dates("2020-01-05"), _dates("2020-01-01"))


# ── Integration with triple_barrier_events: t1_offset gives accurate weights ─
def test_integration_with_triple_barrier_events():
    """
    End-to-end: build labels for two synthetic stocks on a shared calendar,
    derive per-label t1 dates, compute weights. Verify the weights vector has
    correct shape, sits in (0, 1], and that labels with shorter t1_offset
    (early barrier touches) tend to have higher uniqueness than full-horizon
    timeouts inside the same crowded window.
    """
    horizon = 20
    cal = pd.date_range("2020-01-01", periods=120, freq="B")
    rng = np.random.default_rng(5)

    # Two synthetic price paths sharing the same calendar.
    px_a = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.012, 120)), index=cal)
    px_b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.012, 120)), index=cal)

    events_a = triple_barrier_events(px_a, horizon=horizon)
    events_b = triple_barrier_events(px_b, horizon=horizon)

    def to_t0_t1(events: pd.DataFrame, cal: pd.DatetimeIndex):
        valid = events.dropna()
        t0 = valid.index
        # t1 date = the bar `t1_offset` positions ahead in the calendar
        positions = cal.get_indexer(t0)
        t1 = cal[positions + valid["t1_offset"].astype(int).to_numpy()]
        return t0, t1, valid["t1_offset"].to_numpy()

    t0_a, t1_a, off_a = to_t0_t1(events_a, cal)
    t0_b, t1_b, off_b = to_t0_t1(events_b, cal)

    t0 = t0_a.append(t0_b)
    t1 = t1_a.append(t1_b)
    offsets = np.concatenate([off_a, off_b])

    w = uniqueness_weights(t0, t1)

    assert w.shape == (len(t0),)
    assert (w > 0).all() and (w <= 1.0).all(), \
        f"weights out of (0,1]: min={w.min()}, max={w.max()}"

    # Sanity: shorter t1_offset → higher mean weight (less overlap to dilute).
    short_mask = offsets <= horizon // 3
    long_mask = offsets == horizon
    if short_mask.any() and long_mask.any():
        assert w[short_mask].mean() > w[long_mask].mean(), (
            "labels resolving early should on average be more unique than "
            f"full-horizon timeouts; short-mean={w[short_mask].mean()}, "
            f"long-mean={w[long_mask].mean()}"
        )


# ── Defence: input validation ────────────────────────────────────────────────
def test_uniqueness_input_validation():
    with pytest.raises(ValueError):
        uniqueness_weights(_dates("2020-01-01"), _dates("2020-01-01", "2020-01-02"))
    with pytest.raises(ValueError):
        uniqueness_weights(_dates("2020-01-05"), _dates("2020-01-01"))
