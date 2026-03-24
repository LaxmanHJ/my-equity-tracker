"""
Backfill daily OHLCV bars for all portfolio symbols using RapidAPI.

The RapidAPI (indian-stock-exchange-api2) mirrors Yahoo Finance behaviour:
for periods > 1y it silently returns weekly bars instead of daily.
This script fetches the '1y' period (which returns true daily bars) for every
portfolio symbol and upserts the result into price_history.

Effect: the most recent ~252 bars per stock become clean daily bars, overwriting
any weekly stubs that existed for those dates.  Pre-2025 weekly bars remain
unchanged — they contribute less to training because the model applies
rolling indicators that need consistent frequency.

Run once to fix existing data, then periodically to keep current:

    cd /path/to/project
    python -m quant_engine.data.backfill_daily

Flags:
    --period   1y|6m|3m|1m   RapidAPI period to fetch (default 1y = ~252 daily bars)
    --symbols  all|SYM,SYM   Which symbols to fetch (default: all in DB)
"""
import argparse
import logging
import os
import sqlite3
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from project root (two levels above this file)
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.config import DB_PATH
from quant_engine.data.loader import load_all_symbols

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "indian-stock-exchange-api2.p.rapidapi.com"
RAPIDAPI_URL  = f"https://{RAPIDAPI_HOST}/historical_data"
FETCH_DELAY_S = 1.5   # stay within rate limits


def _api_key() -> str:
    key = os.getenv("RAPIDAPI_KEY", "")
    if not key:
        raise RuntimeError("RAPIDAPI_KEY not set in .env")
    return key


def _get_rw_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _fetch_bars(symbol: str, period: str) -> list[dict]:
    """
    Call the RapidAPI historical_data endpoint.
    Returns list of {date, open, high, low, close, volume} dicts, or [] on failure.
    """
    resp = requests.get(
        RAPIDAPI_URL,
        params={"stock_name": symbol.lower(), "period": period, "filter": "price"},
        headers={"X-RapidAPI-Key": _api_key(), "X-RapidAPI-Host": RAPIDAPI_HOST},
        timeout=15,
    )

    if resp.status_code != 200:
        logger.error("%s — HTTP %d: %s", symbol, resp.status_code, resp.text[:200])
        return []

    raw = resp.json()
    if not raw or "datasets" not in raw:
        logger.warning("%s — unexpected response format", symbol)
        return []

    datasets = raw["datasets"]
    price_ds  = next((d for d in datasets if d.get("metric") == "Price"),  None)
    volume_ds = next((d for d in datasets if d.get("metric") == "Volume"), None)

    if not price_ds:
        logger.warning("%s — no Price dataset in response", symbol)
        return []

    vol_map = {}
    if volume_ds:
        for entry in volume_ds.get("values", []):
            vol_map[entry[0]] = int(entry[1]) if entry[1] else 0

    bars = []
    for entry in price_ds.get("values", []):
        date_str, price_raw = entry[0], entry[1]
        if not price_raw:
            continue
        price = round(float(price_raw), 4)
        bars.append({
            "date":   date_str,
            "open":   price,   # API returns single close price; open/high/low same
            "high":   price,
            "low":    price,
            "close":  price,
            "volume": vol_map.get(date_str, 0),
        })

    return bars


def _upsert_bars(conn: sqlite3.Connection, symbol: str, bars: list[dict]) -> int:
    if not bars:
        return 0
    rows = [(symbol, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"])
            for b in bars]
    conn.executemany(
        "INSERT OR REPLACE INTO price_history (symbol, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def backfill_symbol(conn: sqlite3.Connection, symbol: str, period: str) -> int:
    bars = _fetch_bars(symbol, period)
    if not bars:
        return 0
    count = _upsert_bars(conn, symbol, bars)
    logger.info("%s — %d daily bars upserted (%s → %s)",
                symbol, count, bars[0]["date"], bars[-1]["date"])
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period",  default="1yr", help="RapidAPI period: 1m|6m|1yr|3yr|5yr|10yr|max (1yr gives ~252 daily bars)")
    parser.add_argument("--symbols", default="all", help="'all' or comma-separated symbols")
    args = parser.parse_args()

    symbols = load_all_symbols() if args.symbols == "all" else [s.strip() for s in args.symbols.split(",")]
    logger.info("Backfilling %d symbols with period='%s'", len(symbols), args.period)

    conn = _get_rw_connection()
    total = 0
    for i, sym in enumerate(symbols, 1):
        logger.info("[%d/%d] %s", i, len(symbols), sym)
        total += backfill_symbol(conn, sym, args.period)
        if i < len(symbols):
            time.sleep(FETCH_DELAY_S)

    conn.close()
    logger.info("Done — %d total bars upserted across %d symbols", total, len(symbols))


if __name__ == "__main__":
    main()
