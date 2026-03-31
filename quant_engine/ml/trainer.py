"""
ML Trainer for The Sicilian Engine.

Generates training data by computing rolling technical sub-scores across all
available stocks, labels each bar with a 20-day forward return class, then
trains a Random Forest classifier.

The model learns non-linear interactions between indicators (e.g. RSI oversold
+ MACD bullish crossover + volume spike is a far stronger signal than the sum
of those parts) and outputs calibrated class probabilities instead of a naive
vote count.

Labels:
    BUY  ( 1): 20-day forward return > +2%
    SELL (-1): 20-day forward return < -2%
    HOLD ( 0): everything between

Features (all on –1 to +1 scale, matching Sicilian sub-score outputs):
    composite_factor, rsi, macd, trend_ma, bollinger,
    volume, volatility, relative_strength
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

from quant_engine.config import ML_MODEL_DIR, INDUSTRY_TO_NSE_INDEX
from quant_engine.data.loader import (
    load_all_symbols, load_benchmark, load_price_history,
    load_industry_map,
)
from quant_engine.data.delivery_loader import load_delivery_series
from quant_engine.data.market_regime_loader import (
    load_vix_series, vix_to_score, build_markov_score_series,
    load_fii_flow_series, load_fii_fo_series, _flow_to_score,
)
from quant_engine.data.sector_indices_loader import load_sector_series
from quant_engine.strategies.sicilian_strategy import SicilianStrategy

logger = logging.getLogger(__name__)

FEATURE_COLS = [
    # composite_factor dropped: it's a noisy re-blend of relative_strength (63d momentum)
    # and bollinger (20d Z-score), so it just dilutes importances of both.
    "rsi",
    "macd",
    "trend_ma",
    "bollinger",
    "volume",
    "volatility",
    "relative_strength",
    # Cross-stock / external
    "sector_rotation",    # avg 20d return of industry peers vs benchmark
    # analyst_consensus removed: static 2026 ratings applied to 2023 bars = look-ahead bias
    # Market regime (same value for every stock on the same date)
    "vix_regime",         # India VIX rolling percentile → [-1 fear, +1 calm]
    "nifty_trend",        # NIFTY position vs SMA50 + SMA200 → [-1 downtrend, +1 uptrend]
    "markov_regime",      # Markov P(Bull) - P(Bear) from 252-day rolling transition matrix
    # NSE delivery data
    "delivery_score",     # rolling z-score of delivery_pct vs 60-day mean, clipped to [-1, +1]
    # FII flow signals (market-wide, same value for every stock on the same date)
    "fii_flow_score",     # 10-day rolling FII net cash, percentile-ranked → [-1 outflow, +1 inflow]
    "fii_fo_score",       # FII net index futures (long-short), percentile-ranked → [-1 short, +1 long]
]

# 20-day forward return thresholds for label creation.
# ±3% (vs original ±2%) widens the HOLD zone so the class isn't
# starved of samples — with ±2% HOLD was only ~12% of the dataset.
BUY_RETURN_THRESHOLD  =  0.03   # > +3%  → BUY
SELL_RETURN_THRESHOLD = -0.03   # < -3%  → SELL

# Minimum bars a stock needs to contribute training samples
MIN_BARS = 120

# Random Forest hyper-parameters (deliberately conservative to avoid overfitting)
RF_PARAMS = {
    "n_estimators": 300,
    "max_depth": 8,           # cap prevents memorisation (tuner found null=unlimited was overfitting)
    "min_samples_leaf": 30,   # was 60; loosen slightly so trees can fit signal, not just noise
    "max_features": "sqrt",
    "class_weight": "balanced",
    "random_state": 42,
    "n_jobs": -1,
}


def _build_nifty_trend_series(benchmark_df: pd.DataFrame) -> pd.Series:
    """
    Rolling NIFTY trend score in [-1, +1].

    Blends two continuous signals:
      vs_sma50  = (nifty - sma50)  / sma50  × 10  — how far above/below 50-day MA
      vs_sma200 = (nifty - sma200) / sma200 × 10  — how far above/below 200-day MA

    score = 0.5 × vs_sma50 + 0.5 × vs_sma200, clipped to [-1, +1].

    Positive = broad market uptrend (reinforces per-stock BUY signals).
    Negative = broad market downtrend (weakens per-stock BUY signals).
    """
    if benchmark_df.empty:
        return pd.Series(dtype=float)

    close = benchmark_df["close"]
    sma50  = close.rolling(50,  min_periods=30).mean()
    sma200 = close.rolling(200, min_periods=100).mean()

    vs_sma50  = ((close - sma50)  / sma50.replace(0, float("nan"))  * 10).clip(-1.0, 1.0)
    vs_sma200 = ((close - sma200) / sma200.replace(0, float("nan")) * 10).clip(-1.0, 1.0)

    trend = (0.5 * vs_sma50 + 0.5 * vs_sma200).fillna(0.0)
    return trend


def _build_sector_series(
    all_prices: dict, industry_map: dict, benchmark_df: pd.DataFrame
) -> dict:
    """
    Pre-compute a per-industry sector-rotation score series using real NSE sector indices.

    For each industry mapped in INDUSTRY_TO_NSE_INDEX:
      1. Load the official NSE sector index closing prices from the DB.
      2. Compute the index's 20-day return.
      3. Subtract NIFTY 50's 20-day return.
      4. Normalise to [-1, +1] (±20% excess = ±1).

    Falls back to portfolio-peer averaging for any industry not in the DB yet
    (i.e., when sector_indices table is not yet populated).

    Returns: {industry: pd.Series indexed by date, values in [-1, +1]}
    """
    # Pre-load NIFTY 50 from sector_indices table (preferred over benchmark_df
    # because it's date-aligned with all other sector index series).
    nifty_close = load_sector_series("Nifty 50", limit=2000)
    if nifty_close.empty and not benchmark_df.empty:
        # fall back to benchmark loaded from price_history
        nifty_close = benchmark_df["close"].copy()
        nifty_close.index = pd.to_datetime(nifty_close.index)
    nifty_20d = nifty_close.pct_change(20) if not nifty_close.empty else None

    # Collect all unique industries from the portfolio
    unique_industries: set[str] = {ind for ind in industry_map.values() if ind}

    sector_series: dict[str, pd.Series] = {}
    for industry in unique_industries:
        nse_index = INDUSTRY_TO_NSE_INDEX.get(industry, "Nifty 500")
        idx_close = load_sector_series(nse_index, limit=2000)

        if not idx_close.empty:
            # ── Primary path: real NSE sector index ──────────────────
            sector_20d = idx_close.pct_change(20)
            if nifty_20d is not None:
                bench_aligned = nifty_20d.reindex(sector_20d.index, method="ffill")
                excess = sector_20d - bench_aligned
            else:
                excess = sector_20d
            sector_series[industry] = (excess / 0.20).clip(-1.0, 1.0).fillna(0.0)
        else:
            # ── Fallback: average portfolio peers (old behaviour) ─────
            # Used when sector_indices table hasn't been backfilled yet.
            syms = [s for s, ind in industry_map.items() if ind == industry and s in all_prices]
            if not syms:
                continue
            member_rets = [all_prices[s]["close"].pct_change(20) for s in syms]
            sector_avg = pd.concat(member_rets, axis=1).mean(axis=1)
            if nifty_20d is not None and not benchmark_df.empty:
                bench_20d_legacy = benchmark_df["close"].pct_change(20)
                bench_aligned = bench_20d_legacy.reindex(sector_avg.index, method="ffill")
                excess = sector_avg - bench_aligned
            else:
                excess = sector_avg
            sector_series[industry] = (excess / 0.20).clip(-1.0, 1.0).fillna(0.0)
            logger.debug("sector_rotation fallback (no DB data) for industry: %s", industry)

    return sector_series


def _build_feature_frame(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    sector_score: pd.Series,
    vix_score: pd.Series,
    nifty_trend: pd.Series,
    markov_score: pd.Series,
    delivery_score: pd.Series,
    fii_flow_score: pd.Series,
    fii_fo_score: pd.Series,
) -> pd.DataFrame:
    """
    Compute all sub-scores for every bar in df.

    sector_score    – pre-computed industry series aligned by date; reindexed to df.
    vix_score       – market-wide VIX percentile score series; reindexed to df.
    nifty_trend     – market-wide NIFTY trend score series; reindexed to df.
    markov_score    – rolling Markov P(Bull)-P(Bear) series; reindexed to df.
    delivery_score  – rolling z-score of NSE delivery pct vs 60-day mean; reindexed to df.
    fii_flow_score  – 10-day rolling FII net cash, percentile-ranked; reindexed to df.
    fii_fo_score    – FII net index futures, percentile-ranked; reindexed to df.
    """
    strat = SicilianStrategy("_trainer")
    close = df["close"]
    volume = df["volume"]

    def _align(series: pd.Series) -> pd.Series:
        """Reindex a market-level series to this stock's date index.
        Returns zeros if series is empty (e.g. table not yet populated)."""
        if series.empty:
            return pd.Series(0.0, index=df.index)
        return series.reindex(df.index, method="ffill").fillna(0.0)

    return pd.DataFrame(
        {
            "rsi":               strat._rolling_rsi_score(close),
            "macd":              strat._rolling_macd_score(close),
            "trend_ma":          strat._rolling_trend_score(close),
            "bollinger":         strat._rolling_bollinger_score(close),
            "volume":            strat._rolling_volume_score(close, volume),
            "volatility":        strat._rolling_volatility_score(close),
            "relative_strength": strat._rolling_relative_strength_score(close, benchmark_df),
            "sector_rotation":   _align(sector_score),
            "vix_regime":        _align(vix_score),
            "nifty_trend":       _align(nifty_trend),
            "markov_regime":     _align(markov_score),
            "delivery_score":    _align(delivery_score),
            "fii_flow_score":    _align(fii_flow_score),
            "fii_fo_score":      _align(fii_fo_score),
        },
        index=df.index,
    )


def build_training_dataset() -> tuple[pd.DataFrame, pd.Series]:
    """
    Iterate over every stock in the DB, compute features + labels, concatenate.

    Returns:
        X: DataFrame of shape (N, len(FEATURE_COLS)) with FEATURE_COLS columns
        y: Series of shape (N,) with values in {-1, 0, 1}
    """
    symbols      = load_all_symbols()
    benchmark_df = load_benchmark(limit=2000)
    industry_map = load_industry_map()          # {symbol: industry}

    # Load all price histories up front so _build_sector_series can average
    # across peers without re-loading inside the per-stock loop.
    logger.info("Loading price histories for %d symbols …", len(symbols))
    all_prices: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    for sym in symbols:
        try:
            df = load_price_history(sym, limit=2000)
            if len(df) >= MIN_BARS:
                all_prices[sym] = df
            else:
                skipped.append(sym)
                logger.debug("Skipping %s: only %d bars (need %d)", sym, len(df), MIN_BARS)
        except Exception as exc:
            skipped.append(sym)
            logger.warning("Failed to load price history for %s: %s", sym, exc)
    if skipped:
        logger.info("Skipped %d/%d symbols during price load: %s", len(skipped), len(symbols), skipped)

    # Pre-compute sector rotation series once for each industry (uses real NSE sector indices).
    sector_series = _build_sector_series(all_prices, industry_map, benchmark_df)
    logger.info("Sector rotation computed for %d industries (NSE index-based)", len(sector_series))

    # Market regime series (same value for every stock on the same calendar date).
    # VIX: rolling percentile of India VIX → [-1 fear, +1 calm].
    raw_vix = load_vix_series(limit=2000)
    vix_score_series = vix_to_score(raw_vix) if not raw_vix.empty else pd.Series(dtype=float)
    if vix_score_series.empty:
        logger.warning("No VIX data found — vix_regime will be 0 for all bars. "
                       "Run: python -m data.backfill_regime")

    # NIFTY trend: distance from SMA50/SMA200 → [-1 downtrend, +1 uptrend].
    nifty_trend_series = _build_nifty_trend_series(benchmark_df)
    logger.info("NIFTY trend series: %d bars", len(nifty_trend_series))

    # Markov regime: rolling P(Bull) - P(Bear) from 252-day transition matrix.
    markov_score_series = build_markov_score_series(benchmark_df)
    logger.info("Markov regime series: %d bars", len(markov_score_series))

    # FII flow score: 10-day rolling net cash, percentile-ranked → [-1, +1]
    raw_fii_flow = load_fii_flow_series(limit=2000)
    if not raw_fii_flow.empty:
        fii_10d = raw_fii_flow.rolling(10).sum()
        fii_flow_score_series = _flow_to_score(fii_10d)
        logger.info("FII flow series: %d bars", len(fii_flow_score_series))
    else:
        fii_flow_score_series = pd.Series(dtype=float)
        logger.warning("No FII flow data — fii_flow_score will be 0. "
                       "Run: python -m quant_engine.data.backfill_fii_dii --from-csv <path>")

    # FII F&O score: net index futures positioning, percentile-ranked → [-1, +1]
    raw_fii_fo = load_fii_fo_series(limit=2000)
    if not raw_fii_fo.empty:
        fii_fo_score_series = _flow_to_score(raw_fii_fo)
        logger.info("FII F&O series: %d bars", len(fii_fo_score_series))
    else:
        fii_fo_score_series = pd.Series(dtype=float)
        logger.warning("No FII F&O data — fii_fo_score will be 0. "
                       "Run: python -m quant_engine.data.backfill_fo_oi --from 2023-01-01")

    all_X: list[pd.DataFrame] = []
    all_y: list[pd.Series] = []
    skipped_train: list[str] = []

    for symbol, df in all_prices.items():
        try:
            industry       = industry_map.get(symbol)
            sector_score   = sector_series.get(industry, pd.Series(dtype=float))

            # delivery_score: rolling z-score of delivery_pct vs 60-day mean, clipped to [-1, +1]
            delivery_df = load_delivery_series(symbol, limit=2000)
            if not delivery_df.empty and "delivery_pct" in delivery_df.columns:
                delivery_pct = delivery_df["delivery_pct"].reindex(df.index)
                roll_mean = delivery_pct.rolling(60, min_periods=10).mean()
                roll_std  = delivery_pct.rolling(60, min_periods=10).std().replace(0, 1)
                delivery_score_series = ((delivery_pct - roll_mean) / roll_std).clip(-3, 3) / 3
            else:
                delivery_score_series = pd.Series(dtype=float)

            features = _build_feature_frame(
                df, benchmark_df,
                sector_score,
                vix_score_series, nifty_trend_series,
                markov_score_series,
                delivery_score_series,
                fii_flow_score_series,
                fii_fo_score_series,
            )

            # 20-day forward return (labelled without look-ahead: we shift backward)
            forward_return = df["close"].shift(-20) / df["close"] - 1

            # Drop NaN rows (warmup period + last 20 bars with no label)
            valid_mask = features.notna().all(axis=1) & forward_return.notna()
            features = features[valid_mask]
            fwd = forward_return[valid_mask]

            if len(features) < 60:
                continue

            label = pd.Series(0, index=fwd.index, dtype=int)   # HOLD default
            label[fwd > BUY_RETURN_THRESHOLD]  =  1
            label[fwd < SELL_RETURN_THRESHOLD] = -1

            all_X.append(features)
            all_y.append(label)

        except Exception as exc:
            skipped_train.append(symbol)
            logger.warning("Skipping %s during feature build: %s", symbol, exc, exc_info=True)

    if skipped_train:
        logger.info("Skipped %d/%d symbols during training: %s",
                     len(skipped_train), len(symbols), skipped_train)

    if not all_X:
        raise RuntimeError("No training data could be generated — is the DB populated?")

    X = pd.concat(all_X)
    y = pd.concat(all_y)
    # Sort both by date so TimeSeriesSplit cuts on actual time boundaries.
    # Use iloc + argsort (positional) to avoid duplicate-index expansion that
    # .loc[X.index] would cause when the same date appears for many stocks.
    order = X.index.argsort(kind="mergesort")
    X = X.iloc[order]
    y = y.iloc[order]

    logger.info("Training dataset: %d samples from %d stocks", len(X), len(all_X))
    return X, y


def _tune_hyperparams(X: pd.DataFrame, y: pd.Series) -> dict:
    """
    Grid search over min_samples_leaf × max_depth using walk-forward CV.

    min_samples_leaf controls the minimum number of training samples required
    at each leaf node. Smaller values let trees grow deeper and capture finer
    patterns — but risk memorising noise. max_depth caps how many splits a
    single tree can make; shallow trees are fast but underfit.

    We search jointly because they interact: a very deep tree (high max_depth)
    with tiny leaves (low min_samples_leaf) will overfit; the grid lets us find
    the right balance empirically rather than guessing.
    """
    leaf_candidates  = [20, 30, 50, 80]
    depth_candidates = [6, 8, 10, 12]

    tscv = TimeSeriesSplit(n_splits=5)
    best_score = -1.0
    best_params = {}

    logger.info("Hyperparameter search: %d combinations × 5 CV folds",
                len(leaf_candidates) * len(depth_candidates))

    for max_depth in depth_candidates:
        for min_leaf in leaf_candidates:
            params = {**RF_PARAMS, "max_depth": max_depth, "min_samples_leaf": min_leaf}
            fold_scores = []
            for train_idx, test_idx in tscv.split(X):
                clf = RandomForestClassifier(**params)
                clf.fit(X.iloc[train_idx], y.iloc[train_idx])
                fold_scores.append(clf.score(X.iloc[test_idx], y.iloc[test_idx]))
            mean_acc = float(np.mean(fold_scores))
            logger.info("  max_depth=%-4s  min_samples_leaf=%-3d  → CV acc %.4f",
                        str(max_depth), min_leaf, mean_acc)
            if mean_acc > best_score:
                best_score = mean_acc
                best_params = {"max_depth": max_depth, "min_samples_leaf": min_leaf}

    logger.info("Best params: %s  (CV acc %.4f)", best_params, best_score)
    return best_params


def train(X: pd.DataFrame, y: pd.Series) -> dict:
    """
    Tune hyperparameters, then train a Random Forest on X/y with walk-forward CV.
    Saves the model + metadata to ML_MODEL_DIR and returns the metadata dict.
    """
    ML_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: find best min_samples_leaf + max_depth ───────────
    best_params = _tune_hyperparams(X, y)
    final_params = {**RF_PARAMS, **best_params}

    # ── Step 2: CV with best params (for honest accuracy reporting) ──
    tscv = TimeSeriesSplit(n_splits=5)
    cv_accuracies: list[float] = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X), 1):
        clf_cv = RandomForestClassifier(**final_params)
        clf_cv.fit(X.iloc[train_idx], y.iloc[train_idx])
        acc = clf_cv.score(X.iloc[test_idx], y.iloc[test_idx])
        cv_accuracies.append(acc)
        logger.info("CV fold %d accuracy: %.3f", fold, acc)

    # ── Step 3: final model trained on the full dataset ──────────
    clf = RandomForestClassifier(**final_params)
    clf.fit(X, y)

    # Persist
    joblib.dump(clf, ML_MODEL_DIR / "sicilian_rf.pkl")

    class_dist = y.value_counts().to_dict()
    feature_importances = {
        col: round(float(imp), 6)
        for col, imp in zip(FEATURE_COLS, clf.feature_importances_)
    }

    metadata = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_samples": int(len(X)),
        "cv_accuracy_mean": round(float(np.mean(cv_accuracies)), 4),
        "cv_accuracy_folds": [round(a, 4) for a in cv_accuracies],
        "class_distribution": {str(k): int(v) for k, v in class_dist.items()},
        "feature_importances": feature_importances,
        "classes": clf.classes_.tolist(),
        "rf_params": final_params,
    }

    with open(ML_MODEL_DIR / "metadata.json", "w") as fh:
        json.dump(metadata, fh, indent=2)

    logger.info(
        "Model trained. CV accuracy: %.3f ± %.3f",
        np.mean(cv_accuracies),
        np.std(cv_accuracies),
    )
    return metadata


def run_training_pipeline() -> dict:
    """Entry point: build dataset → train → return metadata."""
    logger.info("Building training dataset …")
    X, y = build_training_dataset()
    logger.info("Training Random Forest …")
    return train(X, y)


if __name__ == "__main__":
    run_training_pipeline()
