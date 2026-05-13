"""
Rolling IC-Weighted Factor Weights.

Replaces the static FACTOR_WEIGHTS dict with weights derived from how well
each factor actually predicted 20-day forward returns over the last 252 trading
days, computed across all portfolio stocks.

Algorithm
---------
For each factor f:
  1. Collect (factor_score_t, fwd_return_{t+20}) pairs across all stocks
     and all dates in the lookback window.
  2. Compute cross-sectional Spearman rank IC per date, average across dates.
  3. Apply noise floor: IC below IC_FLOOR → treat as zero.
  4. Long-only: negative IC → zero weight.
  5. Cap at IC_MAX_W to prevent concentration.
  6. Normalise so all weights sum to 1.0.

Fallback: if all ICs are below the floor (or computation fails), returns the
static FACTOR_WEIGHTS from config.py unchanged.

The result is cached for 24 hours so it is computed at most once per server
restart cycle.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from quant_engine.config import FACTOR_WEIGHTS, IC_ADAPTIVE_FACTORS

logger = logging.getLogger(__name__)


def _safe_spearmanr(a, b):
    """Compute Spearman correlation, returning NaN if either array is constant."""
    if len(a) < 2 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    return spearmanr(a, b)[0]

# ── Tunable parameters ────────────────────────────────────────────────────────
IC_LOOKBACK = 252   # trailing trading days used for IC estimation
IC_HORIZON  = 20    # forward return horizon (must match ML training horizon)
IC_FLOOR    = 0.02  # below this magnitude → noise → zero weight
IC_MAX_W    = 0.40  # cap: no single factor receives more than 40% weight
MIN_IC_OBS  = 20    # minimum number of date-level IC observations to trust estimate
MIN_CROSS_N = 5     # minimum stocks per date for a valid cross-sectional IC

_CACHE_TTL  = 24 * 3600  # seconds — recompute once per day

# ── Module-level cache ────────────────────────────────────────────────────────
_cache: dict = {
    "weights":     None,   # computed weight dict or None
    "ic_values":   None,   # raw IC per factor (before constraints)
    "method":      None,   # "ic_weighted" | "static_fallback"
    "computed_at": 0.0,
    "lock":        threading.Lock(),
}


def _panel_row(
    symbol: str,
    benchmark_df: pd.DataFrame,
    lookback: int,
    horizon: int,
) -> Optional[pd.DataFrame]:
    """
    Per-symbol worker for _build_factor_panel.

    Fetches price history + computes the seven rolling price-factor scores,
    left-joins per-day sentiment from sentiment_daily as an eighth factor,
    and attaches the N-day forward return. Returns one stacked frame or None
    if the symbol has insufficient history to contribute. Runs inside a
    ThreadPoolExecutor worker — must be self-contained and tolerate identical
    concurrent calls on other symbols.

    Sentiment join behaviour
    ------------------------
    Most (symbol, date) cells have no sentiment_daily row yet. Those become
    NaN in the panel; `_ic_from_panel` does `dropna()` per date so they're
    skipped cleanly. Until enough valid (date, ≥MIN_CROSS_N stocks)
    observations accumulate, the sentiment IC falls below MIN_IC_OBS and the
    factor receives 0 weight — the same gating the wiki Phase 3 plan
    described, now expressed through the IC engine.
    """
    from quant_engine.data.loader import load_price_history
    from quant_engine.strategies.sicilian_strategy import SicilianStrategy as _S

    df = load_price_history(symbol, limit=lookback + horizon + 30)
    if len(df) < IC_LOOKBACK // 4:   # need at least ~63 bars
        return None

    close = df["close"]
    vol   = df["volume"]

    scores = pd.DataFrame({
        "momentum":          _S._rolling_momentum_score(close),
        "bollinger":         _S._rolling_bollinger_score(close),
        "rsi":               _S._rolling_rsi_score(close),
        "macd":              _S._rolling_macd_score(close),
        "volatility":        _S._rolling_volatility_score(close),
        "volume":            _S._rolling_volume_score(close, vol),
        "relative_strength": _S._rolling_relative_strength_score(close, benchmark_df),
    }, index=df.index)

    # Sentiment join — separate try block so a missing/empty sentiment_daily
    # table doesn't kill the panel row for this symbol; sentiment column
    # simply stays NaN and the IC engine assigns 0 weight on the spot.
    try:
        from quant_engine.sentiment.features import load_sentiment_series
        sent_df = load_sentiment_series(symbol, days_back=lookback + horizon + 30)
        if not sent_df.empty:
            sent_series = sent_df.set_index("date")["sent_score"].astype(float)
            # Normalise both indexes to midnight so a price index of
            # 2026-05-12 09:15 aligns with sentiment_daily.date='2026-05-12'.
            sent_series.index = pd.to_datetime(sent_series.index).normalize()
            price_idx = pd.to_datetime(scores.index).normalize()
            scores["sentiment"] = sent_series.reindex(price_idx).values
        else:
            scores["sentiment"] = float("nan")
    except Exception as exc:  # noqa: BLE001 — soft factor; never block panel build
        logger.debug("sentiment join failed for %s: %s", symbol, exc)
        scores["sentiment"] = float("nan")

    scores["fwd_ret"] = df["close"].shift(-horizon) / df["close"] - 1
    scores["symbol"]  = symbol

    scores = scores.dropna(subset=["fwd_ret"]).tail(lookback)
    if len(scores) < 30:
        return None

    return scores.reset_index()


def _build_factor_panel(lookback: int, horizon: int) -> pd.DataFrame:
    """
    Load price history for every portfolio stock, compute the rolling
    price-factor scores, attach a per-day sentiment column from
    sentiment_daily, and the N-day forward return — returns a stacked panel.

    The panel has columns: date, symbol, <factor_names>, sentiment, fwd_ret
    Only rows where fwd_ret is available (i.e. not the last `horizon` bars)
    are kept.  The last `lookback` rows per stock are retained.

    Per-symbol fetches run in a ThreadPoolExecutor — the work is dominated by
    Turso HTTP round-trips, and the GIL releases during `requests.post`.
    """
    from quant_engine.data.loader import load_all_symbols, load_benchmark

    symbols      = load_all_symbols()
    benchmark_df = load_benchmark()   # loaded once, shared read-only across workers

    frames: list[pd.DataFrame] = []
    if not symbols:
        return pd.DataFrame()

    max_workers = min(len(symbols), 10)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_panel_row, sym, benchmark_df, lookback, horizon)
            for sym in symbols
        ]
        for fut in as_completed(futures):
            try:
                frame = fut.result()
            except Exception as exc:
                logger.warning("IC panel row failed: %s", exc)
                continue
            if frame is not None:
                frames.append(frame)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def _ic_from_panel(panel: pd.DataFrame, factor: str) -> tuple[float, int]:
    """
    Compute mean cross-sectional Spearman IC for one factor.

    Returns (mean_ic, n_date_observations).
    """
    ics = []
    for _, grp in panel.groupby("date"):
        valid = grp[[factor, "fwd_ret"]].dropna()
        if len(valid) < MIN_CROSS_N:
            continue
        ic = _safe_spearmanr(valid[factor], valid["fwd_ret"])
        if not np.isnan(ic):
            ics.append(float(ic))

    if not ics:
        return 0.0, 0

    return float(np.mean(ics)), len(ics)


def compute_ic_weights(
    lookback: int = IC_LOOKBACK,
    horizon:  int = IC_HORIZON,
) -> dict:
    """
    Compute IC-weighted factor weights from historical price data.

    Returns a dict with the same keys as FACTOR_WEIGHTS, values normalised
    to sum to 1.0.  Also updates _cache["ic_values"] with raw ICs.

    Factors listed in `IC_ADAPTIVE_FACTORS` are IC-rebalanced within their
    portion of the total weight budget; all others pass through at their
    static `FACTOR_WEIGHTS` value so they aren't silently zeroed by the IC
    computation. Today every factor (including sentiment) is in
    IC_ADAPTIVE_FACTORS; the reserved mechanism is retained for any future
    hard-static factor.

    Raises nothing — caller should handle exceptions and fall back.
    """
    _t0 = time.perf_counter()
    panel = _build_factor_panel(lookback, horizon)
    _t_panel = time.perf_counter()

    # Static pass-through weights for non-IC-adaptive factors (e.g. sentiment).
    # These are reserved from the total budget; IC rebalancing happens only
    # over IC_ADAPTIVE_FACTORS within the remaining (1 - reserved) share.
    reserved = {
        k: FACTOR_WEIGHTS[k]
        for k in FACTOR_WEIGHTS
        if k not in IC_ADAPTIVE_FACTORS
    }
    adaptive_budget = max(0.0, 1.0 - sum(reserved.values()))

    if panel.empty:
        logger.warning("IC weights: empty panel — returning static weights")
        _cache["ic_values"] = {k: None for k in FACTOR_WEIGHTS}
        _cache["method"]    = "static_fallback"
        return dict(FACTOR_WEIGHTS)

    factor_names = list(IC_ADAPTIVE_FACTORS)
    raw_ic: dict[str, float] = {}

    for name in factor_names:
        if name not in panel.columns:
            # Defensive — _panel_row hardcodes the factor list. A new factor
            # added to IC_ADAPTIVE_FACTORS without a corresponding column in
            # the panel builder must surface here, not silently fall to zero.
            logger.warning("IC weights: factor %s missing from panel", name)
            raw_ic[name] = 0.0
            continue
        mean_ic, n_obs = _ic_from_panel(panel, name)
        raw_ic[name]   = mean_ic if n_obs >= MIN_IC_OBS else 0.0
        logger.debug("Factor %s: IC=%.4f (%d obs)", name, mean_ic, n_obs)

    # Record raw IC for diagnostics; reserved factors get None (no IC).
    _cache["ic_values"] = {**raw_ic, **{k: None for k in reserved}}

    # Apply floor and long-only constraint
    clipped = {}
    for name, ic in raw_ic.items():
        if abs(ic) < IC_FLOOR:
            ic = 0.0
        clipped[name] = max(ic, 0.0)          # negative IC → zero weight

    total = sum(clipped.values())
    if total < 1e-9:
        logger.warning("IC weights: all ICs below floor — returning static weights")
        _cache["method"] = "static_fallback"
        return dict(FACTOR_WEIGHTS)

    # Normalise within the adaptive budget, cap per-factor at IC_MAX_W of the
    # adaptive share, then re-normalise. The cap prevents a single dominant
    # factor from absorbing the entire adaptive share when most others zero.
    adaptive_cap = IC_MAX_W * adaptive_budget if adaptive_budget > 0 else IC_MAX_W
    weights = {k: (v / total) * adaptive_budget for k, v in clipped.items()}
    weights = {k: min(v, adaptive_cap) for k, v in weights.items()}
    total2  = sum(weights.values())
    if total2 > 1e-9:
        weights = {k: (v / total2) * adaptive_budget for k, v in weights.items()}

    # Re-attach reserved factors at their static weight, then round.
    weights.update(reserved)
    weights = {k: round(v, 4) for k, v in weights.items()}

    _cache["method"] = "ic_weighted"
    _t_end = time.perf_counter()
    n_symbols = len(panel["symbol"].unique())
    logger.info(
        "compute_ic_weights: %d symbols, panel %.2fs, ic %.2fs, total %.2fs",
        n_symbols, _t_panel - _t0, _t_end - _t_panel, _t_end - _t0,
    )
    logger.info("IC weights (horizon=%dd, lookback=%dd): %s", horizon, lookback, weights)
    return weights


def get_active_weights() -> dict:
    """
    Return the current IC-weighted factor weights, recomputing at most once per day.

    Thread-safe.  Falls back to static FACTOR_WEIGHTS on any error.
    """
    with _cache["lock"]:
        now = time.time()
        stale = (now - _cache["computed_at"]) > _CACHE_TTL

        if _cache["weights"] is None or stale:
            try:
                _cache["weights"]     = compute_ic_weights()
                _cache["computed_at"] = now
            except Exception as exc:
                logger.error("IC weight computation failed: %s — using static weights", exc)
                if _cache["weights"] is None:
                    _cache["weights"] = dict(FACTOR_WEIGHTS)
                    _cache["method"]  = "static_fallback"

        return _cache["weights"]


def get_weight_metadata() -> dict:
    """
    Return the current weights, raw IC values, and method used.
    Suitable for the /api/ic-weights endpoint response.
    """
    weights = get_active_weights()
    return {
        "weights":       weights,
        "static_weights": dict(FACTOR_WEIGHTS),
        "raw_ic":        _cache.get("ic_values"),
        "method":        _cache.get("method", "static_fallback"),
        "computed_at":   _cache.get("computed_at", 0),
        "parameters": {
            "lookback_days": IC_LOOKBACK,
            "horizon_days":  IC_HORIZON,
            "ic_floor":      IC_FLOOR,
            "max_weight":    IC_MAX_W,
        },
    }
