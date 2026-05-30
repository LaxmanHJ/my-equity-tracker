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
from sklearn.impute import SimpleImputer
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline

from quant_engine.config import ML_MODEL_DIR, INDUSTRY_TO_NSE_INDEX
from quant_engine.data.loader import (
    load_all_symbols, load_benchmark, load_price_history,
    load_industry_map,
)
from quant_engine.data.delivery_loader import load_delivery_series
from quant_engine.data.market_regime_loader import (
    load_vix_series, vix_to_score, build_markov_score_series,
    load_fii_fo_series, _flow_to_score,
)
from quant_engine.data.sector_indices_loader import load_sector_series
from quant_engine.data.intraday_features import build_intraday_features
from quant_engine.strategies.sicilian_strategy import SicilianStrategy
from quant_engine.ml.features import build_feature_frame
from quant_engine.ml.labels import (
    triple_barrier_events,
    triple_barrier_labels,
    DEFAULT_HORIZON as LABEL_HORIZON,
    DEFAULT_VOL_MULT as BARRIER_VOL_MULT,
    DEFAULT_COST as ROUND_TRIP_COST,
)
from quant_engine.ml.sample_weights import uniqueness_weights

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
    # FII F&O positioning (market-wide). fii_flow_score (cash) and pcr_score
    # were dropped 2026-05-20: only ~20 DB rows each, zero importance in the
    # April-19 pickle. Cash FII + PCR are not yet backfilled in Turso.
    "fii_fo_score",       # FII net index futures (long-short), percentile-ranked → [-1 short, +1 long]
    # Intraday-derived features (from 15-min candles, Angel One; ~2018+ coverage)
    "overnight_gap",          # (today_open - prev_close) / prev_close
    "intraday_range_ratio",   # (day_high - day_low) / ATR14
    "last_hour_momentum",     # (close_15:15 - close_14:15) / close_14:15
    "vwap_deviation",         # (day_close - vwap) / vwap
    "opening_drive_vol",      # 9:15+9:30 vol / 20d mean of same, shift(1)
    "closing_spike_vol",      # 15:15 vol / 20d mean of same, shift(1)
    "vol_concentration",      # max(15m_vol) / sum(15m_vol)
]

