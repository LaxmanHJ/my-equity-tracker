"""
Backfill bulk and block deals from NSE archives.

NSE publishes daily bulk/block deal files at:
  https://nsearchives.nseindia.com/content/equities/bulk.csv   (bulk deals)
  https://nsearchives.nseindia.com/content/equities/block.csv  (block deals)

Both files contain today's data in the same format:
  Date, Symbol, Security Name, Client Name, Buy/Sell, Quantity Traded, Trade Price

Files update daily. No historical archive files exist — data accumulates from today onward.
For portfolio symbols only: filters to the symbols tracked in the portfolio.

Run:
    python3 -m quant_engine.data.backfill_bulk_deals         # fetch today
    python3 -m quant_engine.data.backfill_bulk_deals --all   # fetch all symbols (not just portfolio)
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
    UNIQUE(date, symbol, client_name, deal_type)
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


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch today's NSE bulk/block deals")
    parser.add_argument("--all", dest="all_symbols", action="store_true",
                        help="Store all symbols (not just portfolio)")
    args = parser.parse_args()

    count = fetch_today(portfolio_only=not args.all_symbols)
    logger.info("Done — %d deals upserted", count)


if __name__ == "__main__":
    main()
