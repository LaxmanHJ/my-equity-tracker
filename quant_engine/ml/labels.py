"""
Volatility-scaled, cost-aware triple-barrier labelling (López de Prado AFML Ch.3).

Replaces the fixed ±3% / 20-day return threshold that trainer.py and
diagnostic.py used for the 3-class {SELL, HOLD, BUY} target. That fixed
threshold was heteroscedastic: 3% is "noise" for a low-vol large cap and a
"rare event" for a high-vol small cap, so the label encoded cross-sectional
vol regimes rather than directional skill (see wiki/concepts/ml_audit_2026_05_21.md
→ S3). It also used gross returns, biasing the boundary toward trades that
barely clear gross but die to costs (→ S7).

Triple-barrier fixes both:
  • Upper barrier  +k·σ_t  (profit-taking)  → +1 (BUY)
  • Lower barrier  −k·σ_t  (stop-loss)      → −1 (SELL)
  • Vertical barrier (T bars, no touch)     →  0 (HOLD)

σ_t is a *causal* EWM estimate of daily return volatility (uses only past
returns, so there is no look-ahead in the barrier width). k = `vol_mult`
matches the live stop's `riskLimits.stopLoss.volMultiplier` so training labels
and the live stop speak the same language.

Cost-awareness (P1-b): the realised return used for the barrier test is taken
net of an estimated round-trip cost (`cost`, e.g. 50 bp = STT + brokerage +
slippage). A long's realised P&L is `gross − cost` regardless of direction, so
costs make the upper (take-profit) barrier harder to reach and the lower
(stop) barrier easier — exactly the asymmetry real costs impose.

This module is intentionally pure and DB-independent so its ML output can be
unit-tested deterministically (see tests/test_ml_labels.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Defaults — keep in one place so trainer.py and diagnostic.py label identically.
DEFAULT_HORIZON = 20      # vertical-barrier length in trading bars (matches LABEL_HORIZON_DAYS)
DEFAULT_VOL_MULT = 2.5    # barrier width in units of σ_t (matches live stopLoss.volMultiplier)
DEFAULT_COST = 0.005      # 50 bp round-trip cost (STT + brokerage + slippage proxy)
DEFAULT_VOL_SPAN = 20     # EWM span for the causal daily-vol estimate
DEFAULT_VOL_MIN_PERIODS = 10  # min returns before σ_t is defined (else label = NaN)


def daily_vol(close: pd.Series, span: int = DEFAULT_VOL_SPAN,
              min_periods: int = DEFAULT_VOL_MIN_PERIODS) -> pd.Series:
    """
    Causal EWM estimate of daily return volatility, aligned to `close.index`.

    Uses only past returns (pandas .ewm is causal), so σ_t carries no
    look-ahead. Returns NaN for the warm-up window (< `min_periods` returns).
    """
    close = close.astype(float)
    returns = close.pct_change()
    return returns.ewm(span=span, min_periods=min_periods).std()


def triple_barrier_events(
    close: pd.Series,
    horizon: int = DEFAULT_HORIZON,
    vol_mult: float = DEFAULT_VOL_MULT,
    cost: float = DEFAULT_COST,
    vol_span: int = DEFAULT_VOL_SPAN,
    vol_min_periods: int = DEFAULT_VOL_MIN_PERIODS,
) -> pd.DataFrame:
    """
    Compute triple-barrier events: the label AND the bars-to-resolution.

    Returns a DataFrame aligned to `close.index` with columns:
      • label     — {-1, 0, +1}, or NaN (warm-up / trailing tail)
      • t1_offset — bars from i to the resolving barrier (1 … horizon), or
                    `horizon` on a vertical (timeout) touch; NaN where label is NaN

    `t1_offset` is what makes correct sample-uniqueness weighting possible
    (AFML Ch.4): an early barrier touch overlaps far fewer subsequent labels
    than a full-horizon timeout, so its observation is *more* unique. See
    quant_engine/ml/sample_weights.py.

    Same barrier/vol/cost/no-look-ahead semantics as `triple_barrier_labels`.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be ≥ 1, got {horizon}")
    if cost < 0:
        raise ValueError(f"cost must be ≥ 0, got {cost}")

    idx = close.index
    px = close.astype(float).to_numpy()
    n = len(px)

    sigma = daily_vol(close, span=vol_span, min_periods=vol_min_periods).to_numpy()
    width = np.maximum(vol_mult * sigma, cost)

    labels = np.full(n, np.nan, dtype=float)
    t1_off = np.full(n, np.nan, dtype=float)
    for i in range(n):
        wi = width[i]
        if not np.isfinite(wi) or wi <= 0.0 or i + horizon >= n:
            continue
        p0 = px[i]
        lab = 0
        touched_at = horizon  # default: vertical/timeout at the far edge
        for offset in range(1, horizon + 1):
            net = px[i + offset] / p0 - 1.0 - cost
            if net >= wi:
                lab = 1
                touched_at = offset
                break
            if net <= -wi:
                lab = -1
                touched_at = offset
                break
        labels[i] = lab
        t1_off[i] = touched_at

    return pd.DataFrame({"label": labels, "t1_offset": t1_off}, index=idx)


def triple_barrier_labels(
    close: pd.Series,
    horizon: int = DEFAULT_HORIZON,
    vol_mult: float = DEFAULT_VOL_MULT,
    cost: float = DEFAULT_COST,
    vol_span: int = DEFAULT_VOL_SPAN,
    vol_min_periods: int = DEFAULT_VOL_MIN_PERIODS,
) -> pd.Series:
    """
    Compute vol-scaled, cost-aware triple-barrier labels for one price series.

    For each bar i, the barrier half-width is

        width_i = max(vol_mult · σ_i, cost)

    (clipped at `cost` so the profit target always at least clears round-trip
    cost). Scanning forward s = i+1 … i+horizon, with net return
    `net = close[s]/close[i] − 1 − cost`:

        first s with net ≥ +width_i   → label +1  (upper / BUY)
        first s with net ≤ −width_i   → label −1  (lower / SELL)
        no touch within horizon        → label  0  (vertical / HOLD)

    Returns a float Series aligned to `close.index`. Labels are NaN where:
      • σ_i is undefined (warm-up), or
      • fewer than `horizon` forward bars exist (the trailing tail),
    so callers can drop them with `.notna()` exactly like the old
    `forward_return.notna()` mask.

    No look-ahead: label[i] depends only on close[i … i+horizon] (forward path)
    and past-only σ_i — mutating close beyond i+horizon does not change label[i].

    Thin wrapper over `triple_barrier_events` (the `label` column).
    """
    events = triple_barrier_events(
        close, horizon=horizon, vol_mult=vol_mult, cost=cost,
        vol_span=vol_span, vol_min_periods=vol_min_periods,
    )
    return events["label"].rename("label")
