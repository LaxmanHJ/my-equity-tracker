"""
Backfill FII F&O participant open interest data.

NSE publishes daily participant-wise OI for equity derivatives at:
  https://archives.nseindia.com/content/nsccl/fao_participant_oi_{DDMMYYYY}.csv

We extract the FII row and compute:
  fii_fo_net_long = Future Index Long - Future Index Short  (net NIFTY futures positioning)

Stored in the market_regime table (fii_fo_net_long column).

Run:
    python3 -m quant_engine.data.backfill_fo_oi --from 2023-01-01
    python3 -m quant_engine.data.backfill_fo_oi --date 2025-03-26
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
BASE_URL = "https://archives.nseindia.com/content/nsccl/fao_participant_oi_{date}.csv"
FETCH_DELAY_S = 0.5

ALTER_TABLE_SQL = [
    "ALTER TABLE market_regime ADD COLUMN fii_fo_net_long REAL",
]

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_regime (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL UNIQUE,
    india_vix REAL,
    fii_net_cash    REAL,
    dii_net_cash    REAL,
    fii_fo_net_long REAL
)
"""


def _ensure_columns(conn):
    conn.execute(ENSURE_TABLE_SQL)
    for sql in ALTER_TABLE_SQL:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists


def _fetch_fo_oi(trade_date: date):
    """Download F&O participant OI CSV. Returns (fii_index_long, fii_index_short) or None."""
    date_str = trade_date.strftime("%d%m%Y")
    url = BASE_URL.format(date=date_str)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return None  # holiday / non-trading day
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return None

    try:
        # First row is a title ("Participant wise Open Interest..."), skip it
        df = pd.read_csv(io.StringIO(resp.text), skiprows=1)
    except Exception as e:
        logger.warning("Failed to parse CSV for %s: %s", trade_date, e)
        return None

    # Normalise column names (strip whitespace and tabs)
    df.columns = [c.strip() for c in df.columns]
    if "Client Type" not in df.columns:
        logger.warning("'Client Type' column not found in %s. Columns: %s", trade_date, list(df.columns))
        return None
    df["Client Type"] = df["Client Type"].str.strip()

    fii_row = df[df["Client Type"] == "FII"]
    if fii_row.empty:
        return None

    try:
        long_col  = "Future Index Long"
        short_col = "Future Index Short"
        fi_long  = float(str(fii_row[long_col].values[0]).replace(",", "").strip())
        fi_short = float(str(fii_row[short_col].values[0]).replace(",", "").strip())
        return fi_long - fi_short
    except (KeyError, ValueError, IndexError) as e:
        logger.warning("Could not extract FII OI for %s: %s", trade_date, e)
        return None


def backfill_date(conn, trade_date: date) -> bool:
    """Fetch F&O OI for one date and upsert fii_fo_net_long. Returns True if inserted."""
    net_long = _fetch_fo_oi(trade_date)
    if net_long is None:
        return False

    date_str = trade_date.strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO market_regime (date, fii_fo_net_long) VALUES (:date, :val) "
        "ON CONFLICT(date) DO UPDATE SET fii_fo_net_long = excluded.fii_fo_net_long",
        {"date": date_str, "val": net_long},
    )
    return True


def main():
    parser = argparse.ArgumentParser(description="Backfill FII F&O net index futures positioning")
    parser.add_argument("--from", dest="from_date", default="2023-01-01",
                        help="Start date YYYY-MM-DD (default 2023-01-01)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="End date YYYY-MM-DD (default today)")
    parser.add_argument("--date", dest="single_date", default=None,
                        help="Single date YYYY-MM-DD")
    args = parser.parse_args()

    conn = connect()
    _ensure_columns(conn)

    if args.single_date:
        d = datetime.strptime(args.single_date, "%Y-%m-%d").date()
        ok = backfill_date(conn, d)
        logger.info("%s — %s", d, "inserted" if ok else "no data (holiday?)")
        conn.close()
        return

    start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    end   = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else date.today()

    current = start
    inserted = skipped = 0
    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        ok = backfill_date(conn, current)
        if ok:
            inserted += 1
            if inserted % 50 == 0:
                logger.info("%s — %d rows so far", current, inserted)
        else:
            skipped += 1
        current += timedelta(days=1)
        time.sleep(FETCH_DELAY_S)

    logger.info("Done — %d rows inserted, %d non-trading days skipped", inserted, skipped)
    conn.close()


if __name__ == "__main__":
    main()
