"""
Sentiment Factor — news/event sentiment as a composite factor.

Reads the latest aggregated sentiment from `sentiment_daily` via
`build_sentiment_features` and exposes a score in [-1, +1] for the
composite. Unlike the other factors (which derive from OHLCV in a
DataFrame), sentiment is keyed by symbol and queries Turso directly.

Score policy:
  * sent_24h ∈ [-1, +1] when there's a row for the latest day.
  * No row → score = 0.0 (neutral). Better than dropping the factor
    entirely: it gives sentiment a stable "I don't know" stance instead
    of letting the missing weight reshuffle silently across other factors.

The same SentimentFeatures payload is reused by composite.py's
`result["sentiment"]` dashboard field, so we should ideally pass the
features in rather than refetching — but to keep the factor module
self-contained and match the .calculate(...) signature contract of the
other factors, we fetch here. The DB hit is cheap (single row by PK)
and gets folded into the per-symbol worker thread anyway.

Status (2026-05-13): activated at 10% in FACTOR_WEIGHTS ahead of the
wiki's Phase 3 IC gate. See wiki/concepts/sentiment.md for the
productionisation phases.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calculate(symbol: str, features=None) -> dict:
    """
    Compute the sentiment factor score for one symbol.

    Args:
        symbol:   NSE ticker (e.g. "INFY").
        features: optional pre-fetched SentimentFeatures — pass when the
                  caller already built it (composite.py reuses the same
                  features for the dashboard payload).

    Returns:
        Dict shaped like the other factor modules:
          {
            "score":       float in [-1, +1] (0.0 when no sentiment row),
            "sent_24h":    raw 24h mean ∈ [-1, +1] or None,
            "sent_5d":     raw 5d mean ∈ [-1, +1] or None,
            "sent_momentum": sent_24h - sent_5d or None,
            "n_articles_24h": count of articles aggregated for today,
            "last_date":   YYYY-MM-DD of the last sentiment_daily row or None,
          }
    """
    if features is None:
        try:
            from quant_engine.sentiment.features import build_sentiment_features
            features = build_sentiment_features(symbol)
        except Exception as exc:  # noqa: BLE001 — soft factor; never block the composite
            logger.debug("sentiment.calculate fallback for %s: %s", symbol, exc)
            return _empty_payload()

    if features is None:
        return _empty_payload()

    # sent_24h carries the freshest signal; older rolling stats are exposed
    # for the dashboard but not folded into the composite score directly.
    score = features.sent_24h if features.sent_24h is not None else 0.0

    return {
        "score":          round(float(score), 4),
        "sent_24h":       features.sent_24h,
        "sent_5d":        features.sent_5d,
        "sent_momentum":  features.sent_momentum,
        "n_articles_24h": features.n_articles_24h,
        "last_date":      features.last_date,
    }


def _empty_payload() -> dict:
    return {
        "score":          0.0,
        "sent_24h":       None,
        "sent_5d":        None,
        "sent_momentum":  None,
        "n_articles_24h": 0,
        "last_date":      None,
    }
