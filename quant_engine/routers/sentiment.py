"""
Sentiment Router — endpoints for the sentiment pipeline.

Today: a single sync endpoint that the Node `/api/portfolio/sync` flow
calls alongside VIX / FII / PCR. Idempotent — UPSERT on
(symbol, date) means manual force-syncs during the day are safe.

Future endpoints (deferred):
  GET  /api/sentiment/{symbol}    — last 30d of sentiment_daily
  POST /api/sentiment/score       — score arbitrary text via the chain
  GET  /api/sentiment/diagnostic  — per-symbol IC vs forward returns
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

from quant_engine.sentiment import run_pipeline, available_scorers

router = APIRouter(prefix="/api", tags=["sentiment"])
logger = logging.getLogger(__name__)


@router.post("/sync/sentiment")
def sync_sentiment(
    days: int = Query(1, ge=1, le=90, description="Lookback window in days"),
    symbols: Optional[str] = Query(
        None,
        description="Comma-separated NSE symbols; omit for full portfolio map",
    ),
    dry_run: bool = Query(False, description="Score but don't write to Turso"),
):
    """
    Run the sentiment pipeline and upsert sentiment_daily.

    Called by Node's force-sync flow on every refresh. Returns a summary
    dict (symbols processed, articles fetched, rows written, per-symbol
    breakdown, and which scorers are available on this host).

    Failures inside the pipeline raise — the route wraps with a single
    try/except so a partial failure doesn't 500 the entire force-sync.
    """
    sym_list = None
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    try:
        summary = run_pipeline(
            symbols=sym_list,
            days_back=days,
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001 — surface as structured response
        logger.error("Sentiment sync failed: %s", exc, exc_info=True)
        return {
            "success": False,
            "error":   str(exc),
            "available_scorers": available_scorers(),
        }

    return {
        "success":           True,
        "available_scorers": available_scorers(),
        **summary,
    }
