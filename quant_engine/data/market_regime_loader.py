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
from typing import Optional

import numpy as np
import pandas as pd

from quant_engine.data.turso_client import connect


def _get_connection():
    return connect()


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


def build_markov_score_series(benchmark_df: pd.DataFrame, window: int = 60) -> pd.Series:
    """
    Rolling Markov next-day regime score in [-1, +1].

    For each bar, builds a transition matrix from the trailing `window` days
    of NIFTY daily returns (classified as Bear/Sideways/Bull), then returns:
        P(next = Bull | current state) - P(next = Bear | current state)

    Positive → market likely to continue in / transition to Bull.
    Negative → market likely to stay in / transition to Bear.
    Returns zeros for the first `window` bars (warm-up period).
    """
    if benchmark_df.empty or len(benchmark_df) < window + 1:
        return pd.Series(dtype=float)

    close = benchmark_df["close"].astype(float)
    returns = close.pct_change().dropna()

    BULL_T = 0.005   # > +0.5% → Bull
    BEAR_T = -0.005  # < -0.5% → Bear

    # Classify each day: 0=Bear, 1=Sideways, 2=Bull
    states = np.ones(len(returns), dtype=int)
    states[returns.values > BULL_T] = 2
    states[returns.values < BEAR_T] = 0

    scores = np.zeros(len(states), dtype=float)

    for i in range(window, len(states)):
        win = states[i - window:i]
        curr = win[-1]

        # 3×3 transition count matrix
        counts = np.zeros((3, 3), dtype=float)
        for j in range(len(win) - 1):
            counts[win[j], win[j + 1]] += 1

        row_sums = counts.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        trans = counts / row_sums

        scores[i] = trans[curr, 2] - trans[curr, 0]   # P(Bull) - P(Bear)

    result = pd.Series(scores, index=returns.index, dtype=float).clip(-1.0, 1.0)
    result.iloc[:window] = 0.0
    return result


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


def _flow_to_score(series: pd.Series, window: int = 252) -> pd.Series:
    """
    Convert a raw flow series (INR crore or contracts) to [-1, +1] using
    rolling percentile rank over `window` days.

    score = 2 * percentile - 1
      → +1.0 when flow is at multi-year high (maximum inflow / net long)
      → -1.0 when flow is at multi-year low (maximum outflow / net short)
      →  0.0 at historical median
    """
    def _pct(s):
        return s.rank(pct=True).iloc[-1]

    rolling_pct = series.rolling(window, min_periods=30).apply(_pct, raw=False)
    return (2 * rolling_pct - 1).clip(-1.0, 1.0).fillna(0.0)


def load_fii_flow_series(limit: int = 2000) -> pd.Series:
    """
    Returns a pd.Series of raw FII net cash flow (INR crore/day) indexed by date.
    Positive = net buying, negative = net selling.
    Empty series if no data available.
    """
    conn = _get_connection()
    try:
        df = pd.read_sql_query(
            f"SELECT date, fii_net_cash FROM market_regime "
            f"WHERE fii_net_cash IS NOT NULL ORDER BY date ASC LIMIT {limit}",
            conn,
        )
        if df.empty:
            return pd.Series(dtype=float)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")["fii_net_cash"]
    except Exception:
        return pd.Series(dtype=float)
    finally:
        conn.close()


def load_fii_fo_series(limit: int = 2000) -> pd.Series:
    """
    Returns a pd.Series of FII net index futures positioning (contracts) indexed by date.
    Positive = net long (bullish positioning), negative = net short (bearish/hedged).
    Empty series if no data available.
    """
    conn = _get_connection()
    try:
        df = pd.read_sql_query(
            f"SELECT date, fii_fo_net_long FROM market_regime "
            f"WHERE fii_fo_net_long IS NOT NULL ORDER BY date ASC LIMIT {limit}",
            conn,
        )
        if df.empty:
            return pd.Series(dtype=float)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")["fii_fo_net_long"]
    except Exception:
        return pd.Series(dtype=float)
    finally:
        conn.close()


def load_pcr_series(limit: int = 2000) -> pd.Series:
    """
    Returns a pd.Series of PCR values indexed by date.
    Reads from pcr_history table; parses the JSON blob and extracts a scalar PCR.
    PCR > 1 → bearish sentiment (more puts), PCR < 1 → bullish (more calls).
    """
    conn = _get_connection()
    try:
        df = pd.read_sql_query(
            f"SELECT date, pcr_data FROM pcr_history ORDER BY date ASC LIMIT {limit}",
            conn,
        )
        if df.empty:
            return pd.Series(dtype=float)
        df["date"] = pd.to_datetime(df["date"])

        def _extract_pcr(raw):
            import json
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, (int, float)):
                    return float(data)
                if isinstance(data, list) and len(data) > 0:
                    # Take PCR for NIFTY if available
                    for item in data:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                if "pcr" in k.lower() and v:
                                    return float(v)
                    return float(data[0].get("pcr", data[0].get("putCallRatio", np.nan)))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if "pcr" in k.lower() and v:
                            return float(v)
                return np.nan
            except Exception:
                return np.nan

        df["pcr"] = df["pcr_data"].apply(_extract_pcr)
        df = df.dropna(subset=["pcr"])
        if df.empty:
            return pd.Series(dtype=float)
        return df.set_index("date")["pcr"]
    except Exception:
        return pd.Series(dtype=float)
    finally:
        conn.close()


def pcr_to_score(pcr_series: pd.Series, window: int = 252) -> pd.Series:
    """
    Convert raw PCR to [-1, +1] using rolling percentile rank.

    High PCR (many puts, bearish sentiment) → score near -1.
    Low PCR  (many calls, bullish sentiment) → score near +1.

    Inverted because high PCR = fear = bearish.
    """
    def _pct(s):
        return s.rank(pct=True).iloc[-1]

    rolling_pct = pcr_series.rolling(window, min_periods=30).apply(_pct, raw=False)
    score = (1 - 2 * rolling_pct).clip(-1.0, 1.0).fillna(0.0)
    return score


def load_pcr_score_today() -> float:
    """Returns today's PCR regime score. 0.0 if no data."""
    series = load_pcr_series(limit=300)
    if series.empty:
        return 0.0
    score = pcr_to_score(series)
    return float(score.iloc[-1])


def load_fii_flow_score_today() -> float:
    """Returns today's fii_flow_score for live inference. 0.0 if no data."""
    series = load_fii_flow_series(limit=300)
    if series.empty:
        return 0.0
    rolling_10d = series.rolling(10).sum()
    score = _flow_to_score(rolling_10d)
    return float(score.iloc[-1])


def load_fii_fo_score_today() -> float:
    """Returns today's fii_fo_score for live inference. 0.0 if no data."""
    series = load_fii_fo_series(limit=300)
    if series.empty:
        return 0.0
    score = _flow_to_score(series)
    return float(score.iloc[-1])
