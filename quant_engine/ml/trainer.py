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

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

from quant_engine.config import ML_MODEL_DIR
from quant_engine.data.loader import (
    load_all_symbols, load_benchmark, load_price_history,
    load_industry_map, load_analyst_consensus,
)
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
    # New orthogonal features
    "sector_rotation",    # avg 20d return of industry peers vs benchmark
    "analyst_consensus",  # (strong_buy+buy - sell-strong_sell) / total_analysts
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
    "max_depth": 6,
    "min_samples_leaf": 60,   # ~60 trading days of context per leaf
    "max_features": "sqrt",
    "class_weight": "balanced",
    "random_state": 42,
    "n_jobs": -1,
}


def _build_sector_series(
    all_prices: dict, industry_map: dict, benchmark_df: pd.DataFrame
) -> dict:
    """
    Pre-compute a per-industry sector-rotation score series.

    For each industry:
      1. Average the 20-day return across all member stocks on each date.
      2. Subtract the benchmark's 20-day return on that date.
      3. Normalise to [-1, +1] (±20% excess = ±1).

    Doing this once up-front is O(stocks) total; doing it inside the per-stock
    loop would be O(stocks²).

    Returns: {industry: pd.Series indexed by date, values in [-1, +1]}
    """
    # Group available symbols by industry
    industry_groups: dict[str, list[str]] = {}
    for sym, ind in industry_map.items():
        if sym in all_prices and ind:
            industry_groups.setdefault(ind, []).append(sym)

    bench_20d = benchmark_df["close"].pct_change(20) if not benchmark_df.empty else None

    sector_series: dict[str, pd.Series] = {}
    for industry, syms in industry_groups.items():
        member_rets = [all_prices[s]["close"].pct_change(20) for s in syms]
        if not member_rets:
            continue
        sector_avg = pd.concat(member_rets, axis=1).mean(axis=1)
        if bench_20d is not None:
            bench_aligned = bench_20d.reindex(sector_avg.index, method="ffill")
            excess = sector_avg - bench_aligned
        else:
            excess = sector_avg
        sector_series[industry] = (excess / 0.20).clip(-1.0, 1.0).fillna(0.0)

    return sector_series


def _build_feature_frame(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    sector_score: pd.Series,
    analyst_score: float,
) -> pd.DataFrame:
    """
    Compute all sub-scores for every bar in df.

    sector_score  – pre-computed industry series aligned by date; reindexed to df.
    analyst_score – static scalar for this stock (same value across all bars).
    """
    strat = SicilianStrategy("_trainer")
    close = df["close"]
    volume = df["volume"]

    # Align sector series to this stock's date index (forward-fill gaps, e.g. holidays)
    sector_aligned = sector_score.reindex(df.index, method="ffill").fillna(0.0)

    return pd.DataFrame(
        {
            "rsi":               strat._rolling_rsi_score(close),
            "macd":              strat._rolling_macd_score(close),
            "trend_ma":          strat._rolling_trend_score(close),
            "bollinger":         strat._rolling_bollinger_score(close),
            "volume":            strat._rolling_volume_score(close, volume),
            "volatility":        strat._rolling_volatility_score(close),
            "relative_strength": strat._rolling_relative_strength_score(close, benchmark_df),
            "sector_rotation":   sector_aligned,
            "analyst_consensus": pd.Series(analyst_score, index=df.index),
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
    analyst_map  = load_analyst_consensus()     # {symbol: score in [-1,+1]}

    # Load all price histories up front so _build_sector_series can average
    # across peers without re-loading inside the per-stock loop.
    logger.info("Loading price histories for %d symbols …", len(symbols))
    all_prices: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = load_price_history(sym, limit=2000)
            if len(df) >= MIN_BARS:
                all_prices[sym] = df
        except Exception:
            pass

    # Pre-compute sector rotation series once for each industry.
    sector_series = _build_sector_series(all_prices, industry_map, benchmark_df)
    logger.info("Sector rotation computed for %d industries", len(sector_series))

    all_X: list[pd.DataFrame] = []
    all_y: list[pd.Series] = []

    for symbol, df in all_prices.items():
        try:
            industry       = industry_map.get(symbol)
            sector_score   = sector_series.get(industry, pd.Series(dtype=float))
            analyst_score  = analyst_map.get(symbol, 0.0)   # 0 = neutral if no coverage

            features = _build_feature_frame(df, benchmark_df, sector_score, analyst_score)

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
            logger.warning("Skipping %s: %s", symbol, exc)

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
    leaf_candidates  = [5, 10, 20, 40, 60]
    depth_candidates = [6, 8, 10, None]   # None = unlimited depth

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
