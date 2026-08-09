"""
The scoring engine. Cross-sectional and automated by default.

Scoring is never a per-stock chore: ``score_pending`` selects **every** unscored
announcement in the corpus, whatever symbol it belongs to, applies the declared
filters, and scores the lot. The daily run is the collector followed by this,
with no symbol list anywhere in the loop.

Two transports, chosen by deadline rather than preference:

  sync   concurrent Messages calls. Seconds to minutes. **This is what the daily
         forward run uses**, because overnight news must be scored between the
         09:00 IST cutoff and the 09:15 open — a deadline the Batch API's
         up-to-24h SLA cannot be trusted to meet. At ~1,300 headlines/day on
         Haiku the cost of choosing speed here is cents.

  batch  Message Batches API, 50% cheaper, up to 24h. For the ~250k-row
         historical backfill, where nothing is waiting on the answer.
         Resumable through the ``score_batches`` table.

Provenance: every row carries (model_id, prompt_hash, effort, run_id) and the
primary key is (announcement_id, model_id, prompt_hash). Re-running under the
same provenance is a no-op; re-running under a changed prompt creates a new,
separately-analysable arm. Nothing is ever overwritten.

Run:
    python3 -m catalan.scoring.scorer --pending
    python3 -m catalan.scoring.scorer --pending --transport batch --limit 50000
    python3 -m catalan.scoring.scorer --collect            # gather finished batches
    python3 -m catalan.scoring.scorer --headline "Board approves demerger" --company RELIANCE
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from catalan.config import (
    PRIMARY_MODEL,
    SCORE_VALUES,
    SCORING_BATCH_SIZE,
)
from catalan.data.store import store
from catalan.scoring import filters
from catalan.scoring.prompt import PROMPT_TEMPLATE, prompt_hash, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MAX_TOKENS = 200
SYNC_CONCURRENCY = int(os.getenv("CATALAN_SYNC_CONCURRENCY", "8"))
MAX_RETRIES = 4

TRANSPORT_SYNC = "sync"
TRANSPORT_BATCH = "batch"


# ── Response parsing ──────────────────────────────────────────────────────────

# The paper's contract: the answer is in the FIRST LINE, the rationale on the
# next. Anchor to the first line rather than searching the whole response —
# a rationale reading "...this is not a NO for the stock" must not flip the score.
_ANSWER_RE = re.compile(r"\b(YES|NO|UNKNOWN)\b", re.IGNORECASE)


def parse_response(raw: str) -> Tuple[Optional[int], Optional[str]]:
    """Free-text response → (score, rationale), per the paper's format.

    Returns (None, raw) when the first line carries no recognisable answer —
    an abstention is recorded as such rather than silently coerced to 0, since
    0 is a real UNKNOWN verdict and must stay distinguishable from a parse miss.
    """
    if not raw:
        return None, None

    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        return None, None

    match = _ANSWER_RE.search(lines[0])
    if not match:
        return None, raw.strip()[:500]

    score = SCORE_VALUES[match.group(1).upper()]
    rationale = " ".join(lines[1:]).strip() or None
    return score, (rationale[:500] if rationale else None)


# ── Anthropic client ──────────────────────────────────────────────────────────

_client = None


def _anthropic():
    global _client
    if _client is None:
        import anthropic

        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. CATALAN reads the repo .env; in "
                "GitHub Actions it must be provided as a repository secret."
            )
        _client = anthropic.Anthropic(api_key=key)
    return _client


def _log_api_error(context: str, exc: BaseException) -> None:
    """Surface the API's own message — 'rate_limit' vs 'model not found' vs
    'invalid_request_error: field X' are very different problems and the
    generic exception text hides which one happened."""
    body = getattr(exc, "body", None) or {}
    detail = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
    logger.error("%s: %s%s", context, type(exc).__name__,
                 f" — {detail}" if detail else f" — {exc}")


def _message_params(company: str, headline: str, model_id: str) -> Dict:
    return {
        "model": model_id,
        "max_tokens": MAX_TOKENS,
        # No temperature: Opus 5 rejects it with a 400. Determinism comes from
        # pinning (model_id, prompt_hash, effort) instead — see prompt.py.
        "messages": [{"role": "user", "content": render(company, headline)}],
    }


# ── Sync transport ────────────────────────────────────────────────────────────

def _score_one_sync(company: str, headline: str, model_id: str) -> Tuple[Optional[int], Optional[str]]:
    client = _anthropic()
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(**_message_params(company, headline, model_id))
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return parse_response(text)
        except Exception as exc:                          # noqa: BLE001
            transient = type(exc).__name__ in (
                "RateLimitError", "APIStatusError", "APIConnectionError",
                "InternalServerError", "APITimeoutError",
            )
            if not transient or attempt == MAX_RETRIES - 1:
                _log_api_error(f"scoring {company!r}", exc)
                return None, None
            time.sleep(2 ** attempt)
    return None, None


def score_texts(items: Sequence[Tuple[str, str]],
                model_id: str = None) -> List[Tuple[Optional[int], Optional[str]]]:
    """Score (company, headline) pairs concurrently. Order preserved.

    The public entry point for anything that is not a stored announcement —
    manual entry, the A/B harness, ad-hoc checks.
    """
    model_id = model_id or PRIMARY_MODEL
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=SYNC_CONCURRENCY) as pool:
        return list(pool.map(lambda it: _score_one_sync(it[0], it[1], model_id), items))


# ── Batch transport ───────────────────────────────────────────────────────────

def _submit_batch(rows: List[Dict], model_id: str, run_id: str,
                  conn: sqlite3.Connection) -> str:
    """Submit one Message Batch and record it for resumable collection."""
    import anthropic

    requests = [
        {
            "custom_id": f"ann-{row['id']}",
            "params": _message_params(row["company"] or row["symbol"],
                                      row["headline"], model_id),
        }
        for row in rows
    ]
    batch = _anthropic().messages.batches.create(requests=requests)

    conn.execute(
        "INSERT INTO score_batches (batch_id, run_id, model_id, prompt_hash, "
        "announcement_ids, n_requests, status, submitted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)",
        (batch.id, run_id, model_id, prompt_hash(),
         json.dumps([r["id"] for r in rows]), len(rows),
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    logger.info("batch %s submitted: %d requests", batch.id, len(rows))
    return batch.id


def collect_batches(conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """Collect every finished batch and write its scores. Safe to re-run.

    Batches still in progress are left alone, so this can be scheduled on its
    own cadence — a workflow that submits and a workflow that collects do not
    have to be the same run.
    """
    own = conn is None
    conn = conn or store()
    written = pending_batches = 0

    rows = conn.execute(
        "SELECT batch_id, run_id, model_id FROM score_batches WHERE status='submitted'"
    ).fetchall()

    for batch_id, run_id, model_id in rows:
        try:
            batch = _anthropic().messages.batches.retrieve(batch_id)
            if batch.processing_status != "ended":
                pending_batches += 1
                continue

            scored: List[Tuple] = []
            for result in _anthropic().messages.batches.results(batch_id):
                ann_id = int(result.custom_id.split("-", 1)[1])
                if result.result.type != "succeeded":
                    logger.warning("batch %s: %s failed for %s",
                                   batch_id, result.custom_id, result.result.type)
                    continue
                msg = result.result.message
                text = "".join(b.text for b in msg.content
                               if getattr(b, "type", None) == "text")
                score, rationale = parse_response(text)
                scored.append((ann_id, model_id, prompt_hash(), None,
                               score, rationale, run_id,
                               datetime.now().isoformat(timespec="seconds")))

            written += _write_scores(conn, scored)
            conn.execute(
                "UPDATE score_batches SET status='collected', collected_at=? "
                "WHERE batch_id=?",
                (datetime.now().isoformat(timespec="seconds"), batch_id),
            )
            conn.commit()
            logger.info("batch %s collected: %d scores", batch_id, len(scored))
        except Exception as exc:                          # noqa: BLE001
            _log_api_error(f"collecting batch {batch_id}", exc)
            conn.execute("UPDATE score_batches SET status='error' WHERE batch_id=?",
                         (batch_id,))
            conn.commit()

    if own:
        conn.close()
    return {"scores_written": written, "batches_pending": pending_batches}


# ── Selection and persistence ─────────────────────────────────────────────────

PENDING_SQL = """
SELECT a.id, a.symbol, a.company, a.headline, a.category, a.announced_at
FROM announcements a
WHERE NOT EXISTS (
    SELECT 1 FROM scores s
    WHERE s.announcement_id = a.id
      AND s.model_id = ?
      AND s.prompt_hash = ?
)
ORDER BY a.announced_at DESC
"""


def pending(model_id: str = None, conn: Optional[sqlite3.Connection] = None,
            limit: Optional[int] = None, apply_filters: bool = True) -> List[Dict]:
    """Every announcement with no score under this exact provenance pair.

    Cross-sectional by construction — no symbol appears in this query.

    ``apply_filters`` runs the declared category exclusions and near-duplicate
    dedup first. Here that is a cost control as well as a study decision: about
    a fifth of a typical day is 'Copy of Newspaper Publication' and paying to
    score it would be waste. Filtering is at selection time, so a later change
    to the exclusion list simply makes previously-skipped rows pending again.
    """
    model_id = model_id or PRIMARY_MODEL
    own = conn is None
    conn = conn or store()
    try:
        rows = [dict(r) for r in conn.execute(PENDING_SQL, (model_id, prompt_hash()))]
    finally:
        if own:
            conn.close()

    if apply_filters:
        rows, counts = filters.apply_all(rows)
        logger.info(
            "pending: %d candidates → %d after filters "
            "(%d excluded category, %d near-duplicate)",
            counts["input"], counts["kept"],
            counts["excluded_category"], counts["excluded_duplicate"],
        )

    return rows[:limit] if limit else rows


def _write_scores(conn: sqlite3.Connection, rows: Sequence[Tuple]) -> int:
    """Append score rows. INSERT OR IGNORE — never overwrite an existing verdict."""
    if not rows:
        return 0
    before = conn.execute("SELECT count(*) FROM scores").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO scores (announcement_id, model_id, prompt_hash, "
        "effort, score, rationale, run_id, scored_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn.execute("SELECT count(*) FROM scores").fetchone()[0] - before


def score_pending(model_id: str = None, transport: str = TRANSPORT_SYNC,
                  limit: Optional[int] = None, run_id: Optional[str] = None,
                  conn: Optional[sqlite3.Connection] = None) -> Dict:
    """Score every pending announcement across the whole cross-section.

    This is the automated path. It takes no symbol argument by design.
    """
    model_id = model_id or PRIMARY_MODEL
    run_id = run_id or f"run-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    own = conn is None
    conn = conn or store()

    try:
        todo = pending(model_id, conn=conn, limit=limit)
        if not todo:
            logger.info("nothing pending for %s @ %s", model_id, prompt_hash())
            return {"run_id": run_id, "pending": 0, "scored": 0, "abstained": 0}

        logger.info("scoring %d announcements via %s (%s)", len(todo), transport, model_id)

        if transport == TRANSPORT_BATCH:
            batch_ids = [
                _submit_batch(todo[i:i + SCORING_BATCH_SIZE], model_id, run_id, conn)
                for i in range(0, len(todo), SCORING_BATCH_SIZE)
            ]
            return {"run_id": run_id, "pending": len(todo), "submitted": len(todo),
                    "batches": batch_ids,
                    "note": "run --collect once the batches finish"}

        results = score_texts(
            [(r["company"] or r["symbol"], r["headline"]) for r in todo], model_id
        )
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            (r["id"], model_id, prompt_hash(), None, score, rationale, run_id, now)
            for r, (score, rationale) in zip(todo, results)
        ]
        written = _write_scores(conn, rows)
        abstained = sum(1 for s, _ in results if s is None)
        if abstained:
            logger.warning("%d/%d abstained (parse miss or API failure) — these "
                           "stay pending and will be retried next run",
                           abstained, len(results))

        return {"run_id": run_id, "pending": len(todo), "scored": written,
                "abstained": abstained}
    finally:
        if own:
            conn.close()


def score_manual(company: str, headline: str, symbol: Optional[str] = None,
                 model_id: str = None, announced_at: Optional[str] = None,
                 persist: bool = True, conn: Optional[sqlite3.Connection] = None) -> Dict:
    """Score one ad-hoc headline the NSE feed did not carry.

    The escape hatch for news that arrives by some other route. Persisted rows
    are stamped ``source='manual'`` so the study can include or exclude them
    deliberately — a manual headline has no exchange timestamp guarantee, and
    mixing it into the forward log unlabelled would weaken exactly the
    point-in-time claim the forward log exists to make.
    """
    model_id = model_id or PRIMARY_MODEL
    score, rationale = score_texts([(company, headline)], model_id)[0]

    out = {
        "company": company, "symbol": symbol, "headline": headline,
        "model_id": model_id, "prompt_hash": prompt_hash(),
        "score": score, "rationale": rationale,
        "verdict": {1: "YES", 0: "UNKNOWN", -1: "NO"}.get(score, "PARSE_FAILED"),
        "persisted": False,
    }
    if not persist or not symbol:
        return out

    own = conn is None
    conn = conn or store()
    try:
        stamp = announced_at or datetime.now().isoformat(timespec="seconds")
        run_id = f"manual-{datetime.now():%Y%m%dT%H%M%S}"
        cur = conn.execute(
            "INSERT OR IGNORE INTO announcements (symbol, isin, company, announced_at, "
            "headline, category, industry, seq_id, source, ingested_at) "
            "VALUES (?, NULL, ?, ?, ?, 'Manual Entry', NULL, ?, 'manual', ?)",
            (symbol.upper(), company, stamp, headline,
             f"manual:{uuid.uuid4().hex[:16]}", stamp),
        )
        conn.commit()
        ann_id = cur.lastrowid
        if ann_id:
            _write_scores(conn, [(ann_id, model_id, prompt_hash(), None,
                                  score, rationale, run_id, stamp)])
            out.update(announcement_id=ann_id, persisted=True)
    finally:
        if own:
            conn.close()
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="CATALAN scoring engine")
    ap.add_argument("--pending", action="store_true",
                    help="score every unscored announcement (the automated path)")
    ap.add_argument("--collect", action="store_true",
                    help="collect finished Message Batches and write their scores")
    ap.add_argument("--headline", help="manual entry: score one ad-hoc headline")
    ap.add_argument("--company", help="company name for --headline")
    ap.add_argument("--symbol", help="NSE symbol for --headline (persists if given)")
    ap.add_argument("--model", default=PRIMARY_MODEL)
    ap.add_argument("--transport", choices=[TRANSPORT_SYNC, TRANSPORT_BATCH],
                    default=TRANSPORT_SYNC)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be scored, call nothing")
    args = ap.parse_args()

    if args.headline:
        result = score_manual(args.company or args.symbol or "the company",
                              args.headline, symbol=args.symbol, model_id=args.model)
        print(json.dumps(result, indent=2))
    elif args.collect:
        print(json.dumps(collect_batches(), indent=2))
    elif args.pending:
        if args.dry_run:
            todo = pending(args.model, limit=args.limit)
            print(json.dumps({"would_score": len(todo),
                              "model": args.model,
                              "prompt_hash": prompt_hash()}, indent=2))
        else:
            print(json.dumps(score_pending(args.model, args.transport, args.limit),
                             indent=2))
    else:
        ap.error("one of --pending, --collect or --headline is required")


if __name__ == "__main__":
    main()
