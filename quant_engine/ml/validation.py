"""
Backtest-validity statistics — Deflated Sharpe Ratio (DSR) and
Probability of Backtest Overfitting (PBO).

Both are AFML Ch.14 / López de Prado–Bailey 2014 — the tools that translate
"this strategy showed Sharpe X in the backtest" into "the probability that
its TRUE Sharpe is above the noise floor, AFTER accounting for the strategies
we ALSO tested and rejected."

These two numbers, together with the CPCV distribution from P2-a, are the
*gating* statistics for putting live capital on any model in this codebase.

References
----------
Bailey, D.H. & López de Prado, M.L. (2014).
    "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
    Overfitting and Non-Normality". *Journal of Portfolio Management*.
Bailey, D.H., Borwein, J., López de Prado, M.L., Zhu, Q.J. (2014).
    "The Probability of Backtest Overfitting". *Journal of Computational
    Finance*.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy.stats import norm


__all__ = [
    "expected_max_sharpe",
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "probability_of_backtest_overfitting",
]


# Euler-Mascheroni constant — used in the expected-max-Sharpe approximation.
_EULER_MASCHERONI = 0.5772156649015328


# ── Deflated Sharpe Ratio ───────────────────────────────────────────────────
def expected_max_sharpe(
    n_trials: int,
    trial_sharpe_variance: float,
) -> float:
    """The *null* benchmark for DSR: under H₀ "no skill exists," what's the
    expected maximum Sharpe across ``n_trials`` independent strategy variants
    whose individual Sharpe estimates have variance ``trial_sharpe_variance``?

    Closed-form approximation (López de Prado–Bailey 2014, eq.3):

        E[max SR] ≈ √V[SR] · ((1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e)))

    where γ is Euler-Mascheroni. The two-term form is the standard
    extreme-value approximation for Gaussian maxima.
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be ≥ 1, got {n_trials}")
    if trial_sharpe_variance < 0:
        raise ValueError(
            f"trial_sharpe_variance must be ≥ 0, got {trial_sharpe_variance}"
        )
    if n_trials == 1:
        return 0.0  # only one trial → expected max under null is the null mean = 0
    z1 = norm.ppf(1.0 - 1.0 / n_trials)
    z2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(trial_sharpe_variance) * (
        (1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2
    )


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    n_samples: int,
    benchmark_sharpe: float = 0.0,
    returns_skew: float = 0.0,
    returns_kurtosis: float = 3.0,
) -> float:
    """Probability that the *true* Sharpe exceeds ``benchmark_sharpe``, given
    the observed sample Sharpe and its asymptotic standard error.

    PSR(SR_b) = Φ( (SR_obs - SR_b) · √(n-1) /
                   √(1 - γ₃·SR_obs + (γ₄-1)/4 · SR_obs²) )

    All Sharpes are in the same time units (per-bar or annualized — caller's
    responsibility to keep them consistent).

    ``returns_kurtosis`` is the *raw* fourth standardized moment (= 3 for
    Gaussian), NOT excess kurtosis.
    """
    if n_samples < 2:
        raise ValueError(f"n_samples must be ≥ 2, got {n_samples}")
    sr_var_factor = 1.0 - returns_skew * observed_sharpe + \
        (returns_kurtosis - 1.0) / 4.0 * observed_sharpe ** 2
    if sr_var_factor <= 0:
        # Severe non-normality + large SR can numerically push this negative —
        # standard practice is to clip to a small positive to avoid sqrt(neg).
        sr_var_factor = 1e-12
    z = (observed_sharpe - benchmark_sharpe) * math.sqrt(n_samples - 1) / \
        math.sqrt(sr_var_factor)
    return float(norm.cdf(z))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    trial_sharpes: Sequence[float],
    n_samples: int,
    returns_skew: float = 0.0,
    returns_kurtosis: float = 3.0,
) -> float:
    """Probability that the true Sharpe exceeds the *selection-bias-aware*
    noise floor — i.e. the expected-max-Sharpe under H₀ "no skill" given
    the number of variants tried.

    This is the headline number AFML recommends as the gate for live
    capital: ``DSR > 0.95`` means there is a ≥95% probability that the
    observed Sharpe is real, not the maximum of N coin-flips that happened
    to look skilful.

    The deflation kicks in via TWO channels:
      1. ``trial_sharpe_variance`` = variance of ``trial_sharpes`` — bigger
         dispersion among tried variants → more luck available to be selected.
      2. ``n_trials`` = len(trial_sharpes) — more variants → more chances
         for the best to look skilful by accident.
    """
    trial = np.asarray(list(trial_sharpes), dtype=float)
    trial = trial[~np.isnan(trial)]
    n_trials = len(trial)
    if n_trials < 1:
        raise ValueError("trial_sharpes must contain at least one finite value")
    # Variance of trial Sharpes — sample variance, ddof=1 unless n_trials==1.
    trial_var = float(trial.var(ddof=1)) if n_trials > 1 else 0.0
    sr_benchmark = expected_max_sharpe(n_trials, trial_var)
    return probabilistic_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        n_samples=n_samples,
        benchmark_sharpe=sr_benchmark,
        returns_skew=returns_skew,
        returns_kurtosis=returns_kurtosis,
    )


