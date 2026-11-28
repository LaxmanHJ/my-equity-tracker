"""
The daily pipeline: collect → extract PDF text → score both arms → archive →
buy list. One command, no symbol list.

This is what the scheduled GitHub Action runs. Each stage is idempotent, so a
re-run after a partial failure resumes rather than duplicates, and a missed day
is caught up by passing --date.

    rebuild   restore the store from the committed archive (CI starts empty)
    collect   pull NSE announcements for the day
    pdf_text  extract the first pages of every attachment from STUDY_CLOCK_START
              on (free; failed downloads stay pending and are retried)
    score     score EVERY unscored announcement across the whole cross-section,
              headline arm (control) then PDF arm
    archive   append the day's rows to the git-committed NDJSON record
    buy_list  write each complete counted day's YES list once, next to the archive

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

from catalan import buy_list
from catalan.clock import today_ist
from catalan.config import PRIMARY_MODEL, STUDY_CLOCK_START
from catalan.data import archive, collect_daily, pdf_text
from catalan.data.store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _guarded(name: str, out: Dict, fn) -> None:
    """Run a stage whose failure must not cost the day's headlines.

    The error is recorded in the result instead of raised; the workflow's last
    step turns any recorded error into a red run, after the commit.
    """
    try:
        out[name] = fn()
    except Exception as exc:                             # noqa: BLE001
        logger.exception("pipeline: stage %s failed", name)
        out[name] = {"error": f"{type(exc).__name__}: {exc}"[:300],
                     # rows written before the failure (a drift halt sets this)
                     "scored": getattr(exc, "scored", 0)}


def _model_checks(out: Dict, model_id: str, checked_at: str) -> list:
    """One record per scoring arm: did this run get matching responses?"""
    records = []
    for key, arm in (("score_pdf", "pdf"), ("score", "headline")):
        r = out.get(key) or {}
        if "error" in r and r.get("scored", 0) > 0:
            status = "ok"                        # matching rows were stored before the halt
        elif "error" in r:
            status = ("model_gone" if r["error"].startswith("ModelGone")
                      else "drift" if r["error"].startswith("ModelDrift") else "error")
        elif not r.get("pending"):
            status = "idle"                      # nothing to score: no evidence either way
        elif r.get("scored", 0) > 0:
            status = "ok"                        # every stored row passed the served-model check
        elif r.get("quota_deferred"):
            status = "quota"
        else:
            status = "no_match"                  # calls attempted, none produced a stored row
        records.append({"checked_at": checked_at, "model": model_id, "arm": arm, "status": status,
                        "scored": r.get("scored", 0), "api_failed": r.get("api_failed", 0),
                        "quota_deferred": r.get("quota_deferred", 0),
                        "detail": r.get("error")})
    return records


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

        # 3. Archive the headlines FIRST. They are the irreplaceable half; every
        #    later stage can be redone from them, so nothing after this point
        #    may be able to lose them (verifier B1).
        out["archive"] = {
            "announcements": archive.export_announcements(day.isoformat(), conn=conn)}

        # 4. PDF text. No API cost, so it runs even with --skip-score. Only from
        #    the study clock start: the pilot's attachments are not re-fetched
        #    on every run.
        _guarded("pdf_text", out, lambda: pdf_text.extract_pending(
            conn=conn, since=STUDY_CLOCK_START, universe=True))

        # 5. Score every pending announcement in the counted period, across the
        #    study universe: anything that failed on an earlier run is retried
        #    here. PDF arm FIRST: it is the primary endpoint, so on peak days the
        #    free tier's daily quota goes to it before the headline control arm.
        #    The date floor keeps the daily run from bulk-scoring the pilot.
        if skip_score:
            out["score"] = {"skipped": True}
            out["score_pdf"] = {"skipped": True}
        else:
            from catalan.scoring.scorer import ARM_PDF, TRANSPORT_SYNC, score_pending

            _guarded("score_pdf", out, lambda: score_pending(
                model_id, transport=TRANSPORT_SYNC, limit=limit, conn=conn,
                arm=ARM_PDF, since=STUDY_CLOCK_START, universe=True))
            _guarded("score", out, lambda: score_pending(
                model_id, transport=transport, limit=limit, conn=conn,
                since=STUDY_CLOCK_START, universe=True))

        # 6. Archive text, verdicts and the run's model check, keyed by today's
        #    IST date (the clock scored_at and extracted_at are written on).
        today = today_ist()
        out["archive"]["scores"] = archive.export_scores(today, conn=conn)
        out["archive"]["pdf_text"] = archive.export_pdf_texts(today, conn=conn)
        if not skip_score:
            from catalan.clock import now_ist
            _guarded("model_check", out, lambda: archive.append_model_checks(
                today, _model_checks(out, model_id, now_ist())))

        # 7. Buy lists for every counted day that is now complete (all its
        #    filings scored in both arms), each written once. A day whose
        #    scoring failed simply waits for a later run (verifier S1).
        _guarded("buy_list", out, lambda: buy_list.write_ready(
            conn=conn, model_id=model_id, until=day.isoformat()))
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
    ap.add_argument("--result-file",
                    help="also write the result JSON here (the workflow reads this, not stdout)")
    args = ap.parse_args()

    # Yesterday on the IST calendar, whatever the machine clock (UTC in CI).
    day = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
           else date.fromisoformat(today_ist()) - timedelta(days=1))

    result = run(day, skip_score=args.skip_score, model_id=args.model,
                 limit=args.limit, transport=args.transport)
    text = json.dumps(result, indent=2, default=str)
    if args.result_file:
        # Written separately because any library that prints to stdout would
        # otherwise corrupt the JSON the health checks parse (verifier A1).
        with open(args.result_file, "w") as fh:
            fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
