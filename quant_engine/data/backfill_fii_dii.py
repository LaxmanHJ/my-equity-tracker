"""
Backfill FII/DII cash market daily net flows.

Two modes:
  1. --from-csv <path>  One-time import of the NSE historical CSV
                        (Download from nseindia.com → Reports → FII/DII Trading Activity)
  2. --today            Fetch today's data from the live NSE API

The live API endpoint:
  https://www.nseindia.com/api/fiidiiTradeReact
  Returns only the current trading day. Run daily (e.g. via cron at 18:00 IST).

CSV format expected from NSE download (columns may vary slightly):
  Date | Buy Value (FII) | Sell Value (FII) | Net Value (FII) | Buy Value (DII) | Sell Value (DII) | Net Value (DII)

Stored in market_regime table columns:
  fii_net_cash  — FII daily net (INR crore), positive = net buying
  dii_net_cash  — DII daily net (INR crore), positive = net buying

Run:
    python3 -m quant_engine.data.backfill_fii_dii --from-csv ~/Downloads/fiidiiTradeReact.csv
    python3 -m quant_engine.data.backfill_fii_dii --today
"""
import argparse
import logging
import re
from datetime import datetime
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
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.nseindia.com/",
}
LIVE_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

ALTER_SQLS = [
    "ALTER TABLE market_regime ADD COLUMN fii_net_cash REAL",
    "ALTER TABLE market_regime ADD COLUMN dii_net_cash REAL",
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

UPSERT_SQL = (
    "INSERT INTO market_regime (date, fii_net_cash, dii_net_cash) "
    "VALUES (:date, :fii, :dii) "
    "ON CONFLICT(date) DO UPDATE SET "
    "fii_net_cash = excluded.fii_net_cash, "
    "dii_net_cash = excluded.dii_net_cash"
)


def _ensure_columns(conn):
    conn.execute(ENSURE_TABLE_SQL)
    for sql in ALTER_SQLS:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists


def _parse_float(val) -> float:
    """Strip commas and parse to float."""
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_date(val: str) -> str:
    """
    Convert various date formats to YYYY-MM-DD.
    Handles: '27-Mar-2026', '27/03/2026', '2026-03-27', '27-03-2026'
    """
    val = str(val).strip()
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {val!r}")


# ── Mode 1: live API (today only) ──────────────────────────────

def fetch_today(conn) -> int:
    """Fetch today's FII/DII from the live API and upsert. Returns 1 on success."""
    try:
        resp = requests.get(LIVE_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch live FII/DII: %s", e)
        return 0

    fii_net = dii_net = None
    trade_date = None

    for row in data:
        cat = str(row.get("category", "")).strip().upper()
        try:
            d = _parse_date(row.get("date", ""))
        except ValueError:
            continue
        trade_date = d
        net = _parse_float(row.get("netValue", 0))
        if "FII" in cat or "FPI" in cat:
            fii_net = net
        elif "DII" in cat:
            dii_net = net

    if trade_date is None or fii_net is None or dii_net is None:
        logger.warning("Incomplete data from live API: %s", data)
        return 0

    conn.execute(UPSERT_SQL, {"date": trade_date, "fii": fii_net, "dii": dii_net})
    logger.info("%s — FII net: %.2f cr, DII net: %.2f cr", trade_date, fii_net, dii_net)
    return 1


# ── Mode 2: historical CSV import ─────────────────────────────

def _find_columns(df: pd.DataFrame):
    """
    Auto-detect column names from the NSE CSV.
    Returns (date_col, fii_net_col, dii_net_col).
    """
    cols = [c.strip() for c in df.columns]
    df.columns = cols

    # Date column
    date_col = next(
        (c for c in cols if re.search(r"date", c, re.I)), None
    )

    # FII net column — prefer exact "Net Value" under FII section
    fii_net_col = next(
        (c for c in cols if re.search(r"fii.*net|net.*fii", c, re.I)), None
    )
    # Some CSVs use positional columns — fallback: col index 3 (0-based)
    if fii_net_col is None and len(cols) >= 4:
        fii_net_col = cols[3]

    # DII net column
    dii_net_col = next(
        (c for c in cols if re.search(r"dii.*net|net.*dii", c, re.I)), None
    )
    if dii_net_col is None and len(cols) >= 7:
        dii_net_col = cols[6]

    if not date_col or not fii_net_col or not dii_net_col:
        raise ValueError(
            f"Cannot auto-detect columns. Found: {cols}\n"
            "Expected columns containing 'Date', 'FII.*Net', 'DII.*Net'."
        )
    return date_col, fii_net_col, dii_net_col


def import_csv(conn, csv_path: str) -> int:
    """Parse NSE historical FII/DII CSV and upsert all rows. Returns count inserted."""
    path = Path(csv_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    # Try reading with first row as header; skip rows until we find the header
    df = pd.read_csv(path, skiprows=0)

    # Some NSE downloads have a title row on top — find actual header
    if "Date" not in df.columns and not any("date" in c.lower() for c in df.columns):
        df = pd.read_csv(path, skiprows=1)

    date_col, fii_net_col, dii_net_col = _find_columns(df)
    logger.info("Detected columns — date: %r, FII net: %r, DII net: %r",
                date_col, fii_net_col, dii_net_col)

    rows = []
    skipped = 0
    for _, row in df.iterrows():
        try:
            d = _parse_date(row[date_col])
        except ValueError:
            skipped += 1
            continue
        fii = _parse_float(row[fii_net_col])
        dii = _parse_float(row[dii_net_col])
        rows.append({"date": d, "fii": fii, "dii": dii})

    if not rows:
        raise RuntimeError("No valid rows parsed from CSV.")

    conn.executemany(UPSERT_SQL, rows)
    logger.info("Imported %d rows (%d skipped)", len(rows), skipped)
    return len(rows)


# ── Entry point ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill FII/DII cash market flows")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-csv", dest="csv_path", metavar="PATH",
                       help="Path to NSE historical CSV download")
    group.add_argument("--today", action="store_true",
                       help="Fetch today's data from the live NSE API")
    args = parser.parse_args()

    conn = connect()
    _ensure_columns(conn)

    if args.today:
        count = fetch_today(conn)
        logger.info("Done — %d row upserted", count)
    else:
        count = import_csv(conn, args.csv_path)
        logger.info("Done — %d rows imported from CSV", count)

    conn.close()


if __name__ == "__main__":
    main()
