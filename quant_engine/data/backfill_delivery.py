"""
Backfill NSE delivery percentage data from MTO files.

NSE publishes daily Security Wise Delivery Position files (MTO) at:
  https://nsearchives.nseindia.com/archives/equities/mto/MTO_{DDMMYYYY}.DAT

Each file contains delivery quantity and % for all NSE equities.
We filter for portfolio symbols and upsert into the delivery_data table.

Circuit hit detection:
  - Upper circuit: close == high (and pct_change > 0)
  - Lower circuit: close == -1 (we mark -1), upper = +1, none = 0
  Actually: we detect circuit from price_history where close == high AND it's the day high = previous_close * 1.20 (approx)
  Simpler: just store delivery data; circuit detection is optional enhancement

Run:
    python3 -m quant_engine.data.backfill_delivery --from 2023-01-01
    python3 -m quant_engine.data.backfill_delivery --date 2025-03-26
"""
import argparse
import logging
import time
from datetime import date, timedelta, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.data.turso_client import connect
from quant_engine.data.loader import load_all_symbols

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}
BASE_URL = "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{date}.DAT"
FETCH_DELAY_S = 0.5

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS delivery_data (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    date          TEXT NOT NULL,
    traded_qty    INTEGER,
    delivery_qty  INTEGER,
    delivery_pct  REAL,
    circuit_hit   INTEGER DEFAULT 0,
    UNIQUE(symbol, date)
)
"""


def _fetch_mto(trade_date: date) -> list[dict]:
    """Download and parse MTO file for a given date. Returns list of {symbol, traded_qty, delivery_qty, delivery_pct}."""
    date_str = trade_date.strftime("%d%m%Y")
    url = BASE_URL.format(date=date_str)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return []  # holiday / non-trading day
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return []

    rows = []
    for line in resp.text.splitlines():
        parts = line.strip().split(",")
        # Record type 20 = equity delivery data
        if len(parts) < 7 or parts[0].strip() != "20":
            continue
        try:
            symbol = parts[2].strip()
            series = parts[3].strip()
            if series != "EQ":
                continue  # skip non-equity series
            traded_qty = int(parts[4].strip())
            delivery_qty = int(parts[5].strip())
            delivery_pct = float(parts[6].strip())
            rows.append({
                "symbol": symbol,
                "traded_qty": traded_qty,
                "delivery_qty": delivery_qty,
                "delivery_pct": delivery_pct,
            })
        except (ValueError, IndexError):
            continue

    return rows


def _flush_rows(conn, rows: list) -> int:
    """Multi-row VALUES upsert. One statement per ~1500 rows.

    Turso's pipeline endpoint has ~40ms of per-STATEMENT overhead, so the
    old one-statement-per-row executemany cost ~12s per trading day; a
    single multi-row INSERT does the same day in ~1s (measured 2026-06-10).
    """
    CHUNK = 1500  # × 5 params = 7.5k binds, well under SQLite's 32k limit
    written = 0
    cur = conn.cursor()
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        placeholders = ",".join(["(?,?,?,?,?)"] * len(chunk))
        params = []
        for r in chunk:
            params.extend([r["symbol"], r["date"], r["traded_qty"],
                           r["delivery_qty"], r["delivery_pct"]])
        cur.execute(
            "INSERT OR REPLACE INTO delivery_data "
            "(symbol, date, traded_qty, delivery_qty, delivery_pct) VALUES " + placeholders,
            params,
        )
        written += len(chunk)
    return written


def _fetch_filtered(trade_date: date, portfolio_symbols: set) -> list:
    """Fetch + parse one MTO file, returning rows for tracked symbols only."""
    rows = _fetch_mto(trade_date)
    date_str = trade_date.strftime("%Y-%m-%d")
    return [
        {**r, "date": date_str}
        for r in rows
        if r["symbol"] in portfolio_symbols
    ]


def backfill_date(conn, trade_date: date, portfolio_symbols: set) -> int:
    """Fetch MTO for one date and upsert matching portfolio symbols. Returns count inserted."""
    matching = _fetch_filtered(trade_date, portfolio_symbols)
    if not matching:
        return 0
    return _flush_rows(conn, matching)


def main():
    parser = argparse.ArgumentParser(description="Backfill NSE delivery data from MTO files")
    parser.add_argument("--from", dest="from_date", default="2023-01-01",
                        help="Start date YYYY-MM-DD (default 2023-01-01)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="End date YYYY-MM-DD (default today)")
    parser.add_argument("--date", dest="single_date", default=None,
                        help="Fetch a single date YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel date workers (default 8; 1 = serial). "
                             "MTO files are static CDN objects and the upsert "
                             "is idempotent, so dates parallelize cleanly.")
    args = parser.parse_args()

    conn = connect()
    conn.execute(CREATE_TABLE_SQL)

    portfolio_symbols = set(load_all_symbols())
    logger.info("Portfolio symbols: %d", len(portfolio_symbols))

    if args.single_date:
        d = datetime.strptime(args.single_date, "%Y-%m-%d").date()
        count = backfill_date(conn, d, portfolio_symbols)
        logger.info("%s — %d rows upserted", d, count)
        conn.close()
        return
    conn.close()

    start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else date.today()
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)

    # Parallel FETCH, serial WRITE. libSQL is single-writer — concurrent
    # batch writes just lock-contend (the 2026-06-10 8-worker run stalled
    # to zero throughput). MTO files are static CDN objects and fetch in
    # ~0.2s, so workers only fetch+parse; the main thread accumulates rows
    # and flushes multi-row VALUES inserts.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = 0
    skipped = 0
    done = 0
    pending: list = []
    conn = connect()
    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as pool:
        futures = [pool.submit(_fetch_filtered, d, portfolio_symbols) for d in days]
        for fut in as_completed(futures):
            try:
                rows = fut.result()
            except Exception as exc:
                logger.warning("date failed: %s", exc)
                skipped += 1
                continue
            done += 1
            if rows:
                pending.extend(rows)
            else:
                skipped += 1
            if len(pending) >= 1500:
                total += _flush_rows(conn, pending)
                pending = []
            if done % 200 == 0:
                logger.info("progress: %d/%d days, %d rows written", done, len(days), total)
    if pending:
        total += _flush_rows(conn, pending)
    conn.close()

    logger.info("Done — %d total rows upserted, %d non-trading/empty days skipped", total, skipped)


if __name__ == "__main__":
    main()
