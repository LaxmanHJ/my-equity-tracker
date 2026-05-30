"""
Sample-uniqueness weights for overlapping financial labels (AFML Ch.4).

The problem: a 20-bar triple-barrier label at bar `i` shares price information
with up to 19 surrounding labels. Treating those overlapping observations as
i.i.d. inflates effective sample size — and with it, every standard error and
every claimed Sharpe. `class_weight="balanced"` rebalances *classes*, not
*concurrency*, so it doesn't fix this. See wiki/concepts/ml_audit_2026_05_21.md
→ S4 for the audit reference.

López de Prado's fix (AFML §4.5–§4.6):
  • For each label observed at time t0_i with resolution time t1_i,
    define the "indicator" 1_{t0_i ≤ d ≤ t1_i} — the calendar days the
    label is "alive".
  • Concurrency at day d is c_d = Σ_i 1_{t0_i ≤ d ≤ t1_i}.
  • Uniqueness u_i = mean over d ∈ [t0_i, t1_i] of (1 / c_d).
  • Sample weight w_i = u_i (or normalized; we leave normalization to the caller
    so RF's class_weight="balanced" composes correctly).

A label resolved early (t1 close to t0) overlaps fewer subsequent labels and
gets a higher weight; a label that times out (t1 = t0 + horizon) sits inside
the maximum overlap and gets a lower weight. This is the precise asymmetry
`triple_barrier_events.t1_offset` enables — without it we'd have to assume
every label fills the full horizon.

The "calendar" used here is the **master set of label start dates** — that is,
every date where ≥1 label was observed (typically the union of trading days
across all symbols and bars in the training set). This matches AFML's
treatment and is what makes cross-symbol concurrency work correctly when the
trainer concatenates 200 stocks into one fit.

This module is pure / DB-independent; assertions on its output live in
quant_engine/tests/test_ml_sample_weights.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _to_pos(dates: pd.DatetimeIndex, master: pd.DatetimeIndex) -> np.ndarray:
    """Map each date to its position in the master calendar (searchsorted)."""
    return master.searchsorted(np.asarray(dates))


def concurrency_count(
    t0_dates: pd.DatetimeIndex,
    t1_dates: pd.DatetimeIndex,
) -> pd.Series:
    """
    Number of labels alive on each master-calendar date.

    A label `i` is "alive" on calendar date d iff `t0_i ≤ d ≤ t1_i`. The master
    calendar is the unique sorted set of `t0_dates` (every date where some
    label was observed). Returns a Series indexed by that master calendar,
    integer-valued counts.

    Args:
        t0_dates: start date of each label (length N)
        t1_dates: resolution date of each label (length N, t1_i ≥ t0_i)

    Returns:
        pd.Series indexed by the master calendar, values = concurrency count.
    """
    if len(t0_dates) != len(t1_dates):
        raise ValueError(
            f"t0_dates ({len(t0_dates)}) and t1_dates ({len(t1_dates)}) must have equal length"
        )
    t0 = pd.DatetimeIndex(t0_dates)
    t1 = pd.DatetimeIndex(t1_dates)
    if (t1 < t0).any():
        raise ValueError("every t1 must be ≥ its corresponding t0")

    master = pd.DatetimeIndex(sorted(set(t0)))
    if len(master) == 0:
        return pd.Series([], index=master, dtype=int, name="concurrency")

    si = _to_pos(t0, master)
    # last master index ≤ t1_i
    ei = master.searchsorted(np.asarray(t1), side="right") - 1
    ei = np.clip(ei, si, len(master) - 1)  # never go past end; never before start

    # Range-add via the straightforward loop. At N=24k labels × horizon=20 days
    # this is well under a second; the diff-array vectorisation isn't worth the
    # edge-case complexity (ei+1 == len(master) is hostile to np.add.at).
    count = np.zeros(len(master), dtype=np.int64)
    for s, e in zip(si, ei):
        count[s:e + 1] += 1

    return pd.Series(count, index=master, name="concurrency")


def uniqueness_weights(
    t0_dates: pd.DatetimeIndex,
    t1_dates: pd.DatetimeIndex,
) -> np.ndarray:
    """
    Per-label uniqueness weight (AFML §4.6).

    u_i = mean over d ∈ master ∩ [t0_i, t1_i] of (1 / c_d), where c_d is the
    concurrency count from `concurrency_count`. Output is a float numpy array
    of length N, with values in (0, 1].

    A label with no temporal company gets weight 1.0; a label that lives
    entirely inside a window where K others are alive gets weight ~1/K.

    These weights are intended to be passed to sklearn `.fit(sample_weight=…)`.
    They are *not* normalized to sum to N — that's left to the caller so that
    composing with `class_weight="balanced"` behaves predictably.
    """
    if len(t0_dates) != len(t1_dates):
        raise ValueError(
            f"t0_dates ({len(t0_dates)}) and t1_dates ({len(t1_dates)}) must have equal length"
        )
    n = len(t0_dates)
    if n == 0:
        return np.array([], dtype=float)

    t0 = pd.DatetimeIndex(t0_dates)
    t1 = pd.DatetimeIndex(t1_dates)
    if (t1 < t0).any():
        raise ValueError("every t1 must be ≥ its corresponding t0")

    counts = concurrency_count(t0, t1)
    master = counts.index
    inv_c = 1.0 / counts.to_numpy().astype(float)

    si = _to_pos(t0, master)
    ei = master.searchsorted(np.asarray(t1), side="right") - 1
    ei = np.clip(ei, si, len(master) - 1)

    weights = np.empty(n, dtype=float)
    for k in range(n):
        seg = inv_c[si[k]:ei[k] + 1]
        # ei ≥ si by construction → seg non-empty; defensive guard just in case.
        weights[k] = float(seg.mean()) if seg.size else 1.0
    return weights
