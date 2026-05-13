"""
Meta-labeler for the Sicilian engine — SIC-42 Experiment B.

The premise (López de Prado, AFML Ch.3): when a primary signal has edge but
a monolithic ML model can't rediscover that edge from scratch, split the
problem.

* PRIMARY — the 7-factor linear Sicilian composite. Already shipped, has
  +0.040 IC at 20d. Decides direction (BUY / flat). Not retrained here.
* SECONDARY — a binary classifier trained only on primary-BUY bars,
  labelling whether the trade was actually profitable at 20d. Its output
  `P(profitable | primary said BUY)` is a bet-sizing prior ∈ [0, 1].

The secondary is asked an easier question on a cleaner, smaller dataset
than "predict direction from scratch" — hence the name meta-label.

Initial SIC-42 design excluded the 7 primary factor scores from the
secondary, on the theory that letting the secondary see them would
just re-derive the primary. Empirically that was too restrictive: on
the Nifty 200 expanded universe, a regime-only secondary (5 market-
wide cols + sector_rotation) had near-zero cross-sectional variance
and produced 0 pp hit uplift / ~0 rank_IC across 4 folds (n=4124).
The 2026-05-09 revision adds the 7 factor scores back — López de
Prado's original design allows the secondary to use the primary's
features and even its prediction; the value is in the *non-linear*
refinement, not in feature exclusion.

This module:
1. Builds the primary-BUY subset dataset (reuses diagnostic.py's
   build_dataset_with_horizons).
2. Runs walk-forward purged CV training a calibrated LogisticRegression.
3. Reports precision / hit-rate / per-trade-return uplift vs. "always
   follow primary" baseline on OOS test subsets.
4. Writes data/meta_labeler_diagnostic.json for downstream use.

Run:
    python -m quant_engine.ml.meta_labeler

See wiki/papers/lopez_de_prado_afml_2018.md and
wiki/concepts/ml_pipeline.md for theory and prior SIC-41 results.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.calibration import CalibratedClassifierCV
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import joblib

from quant_engine.config import ML_MODEL_DIR, PROJECT_ROOT
from quant_engine.ml.diagnostic import (
    LABEL_HORIZON_DAYS,
    MIN_TEST_OBS,
    MIN_TRAIN_AFTER_PURGE,
    N_SPLITS,
    _purge_train_indices,
    build_dataset_with_horizons,
)

logger = logging.getLogger(__name__)

# Primary-BUY threshold: same value the production Claude gate uses as the
# "high conviction" floor for a linear composite BUY (see
# src/services/signalQueueService.js :: minCompositeScore and SIC-42 spec).
PRIMARY_BUY_THRESHOLD = 0.40

# Production trading threshold on the secondary's probability output.
# Found by --sweep on Nifty 200 universe (2026-05-09): 0.75 produces +3.79 pp
# hit uplift, Sharpe 0.30 → 0.40 on n=1708 OOS. Going higher (0.80) starts
# deteriorating per-fold (fold 3 drops 71→65). 0.75 is the empirical signal-
# to-noise peak. See data/meta_labeler_sweep.json + wiki/concepts/ml_pipeline.md.
META_TRADE_THRESHOLD = 0.75

# Persistence paths for the final-fit production model.
META_MODEL_PATH = ML_MODEL_DIR / "meta_labeler.pkl"
META_METADATA_PATH = ML_MODEL_DIR / "meta_labeler_metadata.json"

# Secondary-model features. Full FEATURE_COLS minus the 7 factor scores the
# primary already uses. Source of truth for the exclusions is SIC-42's
# "Features for the secondary model" section.
PRIMARY_FACTOR_COLS = (
    "rsi",
    "macd",
    "trend_ma",
    "bollinger",
    "volume",
    "volatility",
    "relative_strength",
)
META_FEATURE_COLS = [
    # 7 factor scores — same inputs the primary uses linearly; secondary
    # may refine non-linearly. Available for every symbol with enough OHLC.
    "rsi",
    "macd",
    "trend_ma",
    "bollinger",
    "volume",
    "volatility",
    "relative_strength",
    # Cross-stock
    "sector_rotation",
    # Market regime (constant per date — same value across symbols)
    "vix_regime",
    "nifty_trend",
    "markov_regime",
    "fii_fo_score",
]

MIN_POSITIVE_RATE = 0.05  # reject fold if primary-BUY subset degenerate

# Default thresholds for --sweep mode. Centred above 0.55 because the wider-
# universe Exp-B run showed 89% of primary-BUY rows clearing 0.55, so 0.55 is
# too lax — most informative sweep range is 0.55–0.80.
DEFAULT_SWEEP_THRESHOLDS = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)

DIAG_OUTPUT_PATH = PROJECT_ROOT / "data" / "meta_labeler_diagnostic.json"
SWEEP_OUTPUT_PATH = PROJECT_ROOT / "data" / "meta_labeler_sweep.json"


def _build_secondary_pipeline() -> Pipeline:
    """
    SimpleImputer → StandardScaler → Isotonic-calibrated LogisticRegression.

    The calibration step maps raw LR scores to well-formed probabilities so
    the downstream bet-sizing threshold (META_TRADE_THRESHOLD = 0.55) means
    what it says. Isotonic is non-parametric and handles the small-sample
    training sets we get from the primary-BUY subset better than Platt.

    LogisticRegression chosen over LGBM for the first pass:
    * interpretable coefficients → easy to verify secondary isn't
      accidentally re-learning the primary.
    * regularization (L2) + calibration keeps variance low on the smaller
      primary-BUY training subsets.
    """
    base = LogisticRegression(
        C=1.0,
        penalty="l2",
        solver="lbfgs",
        max_iter=1000,
        random_state=42,
    )
    calibrated = CalibratedClassifierCV(base, method="isotonic", cv=3)
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", calibrated),
    ])


def _make_label(fwd_ret: np.ndarray) -> np.ndarray:
    """Binary profitability label: 1 if fwd_ret_20d > 0 else 0."""
    return (fwd_ret > 0).astype(int)


def _fold_metrics(
    prob: np.ndarray,
    label: np.ndarray,
    fwd_ret: np.ndarray,
    threshold: float,
) -> dict:
    """
    Evaluate secondary vs "always follow primary" baseline on one fold.

    All inputs restricted to primary-BUY test rows.
      prob     — secondary P(profitable)
      label    — binary profitability
      fwd_ret  — fwd_ret_20d on those rows
      threshold— cutoff at which we 'take' the trade
    """
    n = len(prob)
    out: dict = {
        "n_primary_buy": int(n),
        "baseline_hit": None,
        "baseline_mean_ret": None,
        "baseline_std_ret": None,
        "filtered_hit": None,
        "filtered_mean_ret": None,
        "filtered_std_ret": None,
        "filtered_n": 0,
        "precision_at_threshold": None,
        "hit_uplift_abs": None,
        "hit_uplift_rel": None,
        "per_trade_sharpe_baseline": None,
        "per_trade_sharpe_filtered": None,
        "rank_ic_prob_vs_ret": None,
    }
    if n < MIN_TEST_OBS:
        return out

    # Baseline: every primary-BUY bar
    out["baseline_hit"] = round(float(label.mean()) * 100.0, 2)
    out["baseline_mean_ret"] = round(float(fwd_ret.mean()), 5)
    out["baseline_std_ret"] = round(float(fwd_ret.std(ddof=0)), 5)
    if out["baseline_std_ret"] and out["baseline_std_ret"] > 1e-9:
        out["per_trade_sharpe_baseline"] = round(
            out["baseline_mean_ret"] / out["baseline_std_ret"], 4
        )

    # Filtered: secondary says take the trade (P >= threshold)
    mask = prob >= threshold
    k = int(mask.sum())
    out["filtered_n"] = k
    if k > 0:
        f_label = label[mask]
        f_ret = fwd_ret[mask]
        out["filtered_hit"] = round(float(f_label.mean()) * 100.0, 2)
        out["filtered_mean_ret"] = round(float(f_ret.mean()), 5)
        out["filtered_std_ret"] = round(float(f_ret.std(ddof=0)), 5)
        out["precision_at_threshold"] = out["filtered_hit"]
        out["hit_uplift_abs"] = round(out["filtered_hit"] - out["baseline_hit"], 2)
        if out["baseline_hit"] and out["baseline_hit"] > 1e-9:
            out["hit_uplift_rel"] = round(
                (out["filtered_hit"] - out["baseline_hit"]) / out["baseline_hit"] * 100.0, 2
            )
        if out["filtered_std_ret"] and out["filtered_std_ret"] > 1e-9:
            out["per_trade_sharpe_filtered"] = round(
                out["filtered_mean_ret"] / out["filtered_std_ret"], 4
            )

    # Rank IC: does the secondary's probability rank the fwd_ret correctly
    # within the primary-BUY subset? (Captures useful-signal even if the
    # threshold-at-0.55 doesn't fire.)
    if len(prob) >= 3 and np.std(prob) > 1e-12 and np.std(fwd_ret) > 1e-12:
        rho, _ = spearmanr(prob, fwd_ret)
        out["rank_ic_prob_vs_ret"] = round(float(rho), 4)

    return out


def run_meta_diagnostic(
    primary_threshold: float = PRIMARY_BUY_THRESHOLD,
    trade_threshold: float = META_TRADE_THRESHOLD,
    sweep_thresholds: Optional[list[float]] = None,
) -> dict:
    """
    Walk-forward purged CV of the meta-labeler on primary-BUY subset.

    Returns a dict with per-fold metrics and an aggregate across folds.

    If `sweep_thresholds` is provided, additionally evaluates pooled and
    per-fold metrics at each threshold in the list. Reuses the same fitted
    per-fold models — only the threshold-dependent metrics are recomputed.
    The primary diagnostic JSON still uses `trade_threshold`; the sweep
    is written to `SWEEP_OUTPUT_PATH`.
    """
    logger.info(
        "Building diagnostic dataset (primary_threshold=%.2f, "
        "required_feature_cols=%d META cols only — wider universe survives) …",
        primary_threshold,
        len(META_FEATURE_COLS),
    )
    X_all, meta_all = build_dataset_with_horizons(
        required_feature_cols=META_FEATURE_COLS,
    )

    if not all(c in X_all.columns for c in META_FEATURE_COLS):
        missing = [c for c in META_FEATURE_COLS if c not in X_all.columns]
        raise RuntimeError(f"Missing meta features in dataset: {missing}")

    fwd_ret = meta_all["fwd_ret_20d"].values
    linear_score = meta_all["linear_score"].values

    # Meta-label defined only where fwd_ret_20d is valid. The dataset
    # builder already drops rows with NaN fwd_ret_20d — still assert.
    assert not np.isnan(fwd_ret).any(), "fwd_ret_20d should be NaN-free post-build"

    label_all = _make_label(fwd_ret)
    is_primary_buy = linear_score >= primary_threshold
    logger.info(
        "Primary-BUY coverage: %d / %d rows (%.1f%%)",
        int(is_primary_buy.sum()),
        len(is_primary_buy),
        is_primary_buy.mean() * 100,
    )

    dates = pd.DatetimeIndex(X_all.index)
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)

    fold_details: list[dict] = []
    fold_arrays: list[dict] = []  # per-fold (prob, label, fwd_ret) for sweep
    all_probs: list[float] = []
    all_labels: list[int] = []
    all_rets: list[float] = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X_all), 1):
        train_idx = np.asarray(train_idx)
        test_idx = np.asarray(test_idx)

        train_idx_purged = _purge_train_indices(
            train_idx, test_idx, dates, LABEL_HORIZON_DAYS
        )
        if len(train_idx_purged) < MIN_TRAIN_AFTER_PURGE:
            logger.warning(
                "Fold %d: purged training set too small (%d < %d) — skipping",
                fold, len(train_idx_purged), MIN_TRAIN_AFTER_PURGE,
            )
            continue

        # Subset train/test to primary-BUY bars only
        train_pb_mask = is_primary_buy[train_idx_purged]
        test_pb_mask = is_primary_buy[test_idx]

        train_pb_idx = train_idx_purged[train_pb_mask]
        test_pb_idx = test_idx[test_pb_mask]

        n_train_pb = len(train_pb_idx)
        n_test_pb = len(test_pb_idx)
        if n_train_pb < MIN_TRAIN_AFTER_PURGE or n_test_pb < MIN_TEST_OBS:
            logger.warning(
                "Fold %d: primary-BUY subset too small (train=%d, test=%d) — skipping",
                fold, n_train_pb, n_test_pb,
            )
            continue

        # Label variance guard — if the primary-BUY subset has trivial
        # class imbalance in training, calibration will be garbage.
        y_train = label_all[train_pb_idx]
        pos_rate = float(y_train.mean())
        if pos_rate < MIN_POSITIVE_RATE or pos_rate > 1 - MIN_POSITIVE_RATE:
            logger.warning(
                "Fold %d: training class too imbalanced (pos_rate=%.3f) — skipping",
                fold, pos_rate,
            )
            continue

        X_train = X_all.iloc[train_pb_idx][META_FEATURE_COLS]
        X_test = X_all.iloc[test_pb_idx][META_FEATURE_COLS]

        model = _build_secondary_pipeline()
        model.fit(X_train, y_train)

        prob_buy = model.predict_proba(X_test)
        # positive class index
        pos_idx = list(model.named_steps["clf"].classes_).index(1)
        prob = prob_buy[:, pos_idx]

        y_test = label_all[test_pb_idx]
        fwd_test = fwd_ret[test_pb_idx]

        metrics = _fold_metrics(prob, y_test, fwd_test, trade_threshold)
        fold_details.append({
            "fold": fold,
            "train_start": str(pd.Timestamp(dates[train_pb_idx[0]]).date()),
            "train_end": str(pd.Timestamp(dates[train_pb_idx[-1]]).date()),
            "test_start": str(pd.Timestamp(dates[test_pb_idx[0]]).date()),
            "test_end": str(pd.Timestamp(dates[test_pb_idx[-1]]).date()),
            "train_pos_rate": round(pos_rate, 4),
            **metrics,
        })

        all_probs.extend(prob.tolist())
        all_labels.extend(y_test.tolist())
        all_rets.extend(fwd_test.tolist())

        fold_arrays.append({
            "fold": fold,
            "test_start": str(pd.Timestamp(dates[test_pb_idx[0]]).date()),
            "test_end": str(pd.Timestamp(dates[test_pb_idx[-1]]).date()),
            "prob": prob,
            "label": y_test,
            "fwd_ret": fwd_test,
        })

        logger.info(
            "Fold %d: train_pb=%d test_pb=%d  baseline_hit=%.2f%%  "
            "filtered_hit=%.2f%% (@p>=%.2f, n=%d)  rank_IC=%s",
            fold, n_train_pb, n_test_pb,
            metrics.get("baseline_hit") or float("nan"),
            metrics.get("filtered_hit") or float("nan"),
            trade_threshold,
            metrics.get("filtered_n") or 0,
            metrics.get("rank_ic_prob_vs_ret"),
        )

    if not fold_details:
        raise RuntimeError("No folds produced valid meta-labeler metrics.")

    pooled = _fold_metrics(
        np.array(all_probs), np.array(all_labels), np.array(all_rets), trade_threshold
    )

    # ── Threshold sweep ─────────────────────────────────────────────────
    sweep_payload: Optional[dict] = None
    if sweep_thresholds:
        probs_arr = np.array(all_probs)
        labels_arr = np.array(all_labels)
        rets_arr = np.array(all_rets)

        sweep_rows: list[dict] = []
        for thr in sweep_thresholds:
            pooled_t = _fold_metrics(probs_arr, labels_arr, rets_arr, thr)
            row = {"threshold": thr, "pooled": pooled_t, "folds": []}
            for fa in fold_arrays:
                fold_t = _fold_metrics(fa["prob"], fa["label"], fa["fwd_ret"], thr)
                row["folds"].append({
                    "fold": fa["fold"],
                    "test_start": fa["test_start"],
                    "test_end": fa["test_end"],
                    **fold_t,
                })
            sweep_rows.append(row)

        sweep_payload = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "primary_threshold": primary_threshold,
            "sweep_thresholds": list(sweep_thresholds),
            "meta_features": META_FEATURE_COLS,
            "n_folds_evaluated": len(fold_arrays),
            "sweep": sweep_rows,
            "notes": (
                "Threshold sweep — same dataset and same fitted per-fold "
                "models as the primary diagnostic; only the trade-threshold "
                "is varied to find the cutoff that best translates the "
                "secondary's ranking edge into hit-rate / return uplift."
            ),
        }
        SWEEP_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SWEEP_OUTPUT_PATH, "w") as fh:
            json.dump(sweep_payload, fh, indent=2, default=str)
        logger.info("Sweep written to %s", SWEEP_OUTPUT_PATH)

    result = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "primary_threshold": primary_threshold,
        "trade_threshold": trade_threshold,
        "min_positive_rate": MIN_POSITIVE_RATE,
        "label_def": "1 if fwd_ret_20d > 0 else 0",
        "n_folds_completed": len(fold_details),
        "meta_features": META_FEATURE_COLS,
        "excluded_factor_features": list(PRIMARY_FACTOR_COLS),
        "folds": fold_details,
        "aggregate_pooled": pooled,
        "notes": (
            "SIC-42 Experiment B: meta-labeling. Primary = 7-factor linear "
            "composite (unchanged, threshold=primary_threshold). Secondary = "
            "isotonic-calibrated LogisticRegression on non-factor features, "
            "binary label (fwd_ret_20d > 0). Walk-forward purged CV — same "
            "folds and purge logic as diagnostic.py. Uplift metrics compare "
            "the secondary-filtered subset (P >= trade_threshold) to the "
            "'always follow primary' baseline on the same test rows."
        ),
    }

    DIAG_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DIAG_OUTPUT_PATH, "w") as fh:
        json.dump(result, fh, indent=2, default=str)
    logger.info("Meta-labeler diagnostic written to %s", DIAG_OUTPUT_PATH)

    if sweep_payload is not None:
        result["sweep"] = sweep_payload

    return result


# ── Final-fit production training & predict ─────────────────────────────────
# Walk-forward CV in run_meta_diagnostic measures generalization. For live use
# we want a single model fit on ALL primary-BUY history — same pipeline shape
# as the per-fold models, just one global fit.

_model_cache: Optional[Pipeline] = None


def train_final(
    primary_threshold: float = PRIMARY_BUY_THRESHOLD,
) -> dict:
    """
    Fit the secondary pipeline on the full primary-BUY history and persist
    to META_MODEL_PATH. Used for live scoring; the diagnostic CV path
    (run_meta_diagnostic) is for evaluation only.

    Returns the metadata dict that's also written to META_METADATA_PATH.
    """
    logger.info(
        "Building dataset for final fit (primary_threshold=%.2f) …",
        primary_threshold,
    )
    X_all, meta_all = build_dataset_with_horizons(
        required_feature_cols=META_FEATURE_COLS,
    )

    fwd_ret = meta_all["fwd_ret_20d"].values
    linear_score = meta_all["linear_score"].values
    label_all = _make_label(fwd_ret)
    is_primary_buy = linear_score >= primary_threshold

    X_pb = X_all.loc[is_primary_buy, META_FEATURE_COLS]
    y_pb = label_all[is_primary_buy]
    n_total = int(is_primary_buy.sum())

    if n_total < MIN_TRAIN_AFTER_PURGE:
        raise RuntimeError(
            f"Final-fit dataset too small: {n_total} primary-BUY rows "
            f"(need ≥ {MIN_TRAIN_AFTER_PURGE})"
        )

    pos_rate = float(y_pb.mean())
    if pos_rate < MIN_POSITIVE_RATE or pos_rate > 1 - MIN_POSITIVE_RATE:
        raise RuntimeError(
            f"Final-fit class balance degenerate: pos_rate={pos_rate:.3f}"
        )

    logger.info(
        "Fitting on %d primary-BUY rows (pos_rate=%.3f, %d features)",
        n_total, pos_rate, len(META_FEATURE_COLS),
    )

    model = _build_secondary_pipeline()
    model.fit(X_pb, y_pb)

    META_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, META_MODEL_PATH)

    metadata = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "primary_threshold": primary_threshold,
        "trade_threshold": META_TRADE_THRESHOLD,
        "feature_cols": list(META_FEATURE_COLS),
        "n_train_rows": n_total,
        "train_pos_rate": round(pos_rate, 4),
        "n_total_rows_in_dataset": int(len(X_all)),
        "n_stocks": int(meta_all["symbol"].nunique()),
        "label_def": "1 if fwd_ret_20d > 0 else 0",
        "model_path": str(META_MODEL_PATH.relative_to(PROJECT_ROOT)),
        "notes": (
            "Final-fit meta-labeler. Predicts P(profitable @ 20d | primary BUY) "
            "for live scoring. Walk-forward OOS validation lives in "
            "data/meta_labeler_sweep.json — re-run --sweep before each retrain."
        ),
    }
    with open(META_METADATA_PATH, "w") as fh:
        json.dump(metadata, fh, indent=2, default=str)

    invalidate_cache()
    logger.info(
        "Final meta-labeler written to %s (n=%d, pos_rate=%.3f)",
        META_MODEL_PATH, n_total, pos_rate,
    )
    return metadata


def is_model_available() -> bool:
    return META_MODEL_PATH.exists()


def get_metadata() -> Optional[dict]:
    if not META_METADATA_PATH.exists():
        return None
    with open(META_METADATA_PATH) as fh:
        return json.load(fh)


def _load_model() -> Optional[Pipeline]:
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    if not META_MODEL_PATH.exists():
        return None
    logger.info("Loading meta-labeler from %s", META_MODEL_PATH)
    _model_cache = joblib.load(META_MODEL_PATH)
    return _model_cache


def invalidate_cache() -> None:
    """Reset the in-process model cache. Call after retraining."""
    global _model_cache
    _model_cache = None


def predict_proba(features: dict) -> Optional[float]:
    """
    Return calibrated P(profitable @ 20d) given a feature dict.

    Args:
        features: dict containing all META_FEATURE_COLS keys, with values
                  on the −1..+1 scale produced by the engine. Missing keys
                  fall through to the pipeline's SimpleImputer (median fill).

    Returns:
        Probability in [0, 1], or None if the model isn't trained or any
        of the 7 hard-required factor scores are NaN (in which case the
        primary itself shouldn't have produced a BUY anyway).
    """
    model = _load_model()
    if model is None:
        return None

    # Hard gate: the 7 factor scores are derived from raw OHLC and should
    # never be NaN if the primary said BUY. If they are, something's off
    # upstream — abstain rather than impute.
    HARD_REQUIRED = (
        "rsi", "macd", "trend_ma", "bollinger",
        "volume", "volatility", "relative_strength",
    )
    for col in HARD_REQUIRED:
        v = features.get(col)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            logger.info("predict_proba: hard-gate feature %s is NaN — abstain", col)
            return None

    row = {col: features.get(col, np.nan) for col in META_FEATURE_COLS}
    X = pd.DataFrame([row], columns=META_FEATURE_COLS)
    proba = model.predict_proba(X)[0]
    pos_idx = list(model.named_steps["clf"].classes_).index(1)
    return round(float(proba[pos_idx]), 4)


def should_take_trade(features: dict, threshold: float = META_TRADE_THRESHOLD) -> Optional[bool]:
    """Return True/False if model is available, None if not (caller falls back)."""
    p = predict_proba(features)
    if p is None:
        return None
    return p >= threshold


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--primary-threshold",
        type=float,
        default=PRIMARY_BUY_THRESHOLD,
        help=f"Primary linear_score BUY cutoff (default {PRIMARY_BUY_THRESHOLD})",
    )
    parser.add_argument(
        "--trade-threshold",
        type=float,
        default=META_TRADE_THRESHOLD,
        help=f"Secondary meta-probability trade cutoff (default {META_TRADE_THRESHOLD})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Override output path (default data/meta_labeler_diagnostic.json)",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help=f"Also evaluate at sweep thresholds (default {DEFAULT_SWEEP_THRESHOLDS}), "
             "writing data/meta_labeler_sweep.json and printing a comparison table.",
    )
    parser.add_argument(
        "--sweep-thresholds",
        type=str,
        default=None,
        help="Comma-separated list of thresholds to sweep (e.g. '0.50,0.55,0.60'). "
             "Implies --sweep.",
    )
    parser.add_argument(
        "--train-final",
        action="store_true",
        help="Skip diagnostic. Fit the final production model on the full "
             "primary-BUY history and persist to "
             f"{META_MODEL_PATH.relative_to(PROJECT_ROOT)}. Re-run --sweep first "
             "to verify the latest dataset still meets the production gate.",
    )
    args = parser.parse_args()

    if args.output:
        DIAG_OUTPUT_PATH = Path(args.output)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.train_final:
        meta = train_final(primary_threshold=args.primary_threshold)
        print("\n=== SIC-42 Meta-Labeler — Final Fit ===")
        print(f"  trained_at:       {meta['trained_at']}")
        print(f"  n_train_rows:     {meta['n_train_rows']}")
        print(f"  train_pos_rate:   {meta['train_pos_rate']}")
        print(f"  n_stocks:         {meta['n_stocks']}")
        print(f"  primary_thr:      {meta['primary_threshold']}")
        print(f"  trade_thr:        {meta['trade_threshold']} (production gate)")
        print(f"  features:         {len(meta['feature_cols'])} cols")
        print(f"  model_path:       {meta['model_path']}")
        raise SystemExit(0)

    sweep_thresholds = None
    if args.sweep_thresholds:
        sweep_thresholds = [float(t) for t in args.sweep_thresholds.split(",")]
    elif args.sweep:
        sweep_thresholds = list(DEFAULT_SWEEP_THRESHOLDS)

    r = run_meta_diagnostic(
        primary_threshold=args.primary_threshold,
        trade_threshold=args.trade_threshold,
        sweep_thresholds=sweep_thresholds,
    )

    print("\n=== SIC-42 Meta-Labeler — Walk-forward OOS ===")
    print(
        f"folds={r['n_folds_completed']}  "
        f"primary_thr={r['primary_threshold']}  "
        f"trade_thr={r['trade_threshold']}"
    )
    print(f"label={r['label_def']}   meta_features={len(r['meta_features'])}")

    print("\n  Per-fold:")
    for fd in r["folds"]:
        print(
            f"    fold {fd['fold']} {fd['test_start']}→{fd['test_end']}  "
            f"n_pb={fd['n_primary_buy']}  base_hit={fd['baseline_hit']}%  "
            f"filt_hit={fd['filtered_hit']}% "
            f"(@p>={r['trade_threshold']}, n_filt={fd['filtered_n']})  "
            f"rank_IC={fd['rank_ic_prob_vs_ret']}"
        )

    agg = r["aggregate_pooled"]
    print("\n  Pooled:")
    print(f"    n_primary_buy     {agg['n_primary_buy']}")
    print(f"    filtered_n        {agg['filtered_n']}")
    print(f"    baseline_hit      {agg['baseline_hit']}%")
    print(f"    precision@{r['trade_threshold']}    {agg['precision_at_threshold']}%")
    print(f"    hit_uplift_abs    {agg['hit_uplift_abs']} pp")
    print(f"    hit_uplift_rel    {agg['hit_uplift_rel']}%")
    print(f"    baseline_mean_ret {agg['baseline_mean_ret']}")
    print(f"    filtered_mean_ret {agg['filtered_mean_ret']}")
    print(f"    per_trade_sharpe  baseline={agg['per_trade_sharpe_baseline']}  "
          f"filtered={agg['per_trade_sharpe_filtered']}")
    print(f"    rank_IC           {agg['rank_ic_prob_vs_ret']}")

    if "sweep" in r:
        s = r["sweep"]
        print("\n=== Threshold sweep — pooled ===")
        print(f"    {'thr':>6} {'filt_n':>8} {'pass%':>7} {'base_hit':>9} "
              f"{'filt_hit':>9} {'uplift':>8} {'mean_ret_b':>11} {'mean_ret_f':>11} "
              f"{'sharpe_f':>9}")
        n_pb = s["sweep"][0]["pooled"]["n_primary_buy"] if s["sweep"] else 0
        for row in s["sweep"]:
            p = row["pooled"]
            pass_pct = (p["filtered_n"] / n_pb * 100) if n_pb else 0
            print(f"    {row['threshold']:>6.2f} {p['filtered_n']:>8} {pass_pct:>6.1f}% "
                  f"{p['baseline_hit']:>8.2f}% {p['filtered_hit']:>8.2f}% "
                  f"{p['hit_uplift_abs']:>+7.2f}pp {p['baseline_mean_ret']:>+11.5f} "
                  f"{p['filtered_mean_ret']:>+11.5f} "
                  f"{p['per_trade_sharpe_filtered']:>9}")

        print("\n=== Threshold sweep — per-fold filtered_hit ===")
        thr_list = [row["threshold"] for row in s["sweep"]]
        header = "    fold " + " ".join(f"{t:>7.2f}" for t in thr_list)
        print(header)
        # invert layout: rows = folds, cols = thresholds
        n_folds = len(s["sweep"][0]["folds"]) if s["sweep"] else 0
        for fi in range(n_folds):
            fd0 = s["sweep"][0]["folds"][fi]
            row_cells = [f"    {fd0['fold']:>4d}"]
            for row in s["sweep"]:
                fd = row["folds"][fi]
                fh = fd.get("filtered_hit")
                row_cells.append(f"{fh:>7.2f}" if fh is not None else f"{'·':>7}")
            print(" ".join(row_cells))
