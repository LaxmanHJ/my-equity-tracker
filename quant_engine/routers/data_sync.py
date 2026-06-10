"""
Data Sync Router — EOD gap-fill + table freshness reporting.

Born out of the 2026-06-10 staleness incident: sector_indices and
delivery_data sat frozen at 2026-03-27 for 2.5 months while live scoring
silently ffilled the last known values (ml/features.py zero-/forward-fills
macro features, so nothing ever errored). These endpoints make the Force
Sync button self-healing:

  POST /api/sync/eod        gap-fill sector_indices + delivery_data from
                            each table's MAX(date)+1 through today.
  GET  /api/data/freshness  per-table max date + business-day lag + stale
                            flag, for the Force Sync response / dashboard.

Gap-fill is idempotent (NSE archive fetch + UPSERT per date) and bounded by
MAX_GAP_DAYS per press so a months-long gap can't hang the button — repeated
presses converge.
"""
import logging
from datetime import date, timedelta

import numpy as np
from fastapi import APIRouter

from quant_engine.data.turso_client import connect

router = APIRouter(prefix="/api", tags=["data_sync"])
logger = logging.getLogger(__name__)

MAX_GAP_DAYS = 30          # calendar days of gap-fill per press
STALE_BUSINESS_DAYS = 2    # lag > this → stale (NSE EOD files publish in the evening)

# (label, SQL returning a single max-date string YYYY-MM-DD...)
FRESHNESS_QUERIES = [
    ("price_history",    "SELECT MAX(date) FROM price_history"),
    ("sector_indices",   "SELECT MAX(date) FROM sector_indices"),
    ("delivery_data",    "SELECT MAX(date) FROM delivery_data"),
    ("intraday_candles", "SELECT MAX(ts) FROM intraday_candles"),
    ("india_vix",        "SELECT MAX(date) FROM market_regime WHERE india_vix IS NOT NULL"),
    ("fii_net_cash",     "SELECT MAX(date) FROM market_regime WHERE fii_net_cash IS NOT NULL"),
    ("sentiment_daily",  "SELECT MAX(date) FROM sentiment_daily"),
    ("oi_buildup",       "SELECT MAX(date) FROM oi_buildup"),
    ("pcr_history",      "SELECT MAX(date) FROM pcr_history"),
    ("iv_daily",         "SELECT MAX(date) FROM iv_daily"),
]


def _max_date(conn, sql: str):
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    val = rows[0][0] if rows and rows[0] else None
    return str(val)[:10] if val else None


def _business_lag(max_date_str: str) -> int:
    """Business days between a table's max date and today (holiday-naive)."""
    return int(np.busday_count(max_date_str, str(date.today())))


@router.get("/data/freshness")
def data_freshness():
    """
    Report max(date), business-day lag, and stale flag for every monitored
    feature table. `stale` means live features built from that table are
    running on forward-filled values.
    """
    conn = connect()
    try:
        tables = {}
        any_stale = False
        for label, sql in FRESHNESS_QUERIES:
            try:
                md = _max_date(conn, sql)
                lag = _business_lag(md) if md else None
                stale = (lag is None) or (lag > STALE_BUSINESS_DAYS)
                any_stale = any_stale or stale
                tables[label] = {"max_date": md, "lag_business_days": lag, "stale": stale}
            except Exception as exc:  # noqa: BLE001 — one broken table shouldn't hide the rest
                tables[label] = {"max_date": None, "lag_business_days": None,
                                 "stale": True, "error": str(exc)}
                any_stale = True
        return {"success": True, "any_stale": any_stale,
                "stale_business_days_threshold": STALE_BUSINESS_DAYS, "tables": tables}
    finally:
        conn.close()


@router.post("/sync/eod")
def sync_eod_gap_fill():
    """
    Gap-fill sector_indices and delivery_data from each table's MAX(date)+1
    through today via the NSE archive fetchers. Weekend dates are skipped;
    dates whose archive file isn't published yet (today before ~18:00 IST,
    holidays) upsert 0 rows and are retried on the next press.
    """
    from quant_engine.data.backfill_sector_indices import backfill_date as sector_backfill_date
    from quant_engine.data.backfill_delivery import backfill_date as delivery_backfill_date
    from quant_engine.data.loader import load_all_symbols

    today = date.today()
    results = {}
    conn = connect()
    try:
        jobs = {
            "sector_indices": lambda c, d: sector_backfill_date(c, d),
            "delivery_data":  None,  # bound below once symbols are loaded
        }
        symbols = set(load_all_symbols())
        jobs["delivery_data"] = lambda c, d: delivery_backfill_date(c, d, symbols)

        for table, job in jobs.items():
            try:
                md = _max_date(conn, f"SELECT MAX(date) FROM {table}")
                start = (date.fromisoformat(md) + timedelta(days=1)) if md else today
                capped = False
                if (today - start).days > MAX_GAP_DAYS:
                    start = today - timedelta(days=MAX_GAP_DAYS)
                    capped = True
                rows, days_filled = 0, 0
                d = start
                while d <= today:
                    if d.weekday() < 5:
                        n = job(conn, d)
                        if n:
                            rows += n
                            days_filled += 1
                    d += timedelta(days=1)
                conn.commit()
                results[table] = {"ok": True, "from": str(start), "rows": rows,
                                  "days_filled": days_filled, "gap_capped": capped}
                logger.info("EOD gap-fill %s: %d rows over %d day(s) from %s%s",
                            table, rows, days_filled, start, " (capped)" if capped else "")
            except Exception as exc:  # noqa: BLE001 — report per-table, don't abort the rest
                logger.error("EOD gap-fill %s failed: %s", table, exc)
                results[table] = {"ok": False, "error": str(exc)}
        return {"success": all(r.get("ok") for r in results.values()), "tables": results}
    finally:
        conn.close()
