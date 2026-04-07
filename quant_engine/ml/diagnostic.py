"""
Multi-horizon historical IC diagnostic for the Sicilian ML model.

Problem
-------
The live signal_quality tracker has only ~470 settled 1-day observations.
IC standard error at that N is ~1/sqrt(470) ≈ 0.046, so an observed IC of
0.03 is statistically indistinguishable from zero. That is not enough
evidence to justify redesigning the model (e.g. retraining at a shorter
horizon).

Solution
--------
Use the ~10 years of price history already in the DB to run a walk-forward
purged cross-validation and measure IC / hit rate at 1d, 5d, 10d, 20d
horizons across thousands of out-of-sample observations. This gives
statistically meaningful numbers without waiting weeks for live signals to
settle.

Method (Lopez de Prado, AFML Ch.10 — see wiki/papers/lopez_de_prado_afml_2018.md)
---------------------------------------------------------------------------------
1. Build the same dataset `trainer.py` builds (same FEATURE_COLS, same 20d
   label, same valid-mask rules). Additionally retain fwd_ret_1d, 5d, 10d,
   20d per row for multi-horizon evaluation.
2. Walk-forward TimeSeriesSplit with n_splits=5.
3. Before training each fold, PURGE training rows whose 20-day label
   horizon overlaps the test fold start — otherwise the label uses prices
   that leak into the test period (the standard trainer CV does not purge,
   so its reported CV accuracy is slightly optimistic).
4. Train RF with production `RF_PARAMS` (no hyperparameter search — we are
   measuring the actual production model, not searching for a new one).
5. On the test fold, compute a signed score = P(BUY) - P(SELL) and measure:
     - Cross-sectional Spearman IC per date (Grinold-Kahn / signal_quality
       style), averaged across dates → ICIR = mean/std
     - Pooled Pearson + Spearman correlations for extra robustness
     - Hit rate of sign(signed_score) vs sign(fwd_ret)
   at each of the 4 forward horizons.
6. Aggregate fold means → one set of metrics per horizon.
7. Write results to `data/ml_diagnostic.json`.

This is a DIAGNOSTIC: it does NOT touch the live model, `trainer.py`, the
`predict()` path, or `sicilian_rf.pkl`. Re-running it does not retrain the
live model — it only measures how the current model generalises at
different horizons.

Run:
    python -m quant_engine.ml.diagnostic

See also:
    wiki/concepts/ml_pipeline.md   — pipeline context and gaps
    wiki/papers/grinold_kahn_active_portfolio.md — IC / ICIR framework
    wiki/papers/lopez_de_prado_afml_2018.md      — purged CV (Ch.10)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

from quant_engine.config import PROJECT_ROOT
from quant_engine.data.delivery_loader import load_delivery_series
from quant_engine.data.loader import (
    load_all_symbols,
    load_benchmark,
    load_industry_map,
    load_price_history,
)
from quant_engine.data.market_regime_loader import (
    _flow_to_score,
    build_markov_score_series,
    load_fii_flow_series,
    load_fii_fo_series,
    load_vix_series,
    vix_to_score,
)
from quant_engine.ml.trainer import (
    BUY_RETURN_THRESHOLD,
    FEATURE_COLS,
    MIN_BARS,
    RF_PARAMS,
    SELL_RETURN_THRESHOLD,
    _build_feature_frame,
    _build_nifty_trend_series,
    _build_sector_series,
)

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
FWD_HORIZONS = [1, 5, 10, 20]   # trading days
LABEL_HORIZON_DAYS = 20         # must match trainer.py label horizon
N_SPLITS = 5                    # walk-forward folds
MIN_TEST_OBS = 50               # skip horizon if fewer settled obs in a fold
MIN_TRAIN_AFTER_PURGE = 200     # skip fold if purged training set too small

DIAG_OUTPUT_PATH = PROJECT_ROOT / "data" / "ml_diagnostic.json"


# ── Dataset build ────────────────────────────────────────────────────────────
def build_dataset_with_horizons() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build (X, meta) where:
      X    — FEATURE_COLS feature matrix, date-sorted (same as trainer.py).
      meta — parallel DataFrame aligned row-for-row with X, containing:
               symbol, label, fwd_ret_1d, fwd_ret_5d, fwd_ret_10d, fwd_ret_20d

    The row alignment between X and meta is positional (iloc). Both are
    sorted by date ascending via mergesort (stable) — same order that
    trainer.py produces.

    Label is computed identically to trainer.py:
      BUY  ( 1): 20d fwd ret >  BUY_RETURN_THRESHOLD
      SELL (-1): 20d fwd ret <  SELL_RETURN_THRESHOLD
      HOLD ( 0): otherwise
    """
    symbols = load_all_symbols()
    benchmark_df = load_benchmark(limit=2000)
    industry_map = load_industry_map()

    logger.info("Loading price histories for %d symbols …", len(symbols))
    all_prices: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = load_price_history(sym, limit=2000)
            if len(df) >= MIN_BARS:
                all_prices[sym] = df
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping %s: %s", sym, exc)

    # Shared market-wide series — reuse trainer helpers so the dataset is
    # byte-for-byte identical to what production training would produce.
    sector_series = _build_sector_series(all_prices, industry_map, benchmark_df)

    raw_vix = load_vix_series(limit=2000)
    vix_score_series = vix_to_score(raw_vix) if not raw_vix.empty else pd.Series(dtype=float)
    nifty_trend_series = _build_nifty_trend_series(benchmark_df)
    markov_score_series = build_markov_score_series(benchmark_df)

    raw_fii_flow = load_fii_flow_series(limit=2000)
    if not raw_fii_flow.empty:
        fii_flow_score_series = _flow_to_score(raw_fii_flow.rolling(10).sum())
    else:
        fii_flow_score_series = pd.Series(dtype=float)

    raw_fii_fo = load_fii_fo_series(limit=2000)
    fii_fo_score_series = (
        _flow_to_score(raw_fii_fo) if not raw_fii_fo.empty else pd.Series(dtype=float)
    )

    all_X: list[pd.DataFrame] = []
    all_meta: list[pd.DataFrame] = []

    for symbol, df in all_prices.items():
        try:
            industry = industry_map.get(symbol)
            sector_score = sector_series.get(industry, pd.Series(dtype=float))

            # delivery_score: rolling z-score of delivery_pct vs 60-day mean,
            # clipped to [-1, +1] — identical to trainer.py.
            delivery_df = load_delivery_series(symbol, limit=2000)
            if not delivery_df.empty and "delivery_pct" in delivery_df.columns:
                delivery_pct = delivery_df["delivery_pct"].reindex(df.index)
                roll_mean = delivery_pct.rolling(60, min_periods=10).mean()
                roll_std = delivery_pct.rolling(60, min_periods=10).std().replace(0, 1)
                delivery_score_series = (
                    (delivery_pct - roll_mean) / roll_std
                ).clip(-3, 3) / 3
            else:
                delivery_score_series = pd.Series(dtype=float)

            features = _build_feature_frame(
                df,
                benchmark_df,
                sector_score,
                vix_score_series,
                nifty_trend_series,
                markov_score_series,
                delivery_score_series,
                fii_flow_score_series,
                fii_fo_score_series,
            )

            # Multi-horizon forward returns (no look-ahead: we shift backward).
            close = df["close"]
            fwd_rets = pd.DataFrame(
                {f"fwd_ret_{h}d": close.shift(-h) / close - 1 for h in FWD_HORIZONS},
                index=df.index,
            )

            # Valid-mask rule: match trainer.py — require all features + the
            # 20d label to be non-null. 20d is the longest horizon, so the
            # shorter horizons are automatically non-null for these rows.
            label_fwd = fwd_rets["fwd_ret_20d"]
            valid_mask = features.notna().all(axis=1) & label_fwd.notna()
            features = features[valid_mask]
            fwd_rets = fwd_rets[valid_mask]

            if len(features) < 60:
                continue

            label = pd.Series(0, index=fwd_rets.index, dtype=int)
            label[fwd_rets["fwd_ret_20d"] > BUY_RETURN_THRESHOLD] = 1
            label[fwd_rets["fwd_ret_20d"] < SELL_RETURN_THRESHOLD] = -1

            meta = fwd_rets.copy()
            meta["symbol"] = symbol
            meta["label"] = label

            all_X.append(features)
            all_meta.append(meta)

        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping %s during feature build: %s", symbol, exc)

    if not all_X:
        raise RuntimeError("No data available — is the DB populated?")

    X = pd.concat(all_X)
    meta = pd.concat(all_meta)

    # Stable sort by date. iloc[argsort] matches trainer.py's ordering.
    order = X.index.argsort(kind="mergesort")
    X = X.iloc[order]
    meta = meta.iloc[order]

    logger.info(
        "Diagnostic dataset: %d rows, %d features, %d horizons, %d stocks",
        len(X),
        len(FEATURE_COLS),
        len(FWD_HORIZONS),
        meta["symbol"].nunique(),
    )
    return X, meta


