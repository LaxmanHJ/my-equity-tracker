"""
Phase 1a — the forward collector. **This is the highest-urgency component.**

The forward log accrues exactly one day per calendar day, and it is the only
evidence that counts for the C2 ship gate: Opus 5's training cutoff is May 2026,
so anything scored over pre-cutoff headlines is partly recall rather than
prediction. Every day this does not run is a permanently lost observation that
no amount of later work recovers. Ship it on a schedule before anything else.

Fetches NSE corporate announcements and writes them to the local store raw —
**no category filtering, no dedup at ingest**. Filters are query-time concerns
(see config.CATEGORY_EXCLUSIONS, which is not yet frozen), so the corpus stays
auditable and a change to the exclusion list never forces a refetch.

Session warm-up, chunking and INSERT OR IGNORE follow the pattern already
proven against NSE in quant_engine/data/backfill_earnings.py.

Run:
    python3 -m catalan.data.collect_daily                      # yesterday
    python3 -m catalan.data.collect_daily --date 2026-08-07
    python3 -m catalan.data.collect_daily --from 2026-07-01 --to 2026-08-07
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from catalan.config import (
    INGEST_CHUNK_DAYS,
    NSE_ANNOUNCEMENTS_URL,
    NSE_WARMUP_PAGE,
)
from catalan.data.store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SOURCE = "nse_announcements"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# NSE is inconsistent about which key carries row identity across date ranges,
# so try them in order and fall back to a content hash. seq_id only has to be
# stable for the same row across re-fetches — that is what makes re-runs
# idempotent under UNIQUE(symbol, seq_id).
_SEQ_KEYS = ("seqId", "seq_id", "seqNo", "sm_seq_id", "csvId")

_TS_FORMATS = (
    "%d-%b-%Y %H:%M:%S",
    "%d-%b-%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


# ── NSE client (shared with the Phase 1b historical arm) ──────────────────────

def nse_session() -> requests.Session:
    """Warm a session: NSE hands out cookies on the landing + filings pages."""
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.nseindia.com", timeout=15)
    s.get(NSE_WARMUP_PAGE, timeout=15)
    return s


def parse_ts(val: Optional[str]) -> Optional[str]:
    """'05-Jun-2019 18:23:45' or '2026-06-05 18:23:45' → ISO 8601. None if unparseable."""
    if not val:
        return None
    raw = str(val).strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    logger.warning("Unparseable an_dt: %r", raw)
    return None


def _seq_id(row: Dict[str, Any], announced_at: Optional[str], headline: str) -> str:
    for key in _SEQ_KEYS:
        val = row.get(key)
        if val not in (None, "", "-"):
            return str(val)
    digest = hashlib.sha256(
        f"{announced_at}|{headline}".encode("utf-8", "replace")
    ).hexdigest()[:24]
    return f"h:{digest}"


def normalise(row: Dict[str, Any]) -> Optional[Tuple]:
    """NSE JSON row → an announcements tuple. None if unusable."""
    symbol = (row.get("symbol") or "").strip().upper()
    headline = (row.get("attchmntText") or "").strip()
    announced_at = parse_ts(row.get("an_dt"))

    # The June-2026 probe found zero blank headlines in 6,395 in-universe rows,
    # so a blank one means the response shape changed — worth seeing in the log.
    if not symbol or not headline or not announced_at:
        return None

    return (
        symbol,
        (row.get("sm_isin") or "").strip() or None,
        (row.get("sm_name") or "").strip() or None,
        announced_at,
        headline,
        (row.get("desc") or "").strip() or None,
        (row.get("smIndustry") or "").strip() or None,
        _seq_id(row, announced_at, headline),
        SOURCE,
        datetime.now().isoformat(timespec="seconds"),
    )


def fetch_window(session: requests.Session, frm: date, to: date,
                 retries: int = 3) -> List[Dict[str, Any]]:
    """Fetch one date window. NSE serves a full quarter in a single response."""
    params = {
        "index": "equities",
        "from_date": frm.strftime("%d-%m-%Y"),
        "to_date": to.strftime("%d-%m-%Y"),
    }
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            resp = session.get(NSE_ANNOUNCEMENTS_URL, params=params, timeout=45)
            resp.raise_for_status()
            payload = resp.json()
            # NSE returns a bare list here, but has used {"data": [...]} elsewhere.
            if isinstance(payload, dict):
                payload = payload.get("data", [])
            return payload if isinstance(payload, list) else []
        except Exception as exc:            # network, JSON, or 401 on a cold cookie
            last_exc = exc
            logger.warning("fetch %s→%s attempt %d failed: %s", frm, to, attempt + 1, exc)
            time.sleep(2 ** attempt)
            session = nse_session()          # cookies expire; re-warm and retry
    raise RuntimeError(f"fetch {frm}→{to} failed after {retries} attempts") from last_exc


def _chunks(frm: date, to: date, days: int) -> Iterable[Tuple[date, date]]:
    cur = frm
    while cur <= to:
        end = min(cur + timedelta(days=days - 1), to)
        yield cur, end
        cur = end + timedelta(days=1)


# ── Persistence ───────────────────────────────────────────────────────────────

INSERT_SQL = """
INSERT OR IGNORE INTO announcements
    (symbol, isin, company, announced_at, headline, category, industry,
     seq_id, source, ingested_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def persist(conn: sqlite3.Connection, rows: List[Tuple]) -> int:
    """Insert rows, returning how many were actually new."""
    if not rows:
        return 0
    before = conn.execute("SELECT count(*) FROM announcements").fetchone()[0]
    conn.executemany(INSERT_SQL, rows)
    conn.commit()
    after = conn.execute("SELECT count(*) FROM announcements").fetchone()[0]
    return after - before


def collect(frm: date, to: date, conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """Fetch [frm, to] and write to the local store. Idempotent."""
    own_conn = conn is None
    conn = conn or store()
    started = datetime.now().isoformat(timespec="seconds")

    run_id = conn.execute(
        "INSERT INTO ingest_runs (source, window_from, window_to, status, started_at) "
        "VALUES (?, ?, ?, 'running', ?)",
        (SOURCE, frm.isoformat(), to.isoformat(), started),
    ).lastrowid
    conn.commit()

    fetched = inserted = 0
    try:
        session = nse_session()
        for c_from, c_to in _chunks(frm, to, INGEST_CHUNK_DAYS):
            raw = fetch_window(session, c_from, c_to)
            rows = [r for r in (normalise(x) for x in raw) if r is not None]
            dropped = len(raw) - len(rows)
            if dropped:
                logger.warning("%s→%s: dropped %d unusable rows", c_from, c_to, dropped)
            new = persist(conn, rows)
            fetched += len(raw)
            inserted += new
            logger.info("%s→%s: %d fetched, %d new", c_from, c_to, len(raw), new)
    except Exception as exc:
        conn.execute(
            "UPDATE ingest_runs SET status='error', detail=?, rows_fetched=?, "
            "rows_inserted=?, finished_at=? WHERE id=?",
            (str(exc)[:500], fetched, inserted,
             datetime.now().isoformat(timespec="seconds"), run_id),
        )
        conn.commit()
        raise

    conn.execute(
        "UPDATE ingest_runs SET status='ok', rows_fetched=?, rows_inserted=?, "
        "finished_at=? WHERE id=?",
        (fetched, inserted, datetime.now().isoformat(timespec="seconds"), run_id),
    )
    conn.commit()
    if own_conn:
        conn.close()

    return {"fetched": fetched, "inserted": inserted}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> None:
    ap = argparse.ArgumentParser(description="CATALAN forward collector (Phase 1a)")
    ap.add_argument("--date", type=_parse_date, help="single day (default: yesterday)")
    ap.add_argument("--from", dest="frm", type=_parse_date, help="catch-up window start")
    ap.add_argument("--to", dest="to", type=_parse_date, help="catch-up window end")
    args = ap.parse_args()

    if args.frm:
        frm, to = args.frm, (args.to or date.today())
    elif args.date:
        frm = to = args.date
    else:
        frm = to = date.today() - timedelta(days=1)

    if frm > to:
        ap.error(f"--from ({frm}) is after --to ({to})")

    result = collect(frm, to)
    logger.info("done: %d fetched, %d new", result["fetched"], result["inserted"])


if __name__ == "__main__":
    main()
