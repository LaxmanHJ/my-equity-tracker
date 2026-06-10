"""
Backfill earnings announcement events from NSE financial-results filings.

NSE's corporate-filings API serves timestamped quarterly-results broadcasts
(verified 2026-06-10: reaches back to at least 2019, uncapped):

  https://www.nseindia.com/api/corporates-financial-results
      ?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY&period=Quarterly

Each row carries `broadCastDate` ("31-Jan-2024 21:25:51") — the moment the
result hit the exchange — plus symbol, fiscal window, audited/consolidated
flags. This is the PEAD event source (SIC-93): the broadcast timestamp
decides whether the market could react the same session or only the next.

Turso write discipline (2026-06-10 lessons): multi-row VALUES INSERT chunked
~1500 rows, written serially — the pipeline endpoint has ~40ms per-STATEMENT
overhead and libSQL is single-writer, so row-per-statement executemany and
parallel writers are both pathological. Fetches parallelize fine.

Sources (2026-06-10):
  legacy     — /api/corporates-financial-results. Reaches 2019 but fades
               after early 2025 (SEBI moved filings to the integrated format).
  integrated — /api/integrated-filing-results?type=Integrated Filing-
               Financials. Covers ~2024-Q3 onward. Default page size is 20;
               `size=<n>` returns the full window (probed: Nov-2025 = 3,270
               rows in one response).
Both map into the same earnings_events table; UNIQUE key dedupes overlap.

Run:
    python3 -m quant_engine.data.backfill_earnings --from 2019-01-01
    python3 -m quant_engine.data.backfill_earnings --from 2024-10-01 --source integrated
"""
import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.data.turso_client import connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
API_URL = "https://www.nseindia.com/api/corporates-financial-results"
INTEGRATED_URL = "https://www.nseindia.com/api/integrated-filing-results"
WARMUP_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS earnings_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT NOT NULL,
    broadcast_ts   TEXT NOT NULL,
    period_start   TEXT,
    period_end     TEXT,
    relating_to    TEXT,
    audited        TEXT,
    consolidated   TEXT,
    financial_year TEXT,
    seq_number     TEXT,
    UNIQUE(symbol, period_end, consolidated, broadcast_ts)
)
"""

INSERT_COLS = ("symbol", "broadcast_ts", "period_start", "period_end",
               "relating_to", "audited", "consolidated", "financial_year", "seq_number")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.nseindia.com", timeout=15)
    s.get(WARMUP_PAGE, timeout=15)
    return s


def _parse_ts(val: str):
    """'31-Jan-2024 21:25:51' (or without seconds) → '2024-01-31T21:25:51'."""
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(val).strip(), fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def _parse_date(val: str):
    """'01-Oct-2023' → '2023-10-01'."""
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _month_chunks(start: date, end: date):
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(year=cur.year + 1, month=1) if cur.month == 12
               else cur.replace(month=cur.month + 1))
        yield max(cur, start), min(nxt - timedelta(days=1), end)
        cur = nxt


def _fetch_month(session: requests.Session, frm: date, to: date) -> list[dict]:
    resp = session.get(API_URL, params={
        "index": "equities",
        "from_date": frm.strftime("%d-%m-%Y"),
        "to_date": to.strftime("%d-%m-%Y"),
        "period": "Quarterly",
    }, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    raw = data if isinstance(data, list) else data.get("data", [])
    rows = []
    for r in raw:
        symbol = str(r.get("symbol", "")).strip()
        ts = _parse_ts(r.get("broadCastDate"))
        if not symbol or not ts:
            continue
        rows.append({
            "symbol":         symbol,
            "broadcast_ts":   ts,
            "period_start":   _parse_date(r.get("fromDate")),
            "period_end":     _parse_date(r.get("toDate")),
            "relating_to":    str(r.get("relatingTo", "")).strip(),
            "audited":        str(r.get("audited", "")).strip(),
            "consolidated":   str(r.get("consolidated", "")).strip(),
            "financial_year": str(r.get("financialYear", "")).strip(),
            "seq_number":     str(r.get("seqNumber", "")).strip(),
        })
    return rows


def _fetch_month_integrated(session: requests.Session, frm: date, to: date) -> list[dict]:
    """SEBI integrated-filing format (~2024-Q3 onward)."""
    resp = session.get(INTEGRATED_URL, params={
        "index": "equities",
        "from_date": frm.strftime("%d-%m-%Y"),
        "to_date": to.strftime("%d-%m-%Y"),
        "type": "Integrated Filing- Financials",
        "size": "25000",
    }, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    raw = data if isinstance(data, list) else data.get("data", [])
    rows = []
    for r in raw:
        symbol = str(r.get("symbol", "")).strip()
        ts = _parse_ts(r.get("broadcast_Date"))
        if not symbol or not ts:
            continue
        rows.append({
            "symbol":         symbol,
            "broadcast_ts":   ts,
            "period_start":   None,
            "period_end":     _parse_date(r.get("qe_Date")),
            "relating_to":    str(r.get("type_Sub", "")).strip(),
            "audited":        str(r.get("audited", "")).strip(),
            "consolidated":   str(r.get("consolidated", "")).strip(),
            "financial_year": "",
            "seq_number":     str(r.get("seq_Id", "")).strip(),
        })
    return rows


def _flush_rows(conn, rows: list) -> int:
    """Multi-row VALUES upsert, ~1500 rows (9 cols → 13.5k binds < 32k limit)."""
    CHUNK = 1500
    written = 0
    cur = conn.cursor()
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        placeholders = ",".join(["(" + ",".join("?" * len(INSERT_COLS)) + ")"] * len(chunk))
        params = []
        for r in chunk:
            params.extend(r[c] for c in INSERT_COLS)
        cur.execute(
            f"INSERT OR IGNORE INTO earnings_events ({','.join(INSERT_COLS)}) "
            f"VALUES {placeholders}",
            params,
        )
        written += len(chunk)
    return written


def backfill(from_date: date, to_date: date, workers: int = 6, source: str = "legacy") -> int:
    fetcher = _fetch_month if source == "legacy" else _fetch_month_integrated
    conn = connect()
    conn.execute(CREATE_TABLE_SQL)

    months = list(_month_chunks(from_date, to_date))
    session = _session()

    def _worker(chunk):
        frm, to = chunk
        time.sleep(0.5)
        try:
            return frm, fetcher(session, frm, to)
        except Exception:
            # One retry on a fresh session — NSE cookies go stale under load.
            local = _session()
            return frm, fetcher(local, frm, to)

    total = 0
    pending: list = []
    by_year: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, m) for m in months]
        for fut in as_completed(futures):
            try:
                frm, rows = fut.result()
            except Exception as exc:
                logger.warning("month failed permanently: %s", exc)
                continue
            by_year[frm.year] = by_year.get(frm.year, 0) + len(rows)
            pending.extend(rows)
            logger.info("%s: %d filings", frm.strftime("%Y-%m"), len(rows))
            if len(pending) >= 1500:
                total += _flush_rows(conn, pending)
                pending = []
    if pending:
        total += _flush_rows(conn, pending)
    conn.close()

    logger.info("Per-year filings fetched: %s", dict(sorted(by_year.items())))
    return total


def main():
    parser = argparse.ArgumentParser(description="Backfill NSE earnings announcement events")
    parser.add_argument("--from", dest="from_date", default="2019-01-01")
    parser.add_argument("--to", dest="to_date", default=None)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--source", choices=["legacy", "integrated"], default="legacy")
    args = parser.parse_args()

    start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    end = (datetime.strptime(args.to_date, "%Y-%m-%d").date()
           if args.to_date else date.today())
    total = backfill(start, end, workers=args.workers, source=args.source)
    logger.info("Done — %d rows upserted", total)


if __name__ == "__main__":
    main()