# ── Purging ──────────────────────────────────────────────────────────────────
def _purge_train_indices(
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    dates: pd.DatetimeIndex,
    label_horizon_days: int,
) -> np.ndarray:
    """
    Purge training indices whose label horizon overlaps the test fold start.

    For a walk-forward split (train strictly before test), this means dropping
    training rows whose date is within `label_horizon_days` unique trading
    days of the first test date — because their 20d label uses prices that
    fall inside the test fold.

    Reference: Lopez de Prado AFML Ch.10.
    """
    if len(test_idx) == 0 or len(train_idx) == 0:
        return train_idx

    test_start_date = dates[test_idx[0]]
    unique_sorted_dates = sorted(set(dates))
    try:
        test_start_pos = unique_sorted_dates.index(test_start_date)
    except ValueError:
        return train_idx

    purge_pos = max(0, test_start_pos - label_horizon_days)
    purge_threshold_date = unique_sorted_dates[purge_pos]

    train_dates = dates[train_idx]
    keep = train_dates < purge_threshold_date
    return train_idx[np.asarray(keep)]


# ── Metric computation ───────────────────────────────────────────────────────
def _horizon_metrics(
    signed_score: np.ndarray,
    fwd_ret: np.ndarray,
    dates: pd.DatetimeIndex,
) -> dict:
    """
    Compute IC metrics for one horizon on one fold (or pooled across folds).

    Returns:
      mean_cs_ic       — mean cross-sectional Spearman IC (Grinold-Kahn)
      std_cs_ic        — std of per-date ICs
      icir             — mean_cs_ic / std_cs_ic (consistency of edge)
      pooled_pearson   — Pearson correlation on all pooled observations
      pooled_spearman  — Spearman correlation on all pooled observations
      hit_rate         — % of signed-score directions matching fwd_ret sign
      n_obs            — number of non-NaN test observations
      n_dates          — number of distinct dates contributing to cs-IC
    """
    signed_score = np.asarray(signed_score, dtype=float)
    fwd_ret = np.asarray(fwd_ret, dtype=float)

    mask = ~np.isnan(signed_score) & ~np.isnan(fwd_ret)
    n_obs = int(mask.sum())

    base = {
        "mean_cs_ic": None,
        "std_cs_ic": None,
        "icir": None,
        "pooled_pearson": None,
        "pooled_spearman": None,
        "hit_rate": None,
        "n_obs": n_obs,
        "n_dates": 0,
    }
    if n_obs < MIN_TEST_OBS:
        return base

    s = signed_score[mask]
    f = fwd_ret[mask]
    ds = dates[mask]

    # Pooled correlations
    try:
        pearson_ic = float(pearsonr(s, f)[0])
    except Exception:  # noqa: BLE001
        pearson_ic = float("nan")
    try:
        spearman_ic = float(spearmanr(s, f)[0])
    except Exception:  # noqa: BLE001
        spearman_ic = float("nan")

    # Cross-sectional Spearman IC per date, averaged
    ics: list[float] = []
    df_grp = pd.DataFrame({"score": s, "fwd": f, "date": ds})
    for _, grp in df_grp.groupby("date"):
        if len(grp) < 3:
            continue
        try:
            ic = spearmanr(grp["score"], grp["fwd"])[0]
        except Exception:  # noqa: BLE001
            continue
        if ic is not None and not np.isnan(ic):
            ics.append(float(ic))

    mean_cs_ic = float(np.mean(ics)) if ics else None
    std_cs_ic = float(np.std(ics)) if ics else None
    icir = (
        mean_cs_ic / std_cs_ic
        if (mean_cs_ic is not None and std_cs_ic is not None and std_cs_ic > 1e-9)
        else None
    )

    # Hit rate on directional predictions (ignore exact-zero sign ties)
    pred_dir = np.sign(s)
    actual_dir = np.sign(f)
    nonzero = (pred_dir != 0) & (actual_dir != 0)
    if int(nonzero.sum()) > 0:
        hit_rate = float((pred_dir[nonzero] == actual_dir[nonzero]).mean() * 100.0)
    else:
        hit_rate = None

    return {
        "mean_cs_ic": round(mean_cs_ic, 4) if mean_cs_ic is not None else None,
        "std_cs_ic": round(std_cs_ic, 4) if std_cs_ic is not None else None,
        "icir": round(icir, 3) if icir is not None else None,
        "pooled_pearson": round(pearson_ic, 4) if not np.isnan(pearson_ic) else None,
        "pooled_spearman": round(spearman_ic, 4) if not np.isnan(spearman_ic) else None,
        "hit_rate": round(hit_rate, 1) if hit_rate is not None else None,
        "n_obs": n_obs,
        "n_dates": len(ics),
    }