# DEPRECATED 2026-05-21 (ML audit S3/S7): the fixed ±3% / 20d gross threshold
# was replaced by vol-scaled, cost-aware triple-barrier labels (see labels.py and
# the LABEL_HORIZON / BARRIER_VOL_MULT / ROUND_TRIP_COST constants imported above).
# Kept only because diagnostic.py still imports them for its JSON metadata block.
BUY_RETURN_THRESHOLD  =  0.03   # > +3%  → BUY   (no longer used for labelling)
SELL_RETURN_THRESHOLD = -0.03   # < -3%  → SELL  (no longer used for labelling)

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
    fii_fo_score: pd.Series,
    intraday_feats: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute the FEATURE_COLS feature matrix for one symbol.

    Thin wrapper around `quant_engine.ml.features.build_feature_frame` —
    single source of truth shared with `SicilianStrategy._build_ml_features`
    (P1-e refactor, ML audit S9). Argument order kept for diagnostic.py and
    any other positional callers.
    """
    strat = SicilianStrategy("_trainer")
    return build_feature_frame(
        df, benchmark_df,
        strategy=strat,
        sector_score=sector_score,
        vix_score=vix_score,
        nifty_trend=nifty_trend,
        markov_score=markov_score,
        delivery_score=delivery_score,
        fii_fo_score=fii_fo_score,
        intraday_feats=intraday_feats,
        context="_trainer",
    )


def build_training_dataset() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Iterate over every stock in the DB, compute features + labels, concatenate.

    Returns:
        X:             DataFrame (N, len(FEATURE_COLS)) of features
        y:             Series (N,) with values in {-1, 0, 1} — triple-barrier labels
        sample_weight: Series (N,) of AFML §4.6 uniqueness weights ∈ (0, 1]
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
    all_t1: list[pd.DatetimeIndex] = []   # per-label resolution date (for uniqueness weights)
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

            # intraday features per-stock (date-indexed, 3 columns)
            intraday_feats = build_intraday_features(symbol)

            features = _build_feature_frame(
                df, benchmark_df,
                sector_score,
                vix_score_series, nifty_trend_series,
                markov_score_series,
                delivery_score_series,
                fii_fo_score_series,
                intraday_feats,
            )

            # Triple-barrier events (vol-scaled, cost-aware) — AFML Ch.3.
            # Replaces the old fixed ±3% / 20d gross threshold (heteroscedastic
            # across vol regimes + cost-blind). See wiki/concepts/ml_audit_2026_05_21.md
            # S3/S7 and quant_engine/ml/labels.py. Labels are NaN in the warm-up
            # and trailing-horizon windows, so valid_mask drops them exactly like
            # the old forward_return.notna() did. The `t1_offset` column carries
            # per-label resolution time — required for P1-c sample-uniqueness
            # weighting so an early barrier touch isn't treated the same as a
            # full-horizon timeout.
            events = triple_barrier_events(
                df["close"],
                horizon=LABEL_HORIZON,
                vol_mult=BARRIER_VOL_MULT,
                cost=ROUND_TRIP_COST,
            )
            label_series = events["label"]

            valid_mask = features.notna().all(axis=1) & label_series.notna()
            features = features[valid_mask]
            label = label_series[valid_mask].astype(int)
            t1_offset = events.loc[valid_mask, "t1_offset"].astype(int).to_numpy()

            if len(features) < 60:
                continue

            # Map each label's t1_offset to the resolving bar's calendar date,
            # using the symbol's own price index. Positional lookup is safe
            # because valid_mask preserves the original ordering.
            sym_index = df.index
            valid_positions = sym_index.get_indexer(features.index)
            t1_positions = valid_positions + t1_offset
            t1_dates = sym_index[t1_positions]

            all_X.append(features)
            all_y.append(label)
            all_t1.append(pd.DatetimeIndex(t1_dates))

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
    t1_dates = pd.DatetimeIndex(np.concatenate([t1.values for t1 in all_t1]))

    # Sort by date so TimeSeriesSplit cuts on actual time boundaries.
    # Use iloc + argsort (positional) to avoid duplicate-index expansion that
    # .loc[X.index] would cause when the same date appears for many stocks.
    order = X.index.argsort(kind="mergesort")
    X = X.iloc[order]
    y = y.iloc[order]
    t1_dates = t1_dates[order]

    # Sample-uniqueness weights (P1-c / AFML §4.6). Pooled across all symbols:
    # a label observed on date d shares information with every label observed
    # on dates [d, t1] regardless of which symbol it came from.
    t0_dates = pd.DatetimeIndex(X.index)
    sample_weight = pd.Series(
        uniqueness_weights(t0_dates, t1_dates),
        index=X.index,
        name="sample_weight",
    )

    logger.info(
        "Training dataset: %d samples from %d stocks  "
        "(uniqueness weight: mean=%.3f, min=%.3f)",
        len(X), len(all_X),
        float(sample_weight.mean()), float(sample_weight.min()),
    )
    return X, y, sample_weight


def _build_pipeline(rf_params: dict) -> Pipeline:
    """
    Wrap the RF in an imputer→RF pipeline.

    The imputer's medians are learned from the (NaN-free) training data and
    persisted with the pickle. At inference time, bars with NaN in soft
    features (macro regime, sector, intraday) are filled with those medians
    instead of being silently dropped to HOLD — see SIC-29.
    """
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("rf", RandomForestClassifier(**rf_params)),
    ])


def _tune_hyperparams(X: pd.DataFrame, y: pd.Series, sample_weight: pd.Series) -> dict:
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

    # Purge training rows whose 20d label horizon overlaps the test fold (AFML
    # Ch.10). Function-local import breaks the trainer↔diagnostic import cycle
    # (diagnostic imports FEATURE_COLS/_build_feature_frame from this module).
    from quant_engine.ml.diagnostic import _purge_train_indices, LABEL_HORIZON_DAYS
    dates = pd.DatetimeIndex(X.index)

    tscv = TimeSeriesSplit(n_splits=5)
    best_score = -1.0
    best_params = {}

    logger.info("Hyperparameter search: %d combinations × 5 CV folds (label-horizon purged)",
                len(leaf_candidates) * len(depth_candidates))

    for max_depth in depth_candidates:
        for min_leaf in leaf_candidates:
            params = {**RF_PARAMS, "max_depth": max_depth, "min_samples_leaf": min_leaf}
            fold_scores = []
            for train_idx, test_idx in tscv.split(X):
                train_idx = _purge_train_indices(
                    np.asarray(train_idx), np.asarray(test_idx), dates, LABEL_HORIZON_DAYS
                )
                if len(train_idx) == 0:
                    continue
                clf = _build_pipeline(params)
                # Pipeline routes sample_weight to the RF step via "<step>__sample_weight".
                clf.fit(
                    X.iloc[train_idx], y.iloc[train_idx],
                    rf__sample_weight=sample_weight.iloc[train_idx].to_numpy(),
                )
                fold_scores.append(clf.score(X.iloc[test_idx], y.iloc[test_idx]))
            if not fold_scores:
                continue
            mean_acc = float(np.mean(fold_scores))
            logger.info("  max_depth=%-4s  min_samples_leaf=%-3d  → CV acc %.4f",
                        str(max_depth), min_leaf, mean_acc)
            if mean_acc > best_score:
                best_score = mean_acc
                best_params = {"max_depth": max_depth, "min_samples_leaf": min_leaf}

    logger.info("Best params: %s  (CV acc %.4f)", best_params, best_score)
    return best_params


def train(X: pd.DataFrame, y: pd.Series, sample_weight: pd.Series) -> dict:
    """
    Tune hyperparameters, then train a Random Forest on X/y with walk-forward CV.
    Saves the model + metadata to ML_MODEL_DIR and returns the metadata dict.

    `sample_weight` is the AFML §4.6 uniqueness weight produced by
    `build_training_dataset` and is passed to every RF .fit() (tune, CV, final).
    """
    ML_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: find best min_samples_leaf + max_depth ───────────
    best_params = _tune_hyperparams(X, y, sample_weight)
    final_params = {**RF_PARAMS, **best_params}

    # ── Step 2: CV with best params (for honest accuracy reporting) ──
    # Label-horizon purging (AFML Ch.10): drop training rows whose 20d label
    # overlaps the test fold start. Without this, the reported CV accuracy is
    # optimistic by ~3-5pp (the test fold's own future prices leak into the
    # training labels). See wiki/concepts/ml_audit_2026_05_21.md → S2.
    from quant_engine.ml.diagnostic import _purge_train_indices, LABEL_HORIZON_DAYS
    dates = pd.DatetimeIndex(X.index)

    tscv = TimeSeriesSplit(n_splits=5)
    cv_accuracies: list[float] = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X), 1):
        train_idx = _purge_train_indices(
            np.asarray(train_idx), np.asarray(test_idx), dates, LABEL_HORIZON_DAYS
        )
        if len(train_idx) == 0:
            logger.warning("CV fold %d: purged training set empty — skipping", fold)
            continue
        clf_cv = _build_pipeline(final_params)
        clf_cv.fit(
            X.iloc[train_idx], y.iloc[train_idx],
            rf__sample_weight=sample_weight.iloc[train_idx].to_numpy(),
        )
        acc = clf_cv.score(X.iloc[test_idx], y.iloc[test_idx])
        cv_accuracies.append(acc)
        logger.info("CV fold %d accuracy: %.3f (purged train n=%d)", fold, acc, len(train_idx))

    # ── Step 3: final model trained on the full dataset ──────────
    clf = _build_pipeline(final_params)
    clf.fit(X, y, rf__sample_weight=sample_weight.to_numpy())

    # Persist (pipeline: imputer + RF — imputer's learned medians are baked in
    # so inference can fill NaN for soft features without silently dropping bars)
    joblib.dump(clf, ML_MODEL_DIR / "sicilian_rf.pkl")

    rf_step = clf.named_steps["rf"]
    class_dist = y.value_counts().to_dict()
    feature_importances = {
        col: round(float(imp), 6)
        for col, imp in zip(FEATURE_COLS, rf_step.feature_importances_)
    }

    # Expose the imputer's learned medians so inference-time NaN fills are
    # auditable (and so we can detect drift between training and live medians).
    imputer = clf.named_steps["imputer"]
    imputer_medians = {
        col: round(float(val), 6)
        for col, val in zip(FEATURE_COLS, imputer.statistics_)
    }

    # Effective sample size after uniqueness weighting (AFML §4.6).
    # n_eff = (Σ w_i)² / Σ w_i² — the variance-equivalent unique-obs count.
    w_arr = sample_weight.to_numpy()
    n_eff = float((w_arr.sum() ** 2) / (np.square(w_arr).sum())) if w_arr.size else 0.0

    metadata = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n_samples": int(len(X)),
        "cv_accuracy_mean": round(float(np.mean(cv_accuracies)), 4),
        "cv_accuracy_folds": [round(a, 4) for a in cv_accuracies],
        "class_distribution": {str(k): int(v) for k, v in class_dist.items()},
        "feature_importances": feature_importances,
        "feature_cols": FEATURE_COLS,
        "imputer_medians": imputer_medians,
        "classes": rf_step.classes_.tolist(),
        "rf_params": final_params,
        "label_method": "triple_barrier",
        "label_horizon": LABEL_HORIZON,
        "barrier_vol_mult": BARRIER_VOL_MULT,
        "round_trip_cost": ROUND_TRIP_COST,
        "uniqueness_weights": {
            "mean": round(float(w_arr.mean()), 4) if w_arr.size else None,
            "min":  round(float(w_arr.min()),  4) if w_arr.size else None,
            "n_effective": round(n_eff, 1),
            "n_effective_pct": round(100.0 * n_eff / max(len(w_arr), 1), 1),
        },
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
    X, y, sample_weight = build_training_dataset()
    logger.info("Training Random Forest …")
    return train(X, y, sample_weight)


if __name__ == "__main__":
    run_training_pipeline()
