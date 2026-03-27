"""
Backfill the market_regime table with India VIX history.

Two modes:
  1. --from-csv <file>   Import from NSE India historical VIX CSV (recommended).
  2. (no flag)           Try to fetch via RapidAPI (currently unsupported by API).

NSE India VIX CSV format (download from nseindia.com → Market Data → Volatility → Historical VIX):
    Date       ,  Open  ,  High  ,  Low   ,  Close , Prev Close ,  Change ,% Change
    01-Jan-2009,  39.97 ,  40.71 ,  35.30 ,  37.23 ,       0.00 ,    2.44,    6.98

Date formats handled: DD-Mon-YYYY  (01-Jan-2009)
                      DD-MM-YYYY   (01-01-2009)
                      YYYY-MM-DD   (2009-01-01)

Run:
    python3 -m quant_engine.data.backfill_regime --from-csv ~/Downloads/india_vix.csv
    python3 -m quant_engine.data.backfill_regime          # tries RapidAPI (likely fails)
"""
import argparse
import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.data.turso_client import connect, TursoConnection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RAPIDAPI_HOST       = "indian-stock-exchange-api2.p.rapidapi.com"
RAPIDAPI_URL        = f"https://{RAPIDAPI_HOST}/historical_data"
VIX_SYMBOL_CANDIDATES = ["INDIAVIX", "INDIA VIX", "^INDIAVIX", "india vix"]

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_regime (
    date      TEXT PRIMARY KEY,
    india_vix REAL
)
"""


def _get_rw_connection() -> TursoConnection:
    return connect()


# ── CSV import ────────────────────────────────────────────────────

def _parse_vix_csv(csv_path: str) -> list[dict]:
    """
    Parse the NSE India historical VIX CSV.

    Handles:
      - Leading/trailing whitespace in column names and values
      - Date formats: DD-Mon-YYYY, DD-MM-YYYY, YYYY-MM-DD
      - Missing or zero Close values (skipped)
    """
    path = Path(csv_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    df = pd.read_csv(path, skipinitialspace=True)

    # Strip whitespace from column names
    df.columns = [c.strip() for c in df.columns]

    # Identify date and close columns (case-insensitive, flexible naming)
    date_col  = next((c for c in df.columns if c.lower() in ("date",)), None)
    close_col = next((c for c in df.columns if c.lower() in ("close", "vix close", "india vix")), None)

    if date_col is None or close_col is None:
        raise ValueError(
            f"Could not find Date/Close columns. Got: {list(df.columns)}\n"
            "Expected columns named 'Date' and 'Close'."
        )

    # Strip whitespace from values
    df[date_col]  = df[date_col].astype(str).str.strip()
    df[close_col] = pd.to_numeric(df[close_col].astype(str).str.strip(), errors="coerce")

    # Parse dates — try multiple formats
    parsed_dates = None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%b %d, %Y"):
        try:
            parsed_dates = pd.to_datetime(df[date_col], format=fmt)
            break
        except (ValueError, TypeError):
            continue

    if parsed_dates is None:
        # Last resort: let pandas infer
        parsed_dates = pd.to_datetime(df[date_col], infer_datetime_format=True)

    df["_date_parsed"] = parsed_dates

    # Drop rows where Close is missing or zero
    df = df.dropna(subset=["_date_parsed", close_col])
    df = df[df[close_col] > 0]

    rows = [
        {"date": str(row["_date_parsed"].date()), "vix": round(float(row[close_col]), 4)}
        for _, row in df.iterrows()
    ]

    rows.sort(key=lambda r: r["date"])
    return rows


def import_from_csv(conn: TursoConnection, csv_path: str) -> int:
    rows = _parse_vix_csv(csv_path)
    if not rows:
        logger.error("No valid rows parsed from %s", csv_path)
        return 0

    conn.executemany(
        "INSERT OR REPLACE INTO market_regime (date, india_vix) VALUES (:date, :vix)",
        rows,
    )
    conn.commit()
    logger.info("Upserted %d VIX rows from CSV (%s → %s)",
                len(rows), rows[0]["date"], rows[-1]["date"])
    return len(rows)


# ── RapidAPI fallback ─────────────────────────────────────────────

def _try_fetch_vix_api(period: str = "5yr") -> list[dict]:
    """Try RapidAPI — currently unsupported, kept for future use."""
    api_key = os.getenv("RAPIDAPI_KEY", "")
    if not api_key:
        return []

    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": RAPIDAPI_HOST}
    for sym in VIX_SYMBOL_CANDIDATES:
        try:
            resp = requests.get(
                RAPIDAPI_URL,
                params={"stock_name": sym, "period": period, "filter": "price"},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                time.sleep(1)
                continue
            raw = resp.json()
            price_ds = next(
                (d for d in raw.get("datasets", []) if d.get("metric") == "Price"), None
            )
            if price_ds and price_ds.get("values"):
                bars = [
                    {"date": e[0], "vix": round(float(e[1]), 4)}
                    for e in price_ds["values"] if e[1]
                ]
                if bars:
                    logger.info("VIX fetched via RapidAPI symbol '%s' — %d bars", sym, len(bars))
                    return bars
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return []


# ── Entry point ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill India VIX into market_regime table")
    parser.add_argument(
        "--from-csv", metavar="FILE",
        help="Path to NSE India historical VIX CSV file",
    )
    args = parser.parse_args()

    conn = _get_rw_connection()
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()

    if args.from_csv:
        logger.info("Importing VIX from CSV: %s", args.from_csv)
        count = import_from_csv(conn, args.from_csv)
        if count:
            logger.info("Done — %d rows imported. Re-run training to activate vix_regime feature.", count)
        conn.close()
        return

    # No CSV — try RapidAPI
    logger.info("No --from-csv provided. Trying RapidAPI …")
    bars = _try_fetch_vix_api()
    if not bars:
        logger.warning(
            "RapidAPI does not expose India VIX data.\n"
            "Download the CSV from NSE India and run:\n"
            "  python3 -m quant_engine.data.backfill_regime --from-csv <file>"
        )
        conn.close()
        return

    conn.executemany(
        "INSERT OR REPLACE INTO market_regime (date, india_vix) VALUES (:date, :vix)",
        bars,
    )
    conn.commit()
    logger.info("Upserted %d VIX rows (%s → %s)", len(bars), bars[0]["date"], bars[-1]["date"])
    conn.close()


if __name__ == "__main__":
    main()
