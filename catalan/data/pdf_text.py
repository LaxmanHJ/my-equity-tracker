"""
PDF attachment text — the input to the PDF scoring arm.

A headline is often only a filing's label: "has submitted to the Exchange the
financial results" carries no information about whether the results were good.
The attached PDF does. This module downloads each attachment once, extracts the
first ``PDF_PAGE_WINDOW`` pages, and stores the text so it can be archived.

**What is stored, and what is not.** Only deterministic outcomes of a completed
download are recorded:

    ok          every page in the window has a usable text layer → text stored
    unreadable  some page in the window is a scan or a broken font map → the
                filing is scored from its headline alone, and is flagged
    not_pdf     the attachment is not a PDF (XML, HTML error page, or a zip
                with no PDF inside)

**ZIP attachments.** NSE files most director/KMP resignations as a .zip (331
of 337 in the pilot). The first ``.pdf`` member by name is read, under the same
rules; a zip with no PDF inside is ``not_pdf``. The hash is of the zip as
downloaded.

A network failure is NOT an outcome. It is logged and the row stays pending, so
the next run retries it. Recording a failure as if it were a verdict on the
document is exactly how the scorer silently lost a week of scores in 2026-08.

**Why the whole filing falls back on one bad page.** The rule is deliberately
all-or-nothing. Scoring the readable pages of a partly-scanned filing would feed
the model a covering letter whose substance is on the scanned page, which is a
headline with extra boilerplate — a third, undeclared instrument.

Run:
    python3 -m catalan.data.pdf_text --pending                  # everything unextracted
    python3 -m catalan.data.pdf_text --pending --date 2026-09-25
    python3 -m catalan.data.pdf_text --pending --limit 50 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
import sqlite3
import time
import zipfile
from datetime import datetime
from typing import Dict, List, Optional

import fitz  # PyMuPDF
import requests

from catalan.config import (
    PDF_GARBLE_ALLOWED,
    PDF_GARBLE_MAX,
    PDF_MAX_CHARS,
    PDF_MIN_PAGE_CHARS,
    PDF_PAGE_WINDOW,
)
from catalan.clock import now_ist
from catalan.data.collect_daily import HEADERS
from catalan.data.store import store
from catalan.scoring import filters

logger = logging.getLogger(__name__)

EXTRACTOR = f"pymupdf-{fitz.VersionBind}"

# MuPDF prints its own error messages straight to stdout, bypassing Python
# (verifier A1): one truncated PDF wrote 50 lines into the JSON the workflow
# parses. The document's status already records what went wrong.
fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)

# Non-ASCII characters outside PDF_GARBLE_ALLOWED, counted against a page's
# letters, signal a broken font character map: the extracted text is noise
# (e.g. "ǼÌŘŘȖĶǊ" for an AGM notice).
_GARBLE_RE = re.compile(f"[^\\x00-\\x7F{PDF_GARBLE_ALLOWED}]")
_SPACES_RE = re.compile(r"[ \t]+")

REQUEST_PAUSE_SECONDS = 0.3
FETCH_TIMEOUT_SECONDS = 90
ZIP_MEMBER_MAX_BYTES = 100 * 1024 * 1024   # refuse a member that inflates beyond this


def _pdf_from_zip(data: bytes) -> Optional[bytes]:
    """The first .pdf member by name, or None. Deterministic: sorted names."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = sorted(
                (i for i in zf.infolist()
                 if i.filename.lower().endswith(".pdf") and not i.is_dir()),
                key=lambda i: i.filename)
            if not members or members[0].file_size > ZIP_MEMBER_MAX_BYTES:
                return None
            return zf.read(members[0])
    except Exception:                                    # noqa: BLE001
        # Corrupt deflate data raises zlib.error, not BadZipFile. Any failure
        # to unpack fixed bytes is a fixed outcome: no PDF to read.
        return None


def classify_page(text: str) -> str:
    """TEXT, NO_TEXT (a scan: no text layer) or GARBLED (a broken font map)."""
    stripped = text.strip()
    if len(stripped) < PDF_MIN_PAGE_CHARS:
        return "NO_TEXT"
    letters = sum(ch.isalpha() for ch in stripped) or 1
    if len(_GARBLE_RE.findall(stripped)) / letters > PDF_GARBLE_MAX:
        return "GARBLED"
    return "TEXT"


def extract(pdf_bytes: bytes) -> Dict:
    """Extract the page window from one attachment. Pure: no I/O, no clock."""
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    base = {"sha256": digest, "extractor": EXTRACTOR, "total_pages": None,
            "page_kinds": None, "text": None, "truncated": 0}
    if pdf_bytes.startswith(b"PK\x03\x04"):
        pdf_bytes = _pdf_from_zip(pdf_bytes) or b""
    # A few servers prepend junk before the header; the PDF spec allows the
    # header anywhere in the first 1024 bytes.
    start = pdf_bytes.find(b"%PDF", 0, 1024)
    if start < 0:
        return {**base, "status": "not_pdf"}
    pdf_bytes = pdf_bytes[start:]
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:                                    # noqa: BLE001
        return {**base, "status": "not_pdf"}

    # The bytes are fixed, so every failure below is a fixed outcome about this
    # document, never a reason to crash the day's pipeline (verifier B1: one
    # password-protected PDF would otherwise stop the forward log every day).
    try:
        with doc:
            if doc.needs_pass:
                return {**base, "total_pages": doc.page_count, "status": "unreadable"}
            pages = [_SPACES_RE.sub(" ", doc[i].get_text()).strip()
                     for i in range(min(PDF_PAGE_WINDOW, doc.page_count))]
            total = doc.page_count
    except Exception:                                    # noqa: BLE001
        return {**base, "status": "unreadable"}

    kinds = [classify_page(t) for t in pages]
    out = {**base, "total_pages": total, "page_kinds": kinds}
    if not pages or any(k != "TEXT" for k in kinds):
        return {**out, "status": "unreadable"}

    text = "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(pages, 1))
    if len(text) > PDF_MAX_CHARS:
        return {**out, "status": "ok", "text": text[:PDF_MAX_CHARS], "truncated": 1}
    return {**out, "status": "ok", "text": text}


