"""
Combinatorial Purged Cross-Validation (CPCV) — AFML Ch.12.

Walk-forward purged CV gives ONE backtest path. CPCV gives a DISTRIBUTION:
for N contiguous time groups and a test-group size k, it enumerates all
C(N,k) train/test combinations, applies purge + embargo at every group
boundary, and lets the caller assemble:

  • a per-combination distribution of {IC, Sharpe, hit-rate, …}
  • the φ ≡ k/N · C(N,k) unique "backtest paths" obtained by gluing
    consecutive test groups — each path covers every observation
    exactly once and is a complete, non-overlapping backtest history.

This module is the gating dependency for P2-b (Deflated Sharpe Ratio +
PBO): DSR's haircut formula consumes the variance of trial Sharpes,
which only a distribution-aware CV like CPCV can supply honestly.

The split iterator is pure index manipulation — no model, no data,
no DB — so it is fully deterministic and exhaustively tested in
``tests/test_ml_cpcv.py``.

References
----------
De Prado, M.L. (2018). *Advances in Financial Machine Learning*, Ch.12
"Cross-Validation in Finance". Wiley.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Iterator

import numpy as np
import pandas as pd


__all__ = [
    "CPCVSplit",
    "make_time_groups",
    "cpcv_splits",
    "n_cpcv_combinations",
    "n_cpcv_paths",
    "assemble_backtest_paths",
]


# ── Public types ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CPCVSplit:
    """One train/test split emitted by ``cpcv_splits``.

    ``combination_id`` is the 0-indexed enumeration over the C(N,k) combinations
    of test groups. ``test_group_ids`` is the sorted tuple of group indices that
    make up the test fold — useful for the path-assembly step.
    """
    combination_id: int
    test_group_ids: tuple[int, ...]
    train_idx: np.ndarray
    test_idx: np.ndarray


# ── Group construction ──────────────────────────────────────────────────────
def make_time_groups(
    dates: pd.DatetimeIndex | np.ndarray,
    n_groups: int,
) -> np.ndarray:
    """Partition observations into ``n_groups`` contiguous time blocks.

    Returns an int array of length len(dates) assigning each observation to a
    group id in ``[0, n_groups)``. Groups are sized as evenly as possible; if
    the count doesn't divide evenly, the leading groups are one observation
    larger. Group ordering follows the chronological order of ``dates``.

    Rationale
    ---------
    CPCV needs CONTIGUOUS time groups so that "purge + embargo at every
    boundary" is well-defined. We sort observations by date once, then chop
    by quantile of position-in-time. Ties in dates land in the same group,
    which preserves cross-section integrity (no half-of-a-trading-day fold).
    """
    if n_groups < 2:
        raise ValueError(f"n_groups must be ≥ 2, got {n_groups}")
    dates = pd.DatetimeIndex(dates)
    n = len(dates)
    if n < n_groups:
        raise ValueError(
            f"n_obs={n} < n_groups={n_groups}; cannot partition into contiguous "
            f"groups of size ≥ 1"
        )
    order = np.argsort(dates.values, kind="stable")
    group_of_position = np.zeros(n, dtype=np.int64)
    boundaries = np.linspace(0, n, n_groups + 1).astype(int)
    for g, (lo, hi) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        group_of_position[order[lo:hi]] = g
    return group_of_position


# ── Combinatorics helpers ───────────────────────────────────────────────────
def n_cpcv_combinations(n_groups: int, n_test_groups: int) -> int:
    """Number of train/test splits ``cpcv_splits`` will emit = C(N, k)."""
    if not (1 <= n_test_groups < n_groups):
        raise ValueError(
            f"need 1 ≤ n_test_groups < n_groups, got {n_test_groups}/{n_groups}"
        )
    return math.comb(n_groups, n_test_groups)


def n_cpcv_paths(n_groups: int, n_test_groups: int) -> int:
    """Number of unique CPCV backtest paths = k/N · C(N, k).

    Each path is the result of gluing N/k consecutive test groups, where each
    group is contributed by a different combination. Algebraically this equals
    C(N-1, k-1) — every group is in test in exactly C(N-1, k-1) combinations,
    and each contributes to one path.
    """
    n_comb = n_cpcv_combinations(n_groups, n_test_groups)
    return n_comb * n_test_groups // n_groups


# ── The split iterator (the algorithm) ──────────────────────────────────────
def cpcv_splits(
    dates: pd.DatetimeIndex | np.ndarray,
    n_groups: int,
    n_test_groups: int,
    *,
    label_horizon: int,
    embargo_days: int | None = None,
) -> Iterator[CPCVSplit]:
    """Enumerate all C(N, k) CPCV train/test splits with purge + embargo.

    Parameters
    ----------
    dates : ordered DatetimeIndex (length n_obs) — observation timestamps.
    n_groups, n_test_groups : N, k.
    label_horizon : trading days the label looks forward — used as the purge
        window (drop train rows whose label COULD use information from inside
        a test group).
    embargo_days : forward embargo applied AFTER each test group (drop train
        rows whose features encode information from inside the test group).
        Defaults to ``label_horizon``.

    Notes on purge/embargo at multiple boundaries
    ---------------------------------------------
    In walk-forward CV the test is one contiguous block at the end, so purge
    is one-sided (backward) and embargo is the other side (forward). In CPCV
    the test is a UNION of k blocks, possibly scattered. Purge + embargo are
    therefore applied at *every* boundary of *every* test block — for each
    train row we drop it if its date falls within the leak window of ANY
    test group, not just the chronologically-next one.

    Properties guaranteed by this implementation (asserted in tests):
      1. Exactly C(N, k) splits are emitted.
      2. Each observation appears in test in exactly C(N-1, k-1) splits.
      3. train_idx and test_idx are disjoint in every split.
      4. No train row has a date in [test_g_start - horizon, test_g_start)
         for any test group g.
      5. No train row has a date in (test_g_end, test_g_end + embargo] for
         any test group g (CPCV-ready forward embargo).
    """
    if embargo_days is None:
        embargo_days = label_horizon
    if label_horizon < 0 or embargo_days < 0:
        raise ValueError(
            f"label_horizon ({label_horizon}) and embargo_days ({embargo_days}) "
            f"must be ≥ 0"
        )

    dates = pd.DatetimeIndex(dates)
    n_obs = len(dates)
    groups = make_time_groups(dates, n_groups)

    # Per-group calendar bounds — used to compute leak windows for purge/embargo.
    group_start: dict[int, pd.Timestamp] = {}
    group_end: dict[int, pd.Timestamp] = {}
    group_indices: dict[int, np.ndarray] = {}
    for g in range(n_groups):
        mask = groups == g
        idx = np.flatnonzero(mask)
        group_indices[g] = idx
        d = dates[idx]
        group_start[g] = d.min()
        group_end[g] = d.max()

    # Master calendar of unique sorted trading days — used to compute the
    # business-day-offset purge / embargo windows. We rely on it (rather than
    # calendar-day timedelta arithmetic) because label_horizon is measured in
    # TRADING days, not calendar days.
    master_calendar = pd.DatetimeIndex(np.unique(dates.values)).sort_values()

    def _purge_window(test_start: pd.Timestamp) -> pd.Timestamp:
        """Returns the earliest date that may safely appear in train. Train
        rows with date in ``[purge_window, test_start)`` must be DROPPED
        because their forward label would see information inside the test
        group."""
        pos = master_calendar.searchsorted(test_start, side="left")
        purge_pos = max(pos - label_horizon, 0)
        return master_calendar[purge_pos]

    def _embargo_window(test_end: pd.Timestamp) -> pd.Timestamp:
        """Returns the last date contaminated by the test group. Train rows
        with date in ``(test_end, embargo_end]`` must be DROPPED."""
        pos = master_calendar.searchsorted(test_end, side="right") - 1
        emb_pos = min(pos + embargo_days, len(master_calendar) - 1)
        return master_calendar[emb_pos]

    all_idx = np.arange(n_obs)
    for comb_id, test_group_ids in enumerate(
        combinations(range(n_groups), n_test_groups)
    ):
        test_mask = np.zeros(n_obs, dtype=bool)
        train_drop_mask = np.zeros(n_obs, dtype=bool)
        for g in test_group_ids:
            test_mask[group_indices[g]] = True
            purge_lo = _purge_window(group_start[g])
            embargo_hi = _embargo_window(group_end[g])
            # Drop train rows in [purge_lo, group_start[g]) — backward purge.
            train_drop_mask |= (dates >= purge_lo) & (dates < group_start[g])
            # Drop train rows in (group_end[g], embargo_hi] — forward embargo.
            train_drop_mask |= (dates > group_end[g]) & (dates <= embargo_hi)
        train_mask = (~test_mask) & (~train_drop_mask)
        yield CPCVSplit(
            combination_id=comb_id,
            test_group_ids=test_group_ids,
            train_idx=all_idx[train_mask],
            test_idx=all_idx[test_mask],
        )


# ── Path assembly ───────────────────────────────────────────────────────────
def assemble_backtest_paths(
    n_groups: int,
    n_test_groups: int,
    per_combination_test_groups: list[tuple[int, ...]],
    per_combination_predictions: list[dict[int, np.ndarray]],
) -> list[np.ndarray]:
    """Glue per-combination predictions into φ backtest paths.

    Each path is a full-history sequence of predictions, made of N/k consecutive
    test groups where every group is contributed by a different combination.
    Concretely: for group g (∈ 0…N-1), there are C(N-1, k-1) combinations that
    place g in test; we round-robin those combinations across the φ paths so
    that each path receives one prediction per group.

    Parameters
    ----------
    per_combination_test_groups : list of length C(N,k), each entry is the
        sorted tuple of test-group ids for that combination.
    per_combination_predictions : list of length C(N,k); entry c is a dict
        ``{g: pred_array_for_group_g}`` for every g in
        ``per_combination_test_groups[c]``.

    Returns
    -------
    paths : list of length φ. paths[p] is the concatenated prediction array
        for path p in chronological group order (0, 1, …, N-1).

    Failure modes
    -------------
    Asserts that every group g is covered by exactly C(N-1, k-1) predictions
    across the combinations supplied — i.e. the caller has indeed supplied
    predictions for ALL C(N,k) combinations, not a strict subset. If you ran
    only a subset of splits (e.g. early-stopped) this will fail loudly rather
    than silently produce incomplete paths.
    """
    n_paths = n_cpcv_paths(n_groups, n_test_groups)
    expected_per_group = math.comb(n_groups - 1, n_test_groups - 1)

    # Bucket predictions by group: per_group_preds[g] = list of pred arrays
    # contributed by each combination that included g in test, in combination
    # enumeration order.
    per_group_preds: dict[int, list[np.ndarray]] = {g: [] for g in range(n_groups)}
    for test_groups, preds in zip(
        per_combination_test_groups, per_combination_predictions
    ):
        for g in test_groups:
            if g not in preds:
                raise ValueError(
                    f"combination supplied test_groups={test_groups} but "
                    f"predictions dict missing key {g}"
                )
            per_group_preds[g].append(preds[g])

    for g, lst in per_group_preds.items():
        if len(lst) != expected_per_group:
            raise ValueError(
                f"group {g} has {len(lst)} contributions across combinations, "
                f"expected C(N-1,k-1)={expected_per_group}. Did you supply all "
                f"C({n_groups},{n_test_groups})={n_cpcv_combinations(n_groups, n_test_groups)} "
                f"combinations?"
            )

    # Round-robin assemble: path p takes contribution[p] from each group.
    paths: list[np.ndarray] = []
    for p in range(n_paths):
        chunks = [per_group_preds[g][p] for g in range(n_groups)]
        paths.append(np.concatenate(chunks))
    return paths
