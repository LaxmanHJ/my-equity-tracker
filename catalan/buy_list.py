"""
The daily buy list — the model's YES verdicts for one announcement day.

Written once per day by the pipeline to
``catalan/data/forward_log/buy_list/YYYY-MM-DD.md``, so it is committed with
the day's headlines and verdicts and can be read on GitHub from a phone.

**What it is and is not.** It is the model's full YES list: the object the
amended pre-registration tests. It is not advice, and it is not a trading
signal until the C2 gate says so. It deliberately carries no prices and no
returns (the NO-PEEK rule), and it is never rewritten once written, so each
file is a timestamped record of what the model said before the outcome.

Verdict per announcement: the PDF arm's when it has one (the model read the
filing), otherwise the headline arm's, flagged "headline only".

Run:
    python3 -m catalan.buy_list --date 2026-10-01
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from catalan.clock import today_ist
from catalan.config import PRIMARY_MODEL, STUDY_CLOCK_START, STUDY_VERSION
from catalan.data.archive import ARCHIVE_ROOT
from catalan.data.collect_daily import now_ist
from catalan.data.panel import OVERNIGHT, classify_session
from catalan.data.store import store
from catalan.scoring import filters
from catalan.scoring.scorer import ARM_HEADLINE, ARM_PDF, arm_hash, temperature_for

logger = logging.getLogger(__name__)

BUY_LIST_DIR = ARCHIVE_ROOT / "buy_list"

# A filing whose PDF download keeps failing must not hold its day's list back
# forever. After this many days the list is written with whatever verdicts exist
# (that filing falls back to its headline verdict, labelled as such).
PDF_WAIT_DAYS = 3
# Same escape for a day held back by filings whose scoring keeps failing (e.g.
# an input that always comes back empty). After this many days the list is
# written, and it states how many filings are still unscored.
SCORE_WAIT_DAYS = 7     # long enough for a results-season quota backlog to clear

DAY_SQL = """
SELECT a.id, a.symbol, a.company, a.headline, a.category, a.announced_at, a.attachment_size,
       sp.score AS pdf_score, sp.rationale AS pdf_rationale,
       sh.score AS hl_score,  sh.rationale AS hl_rationale
FROM announcements a
LEFT JOIN scores sp ON sp.announcement_id = a.id AND sp.model_id = ? AND sp.prompt_hash = ?
LEFT JOIN scores sh ON sh.announcement_id = a.id AND sh.model_id = ? AND sh.prompt_hash = ?
WHERE substr(a.announced_at, 1, 10) = ?
ORDER BY a.announced_at, a.id
"""


def _members(day: str) -> Optional[frozenset]:
    """The catalan-v2 study universe (local file, no Turso), or None if the
    frozen file cannot be read. ``day`` is unused: the universe is fixed for
    the whole counted period (Amendment 1)."""
    try:
        from catalan.data import universe
        return universe.study_universe()
    except Exception as exc:                             # noqa: BLE001
        logger.warning("buy list: study universe unavailable (%s)", type(exc).__name__)
        return None


def entries(day: str, conn: sqlite3.Connection, model_id: str = None) -> List[Dict]:
    """Every YES for ``day`` after the study's own exclusions and dedup."""
    model_id = model_id or PRIMARY_MODEL
    rows = [dict(r) for r in conn.execute(
        DAY_SQL, (model_id, arm_hash(ARM_PDF), model_id, arm_hash(ARM_HEADLINE), day))]
    kept, _ = filters.apply_all(rows)
    out = []
    for r in kept:
        if r["pdf_score"] is not None:
            score, reason, source = r["pdf_score"], r["pdf_rationale"], "filing"
        else:
            score, reason, source = r["hl_score"], r["hl_rationale"], "headline only"
        if score != 1:
            continue
        session = classify_session(datetime.fromisoformat(r["announced_at"]))
        out.append({**r, "reason": reason, "source": source, "overnight": session == OVERNIGHT})
    return out


