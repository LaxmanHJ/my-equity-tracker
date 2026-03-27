"""
Backfill NSE sector index daily data.

NSE publishes end-of-day data for all ~90 indices at:
  https://nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv

Each file has ~90 rows covering all sectoral, thematic and strategy indices.
We store everything (not just current portfolio needs) to allow future queries.

Run:
    python3 -m quant_engine.data.backfill_sector_indices --from 2023-01-01
    python3 -m quant_engine.data.backfill_sector_indices --date 2025-03-26
"""
import argparse
import io
import logging
import time
from datetime import date, timedelta, datetime
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.data.turso_client import connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}
BASE_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{date}.csv"
FETCH_DELAY_S = 0.5

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sector_indices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    index_name  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    pct_change  REAL,
    pe_ratio    REAL,
    pb_ratio    REAL,
    div_yield   REAL,
    UNIQUE(date, index_name)
)
"""


def _safe_float(val) -> float | None:
    """Convert to float, returning None for blanks/dashes/non-numeric."""
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def _fetch_sector_indices(trade_date: date) -> list[dict]:
    """Download and parse the index EOD CSV for a given date."""
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

    try:
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception as e:
        logger.warning("Failed to parse CSV for %s: %s", trade_date, e)
        return []

    # Normalise column names — NSE header has mixed capitalisation
    df.columns = [c.strip() for c in df.columns]

    # Expected columns (from NSE format):
    # Index Name, Index Date, Open Index Value, High Index Value, Low Index Value,
    # Closing Index Value, Points Change, Change(%), Volume, Turnover (Rs. Cr.), P/E, P/B, Div Yield
    rows = []
    date_str_iso = trade_date.strftime("%Y-%m-%d")
    for _, row in df.iterrows():
        index_name = str(row.get("Index Name", "")).strip()
        if not index_name:
            continue
        rows.append({
            "date":       date_str_iso,
            "index_name": index_name,
            "open":       _safe_float(row.get("Open Index Value")),
            "high":       _safe_float(row.get("High Index Value")),
            "low":        _safe_float(row.get("Low Index Value")),
            "close":      _safe_float(row.get("Closing Index Value")),
            "pct_change": _safe_float(row.get("Change(%)")),
            "pe_ratio":   _safe_float(row.get("P/E")),
            "pb_ratio":   _safe_float(row.get("P/B")),
            "div_yield":  _safe_float(row.get("Div Yield")),
        })

    return rows


def backfill_date(conn, trade_date: date) -> int:
    """Fetch sector indices for one date and upsert. Returns count inserted."""
    rows = _fetch_sector_indices(trade_date)
    if not rows:
        return 0

    conn.executemany(
        "INSERT OR REPLACE INTO sector_indices "
        "(date, index_name, open, high, low, close, pct_change, pe_ratio, pb_ratio, div_yield) "
        "VALUES (:date, :index_name, :open, :high, :low, :close, :pct_change, :pe_ratio, :pb_ratio, :div_yield)",
        rows,
    )
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Backfill NSE sector index EOD data")
    parser.add_argument("--from", dest="from_date", default="2023-01-01",
                        help="Start date YYYY-MM-DD (default 2023-01-01)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="End date YYYY-MM-DD (default today)")
    parser.add_argument("--date", dest="single_date", default=None,
                        help="Fetch a single date YYYY-MM-DD")
    args = parser.parse_args()

    conn = connect()
    conn.execute(CREATE_TABLE_SQL)

    if args.single_date:
        d = datetime.strptime(args.single_date, "%Y-%m-%d").date()
        count = backfill_date(conn, d)
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
        count = backfill_date(conn, current)
        if count == 0:
            skipped += 1
        else:
            total += count
            logger.info("%s — %d index rows", current, count)
        current += timedelta(days=1)
        time.sleep(FETCH_DELAY_S)

    logger.info("Done — %d total rows upserted, %d non-trading days skipped", total, skipped)
    conn.close()


if __name__ == "__main__":
    main()
