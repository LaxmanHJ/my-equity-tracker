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


def backfill_date(conn, trade_date: date, portfolio_symbols: set) -> int:
    """Fetch MTO for one date and upsert matching portfolio symbols. Returns count inserted."""
    rows = _fetch_mto(trade_date)
    if not rows:
        return 0

    date_str = trade_date.strftime("%Y-%m-%d")
    matching = [r for r in rows if r["symbol"] in portfolio_symbols]
    if not matching:
        return 0

    conn.executemany(
        "INSERT OR REPLACE INTO delivery_data (symbol, date, traded_qty, delivery_qty, delivery_pct) "
        "VALUES (:symbol, :date, :traded_qty, :delivery_qty, :delivery_pct)",
        [{"symbol": r["symbol"], "date": date_str, "traded_qty": r["traded_qty"],
          "delivery_qty": r["delivery_qty"], "delivery_pct": r["delivery_pct"]}
         for r in matching]
    )
    return len(matching)


def main():
    parser = argparse.ArgumentParser(description="Backfill NSE delivery data from MTO files")
    parser.add_argument("--from", dest="from_date", default="2023-01-01",
                        help="Start date YYYY-MM-DD (default 2023-01-01)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="End date YYYY-MM-DD (default today)")
    parser.add_argument("--date", dest="single_date", default=None,
                        help="Fetch a single date YYYY-MM-DD")
    args = parser.parse_args()

    conn = connect()
    conn.execute(CREATE_TABLE_SQL)

    portfolio_symbols = set(load_all_symbols())
    logger.info("Portfolio symbols: %s", portfolio_symbols)

    if args.single_date:
        d = datetime.strptime(args.single_date, "%Y-%m-%d").date()
        count = backfill_date(conn, d, portfolio_symbols)
        logger.info("%s — %d rows upserted", d, count)
        conn.close()
        return

    start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else date.today()

    current = start
    total = 0
    skipped = 0
    while current <= end:
        if current.weekday() >= 5:  # skip weekends
            current += timedelta(days=1)
            continue
        count = backfill_date(conn, current, portfolio_symbols)
        if count == 0:
            skipped += 1
        else:
            total += count
            logger.info("%s — %d rows", current, count)
        current += timedelta(days=1)
        time.sleep(FETCH_DELAY_S)

    logger.info("Done — %d total rows upserted, %d non-trading days skipped", total, skipped)
    conn.close()


if __name__ == "__main__":
    main()