# ── Probability of Backtest Overfitting (PBO) ───────────────────────────────
def _logit(p: float) -> float:
    eps = 1e-12
    p = min(max(p, eps), 1 - eps)
    return math.log(p / (1.0 - p))


def probability_of_backtest_overfitting(
    returns_matrix: np.ndarray,
    n_slices: int = 16,
    return_diagnostics: bool = False,
) -> float | dict:
    """Bailey-Borwein-López de Prado-Zhu (2014) combinatorial PBO.

    Setup
    -----
    ``returns_matrix`` is shape (n_periods, n_strategies). Each column is the
    per-period return series of one *variant* (different model, threshold,
    horizon, …). The estimator slices time into ``n_slices`` equal blocks,
    enumerates all C(n_slices, n_slices/2) ways of choosing half as IS and
    half as OOS, and for each combination asks:

      "Did the strategy that was BEST in-sample rank below median out-of-sample?"

    PBO is the *fraction* of combinations where the answer is yes. ``n_slices``
    must be even.

    Interpretation
    --------------
    - PBO ≈ 50% → IS-best is a coin-flip OOS → severely overfit.
    - PBO ≈ 0  → IS-best stays best OOS → genuine edge selection.
    - PBO < 10–15% is the practical "ship it" threshold AFML cites.

    If ``return_diagnostics`` is True, returns a dict with the PBO scalar AND
    the per-combination logit ranks (for stochastic-dominance / SD₂ plots).
    """
    R = np.asarray(returns_matrix, dtype=float)
    if R.ndim != 2:
        raise ValueError(f"returns_matrix must be 2-D, got shape {R.shape}")
    n_periods, n_strategies = R.shape
    if n_strategies < 2:
        raise ValueError(
            f"PBO is undefined for <2 strategies, got n_strategies={n_strategies}"
        )
    if n_slices < 2 or n_slices % 2 != 0:
        raise ValueError(f"n_slices must be a positive even integer, got {n_slices}")
    if n_periods < n_slices:
        raise ValueError(
            f"n_periods={n_periods} < n_slices={n_slices}; cannot slice evenly"
        )

    # Partition rows into ``n_slices`` contiguous slices of as-equal-as-possible size.
    slice_boundaries = np.linspace(0, n_periods, n_slices + 1).astype(int)
    slice_indices = [
        np.arange(slice_boundaries[i], slice_boundaries[i + 1])
        for i in range(n_slices)
    ]

    from itertools import combinations
    k = n_slices // 2
    all_slice_ids = list(range(n_slices))

    overfit_events = 0
    total_events = 0
    logit_ranks: list[float] = []

    for is_slice_ids in combinations(all_slice_ids, k):
        is_set = set(is_slice_ids)
        oos_slice_ids = [s for s in all_slice_ids if s not in is_set]
        is_rows = np.concatenate([slice_indices[s] for s in is_slice_ids])
        oos_rows = np.concatenate([slice_indices[s] for s in oos_slice_ids])

        # IS Sharpe per strategy.
        is_ret = R[is_rows]
        oos_ret = R[oos_rows]
        is_mean = is_ret.mean(axis=0)
        is_std = is_ret.std(axis=0, ddof=1)
        oos_mean = oos_ret.mean(axis=0)
        oos_std = oos_ret.std(axis=0, ddof=1)
        # Guard against degenerate-constant strategies.
        is_std = np.where(is_std > 0, is_std, np.nan)
        oos_std = np.where(oos_std > 0, oos_std, np.nan)
        is_sharpe = is_mean / is_std
        oos_sharpe = oos_mean / oos_std

        # Strategy with highest IS Sharpe.
        if np.all(np.isnan(is_sharpe)):
            continue
        n_star = int(np.nanargmax(is_sharpe))

        # OOS rank of n_star — rank 1 is worst, n is best (standard PBO convention).
        # If n_star's OOS Sharpe is NaN we can't rank it; skip.
        if np.isnan(oos_sharpe[n_star]):
            continue
        # Replace NaN with -inf for ranking purposes (worst).
        oos_for_rank = np.where(np.isnan(oos_sharpe), -np.inf, oos_sharpe)
        rank = (oos_for_rank < oos_for_rank[n_star]).sum() + 1
        relative_rank = rank / n_strategies  # in (0, 1]
        logit_ranks.append(_logit(relative_rank))

        # "Overfit event" = IS-best is below OOS median.
        if relative_rank <= 0.5:
            overfit_events += 1
        total_events += 1

    if total_events == 0:
        raise RuntimeError(
            "PBO: no valid combinations produced finite IS-best — every IS "
            "Sharpe was NaN. Check that returns_matrix has variance."
        )
    pbo = overfit_events / total_events

    if return_diagnostics:
        return {
            "pbo": pbo,
            "n_combinations": total_events,
            "logit_ranks": np.array(logit_ranks),
        }
    return pbo
