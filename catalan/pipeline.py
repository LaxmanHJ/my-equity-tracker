"""
The daily pipeline: collect → score → archive. One command, no symbol list.

This is what the scheduled GitHub Action runs. Each stage is idempotent, so a
re-run after a partial failure resumes rather than duplicates, and a missed day
is caught up by passing --date.

    rebuild   restore the store from the committed archive (CI starts empty)
    collect   pull NSE announcements for the day
    score     score EVERY unscored announcement across the whole cross-section
    archive   append the day's rows to the git-committed NDJSON record

Ordering note: scoring runs before archiving so that one commit carries both the
headlines and their verdicts, timestamped together.

Run:
    python3 -m catalan.pipeline                       # yesterday, full pipeline
    python3 -m catalan.pipeline --date 2026-08-07
    python3 -m catalan.pipeline --skip-score          # collect + archive only
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from typing import Dict, Optional

from catalan.config import PRIMARY_MODEL
from catalan.data import archive, collect_daily
from catalan.data.store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run(day: date, skip_score: bool = False, model_id: Optional[str] = None,
        limit: Optional[int] = None, transport: str = "sync") -> Dict:
    """Full daily pipeline for one day. Every stage is idempotent."""
    model_id = model_id or PRIMARY_MODEL
    started = datetime.now()
    conn = store()
    out: Dict = {"date": day.isoformat(), "model": model_id}

    try:
        # 1. CI starts with an empty disk — the archive is the system of record.
        out["rebuild"] = archive.rebuild(conn=conn)

        # 2. Collect. Idempotent via UNIQUE(symbol, seq_id).
        out["collect"] = collect_daily.collect(day, day, conn=conn)

        # 3. Score the entire cross-section, not just today's rows: anything
        #    that abstained or failed on an earlier run is still pending and
        #    gets retried here automatically.
        if skip_score:
            out["score"] = {"skipped": True}
        else:
            from catalan.scoring.scorer import score_pending

            out["score"] = score_pending(model_id, transport=transport,
                                         limit=limit, conn=conn)

        # 4. Archive last, so one commit carries headlines and verdicts together.
        out["archive"] = {
            "announcements": archive.export_announcements(day.isoformat(), conn=conn),
            "scores": archive.export_scores(datetime.now().date().isoformat(), conn=conn),
        }
    finally:
        conn.close()

    out["elapsed_seconds"] = round((datetime.now() - started).total_seconds(), 1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="CATALAN daily pipeline")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--skip-score", action="store_true",
                    help="collect and archive only — no Anthropic calls")
    ap.add_argument("--model", default=PRIMARY_MODEL)
    ap.add_argument("--limit", type=int, help="cap headlines scored this run")
    ap.add_argument("--transport", choices=["sync", "batch"], default="sync")
    args = ap.parse_args()

    day = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
           else date.today() - timedelta(days=1))

    result = run(day, skip_score=args.skip_score, model_id=args.model,
                 limit=args.limit, transport=args.transport)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
