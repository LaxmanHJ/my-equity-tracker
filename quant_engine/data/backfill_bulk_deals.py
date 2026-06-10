"""
Backfill bulk and block deals from NSE archives.

NSE publishes daily bulk/block deal files at:
  https://nsearchives.nseindia.com/content/equities/bulk.csv   (bulk deals)
  https://nsearchives.nseindia.com/content/equities/block.csv  (block deals)

Both files contain today's data in the same format:
  Date, Symbol, Security Name, Client Name, Buy/Sell, Quantity Traded, Trade Price

Files update daily. The daily files have no archive — BUT NSE's report-detail
API serves full history via session-cookied CSV download (verified 2026-06-10):
  https://www.nseindia.com/api/historicalOR/bulk-block-short-deals
      ?optionType=bulk_deals|block_deals&from=DD-MM-YYYY&to=DD-MM-YYYY&csv=true
The JSON variant of the same endpoint caps at one day per call; the csv=true
variant returns the whole requested range (~4k rows/month for bulk).

Run:
    python3 -m quant_engine.data.backfill_bulk_deals                       # fetch today
    python3 -m quant_engine.data.backfill_bulk_deals --all                 # today, all symbols
    python3 -m quant_engine.data.backfill_bulk_deals --from 2019-01-01     # historical (all symbols)
"""
import io
import logging
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.data.turso_client import connect
from quant_engine.data.loader import load_all_symbols

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

URLS = {
    "BULK":  "https://nsearchives.nseindia.com/content/equities/bulk.csv",
    "BLOCK": "https://nsearchives.nseindia.com/content/equities/block.csv",
}

# UNIQUE key includes trade_type/quantity/price — the original key
# (date, symbol, client, deal_type) collapsed a churner client's BUY and SELL
# legs on the same symbol-day into one row, silently biasing any net-flow
# computation toward whichever side was inserted first (found 2026-06-10).
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bulk_block_deals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    client_name TEXT,
    trade_type  TEXT,
    quantity    INTEGER,
    price       REAL,
    deal_type   TEXT,
    UNIQUE(date, symbol, client_name, deal_type, trade_type, quantity, price)
)
"""

UPSERT_SQL = (
    "INSERT OR IGNORE INTO bulk_block_deals "
    "(date, symbol, client_name, trade_type, quantity, price, deal_type) "
    "VALUES (:date, :symbol, :client_name, :trade_type, :quantity, :price, :deal_type)"
)


def _parse_date(val: str) -> str:
    """Convert '27-MAR-2026' → '2026-03-27'."""
    from datetime import datetime
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return str(val).strip()


def _safe_int(val) -> int:
    try:
        return int(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def fetch_today(portfolio_only: bool = True) -> int:
    """Fetch today's bulk and block deals from NSE archives. Returns total rows inserted."""
    conn = connect()
    conn.execute(CREATE_TABLE_SQL)

    portfolio_symbols = set(load_all_symbols()) if portfolio_only else None
    total = 0

    for deal_type, url in URLS.items():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 404:
                logger.info("%s deals: no data today (404)", deal_type)
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to fetch %s deals: %s", deal_type, e)
            continue

        try:
            df = pd.read_csv(io.StringIO(resp.text))
        except Exception as e:
            logger.warning("Failed to parse %s CSV: %s", deal_type, e)
            continue

        df.columns = [c.strip() for c in df.columns]

        rows = []
        for _, row in df.iterrows():
            symbol = str(row.get("Symbol", "")).strip()
            if portfolio_only and symbol not in portfolio_symbols:
                continue

            date_val = _parse_date(row.get("Date", ""))
            client = str(row.get("Client Name", "")).strip()
            trade_type = str(row.get("Buy/Sell", "")).strip().upper()
            quantity = _safe_int(row.get("Quantity Traded", 0))
            price = _safe_float(row.get("Trade Price / Wght. Avg. Price", 0))

            rows.append({
                "date":        date_val,
                "symbol":      symbol,
                "client_name": client,
                "trade_type":  trade_type,
                "quantity":    quantity,
                "price":       price,
                "deal_type":   deal_type,
            })

        if rows:
            conn.executemany(UPSERT_SQL, rows)
            logger.info("%s deals: %d rows for portfolio symbols", deal_type, len(rows))
            total += len(rows)
        else:
            logger.info("%s deals: no portfolio symbols in today's data", deal_type)

    conn.close()
    return total