def _table(items: List[Dict]) -> str:
    if not items:
        return "_None._\n"
    lines = ["| Time (IST) | Symbol | Company | Category | Read from | Headline | Model's reason |",
             "|---|---|---|---|---|---|---|"]
    for e in items:
        cells = [e["announced_at"][11:16], e["symbol"], e["company"] or "", e["category"] or "",
                 e["source"], (e["headline"] or "")[:160], (e["reason"] or "")[:220]]
        lines.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def render(day: str, conn: sqlite3.Connection, model_id: str = None) -> str:
    model_id = model_id or PRIMARY_MODEL
    items = entries(day, conn, model_id)
    members = _members(day)
    in_uni = (lambda e: e["symbol"] in members) if members is not None else (lambda e: False)
    study = [e for e in items if e["overnight"] and in_uni(e)]
    other_overnight = [e for e in items if e["overnight"] and not in_uni(e)]
    intraday = [e for e in items if not e["overnight"]]
    roster = ("Study universe unavailable in this run: every item is listed under "
              "'other companies'." if members is None else "")
    from catalan.scoring.scorer import ARM_PDF, pending
    unscored = sum(
        1 for arm in (ARM_PDF, None)
        for r in pending(model_id, conn=conn, since=day, universe=True,
                         **({"arm": arm} if arm else {}))
        if (r["announced_at"] or "")[:10] == day)
    roster += (f" {unscored} filing-arm verdicts were still missing when this list was "
               f"written (scoring kept failing); those filings are not listed." if unscored else "")
    return "\n".join([
        f"# CATALAN buy list — announcements of {day}",
        "",
        f"Generated {now_ist()} IST · {STUDY_VERSION} · {model_id} · "
        f"temperature {temperature_for(model_id)} (configured)",
        "",
        "**Not advice.** This is the model's YES list, recorded before the outcome. "
        "The study has not shown an edge yet; nothing here should be traded with "
        "real money before the C2 verdict.",
        "",
        f"{len(items)} YES verdicts: {len(study)} overnight in the NIFTY500 (the study "
        f"basket, bought at the open of the session each item is attributed to), "
        f"{len(other_overnight)} overnight elsewhere, "
        f"{len(intraday)} during market hours (not part of the study). {roster}",
        "",
        "## Study basket — overnight news, NIFTY500",
        _table(study),
        "## Overnight news, other companies",
        _table(other_overnight),
        "## Published during market hours (not traded by the study)",
        _table(intraday),
    ])


def ready_days(conn: sqlite3.Connection, model_id: str = None,
               since: str = STUDY_CLOCK_START, until: str = None,
               today: Optional[str] = None) -> List[str]:
    """Counted days whose filings are all scored in both arms.

    A day with any pending headline-arm or PDF-arm row is not ready: writing it
    would freeze a partial list as evidence (verifier S1). Nor is a day with
    unextracted attachments, until PDF_WAIT_DAYS have passed.
    """
    from catalan.data import pdf_text
    from catalan.scoring.scorer import ARM_PDF, pending

    model_id = model_id or PRIMARY_MODEL
    # Never today: NSE keeps filing until ~23:59 IST, so today is incomplete
    # and its list would be frozen partial (verifier review 2).
    yesterday = (date.fromisoformat(today or today_ist()) - timedelta(days=1)).isoformat()
    until = min(until or yesterday, yesterday)

    def days(rows):
        return {(r["announced_at"] or "")[:10] for r in rows}

    # Universe only: filings outside it are never scored, so counting them as
    # "pending" would hold every day back forever.
    blocked = (days(pending(model_id, conn=conn, since=since, universe=True))
               | days(pending(model_id, conn=conn, arm=ARM_PDF, since=since, universe=True)))
    waiting = days(pdf_text.pending(conn, since=since, universe=True))
    present = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(announced_at, 1, 10) FROM announcements "
        "WHERE substr(announced_at, 1, 10) BETWEEN ? AND ? ORDER BY 1", (since, until))]
    ready = []
    for d in present:
        age = (date.fromisoformat(until) - date.fromisoformat(d)).days
        if d in blocked and age < SCORE_WAIT_DAYS:
            continue
        if d in waiting and age < PDF_WAIT_DAYS:
            continue
        ready.append(d)
    return ready


def write_ready(conn: Optional[sqlite3.Connection] = None, model_id: str = None,
                since: str = STUDY_CLOCK_START, until: str = None,
                today: Optional[str] = None) -> Dict:
    """Write the list for every ready counted day that has none yet."""
    own = conn is None
    conn = conn or store()
    try:
        written = [d for d in ready_days(conn, model_id, since, until, today)
                   if write(d, conn=conn, model_id=model_id)["written"]]
    finally:
        if own:
            conn.close()
    return {"written": written}


def write(day: str, conn: Optional[sqlite3.Connection] = None,
          model_id: str = None) -> Dict:
    """Write the day's list once. An existing file is never rewritten."""
    path = BUY_LIST_DIR / f"{day}.md"
    if path.exists():
        return {"path": str(path), "written": False, "reason": "already exists"}
    own = conn is None
    conn = conn or store()
    try:
        text = render(day, conn, model_id)
    finally:
        if own:
            conn.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return {"path": str(path), "written": True}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="CATALAN daily buy list")
    ap.add_argument("--date", required=True, help="announcement day, YYYY-MM-DD")
    ap.add_argument("--print", action="store_true", help="print instead of writing the file")
    args = ap.parse_args()
    if args.print:
        conn = store()
        try:
            print(render(args.date, conn))
        finally:
            conn.close()
    else:
        print(write(args.date))


if __name__ == "__main__":
    main()
