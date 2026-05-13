"""
Derived sentiment features for the Sicilian engine and ML model.

Reads `sentiment_daily` from Turso, returns time-series indexed by date.
The engine treats every feature as **soft** — missing days → NaN, the
SimpleImputer downstream in the ML pipeline fills with the training
median. The linear composite weights sentiment at 0% on first ship until
we have ≥6 months of live data to estimate per-stock IC; the data
collection runs from day 1 regardless.

Features (all in roughly [-1, +1]):

  sent_24h        — sentiment of the most recent calendar day with data
  sent_5d         — 5-day rolling mean sentiment
  sent_momentum   — sent_24h - sent_5d (acceleration/dispersion)
  sent_n_5d       — articles over last 5d, capped to [-1, +1] via log scaling
                    so symbols with no coverage produce a useful "low
                    attention" signal instead of NaN

Returns a SentimentFeatures dataclass with these on the last bar of the
input date range, so the live scorer can pull `.sent_24h` etc. directly.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from quant_engine.data.turso_client import connect

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentimentFeatures:
    sent_24h: Optional[float]
    sent_5d: Optional[float]
    sent_momentum: Optional[float]
    sent_n_5d: Optional[float]
    last_date: Optional[str]
    n_articles_24h: int = 0

    @classmethod
    def empty(cls) -> "SentimentFeatures":
        return cls(None, None, None, None, None, 0)

    def to_dict(self) -> dict:
        return {
            "sent_24h":       self.sent_24h,
            "sent_5d":        self.sent_5d,
            "sent_momentum":  self.sent_momentum,
            "sent_n_5d":      self.sent_n_5d,
            "n_articles_24h": self.n_articles_24h,
            "last_date":      self.last_date,
        }


def load_sentiment_series(
    symbol: str,
    days_back: int = 30,
) -> pd.DataFrame:
    """
    Read raw sentiment_daily rows for one symbol. Empty DataFrame if the
    table doesn't exist yet (first run before any backfill).
    """
    conn = connect()
    try:
        # Probe schema — return empty cleanly if table absent.
        try:
            cur = conn.execute(
                """
                SELECT date, sent_score, n_articles, sources, scorer_version
                FROM sentiment_daily
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (symbol, int(days_back)),
            )
        except Exception as exc:  # noqa: BLE001 — likely "no such table"
            logger.debug("sentiment_daily not readable for %s: %s", symbol, exc)
            return pd.DataFrame(columns=["date", "sent_score", "n_articles", "sources", "scorer_version"])

        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=["date", "sent_score", "n_articles", "sources", "scorer_version"])

        df = pd.DataFrame(rows, columns=["date", "sent_score", "n_articles", "sources", "scorer_version"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    finally:
        conn.close()


def _log_scale_count(n: float, soft_cap: float = 50.0) -> float:
    """
    Compress raw article counts to [0, +1] then re-centre to [-1, +1].
    Tuned so 50 articles ≈ +1; 0 articles ≈ -1.
    """
    if n <= 0:
        return -1.0
    return min(1.0, max(-1.0, 2.0 * math.log1p(n) / math.log1p(soft_cap) - 1.0))


def build_sentiment_features(symbol: str) -> SentimentFeatures:
    """
    Build the four sentiment features for the latest data we have on `symbol`.

    Returns SentimentFeatures.empty() if the table is missing or has no rows;
    callers treat that as a soft-NaN feature (imputer fills downstream).
    """
    df = load_sentiment_series(symbol, days_back=30)
    if df.empty:
        return SentimentFeatures.empty()

    latest = df.iloc[-1]
    sent_24h = float(latest["sent_score"]) if pd.notna(latest["sent_score"]) else None

    last5 = df.tail(5)
    if last5.empty or last5["sent_score"].isna().all():
        sent_5d = None
    else:
        sent_5d = float(last5["sent_score"].mean(skipna=True))

    if sent_24h is not None and sent_5d is not None:
        sent_momentum = round(sent_24h - sent_5d, 4)
    else:
        sent_momentum = None

    n_5d = int(last5["n_articles"].sum(skipna=True))
    sent_n_5d = round(_log_scale_count(float(n_5d)), 4)

    return SentimentFeatures(
        sent_24h=round(sent_24h, 4) if sent_24h is not None else None,
        sent_5d=round(sent_5d, 4) if sent_5d is not None else None,
        sent_momentum=sent_momentum,
        sent_n_5d=sent_n_5d,
        last_date=str(latest["date"].date()),
        n_articles_24h=int(latest["n_articles"]),
    )