def fetch(session: requests.Session, url: str) -> Optional[bytes]:
    """Download an attachment. None on any network or HTTP failure — retry later."""
    try:
        r = session.get(url, timeout=FETCH_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("pdf fetch failed %s: %s", url, type(exc).__name__)
        return None
    if not r.ok:
        logger.warning("pdf fetch HTTP %s %s", r.status_code, url)
        return None
    if r.headers.get("content-type", "").lower().startswith("text/html"):
        # An error page served with 200. Not a fact about the document: retry.
        logger.warning("pdf fetch got HTML instead of a document %s", url)
        return None
    return r.content


PENDING_SQL = """
SELECT a.id, a.symbol, a.headline, a.category, a.announced_at, a.attachment_url
FROM announcements a
LEFT JOIN pdf_texts p ON p.announcement_id = a.id
WHERE p.announcement_id IS NULL
  AND a.attachment_url LIKE 'http%'      -- not NULL, '' or NSE's '-' placeholder
  {day_clause}
ORDER BY a.announced_at
"""


def pending(conn: sqlite3.Connection, day: Optional[str] = None,
            since: Optional[str] = None, universe: bool = False) -> List[Dict]:
    """Announcements whose attachment has not been extracted yet.

    Cross-sectional like the scorer: no symbol filter. The declared category and
    headline exclusions are applied so no bandwidth is spent on filings that can
    never be scored. Dedup is deliberately NOT applied here — which member of a
    near-duplicate cluster survives is a scoring-time decision.
    """
    clauses, params = [], []
    if day:
        clauses.append("AND substr(a.announced_at, 1, 10) = ?")
        params.append(day)
    if since:
        clauses.append("AND substr(a.announced_at, 1, 10) >= ?")
        params.append(since)
    rows = [dict(r) for r in conn.execute(PENDING_SQL.format(day_clause=" ".join(clauses)), params)]
    if universe:                       # the declared study universe only (Amendment 1)
        from catalan.data.universe import study_universe
        members = study_universe()
        rows = [r for r in rows if r["symbol"] in members]
    return [r for r in rows if not filters.is_excluded(r)]


def _store_result(conn: sqlite3.Connection, announcement_id: int, res: Dict) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pdf_texts (announcement_id, status, total_pages, "
        "page_kinds, text, truncated, sha256, extractor, extracted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (announcement_id, res["status"], res["total_pages"],
         json.dumps(res["page_kinds"]) if res["page_kinds"] is not None else None,
         res["text"], res["truncated"], res["sha256"], res["extractor"],
         now_ist()),
    )


def extract_pending(conn: Optional[sqlite3.Connection] = None, day: Optional[str] = None,
                    limit: Optional[int] = None, dry_run: bool = False,
                    session: Optional[requests.Session] = None,
                    since: Optional[str] = None, universe: bool = False) -> Dict[str, int]:
    """Download and extract every pending attachment. Idempotent."""
    own = conn is None
    conn = conn or store()
    counts = {"pending": 0, "ok": 0, "unreadable": 0, "not_pdf": 0,
              "fetch_failed": 0, "extract_error": 0, "truncated": 0}
    try:
        todo = pending(conn, day, since, universe)
        counts["pending"] = len(todo)
        if limit:
            todo = todo[:limit]
        if dry_run:
            return {**counts, "would_extract": len(todo)}

        if session is None:
            session = requests.Session()
            session.headers.update(HEADERS)
        for n, row in enumerate(todo, 1):
            content = fetch(session, row["attachment_url"])
            if content is None:
                counts["fetch_failed"] += 1
            else:
                try:
                    res = extract(content)
                except Exception:                        # noqa: BLE001
                    # Last-resort guard: extract() handles document faults itself,
                    # so this is a bug, not a document fact. Not stored; retried.
                    logger.exception("pdf_text: extract crashed on %s", row["attachment_url"])
                    counts["extract_error"] += 1
                else:
                    _store_result(conn, row["id"], res)
                    counts[res["status"]] += 1
                    counts["truncated"] += res["truncated"]
            if n % 50 == 0:
                conn.commit()
                logger.info("pdf_text: %d/%d extracted", n, len(todo))
            time.sleep(REQUEST_PAUSE_SECONDS)
        conn.commit()
    finally:
        if own:
            conn.close()

    logger.info("pdf_text: %s", counts)
    if counts["fetch_failed"]:
        logger.warning("pdf_text: %d downloads failed and stay pending for the next run",
                       counts["fetch_failed"])
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="CATALAN — extract PDF attachment text")
    ap.add_argument("--pending", action="store_true", required=True,
                    help="extract every attachment not yet extracted")
    ap.add_argument("--date", help="only announcements from this day (YYYY-MM-DD)")
    ap.add_argument("--since", help="only announcements on or after this day (YYYY-MM-DD)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true", help="count, download nothing")
    args = ap.parse_args()
    print(json.dumps(extract_pending(day=args.date, limit=args.limit,
                                     dry_run=args.dry_run, since=args.since), indent=2))


if __name__ == "__main__":
    main()
