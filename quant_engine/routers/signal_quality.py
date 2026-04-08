"""
Signal Quality Router — forward IC, ICIR, and hit rate per horizon.

Reads signals_log joined to price_history and computes:
  - Rank IC (Spearman): cross-sectional correlation between composite_score
    and forward return, computed per date then averaged across dates.
  - ICIR: mean_IC / std_IC — measures consistency of the signal edge.
  - Hit rate: % of directional (LONG/SHORT) calls that were correct.

Horizons evaluated: 1d, 5d, 10d, 20d trading days.
"""
import logging
import math

import numpy as np
import pandas as pd
from fastapi import APIRouter
from scipy.stats import spearmanr

from quant_engine.data.turso_client import connect


def _clean(v):
    """Replace NaN/Inf floats with None so FastAPI can JSON-serialise them."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _clean_dict(d: dict) -> dict:
    return {k: _clean(v) for k, v in d.items()}


def _clean_records(records: list) -> list:
    return [_clean_dict(r) for r in records]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quant", tags=["signal-quality"])

_HORIZONS = [
    {"days": 1,  "col": "fwd_ret_1d",  "offset": 0},
    {"days": 5,  "col": "fwd_ret_5d",  "offset": 4},
    {"days": 10, "col": "fwd_ret_10d", "offset": 9},
    {"days": 20, "col": "fwd_ret_20d", "offset": 19},
]

_FETCH_SQL = """
    SELECT
        sl.signal_date,
        sl.symbol,
        sl.signal,
        sl.linear_signal,
        sl.ml_confidence,
        sl.composite_score,
        entry.close   AS entry_price,
        entry.date    AS entry_bar_date,
        exit1.close   AS exit_price_1d,
        exit5.close   AS exit_price_5d,
        exit10.close  AS exit_price_10d,
        exit20.close  AS exit_price_20d
    FROM signals_log sl
    LEFT JOIN price_history entry
        ON entry.symbol = sl.symbol
        AND entry.date = (
            SELECT date FROM price_history
            WHERE symbol = sl.symbol AND date <= sl.signal_date
            ORDER BY date DESC LIMIT 1
        )
    LEFT JOIN price_history exit1
        ON exit1.symbol = sl.symbol
        AND exit1.date = (
            SELECT date FROM price_history
            WHERE symbol = sl.symbol AND date > sl.signal_date
            ORDER BY date ASC LIMIT 1 OFFSET 0
        )
    LEFT JOIN price_history exit5
        ON exit5.symbol = sl.symbol
        AND exit5.date = (
            SELECT date FROM price_history
            WHERE symbol = sl.symbol AND date > sl.signal_date
            ORDER BY date ASC LIMIT 1 OFFSET 4
        )
    LEFT JOIN price_history exit10
        ON exit10.symbol = sl.symbol
        AND exit10.date = (
            SELECT date FROM price_history
            WHERE symbol = sl.symbol AND date > sl.signal_date
            ORDER BY date ASC LIMIT 1 OFFSET 9
        )
    LEFT JOIN price_history exit20
        ON exit20.symbol = sl.symbol
        AND exit20.date = (
            SELECT date FROM price_history
            WHERE symbol = sl.symbol AND date > sl.signal_date
            ORDER BY date ASC LIMIT 1 OFFSET 19
        )
    ORDER BY sl.signal_date DESC
    LIMIT ?
