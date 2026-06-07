"""
Assertion tests for P2-b — Deflated Sharpe Ratio (DSR) and Probability of
Backtest Overfitting (PBO) in ``quant_engine/ml/validation.py``.

These statistics are the gate AFML recommends for putting live capital
on any backtested strategy. If they're wrong, every "should we ship?"
decision based on them is wrong.

Coverage strategy: most properties are *closed-form invariants* the
estimators must satisfy. We synthesize returns matrices and trial
Sharpe distributions where the right answer is known analytically,
then assert the function returns it (within tolerance).

No DB, no model — pure NumPy + SciPy.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from quant_engine.ml.validation import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
)


# ── Expected max Sharpe ──────────────────────────────────────────────────────
def test_expected_max_sharpe_is_zero_for_single_trial():
    """One trial — no selection bias to correct for."""
    assert expected_max_sharpe(n_trials=1, trial_sharpe_variance=0.25) == 0.0


def test_expected_max_sharpe_grows_with_more_trials():
    """The more strategies you test, the higher the expected-max under H₀."""
    v = 0.25
    e10 = expected_max_sharpe(10, v)
    e100 = expected_max_sharpe(100, v)
    e1000 = expected_max_sharpe(1000, v)
    assert e10 < e100 < e1000, (
        f"expected_max_sharpe must be monotone-increasing in n_trials, "
        f"got {e10:.3f} → {e100:.3f} → {e1000:.3f}"
    )


def test_expected_max_sharpe_scales_with_sqrt_variance():
    """Doubling the variance of trial Sharpes should ≈√2 the expected max."""
    e_v1 = expected_max_sharpe(100, 1.0)
    e_v4 = expected_max_sharpe(100, 4.0)
    ratio = e_v4 / e_v1
    assert 1.95 < ratio < 2.05, (
        f"expected_max with 4× variance should be 2× larger (√4 scaling), "
        f"got ratio={ratio:.3f}"
    )


def test_expected_max_sharpe_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="n_trials must be"):
        expected_max_sharpe(0, 0.25)
    with pytest.raises(ValueError, match="trial_sharpe_variance"):
        expected_max_sharpe(100, -0.1)


# ── Probabilistic Sharpe Ratio ───────────────────────────────────────────────
def test_psr_equals_one_half_when_observed_equals_benchmark():
    """At SR_obs = SR_benchmark, PSR is exactly 0.5 by symmetry of Φ(0)."""
    psr = probabilistic_sharpe_ratio(observed_sharpe=0.5, n_samples=100, benchmark_sharpe=0.5)
    assert abs(psr - 0.5) < 1e-9, f"PSR at SR=SR_b must be 0.5, got {psr}"


def test_psr_monotone_increasing_in_observed_sharpe():
    """Higher SR_obs ⇒ higher PSR (more confidence true SR > benchmark)."""
    psr_low = probabilistic_sharpe_ratio(0.1, 100, benchmark_sharpe=0.0)
    psr_mid = probabilistic_sharpe_ratio(0.5, 100, benchmark_sharpe=0.0)
    psr_high = probabilistic_sharpe_ratio(1.0, 100, benchmark_sharpe=0.0)
    assert psr_low < psr_mid < psr_high


def test_psr_monotone_increasing_in_sample_size():
    """For fixed SR_obs > SR_b, larger n ⇒ tighter SE ⇒ higher PSR."""
    p100 = probabilistic_sharpe_ratio(0.5, 100, benchmark_sharpe=0.0)
    p1000 = probabilistic_sharpe_ratio(0.5, 1000, benchmark_sharpe=0.0)
    assert p100 < p1000, (
        f"PSR must increase with n at fixed positive SR_obs: "
        f"got n=100→{p100:.4f}, n=1000→{p1000:.4f}"
    )


def test_psr_in_unit_interval():
    """PSR is a probability — must always live in [0, 1]."""
    for sr in [-2.0, -0.5, 0.0, 0.5, 2.0, 5.0]:
        for n in [10, 100, 1000]:
            psr = probabilistic_sharpe_ratio(sr, n)
            assert 0.0 <= psr <= 1.0, f"PSR={psr} out of [0,1] at SR={sr}, n={n}"


def test_psr_rejects_too_few_samples():
    with pytest.raises(ValueError, match="n_samples must be"):
        probabilistic_sharpe_ratio(0.5, n_samples=1)


# ── Deflated Sharpe Ratio ────────────────────────────────────────────────────
def test_dsr_equals_psr_at_benchmark_zero_when_one_trial_zero_variance():
    """Single trial with zero variance → benchmark = 0 → DSR collapses to PSR(0)."""
    sr_obs = 0.8
    n = 250
    dsr = deflated_sharpe_ratio(sr_obs, trial_sharpes=[sr_obs], n_samples=n)
    psr = probabilistic_sharpe_ratio(sr_obs, n_samples=n, benchmark_sharpe=0.0)
    assert abs(dsr - psr) < 1e-9, (
        f"DSR with single trial + zero variance must equal PSR(0), "
        f"got DSR={dsr:.6f}, PSR={psr:.6f}"
    )


def test_dsr_decreases_with_more_trials_at_same_sharpe():
    """Holding SR_obs and trial-variance fixed, more trials → higher
    benchmark → smaller DSR. This is the headline 'selection-bias haircut'."""
    sr_obs = 0.6
    n = 500
    rng = np.random.default_rng(0)
    # Synthesize trial Sharpes around SR_obs with fixed variance.
    trial_sigma = 0.3
    trials_10 = rng.normal(sr_obs, trial_sigma, 10).tolist()
    trials_100 = rng.normal(sr_obs, trial_sigma, 100).tolist()
    trials_1000 = rng.normal(sr_obs, trial_sigma, 1000).tolist()
    d10 = deflated_sharpe_ratio(sr_obs, trials_10, n_samples=n)
    d100 = deflated_sharpe_ratio(sr_obs, trials_100, n_samples=n)
    d1000 = deflated_sharpe_ratio(sr_obs, trials_1000, n_samples=n)
    assert d10 > d100 > d1000, (
        f"DSR must decrease as N_trials grows at fixed SR_obs: "
        f"got 10→{d10:.4f}, 100→{d100:.4f}, 1000→{d1000:.4f}"
    )


def test_dsr_increases_with_higher_observed_sharpe():
    """Stronger evidence (higher SR_obs) ⇒ higher DSR at fixed trial set."""
    rng = np.random.default_rng(1)
    trials = rng.normal(0.4, 0.2, 50).tolist()
    n = 500
    d_low = deflated_sharpe_ratio(0.3, trials, n_samples=n)
    d_mid = deflated_sharpe_ratio(0.8, trials, n_samples=n)
    d_high = deflated_sharpe_ratio(1.5, trials, n_samples=n)
    assert d_low < d_mid < d_high


def test_dsr_in_unit_interval():
    rng = np.random.default_rng(2)
    trials = rng.normal(0.5, 0.4, 100).tolist()
    for sr in [-1.0, 0.0, 0.5, 1.5, 3.0]:
        d = deflated_sharpe_ratio(sr, trials, n_samples=300)
        assert 0.0 <= d <= 1.0, f"DSR={d} out of [0,1] at SR={sr}"


def test_dsr_rejects_empty_trial_distribution():
    with pytest.raises(ValueError, match="trial_sharpes"):
        deflated_sharpe_ratio(0.5, trial_sharpes=[], n_samples=100)


def test_dsr_handles_nan_in_trials():
    """NaNs in the trial distribution must be silently filtered, not propagate."""
    trials = [0.1, 0.5, np.nan, 0.3, np.nan, 0.8]
    d = deflated_sharpe_ratio(0.7, trials, n_samples=200)
    assert 0.0 <= d <= 1.0
    assert not math.isnan(d), "DSR must not return NaN when finite trials exist"


# ── Probability of Backtest Overfitting ──────────────────────────────────────
def test_pbo_zero_for_one_dominantly_skilled_strategy():
    """If one column has a clear edge and others are random noise, the
    IS-best strategy is consistently the same one, and its OOS rank is
    consistently top → PBO ≈ 0."""
    rng = np.random.default_rng(0)
    n_periods, n_strategies = 256, 6
    R = rng.normal(0, 1, (n_periods, n_strategies))
    R[:, 0] += 0.3  # strategy 0 has a clear +0.3σ alpha
    pbo = probability_of_backtest_overfitting(R, n_slices=16)
    assert pbo < 0.10, (
        f"PBO must be small (<0.10) when one strategy clearly dominates, "
        f"got pbo={pbo:.3f}"
    )


def test_pbo_near_one_half_for_pure_noise_averaged_across_seeds():
    """If every strategy is noise, the IS-best is random → OOS rank is
    uniformly distributed → PBO ≈ 0.5 *in expectation*.

    A single PBO realization on one returns matrix is noisy because the
    C(16,8)=12,870 combinations are heavily dependent (they share slices),
    so we average across 8 independent matrices and assert the mean is
    near 0.5 — the population property the estimator targets."""
    n_periods, n_strategies = 256, 12
    pbos = []
    for seed in range(8):
        rng = np.random.default_rng(seed)
        R = rng.normal(0, 1, (n_periods, n_strategies))
        pbos.append(probability_of_backtest_overfitting(R, n_slices=16))
    mean_pbo = float(np.mean(pbos))
    assert 0.35 <= mean_pbo <= 0.65, (
        f"mean PBO across 8 noise matrices should be near 0.5, got {mean_pbo:.3f} "
        f"(individuals: {[round(p, 2) for p in pbos]})"
    )


def test_pbo_in_unit_interval():
    rng = np.random.default_rng(99)
    R = rng.normal(0, 1, (200, 5))
    pbo = probability_of_backtest_overfitting(R, n_slices=10)
    assert 0.0 <= pbo <= 1.0


def test_pbo_rejects_odd_n_slices():
    rng = np.random.default_rng(0)
    R = rng.normal(0, 1, (200, 5))
    with pytest.raises(ValueError, match="even integer"):
        probability_of_backtest_overfitting(R, n_slices=7)


def test_pbo_rejects_too_few_strategies():
    rng = np.random.default_rng(0)
    R = rng.normal(0, 1, (200, 1))
    with pytest.raises(ValueError, match="<2 strategies"):
        probability_of_backtest_overfitting(R, n_slices=8)


def test_pbo_diagnostics_payload_has_expected_shape():
    rng = np.random.default_rng(7)
    R = rng.normal(0, 1, (128, 4))
    out = probability_of_backtest_overfitting(R, n_slices=8, return_diagnostics=True)
    assert isinstance(out, dict)
    assert "pbo" in out and "n_combinations" in out and "logit_ranks" in out
    assert out["n_combinations"] == math.comb(8, 4) == 70
    assert len(out["logit_ranks"]) == out["n_combinations"]
    assert 0.0 <= out["pbo"] <= 1.0


# ── End-to-end story: CPCV → trial-Sharpe distribution → DSR ────────────────
def test_end_to_end_dsr_workflow_with_synthetic_cpcv_distribution():
    """
    Realistic usage: CPCV (P2-a) gives us 45 trial Sharpes from C(10,2)
    combinations. DSR (P2-b) consumes that distribution and tells us
    whether the headline Sharpe is past the selection-bias floor.

    Hard-coded story: observed SR=0.5 on 504 daily returns (~2 years),
    trial distribution centred at 0.3 with sigma 0.2. The headline Sharpe
    is 1 sigma above the trial mean — DSR should be high but not 1.0.
    """
    rng = np.random.default_rng(123)
    trial_sharpes = rng.normal(0.3, 0.2, 45).tolist()
    dsr = deflated_sharpe_ratio(
        observed_sharpe=0.5,
        trial_sharpes=trial_sharpes,
        n_samples=504,
    )
    assert 0.5 <= dsr <= 0.99, (
        f"End-to-end DSR for a 0.5 Sharpe vs N(0.3, 0.2) trials should land "
        f"in [0.5, 0.99], got {dsr:.3f}"
    )