HIST_URL = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
HIST_OPTION_TYPES = {"BULK": "bulk_deals", "BLOCK": "block_deals"}
HIST_PAGE = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"


def _hist_session() -> requests.Session:
    """NSE needs main-page + report-page cookies before the API responds."""
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.nseindia.com", timeout=15)
    s.get(HIST_PAGE, timeout=15)
    return s


def _month_chunks(start, end):
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(year=cur.year + 1, month=1) if cur.month == 12
               else cur.replace(month=cur.month + 1))
        yield max(cur, start), min(nxt - __import__("datetime").timedelta(days=1), end)
        cur = nxt


def fetch_historical(from_date, to_date) -> int:
    """Backfill the full date range in monthly chunks via the csv=true API.

    Stores ALL symbols (research-grade — symbols without price coverage are
    simply unused downstream). Same UPSERT/UNIQUE key as the daily path, so
    overlap with already-ingested daily rows is a no-op.
    """
    import time as _time

    conn = connect()
    conn.execute(CREATE_TABLE_SQL)
    session = _hist_session()
    total = 0

    for deal_type, option_type in HIST_OPTION_TYPES.items():
        for chunk_from, chunk_to in _month_chunks(from_date, to_date):
            params = {
                "optionType": option_type,
                "from": chunk_from.strftime("%d-%m-%Y"),
                "to": chunk_to.strftime("%d-%m-%Y"),
                "csv": "true",
            }
            try:
                resp = session.get(HIST_URL, params=params, timeout=60)
                resp.raise_for_status()
                # utf-8-sig: NSE CSVs lead with a BOM that otherwise becomes
                # part of the first column name ('﻿Date') and silently
                # breaks the date lookup.
                df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig")
            except Exception as e:
                logger.warning("%s %s→%s failed: %s — re-initializing session",
                               deal_type, chunk_from, chunk_to, e)
                _time.sleep(3)
                session = _hist_session()
                continue

            # Historical CSV headers carry trailing spaces and slightly
            # different names than the daily files ("Buy / Sell" vs "Buy/Sell").
            df.columns = [c.strip().replace(" / ", "/").replace(" ", "_").lower()
                          for c in df.columns]
            col = {c: c for c in df.columns}
            qty_col = next((c for c in df.columns if "quantity" in c), None)
            price_col = next((c for c in df.columns if "price" in c), None)
            side_col = next((c for c in df.columns if "buy" in c and "sell" in c), None)
            if not all([qty_col, price_col, side_col]) or "symbol" not in col:
                logger.warning("%s %s→%s: unexpected columns %s",
                               deal_type, chunk_from, chunk_to, list(df.columns))
                continue

            rows = []
            for _, row in df.iterrows():
                symbol = str(row.get("symbol", "")).strip()
                if not symbol or symbol.lower() == "nan":
                    continue
                rows.append({
                    "date":        _parse_date(row.get("date", "")),
                    "symbol":      symbol,
                    "client_name": str(row.get("client_name", "")).strip(),
                    "trade_type":  str(row.get(side_col, "")).strip().upper(),
                    "quantity":    _safe_int(row.get(qty_col, 0)),
                    "price":       _safe_float(row.get(price_col, 0)),
                    "deal_type":   deal_type,
                })
            # Chunked upsert — a full month is ~4k rows and a single
            # executemany blows the Turso HTTP read timeout.
            for i in range(0, len(rows), 500):
                conn.executemany(UPSERT_SQL, rows[i:i + 500])
            total += len(rows)
            logger.info("%s %s→%s: %d rows", deal_type, chunk_from, chunk_to, len(rows))
            _time.sleep(1.0)

    conn.close()
    return total


def main():
    import argparse
    from datetime import datetime as _dt, date as _date
    parser = argparse.ArgumentParser(description="Fetch NSE bulk/block deals")
    parser.add_argument("--all", dest="all_symbols", action="store_true",
                        help="Store all symbols (not just portfolio)")
    parser.add_argument("--from", dest="from_date", default=None,
                        help="Historical mode: start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="Historical mode: end date YYYY-MM-DD (default today)")
    args = parser.parse_args()

    if args.from_date:
        start = _dt.strptime(args.from_date, "%Y-%m-%d").date()
        end = _dt.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else _date.today()
        count = fetch_historical(start, end)
    else:
        count = fetch_today(portfolio_only=not args.all_symbols)
    logger.info("Done — %d deals upserted", count)


if __name__ == "__main__":
    main()