"""


def _fetch_signals(limit: int) -> pd.DataFrame:
    conn = connect()
    df = pd.read_sql_query(_FETCH_SQL, conn, params=(limit,))
    conn.close()

    for h in _HORIZONS:
        exit_col = f"exit_price_{h['days']}d"
        df[h["col"]] = (df[exit_col] - df["entry_price"]) / df["entry_price"] * 100

    # Signed ML confidence: LONG=+confidence, SHORT=-confidence, HOLD=0
    ml_dir = df["signal"].map({"LONG": 1.0, "SHORT": -1.0, "HOLD": 0.0}).fillna(0.0)
    df["signed_confidence"] = df["ml_confidence"].fillna(50.0) * ml_dir

    # Signed linear score: composite_score already carries direction (-100 to +100)
    df["signed_linear"] = df["composite_score"].fillna(0.0)

    return df


def _horizon_stats(df: pd.DataFrame, fwd_col: str, days: int) -> dict:
    """IC, ICIR, hit_rate for one forward horizon."""
    settled = df.dropna(subset=["signed_confidence", fwd_col])
    n_obs = len(settled)

    base = {"days": days, "mean_ic": None, "icir": None, "hit_rate": None, "n_obs": n_obs}
    if n_obs < 10:
        return base

    # Cross-sectional rank IC per date
    ics = []
    for _, grp in settled.groupby("signal_date"):
        if len(grp) < 3:
            continue
        ic, _ = spearmanr(grp["signed_confidence"], grp[fwd_col])
        if not np.isnan(ic):
            ics.append(float(ic))

    if not ics:
        return base

    ic_arr  = np.array(ics)
    mean_ic = float(np.mean(ic_arr))
    std_ic  = float(np.std(ic_arr))
    icir    = round(mean_ic / std_ic, 3) if std_ic > 1e-9 else 0.0

    # Hit rate on directional signals only
    directional = settled[settled["signal"].isin(["LONG", "SHORT"])]
    if len(directional) > 0:
        wins = (
            ((directional["signal"] == "LONG")  & (directional[fwd_col] > 0)) |
            ((directional["signal"] == "SHORT") & (directional[fwd_col] < 0))
        )
        hit_rate = round(float(wins.sum() / len(directional) * 100), 1)
    else:
        hit_rate = None

    return _clean_dict({
        "days":     days,
        "mean_ic":  round(mean_ic, 4),
        "icir":     round(icir, 3),
        "hit_rate": hit_rate,
        "n_obs":    n_obs,
    })


def _engine_horizons(df: pd.DataFrame, score_col: str, signal_col: str) -> list:
    """Compute horizon stats using a specific engine's score and signal columns."""
    df_eng = df.copy()
    # Recompute hit rate using this engine's signal direction
    df_eng["_signal"] = df_eng[signal_col]
    results = []
    for h in _HORIZONS:
        fwd_col = h["col"]
        settled = df_eng.dropna(subset=[score_col, fwd_col])
        n_obs = len(settled)
        base = {"days": h["days"], "mean_ic": None, "icir": None, "hit_rate": None, "n_obs": n_obs}
        if n_obs < 10:
            results.append(base)
            continue
        ics = []
        for _, grp in settled.groupby("signal_date"):
            if len(grp) < 3:
                continue
            ic, _ = spearmanr(grp[score_col], grp[fwd_col])
            if not np.isnan(ic):
                ics.append(float(ic))
        if not ics:
            results.append(base)
            continue
        ic_arr  = np.array(ics)
        mean_ic = float(np.mean(ic_arr))
        std_ic  = float(np.std(ic_arr))
        icir    = round(mean_ic / std_ic, 3) if std_ic > 1e-9 else 0.0
        directional = settled[settled["_signal"].isin(["LONG", "SHORT"])]
        if len(directional) > 0:
            wins = (
                ((directional["_signal"] == "LONG")  & (directional[fwd_col] > 0)) |
                ((directional["_signal"] == "SHORT") & (directional[fwd_col] < 0))
            )
            hit_rate = round(float(wins.sum() / len(directional) * 100), 1)
        else:
            hit_rate = None
        results.append(_clean_dict({
            "days": h["days"], "mean_ic": round(mean_ic, 4),
            "icir": round(icir, 3), "hit_rate": hit_rate, "n_obs": n_obs,
        }))
    return results


@router.get("/signal-quality")
def get_signal_quality(limit: int = 500):
    """
    Return IC, ICIR, hit_rate per forward horizon for both ML and linear engines,
    plus a recent signal journal showing both signals side by side.
    """
    try:
        df = _fetch_signals(limit)
    except Exception as exc:
        logger.error("signal_quality DB error: %s", exc)
        return {"error": str(exc), "ml": {}, "linear": {}, "recent_signals": []}

    # Per-engine horizon stats
    ml_horizons     = _engine_horizons(df, "signed_confidence", "signal")
    linear_horizons = _engine_horizons(df, "signed_linear",     "linear_signal")

    def _summary(horizons, fwd_col):
        h20 = next((h for h in horizons if h["days"] == 20), {})
        return _clean_dict({
            "mean_ic_20d":   h20.get("mean_ic"),
            "icir_20d":      h20.get("icir"),
            "hit_rate_20d":  h20.get("hit_rate"),
            "settled_20d":   int(h20.get("n_obs", 0)),
            "pending_20d":   int(df[fwd_col].isna().sum()),
            "total_signals": int(len(df)),
        })

    # Most recent 40 signals — both engines side by side
    journal_cols = [
        "signal_date", "symbol",
        "signal", "ml_confidence",       # ML engine
        "linear_signal", "composite_score",  # Linear engine
        "entry_price", "entry_bar_date",
        "fwd_ret_1d", "fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d",
    ]
    # only keep cols that actually exist in df
    existing = [c for c in journal_cols if c in df.columns]
    recent = df[existing].head(40).where(pd.notna(df[existing]), None)

    return {
        "ml":             {"summary": _summary(ml_horizons,     "fwd_ret_20d"),
                           "horizons": _clean_records(ml_horizons)},
        "linear":         {"summary": _summary(linear_horizons, "fwd_ret_20d"),
                           "horizons": _clean_records(linear_horizons)},
        "recent_signals": _clean_records(recent.to_dict(orient="records")),
    }
