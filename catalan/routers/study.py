"""
CATALAN's HTTP surface. Read-only reporting over the local store.

The most important endpoint here is /api/study/coverage. A silently-dead
forward collector is this study's main operational failure mode — the log
accrues one day per calendar day and a missed day is gone for good — so
freshness is a first-class number on the dashboard, not a log line.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from catalan.config import GATES, PRIMARY_MODEL, STUDY_VERSION, UNIVERSE_INDEX
from catalan.data.store import store

router = APIRouter(prefix="/api/study", tags=["study"])


class ManualHeadline(BaseModel):
    """Ad-hoc headline the NSE feed did not carry."""

    headline: str = Field(..., min_length=3, max_length=1000)
    company: str = Field(..., min_length=1, max_length=200)
    symbol: Optional[str] = Field(None, max_length=30)
    model_id: Optional[str] = None
    persist: bool = True


@router.get("/coverage")
def coverage() -> Dict[str, Any]:
    """Corpus size, per-month counts, and how stale the forward log is."""
    conn = store()
    try:
        total, symbols, first, last = conn.execute(
            "SELECT count(*), count(DISTINCT symbol), min(announced_at), "
            "max(announced_at) FROM announcements"
        ).fetchone()

        by_month: List[Dict[str, Any]] = [
            {"month": r[0], "announcements": r[1], "symbols": r[2]}
            for r in conn.execute(
                "SELECT substr(announced_at, 1, 7) AS m, count(*), "
                "count(DISTINCT symbol) FROM announcements GROUP BY m ORDER BY m"
            ).fetchall()
        ]

        runs = [
            dict(r) for r in conn.execute(
                "SELECT source, window_from, window_to, rows_fetched, rows_inserted, "
                "status, detail, started_at, finished_at FROM ingest_runs "
                "ORDER BY id DESC LIMIT 10"
            ).fetchall()
        ]
    finally:
        conn.close()

    stale_days = None
    if last:
        stale_days = (date.today() - datetime.fromisoformat(last).date()).days

    return {
        "announcements": total or 0,
        "symbols": symbols or 0,
        "first_announcement": first,
        "last_announcement": last,
        "days_since_last_announcement": stale_days,
        "by_month": by_month,
        "recent_runs": runs,
    }


@router.get("/scores")
def scores() -> Dict[str, Any]:
    """Scoring progress per arm — (model_id, prompt_hash) pairs and their counts."""
    conn = store()
    try:
        arms = [
            {"model_id": r[0], "prompt_hash": r[1], "scored": r[2], "runs": r[3]}
            for r in conn.execute(
                "SELECT model_id, prompt_hash, count(*), count(DISTINCT run_id) "
                "FROM scores GROUP BY model_id, prompt_hash ORDER BY count(*) DESC"
            ).fetchall()
        ]
        unscored = conn.execute(
            "SELECT count(*) FROM announcements a WHERE NOT EXISTS "
            "(SELECT 1 FROM scores s WHERE s.announcement_id = a.id)"
        ).fetchone()[0]
    finally:
        conn.close()

    return {"arms": arms, "unscored_announcements": unscored}


@router.post("/score")
def score_manual_headline(item: ManualHeadline) -> Dict[str, Any]:
    """Manual entry — score one headline the automated feed did not pick up.

    The escape hatch, not the main path: the automated pipeline scores the whole
    cross-section daily. Persisted rows are stamped `source='manual'` so they can
    be included or excluded deliberately, since a manual headline carries no
    exchange timestamp guarantee and mixing it unlabelled into the forward log
    would weaken the point-in-time claim that log exists to make.
    """
    from catalan.scoring.scorer import score_manual

    try:
        return score_manual(
            company=item.company,
            headline=item.headline,
            symbol=item.symbol,
            model_id=item.model_id,
            persist=item.persist,
        )
    except RuntimeError as exc:                    # missing key, frozen prompt
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/pending")
def pending_count(model_id: Optional[str] = None) -> Dict[str, Any]:
    """How many announcements are waiting to be scored, after filters.

    Cross-sectional: this is a single number for the whole corpus, because
    scoring is never scoped to a symbol.
    """
    from catalan.scoring.prompt import prompt_hash
    from catalan.scoring.scorer import pending

    model = model_id or PRIMARY_MODEL
    return {
        "model_id": model,
        "prompt_hash": prompt_hash(),
        "pending_after_filters": len(pending(model)),
        "pending_unfiltered": len(pending(model, apply_filters=False)),
    }


@router.get("/summary")
def summary() -> Dict[str, Any]:
    """The pre-registered gate cells.

    Every cell reports `awaiting data` until the analysis phase lands. C2 is
    the ship gate; C1 without C2 is not a result, so the API never returns a
    gross number without its net companion beside it.
    """
    cov = coverage()
    return {
        "study": STUDY_VERSION,
        "universe": UNIVERSE_INDEX,
        "primary_model": PRIMARY_MODEL,
        "phase": "0/1a — scaffold; collector live, nothing scored",
        "gates": {
            "C0": {
                "name": "Comprehension — initial-reaction hit rate",
                "tradable": False,
                "paper_reference": "93.3% (US)",
                "status": "awaiting data",
                "value": None,
            },
            "C1": {
                "name": "Drift, gross of costs",
                "tradable": True,
                "paper_reference": "34 bps/day, 2.97 Sharpe (US)",
                "status": "awaiting data",
                "value": None,
            },
            "C2": {
                "name": "Drift, NET of costs — THE SHIP GATE",
                "tradable": True,
                "paper_reference": "dies at 20 bps round-trip (US)",
                "status": "awaiting data",
                "value": None,
            },
        },
        "gate_order": list(GATES),
        "coverage": {
            "announcements": cov["announcements"],
            "symbols": cov["symbols"],
            "last_announcement": cov["last_announcement"],
            "days_since_last_announcement": cov["days_since_last_announcement"],
        },
    }
