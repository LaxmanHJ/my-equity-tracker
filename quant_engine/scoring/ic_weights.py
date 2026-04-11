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

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from quant_engine.config import FACTOR_WEIGHTS

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


def _build_factor_panel(lookback: int, horizon: int) -> pd.DataFrame:
    """
    Load price history for every portfolio stock, compute all 7 rolling factor
    scores, attach the N-day forward return, and return a stacked panel.

    The panel has columns: date, symbol, <factor_names>, fwd_ret
    Only rows where fwd_ret is available (i.e. not the last `horizon` bars)
    are kept.  The last `lookback` rows per stock are retained.
    """
    # Local imports to avoid circular deps at module load time
    from quant_engine.data.loader import load_price_history, load_all_symbols, load_benchmark
    from quant_engine.strategies.sicilian_strategy import SicilianStrategy as _S

    symbols      = load_all_symbols()
    benchmark_df = load_benchmark()
    factor_names = list(FACTOR_WEIGHTS.keys())

    frames = []
    for symbol in symbols:
        df = load_price_history(symbol, limit=lookback + horizon + 30)
        if len(df) < IC_LOOKBACK // 4:   # need at least ~63 bars
            continue

        close  = df["close"]
        vol    = df["volume"]

        scores = pd.DataFrame({
            "momentum":          _S._rolling_momentum_score(close),
            "bollinger":         _S._rolling_bollinger_score(close),
            "rsi":               _S._rolling_rsi_score(close),
            "macd":              _S._rolling_macd_score(close),
            "volatility":        _S._rolling_volatility_score(close),
            "volume":            _S._rolling_volume_score(close, vol),
            "relative_strength": _S._rolling_relative_strength_score(close, benchmark_df),
        }, index=df.index)

        scores["fwd_ret"] = df["close"].shift(-horizon) / df["close"] - 1
        scores["symbol"]  = symbol

        # Keep settled rows only (fwd_ret is NaN for the last `horizon` bars)
        scores = scores.dropna(subset=["fwd_ret"]).tail(lookback)
        if len(scores) < 30:
            continue

        frames.append(scores.reset_index())

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

    Raises nothing — caller should handle exceptions and fall back.
    """
    panel = _build_factor_panel(lookback, horizon)

    if panel.empty:
        logger.warning("IC weights: empty panel — returning static weights")
        _cache["ic_values"] = {k: None for k in FACTOR_WEIGHTS}
        _cache["method"]    = "static_fallback"
        return dict(FACTOR_WEIGHTS)

    factor_names = list(FACTOR_WEIGHTS.keys())
    raw_ic: dict[str, float] = {}

    for name in factor_names:
        mean_ic, n_obs = _ic_from_panel(panel, name)
        raw_ic[name]   = mean_ic if n_obs >= MIN_IC_OBS else 0.0
        logger.debug("Factor %s: IC=%.4f (%d obs)", name, mean_ic, n_obs)

    _cache["ic_values"] = raw_ic

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

    # Normalise first, then cap per-factor at IC_MAX_W, then re-normalise.
    # Applying the cap post-normalisation prevents a single dominant factor
    # from absorbing all weight when most others are zeroed.
    weights = {k: v / total for k, v in clipped.items()}
    weights = {k: min(v, IC_MAX_W) for k, v in weights.items()}
    total2  = sum(weights.values())
    weights = {k: round(v / total2, 4) for k, v in weights.items()}

    _cache["method"] = "ic_weighted"
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
