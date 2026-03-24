"""
Market regime data loader.

Reads the market_regime table (populated by backfill_regime.py) and exposes:

  load_vix_series()  →  pd.Series indexed by date, values = raw India VIX
  load_vix_score()   →  float in [-1, +1] for today (live engine)

VIX score design:
  - Rank today's VIX within the trailing 252-day distribution (percentile).
  - score = 1 - 2 * percentile  →  low VIX (calm) = +1, high VIX (fearful) = -1.

This makes the feature robust to absolute VIX level shifts over years:
what matters is whether VIX is high or low *relative to recent history*.
"""
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

from quant_engine.config import DB_PATH


def _get_connection():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_vix_series(limit: int = 2000) -> pd.Series:
    """
    Returns a pd.Series of raw India VIX closing values indexed by date.
    Empty series if the market_regime table doesn't exist yet (run backfill_regime.py).
    """
    conn = _get_connection()
    try:
        df = pd.read_sql_query(
            f"SELECT date, india_vix FROM market_regime WHERE india_vix IS NOT NULL ORDER BY date ASC LIMIT {limit}",
            conn,
        )
        if df.empty:
            return pd.Series(dtype=float)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")["india_vix"]
    except Exception:
        return pd.Series(dtype=float)
    finally:
        conn.close()


def vix_to_score(vix_series: pd.Series, window: int = 252) -> pd.Series:
    """
    Convert a raw VIX series to a score in [-1, +1] using rolling percentile rank.

    For each date, score = 1 - 2 * percentile(VIX, trailing `window` days).
    Low VIX (below recent norms) → score near +1 (bullish regime).
    High VIX (above recent norms) → score near -1 (bearish regime).
    """
    def _percentile(s):
        return s.rank(pct=True).iloc[-1]

    rolling_pct = vix_series.rolling(window, min_periods=30).apply(_percentile, raw=False)
    score = (1 - 2 * rolling_pct).clip(-1.0, 1.0).fillna(0.0)
    return score


def load_vix_score_today() -> Optional[float]:
    """
    Returns today's (or most recent available) VIX regime score for live inference.
    Returns None if no data is available.
    """
    vix = load_vix_series(limit=300)   # need ~252 days for percentile window
    if vix.empty:
        return None
    score_series = vix_to_score(vix)
    return float(score_series.iloc[-1])
