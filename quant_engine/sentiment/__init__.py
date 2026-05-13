"""
Sentiment pipeline — news/event sentiment as an alpha factor.

Top-level design (see wiki/concepts/sentiment.md):
  sources.py     — fetchers (NewsAPI, RSS feeds)
  scorer.py      — pluggable text-to-score (TextBlob today, FinBERT next)
  aggregator.py  — per-symbol daily aggregation + Turso persistence
  features.py    — derived features (24h, 5d, momentum, dispersion)
  backfill.py    — CLI to run sources → scorer → aggregator end-to-end

Status: MVP scaffolding. NEWS_API_KEY in .env enables live fetch; absent key
returns neutral scores so downstream callers never need a conditional branch.
"""

from quant_engine.sentiment.scorer import score_text, score_batch, ScorerInfo, available_scorers
from quant_engine.sentiment.features import (
    SentimentFeatures,
    build_sentiment_features,
)
from quant_engine.sentiment.backfill import run_pipeline

__all__ = [
    "score_text",
    "score_batch",
    "ScorerInfo",
    "available_scorers",
    "SentimentFeatures",
    "build_sentiment_features",
    "run_pipeline",
]