# ── Main diagnostic run ──────────────────────────────────────────────────────
def run_diagnostic() -> dict:
    """
    Run the full walk-forward purged CV diagnostic and return results.

    Writes results to DIAG_OUTPUT_PATH as JSON so the FastAPI endpoint can
    serve cached results without re-running the (slow) walk-forward on
    every request.
    """
    logger.info("Building diagnostic dataset …")
    X, meta = build_dataset_with_horizons()

    dates = pd.DatetimeIndex(X.index)
    y = meta["label"].values

    logger.info(
        "Walk-forward CV: %d splits, label horizon=%d, purge=%d days",
        N_SPLITS,
        LABEL_HORIZON_DAYS,
        LABEL_HORIZON_DAYS,
    )

    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    fold_details: list[dict] = []

    # Collect all test predictions for pooled across-fold aggregation.
    all_scores: dict[int, list[float]] = {h: [] for h in FWD_HORIZONS}
    all_fwds: dict[int, list[float]] = {h: [] for h in FWD_HORIZONS}
    all_dates: dict[int, list[pd.Timestamp]] = {h: [] for h in FWD_HORIZONS}

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X), 1):
        train_idx_purged = _purge_train_indices(
            np.asarray(train_idx), np.asarray(test_idx), dates, LABEL_HORIZON_DAYS
        )

        if len(train_idx_purged) < MIN_TRAIN_AFTER_PURGE:
            logger.warning(
                "Fold %d: purged training set too small (%d < %d) — skipping",
                fold,
                len(train_idx_purged),
                MIN_TRAIN_AFTER_PURGE,
            )
            continue

        logger.info(
            "Fold %d: train %d → %d (purged), test %d",
            fold,
            len(train_idx),
            len(train_idx_purged),
            len(test_idx),
        )

        X_train = X.iloc[train_idx_purged]
        y_train = y[train_idx_purged]
        X_test = X.iloc[test_idx]
        test_meta = meta.iloc[test_idx]

        clf = RandomForestClassifier(**RF_PARAMS)
        clf.fit(X_train, y_train)

        classes = list(clf.classes_)
        if 1 not in classes or -1 not in classes:
            logger.warning("Fold %d: missing BUY or SELL class in training — skipping", fold)
            continue

        proba = clf.predict_proba(X_test)
        buy_i = classes.index(1)
        sell_i = classes.index(-1)
        signed_score = proba[:, buy_i] - proba[:, sell_i]

        test_dates = dates[test_idx]

        fold_entry: dict = {
            "fold": fold,
            "train_size": int(len(train_idx_purged)),
            "test_size": int(len(test_idx)),
            "train_start": str(pd.Timestamp(dates[train_idx_purged[0]]).date()),
            "train_end": str(pd.Timestamp(dates[train_idx_purged[-1]]).date()),
            "test_start": str(pd.Timestamp(test_dates[0]).date()),
            "test_end": str(pd.Timestamp(test_dates[-1]).date()),
            "horizons": {},
        }

        for h in FWD_HORIZONS:
            fwd = test_meta[f"fwd_ret_{h}d"].values
            metrics = _horizon_metrics(signed_score, fwd, test_dates)
            fold_entry["horizons"][f"{h}d"] = metrics

            # Accumulate for pooled across-fold metric.
            mask = ~np.isnan(signed_score) & ~np.isnan(fwd)
            all_scores[h].extend(signed_score[mask].tolist())
            all_fwds[h].extend(fwd[mask].tolist())
            all_dates[h].extend(test_dates[mask].tolist())

        fold_details.append(fold_entry)

    # Aggregate across all folds (pooled metric — tighter SE than fold means).
    aggregate: dict[str, Optional[dict]] = {}
    for h in FWD_HORIZONS:
        if not all_scores[h]:
            aggregate[f"{h}d"] = None
            continue
        aggregate[f"{h}d"] = _horizon_metrics(
            np.array(all_scores[h]),
            np.array(all_fwds[h]),
            pd.DatetimeIndex(all_dates[h]),
        )

    # Mean-of-fold-means (alternative aggregate, useful for fold variance view)
    fold_means: dict[str, Optional[dict]] = {}
    for h in FWD_HORIZONS:
        key = f"{h}d"
        ic_list = [
            fd["horizons"][key]["mean_cs_ic"]
            for fd in fold_details
            if fd["horizons"].get(key, {}).get("mean_cs_ic") is not None
        ]
        hit_list = [
            fd["horizons"][key]["hit_rate"]
            for fd in fold_details
            if fd["horizons"].get(key, {}).get("hit_rate") is not None
        ]
        if ic_list:
            fold_means[key] = {
                "mean_of_fold_ics": round(float(np.mean(ic_list)), 4),
                "std_of_fold_ics": round(float(np.std(ic_list)), 4),
                "mean_of_fold_hits": round(float(np.mean(hit_list)), 1) if hit_list else None,
                "n_folds_with_data": len(ic_list),
            }
        else:
            fold_means[key] = None

    result = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "n_samples_total": int(len(X)),
        "n_folds_completed": len(fold_details),
        "label_horizon_days": LABEL_HORIZON_DAYS,
        "label_thresholds": {
            "buy": BUY_RETURN_THRESHOLD,
            "sell": SELL_RETURN_THRESHOLD,
        },
        "purge_days": LABEL_HORIZON_DAYS,
        "rf_params": RF_PARAMS,
        "feature_cols": FEATURE_COLS,
        "folds": fold_details,
        "aggregate_pooled": aggregate,
        "aggregate_fold_means": fold_means,
        "notes": (
            "Walk-forward TimeSeriesSplit with label-horizon purging "
            "(Lopez de Prado AFML Ch.10). Hyperparameters are fixed at the "
            "production RF_PARAMS — no re-tuning, so this measures the "
            "actual production model. Cross-sectional IC is Spearman per "
            "date averaged across dates (Grinold-Kahn). Signed score = "
            "P(BUY) - P(SELL). Unlike trainer.py's CV, this version purges "
            "labels that leak into the test fold — so the IC here is a "
            "cleaner out-of-sample number than the reported CV accuracy."
        ),
    }

    DIAG_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DIAG_OUTPUT_PATH, "w") as fh:
        json.dump(result, fh, indent=2, default=str)
    logger.info("Diagnostic written to %s", DIAG_OUTPUT_PATH)

    return result


def load_last_result() -> Optional[dict]:
    """Read the cached diagnostic result from disk, if any."""
    if not DIAG_OUTPUT_PATH.exists():
        return None
    try:
        with open(DIAG_OUTPUT_PATH) as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load diagnostic JSON: %s", exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    result = run_diagnostic()

    print("\n=== Sicilian ML Diagnostic — Pooled Aggregate ===")
    print(f"samples={result['n_samples_total']}  folds={result['n_folds_completed']}")
    for h, metrics in result["aggregate_pooled"].items():
        if metrics is None:
            print(f"  {h}: no data")
            continue
        print(
            f"  {h}:  cs_IC={metrics['mean_cs_ic']}  "
            f"ICIR={metrics['icir']}  "
            f"hit={metrics['hit_rate']}%  "
            f"n={metrics['n_obs']}  dates={metrics['n_dates']}"
        )
