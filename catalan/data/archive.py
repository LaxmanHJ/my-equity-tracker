"""
The durable, git-committed form of the corpus.

**Why this exists.** GitHub Actions runners are ephemeral: a SQLite file written
during a scheduled run is gone when the job ends, and Turso writes are blocked
repo-wide. So the local store cannot be the system of record for anything
collected in CI. The record is instead an append-only NDJSON archive under
``catalan/data/forward_log/``, committed back to the branch by the workflow;
``catalan.db`` becomes a rebuildable local query cache.

**The bonus, which is actually the main point.** A git commit timestamp is
independent, tamper-evident proof that a headline was recorded *before* the
outcome it predicts was known. That is exactly the evidence the forward log
exists to produce, and it is far stronger than a row in a database the
researcher can rewrite. The contamination argument in PREREGISTRATION.md rests
on it.

Layout — one gzipped file per day, so daily commits stay small and
conflict-free:

    catalan/data/forward_log/announcements/YYYY-MM-DD.ndjson.gz
    catalan/data/forward_log/scores/YYYY-MM-DD.ndjson.gz

Gzip is measured, not assumed: a full NSE day is 540 KB raw and 69 KB gzipped,
so the archive costs ~17 MB/year instead of ~135 MB. These are append-only data
files, not code — nobody reviews them line by line, and ``zcat`` reads them —
so losing the plaintext diff in a PR view is a cheap price for an 8x saving
over a multi-year study.

Read one directly:

    zcat catalan/data/forward_log/announcements/2026-08-07.ndjson.gz | head

Rebuild the store from the archive at any time:

    python3 -m catalan.data.archive --rebuild
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from catalan.config import CATALAN_ROOT
from catalan.data.store import store

logger = logging.getLogger(__name__)

ARCHIVE_ROOT = Path(CATALAN_ROOT) / "data" / "forward_log"
ANNOUNCEMENTS_DIR = ARCHIVE_ROOT / "announcements"
SCORES_DIR = ARCHIVE_ROOT / "scores"

ANNOUNCEMENT_COLS = ("symbol", "isin", "company", "announced_at", "headline",
                     "category", "industry", "seq_id", "source", "ingested_at",
                     # Captured, not read — see schema.sql. Rows archived before
                     # these columns existed simply carry nulls on rebuild.
                     "attachment_url", "attachment_size", "has_xbrl",
                     "disseminated_at")
SCORE_COLS = ("announcement_id", "model_id", "prompt_hash", "effort", "score",
              "rationale", "run_id", "scored_at")


def read_ndjson(path: Path) -> List[Dict]:
    """Read a gzipped NDJSON archive file. Empty list if it does not exist."""
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _append_ndjson(path: Path, rows: Iterable[Dict], key_fields: tuple) -> Dict[str, int]:
    """Merge rows into a day's archive file. Idempotent — a re-run changes nothing.

    Ordering is preserved: existing rows keep their positions and new rows
    append after them. The archive is the evidentiary record, so it is never
    reordered.

    **Enrichment is additive only.** If a row already present acquires a field
    it did not have before (this happened when `attachment_url` was added to
    the schema after 2026-08-07 was already archived), the missing field is
    filled in. A field that already holds a non-null value is NEVER changed —
    so this can add information to the record but can never alter a claim it
    already made, which is what keeps the git timestamps meaningful as evidence.
    """
    rows = list(rows)
    existing = read_ndjson(path)
    index = {tuple(str(rec.get(k)) for k in key_fields): rec for rec in existing}

    fresh: List[Dict] = []
    enriched = 0
    for row in rows:
        key = tuple(str(row.get(k)) for k in key_fields)
        prior = index.get(key)
        if prior is None:
            fresh.append(row)
            index[key] = row
            continue
        added = {k: v for k, v in row.items()
                 if v is not None and prior.get(k) is None}
        if added:
            prior.update(added)
            enriched += 1

    if not fresh and not enriched:
        return {"added": 0, "enriched": 0}

    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the gzip header byte-identical for identical content, so a
    # no-op re-run cannot produce a spurious git diff.
    with gzip.GzipFile(str(path), "wb", compresslevel=9, mtime=0) as gz:
        for row in existing + fresh:
            line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            gz.write(line.encode("utf-8"))
    return {"added": len(fresh), "enriched": enriched}


def export_announcements(day: str, conn: Optional[sqlite3.Connection] = None) -> int:
    """Write one day's announcements to the archive. Returns rows added."""
    own = conn is None
    conn = conn or store()
    try:
        rows = [
            dict(zip(ANNOUNCEMENT_COLS, r))
            for r in conn.execute(
                f"SELECT {', '.join(ANNOUNCEMENT_COLS)} FROM announcements "
                "WHERE substr(announced_at, 1, 10) = ? ORDER BY announced_at",
                (day,),
            )
        ]
    finally:
        if own:
            conn.close()

    r = _append_ndjson(ANNOUNCEMENTS_DIR / f"{day}.ndjson.gz", rows, ("symbol", "seq_id"))
    logger.info("archive: %s announcements +%d added, %d enriched (%d in store)",
                day, r["added"], r["enriched"], len(rows))
    return r["added"]


