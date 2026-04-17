"""
Backfill signals_log with both ML (walk-forward OOS) and linear composite
scores for every historical bar, so the Signal Quality live tracker has
real sample size instead of a log polluted with linear-only legacy rows.

Why walk-forward: using the live trained model to score historical bars
leaks labels into predictions (the model was trained on later data). To
produce honest out-of-sample ML confidences, we reproduce diagnostic.py's
purged TimeSeriesSplit — a model trained only on data strictly before
each fold's test window predicts that fold. Rows earlier than the first
test fold have no OOS ML prediction and receive linear-only rows.

Linear composite uses the current production FACTOR_WEIGHTS (bollinger
replaces mean_reversion; RSI is trend-confirming).

Row semantics written to signals_log (matches live engine):
  signal          — ML verdict (LONG/HOLD/SHORT) when ML OOS exists,
                    else linear signal
  composite_score — linear composite scaled to [-100, +100]
  ml_confidence   — winning-class probability × 100, or NULL
  linear_signal   — LONG/HOLD/SHORT from linear composite ±40 threshold
"""
import logging
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

from quant_engine.config import FACTOR_WEIGHTS, LONG_THRESHOLD, SHORT_THRESHOLD
from quant_engine.data.loader import load_benchmark, load_price_history
from quant_engine.data.turso_client import connect
from quant_engine.ml.diagnostic import (
    LABEL_HORIZON_DAYS,
    MIN_TRAIN_AFTER_PURGE,
    N_SPLITS,
    _purge_train_indices,
    build_dataset_with_horizons,
)
from quant_engine.ml.trainer import RF_PARAMS
from quant_engine.strategies.sicilian_strategy import SicilianStrategy

logger = logging.getLogger(__name__)

SKIP_SYMBOLS = {"^BSESN", "^NSEI"}

VERDICT_MAP = {1: "LONG", 0: "HOLD", -1: "SHORT"}


def _linear_signal_label(score_pct: float) -> str:
    if score_pct >= LONG_THRESHOLD:
        return "LONG"
    if score_pct <= SHORT_THRESHOLD:
        return "SHORT"
    return "HOLD"


def run_walk_forward_ml(X: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    """Produce OOS ML predictions for every row that falls in a test fold.

    Each fold trains a fresh RF on purged training rows and predicts only
    the test rows, so predictions are lookahead-free.
    """
    dates = pd.DatetimeIndex(X.index)
    y = meta["label"].values

    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    frames = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X), 1):
        train_idx_purged = _purge_train_indices(
            np.asarray(train_idx), np.asarray(test_idx), dates, LABEL_HORIZON_DAYS
        )
        if len(train_idx_purged) < MIN_TRAIN_AFTER_PURGE:
            logger.warning("Fold %d: purged train too small (%d), skipping",
                           fold, len(train_idx_purged))
            continue

        clf = RandomForestClassifier(**RF_PARAMS)
        clf.fit(X.iloc[train_idx_purged], y[train_idx_purged])

        classes = np.array(clf.classes_)
        proba = clf.predict_proba(X.iloc[test_idx])

        winning_col = np.argmax(proba, axis=1)
        winning_class = classes[winning_col]
        confidence = proba[np.arange(len(proba)), winning_col] * 100.0

        test_meta = meta.iloc[test_idx]
        frames.append(pd.DataFrame({
            "symbol":        test_meta["symbol"].values,
            "signal_date":   dates[test_idx],
            "ml_verdict":    [VERDICT_MAP[int(c)] for c in winning_class],
            "ml_confidence": confidence.astype(float),
        }))

        logger.info("Fold %d: train=%d test=%d", fold,
                    len(train_idx_purged), len(test_idx))

    if not frames:
        return pd.DataFrame(columns=["symbol", "signal_date",
                                     "ml_verdict", "ml_confidence"])
    return pd.concat(frames, ignore_index=True)


def compute_linear_per_symbol(symbol: str,
                              benchmark_df: pd.DataFrame,
                              weights: dict) -> pd.DataFrame:
    """Per-bar linear composite (-100..+100) + LONG/HOLD/SHORT label."""
    df = load_price_history(symbol, limit=3000)
    if df.empty or len(df) < 30:
        return pd.DataFrame()

    strat = SicilianStrategy("_backfill")
    close = df["close"]
    volume = df["volume"]

    factor_scores = {
        "momentum":          strat._rolling_momentum_score(close),
        "bollinger":         strat._rolling_bollinger_score(close),
        "rsi":               strat._rolling_rsi_score(close),
        "macd":              strat._rolling_macd_score(close),
        "volatility":        strat._rolling_volatility_score(close),
        "volume":            strat._rolling_volume_score(close, volume),
        "relative_strength": strat._rolling_relative_strength_score(close, benchmark_df),
    }

    composite = sum(
        weights[name] * scores for name, scores in factor_scores.items()
    ).clip(-1.0, 1.0)
    scaled = composite * 100.0

    labels = scaled.apply(_linear_signal_label)
    out = pd.DataFrame({
        "symbol":        symbol,
        "signal_date":   df.index,
        "composite":     scaled.values,
        "linear_signal": labels.values,
    })
    return out.dropna(subset=["composite"])


