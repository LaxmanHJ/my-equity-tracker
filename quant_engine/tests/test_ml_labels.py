"""
Assertion tests for the triple-barrier labeller (quant_engine/ml/labels.py).

These validate the *ML output* (the label series) deterministically, with no
DB dependency — part of the P1 testing discipline: every ML logic change ships
with assertions on its output. See wiki/concepts/ml_audit_2026_05_21.md.
"""
import numpy as np
import pandas as pd
import pytest

from quant_engine.ml.labels import (
    DEFAULT_HORIZON,
    daily_vol,
    triple_barrier_labels,
)


def _series(prices) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=len(prices), freq="B")
    return pd.Series(np.asarray(prices, dtype=float), index=idx)


def _geometric(n, step, start=100.0):
    return _series([start * (1.0 + step) ** t for t in range(n)])


# ── Output domain & shape ────────────────────────────────────────────────────
def test_labels_domain_and_index_alignment():
    rng = np.random.default_rng(0)
    px = _series(100 * np.cumprod(1 + rng.normal(0, 0.01, 200)))
    lab = triple_barrier_labels(px, horizon=20)

    assert lab.index.equals(px.index), "label index must match input index"
    valid = lab.dropna()
    assert set(np.unique(valid.to_numpy())).issubset({-1.0, 0.0, 1.0}), \
        "every non-NaN label must be in {-1, 0, 1}"


def test_trailing_tail_is_nan():
    """The last `horizon` bars have no full forward window → must be NaN,
    matching the old forward_return.notna() drop behaviour."""
    horizon = 20
    px = _geometric(120, 0.001)
    lab = triple_barrier_labels(px, horizon=horizon, cost=0.0)
    assert lab.iloc[-horizon:].isna().all(), "trailing horizon bars must be NaN"
    assert lab.iloc[-(horizon + 1)] == lab.iloc[-(horizon + 1)], \
        "the bar just before the tail must have a (non-NaN) label"


def test_warmup_is_nan():
    """Bars before σ_t is defined (min_periods returns) must be NaN."""
    px = _geometric(120, 0.001)
    lab = triple_barrier_labels(px, horizon=20, vol_min_periods=10)
    # First few bars have undefined EWM std → NaN width → NaN label.
    assert lab.iloc[0] != lab.iloc[0] or np.isnan(lab.iloc[0]), \
        "first bar must be NaN (vol undefined)"


# ── Directional certainty on monotone paths ──────────────────────────────────
def test_strictly_rising_path_labels_buy():
    """A path that only rises must hit the upper barrier first → all +1."""
    px = _geometric(120, 0.02)  # +2%/bar compounding → always crosses up
    lab = triple_barrier_labels(px, horizon=20, vol_mult=2.5, cost=0.005)
    valid = lab.dropna()
    assert (valid == 1.0).all(), "monotone-up path must label every bar BUY (+1)"


def test_strictly_falling_path_labels_sell():
    """A path that only falls must hit the lower barrier first → all -1."""
    px = _geometric(120, -0.02)
    lab = triple_barrier_labels(px, horizon=20, vol_mult=2.5, cost=0.005)
    valid = lab.dropna()
    assert (valid == -1.0).all(), "monotone-down path must label every bar SELL (-1)"


def test_hold_appears_on_bounded_oscillation():
    """A low-amplitude oscillation that never clears the barrier yields HOLDs."""
    # Tiny zig-zag: cumulative move stays well inside vol_mult·σ.
    moves = np.array([0.001, -0.001] * 80)
    px = _series(100 * np.cumprod(1 + moves))
    lab = triple_barrier_labels(px, horizon=20, vol_mult=3.0, cost=0.0)
    valid = lab.dropna()
    assert (valid == 0.0).any(), "bounded oscillation must produce some HOLD (0) labels"


# ── Volatility scaling: wider barriers → fewer touches ───────────────────────
def test_higher_vol_mult_reduces_touches():
    rng = np.random.default_rng(7)
    px = _series(100 * np.cumprod(1 + rng.normal(0.0003, 0.012, 300)))
    counts = []
    for k in (1.0, 2.0, 3.0, 4.0):
        lab = triple_barrier_labels(px, horizon=20, vol_mult=k, cost=0.0).dropna()
        counts.append(int((lab != 0).sum()))
    assert counts == sorted(counts, reverse=True), \
        f"nonzero-label count must be non-increasing in vol_mult, got {counts}"


# ── Cost asymmetry: costs make BUYs rarer and SELLs commoner ──────────────────
def test_cost_makes_buys_rarer_and_sells_commoner():
    rng = np.random.default_rng(11)
    px = _series(100 * np.cumprod(1 + rng.normal(0.0, 0.012, 300)))
    buys, sells = [], []
    for c in (0.0, 0.005, 0.01, 0.02):
        lab = triple_barrier_labels(px, horizon=20, vol_mult=1.0, cost=c).dropna()
        buys.append(int((lab == 1.0).sum()))
        sells.append(int((lab == -1.0).sum()))
    assert buys == sorted(buys, reverse=True), \
        f"BUY count must be non-increasing as cost rises, got {buys}"
    assert sells == sorted(sells), \
        f"SELL count must be non-decreasing as cost rises, got {sells}"


# ── No look-ahead: label[i] independent of prices beyond i+horizon ────────────
def test_no_lookahead_beyond_horizon():
    rng = np.random.default_rng(3)
    base = 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, 200))
    px = _series(base)
    horizon = 20
    lab_a = triple_barrier_labels(px, horizon=horizon, cost=0.005)

    # Mutate a far-future bar; every label at i with i+horizon < mutate_at
    # must be unchanged (forward path + causal vol only).
    mutate_at = 150
    perturbed = base.copy()
    perturbed[mutate_at:] *= 1.5
    lab_b = triple_barrier_labels(_series(perturbed), horizon=horizon, cost=0.005)

    safe = mutate_at - horizon  # labels strictly before this can't see the mutation
    a = lab_a.iloc[:safe].to_numpy()
    b = lab_b.iloc[:safe].to_numpy()
    both = ~np.isnan(a) & ~np.isnan(b)
    assert np.array_equal(a[both], b[both]), \
        "labels before (mutate_at − horizon) must not change when future prices change"


# ── daily_vol causality / shape ──────────────────────────────────────────────
def test_daily_vol_is_causal_and_nonnegative():
    px = _geometric(60, 0.001)
    vol = daily_vol(px)
    assert vol.index.equals(px.index)
    valid = vol.dropna()
    assert (valid >= 0).all(), "volatility must be non-negative"


# ── Input validation ─────────────────────────────────────────────────────────
def test_invalid_args_raise():
    px = _geometric(40, 0.001)
    with pytest.raises(ValueError):
        triple_barrier_labels(px, horizon=0)
    with pytest.raises(ValueError):
        triple_barrier_labels(px, cost=-0.01)


def test_default_horizon_matches_label_horizon():
    """Guard against trainer/diagnostic label-horizon drift."""
    assert DEFAULT_HORIZON == 20