def export_scores(day: str, conn: Optional[sqlite3.Connection] = None) -> int:
    """Write scores produced on ``day`` to the archive, keyed by scored_at.

    Scores carry the announcement's (symbol, seq_id) rather than its local
    integer id: row ids are an artefact of one machine's ingest order and would
    not survive a rebuild on another.
    """
    own = conn is None
    conn = conn or store()
    try:
        rows = [
            {
                "symbol": r[0], "seq_id": r[1], "model_id": r[2],
                "prompt_hash": r[3], "effort": r[4], "score": r[5],
                "rationale": r[6], "run_id": r[7], "scored_at": r[8],
            }
            for r in conn.execute(
                "SELECT a.symbol, a.seq_id, s.model_id, s.prompt_hash, s.effort, "
                "s.score, s.rationale, s.run_id, s.scored_at "
                "FROM scores s JOIN announcements a ON a.id = s.announcement_id "
                "WHERE substr(s.scored_at, 1, 10) = ? ORDER BY s.scored_at",
                (day,),
            )
        ]
    finally:
        if own:
            conn.close()

    r = _append_ndjson(SCORES_DIR / f"{day}.ndjson.gz", rows,
                       ("symbol", "seq_id", "model_id", "prompt_hash"))
    logger.info("archive: %s scores +%d added, %d enriched (%d in store)",
                day, r["added"], r["enriched"], len(rows))
    return r["added"]


def rebuild(conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """Rebuild the local store from the archive. The disaster-recovery path.

    Also what a fresh clone runs to get a working database without refetching
    anything from NSE.
    """
    own = conn is None
    conn = conn or store()
    try:
        ann = 0
        for path in sorted(ANNOUNCEMENTS_DIR.glob("*.ndjson.gz")):
            rows = read_ndjson(path)
            conn.executemany(
                f"INSERT OR IGNORE INTO announcements ({', '.join(ANNOUNCEMENT_COLS)}) "
                f"VALUES ({', '.join('?' * len(ANNOUNCEMENT_COLS))})",
                [tuple(r.get(c) for c in ANNOUNCEMENT_COLS) for r in rows],
            )
            ann += len(rows)
        conn.commit()

        sc = 0
        for path in sorted(SCORES_DIR.glob("*.ndjson.gz")):
            rows = read_ndjson(path)
            for r in rows:
                hit = conn.execute(
                    "SELECT id FROM announcements WHERE symbol=? AND seq_id=?",
                    (r["symbol"], r["seq_id"]),
                ).fetchone()
                if not hit:
                    logger.warning("archived score has no announcement: %s/%s",
                                   r["symbol"], r["seq_id"])
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO scores (announcement_id, model_id, "
                    "prompt_hash, effort, score, rationale, run_id, scored_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (hit[0], r["model_id"], r["prompt_hash"], r.get("effort"),
                     r.get("score"), r.get("rationale"), r["run_id"], r["scored_at"]),
                )
                sc += 1
        conn.commit()

        total_a = conn.execute("SELECT count(*) FROM announcements").fetchone()[0]
        total_s = conn.execute("SELECT count(*) FROM scores").fetchone()[0]
    finally:
        if own:
            conn.close()

    logger.info("rebuild: %d announcements, %d scores in store", total_a, total_s)
    return {"announcements_read": ann, "scores_read": sc,
            "announcements_in_store": total_a, "scores_in_store": total_s}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="CATALAN archive — the durable corpus")
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild catalan.db from the committed NDJSON archive")
    ap.add_argument("--export", metavar="YYYY-MM-DD",
                    help="export one day's announcements and scores to the archive")
    args = ap.parse_args()

    if args.rebuild:
        print(json.dumps(rebuild(), indent=2))
    elif args.export:
        print(json.dumps({"announcements": export_announcements(args.export),
                          "scores": export_scores(args.export)}, indent=2))
    else:
        ap.error("one of --rebuild or --export is required")


if __name__ == "__main__":
    main()