UPSERT_SQL = """
INSERT INTO signals_log (
    signal_date, symbol, signal, composite_score,
    ml_confidence, linear_signal, recorded_at
) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
ON CONFLICT(signal_date, symbol) DO UPDATE SET
    signal          = excluded.signal,
    composite_score = excluded.composite_score,
    ml_confidence   = excluded.ml_confidence,
    linear_signal   = excluded.linear_signal,
    recorded_at     = excluded.recorded_at
"""

DELETE_POLLUTED_SQL = (
    "DELETE FROM signals_log WHERE ml_confidence IS NULL AND linear_signal IS NULL"
)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    logger.info("Building diagnostic dataset (features + labels + horizons) ...")
    X, meta = build_dataset_with_horizons()
    logger.info("Dataset: %d rows, %d symbols",
                len(X), meta["symbol"].nunique())

    logger.info("Running walk-forward ML predictions ...")
    ml_df = run_walk_forward_ml(X, meta)
    ml_df["signal_date"] = pd.to_datetime(ml_df["signal_date"]).dt.strftime("%Y-%m-%d")
    logger.info("ML OOS rows: %d", len(ml_df))

    logger.info("Computing linear composite per symbol ...")
    benchmark_df = load_benchmark(limit=3000)
    symbols = [s for s in meta["symbol"].unique() if s not in SKIP_SYMBOLS]
    linear_frames = []
    for sym in symbols:
        lf = compute_linear_per_symbol(sym, benchmark_df, FACTOR_WEIGHTS)
        if not lf.empty:
            linear_frames.append(lf)
    if not linear_frames:
        raise RuntimeError("No linear composite rows computed — empty DB?")
    linear_df = pd.concat(linear_frames, ignore_index=True)
    linear_df["signal_date"] = pd.to_datetime(linear_df["signal_date"]).dt.strftime("%Y-%m-%d")
    logger.info("Linear rows: %d", len(linear_df))

    merged = linear_df.merge(ml_df, on=["symbol", "signal_date"], how="left")
    logger.info("Merged rows: %d  (with ML: %d)",
                len(merged), int(merged["ml_confidence"].notna().sum()))

    params = []
    for row in merged.itertuples(index=False):
        if pd.notna(row.ml_verdict):
            signal = row.ml_verdict
            ml_conf = float(row.ml_confidence)
        else:
            signal = row.linear_signal
            ml_conf = None
        params.append((
            row.signal_date,
            row.symbol,
            signal,
            float(row.composite),
            ml_conf,
            row.linear_signal,
        ))

    conn = connect()
    cur = conn.cursor()

    logger.info("Deleting polluted legacy rows (both ML+linear NULL) ...")
    cur.execute(DELETE_POLLUTED_SQL)

    logger.info("Upserting %d rows in chunks ...", len(params))
    CHUNK = 200  # smaller chunks → smaller HTTP payloads, fewer resets
    for i in range(0, len(params), CHUNK):
        batch = params[i:i + CHUNK]
        for attempt in range(5):
            try:
                cur.executemany(UPSERT_SQL, batch)
                break
            except Exception as exc:
                wait = 2 ** attempt
                logger.warning("chunk %d attempt %d failed (%s); retrying in %ds",
                               i, attempt + 1, exc.__class__.__name__, wait)
                time.sleep(wait)
                # reconnect — libsql HTTP session may be dead
                try:
                    conn.close()
                except Exception:
                    pass
                conn = connect()
                cur = conn.cursor()
        else:
            raise RuntimeError(f"chunk starting at {i} failed after 5 retries")
        if i and (i // CHUNK) % 40 == 0:
            logger.info("  ... %d / %d", i, len(params))

    if hasattr(conn, "commit"):
        conn.commit()

    cur.execute(
        "SELECT COUNT(*), COUNT(ml_confidence), COUNT(linear_signal) FROM signals_log"
    )
    total, ml_n, lin_n = cur.fetchone()
    logger.info("signals_log final: total=%d  with_ml=%d  with_linear=%d",
                total, ml_n, lin_n)


if __name__ == "__main__":
    main()
