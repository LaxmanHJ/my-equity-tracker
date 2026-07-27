"""
Scores Router — API endpoints for the quant scoring engine.
"""
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Query
from quant_engine.scoring.composite import score_all_stocks, score_single_stock
from quant_engine.data.loader import load_benchmark
from quant_engine.scoring.ic_weights import get_weight_metadata

router = APIRouter(prefix="/api", tags=["scores"])
logger = logging.getLogger(__name__)


@router.get("/scores")
def get_all_scores(symbols: Optional[str] = Query(None)):
    """
    Return composite factor scores for the requested universe.

    `symbols` is a comma-separated list (e.g. ?symbols=INFY,TANLA). Node's
    proxy passes the portfolio's displaySymbols here so live scoring stays
    bounded; without it the route falls back to every symbol in price_history
    (~200 after the Nifty 200 backfill — too slow for live).
    """
    universe = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else None
    )
    results = score_all_stocks(symbols=universe)

    summary = {
        "total": len(results),
        "long": sum(1 for r in results if r["signal"] == "LONG"),
        "hold": sum(1 for r in results if r["signal"] == "HOLD"),
        "short": sum(1 for r in results if r["signal"] == "SHORT"),
    }

    return {"summary": summary, "stocks": results}


@router.get("/ic-weights")
def get_ic_weights():
    """
    Return the current IC-weighted factor weights alongside the static fallback.
    Shows which factors the market has been rewarding over the last 252 days.
    """
    return get_weight_metadata()


@router.get("/scores/{symbol}")
def get_stock_score(symbol: str):
    """
    Return detailed factor breakdown for a single stock.
    """
    benchmark_df = load_benchmark()
    result = score_single_stock(symbol.upper(), benchmark_df)

    if result is None:
        return {"error": f"No data found for symbol '{symbol}'"}

    return result


@router.post("/sync/vix")
def sync_vix_today():
    """
    Fetch today's India VIX from NSE and upsert into market_regime table.

    Called automatically by the Node.js Force Sync handler so VIX stays
    current without any manual CSV downloads.

    Returns the date and VIX value that was upserted, or an error message
    if NSE is unreachable.
    """
    import time
    from quant_engine.data.nse_fetcher import NSEFetcher
    from quant_engine.data.turso_client import connect

    try:
        fetcher = NSEFetcher()
        time.sleep(1)
        vix_data = fetcher.fetch_vix()

        if not vix_data or not vix_data.get("vix"):
            return {"success": False, "error": "NSE returned no VIX data"}

        vix_value = float(vix_data["vix"])
        today     = str(date.today())

        conn = connect()
        # Must be a column-scoped upsert, never INSERT OR REPLACE: REPLACE
        # deletes the conflicting row and reinserts it, so every other column
        # on that date (fii_net_cash, dii_net_cash, fii_fo_net_long) is reset
        # to NULL. That is what silently wiped the FII flows written moments
        # earlier by the /sync/fii step of the same force-sync pass.
        conn.execute(
            "INSERT INTO market_regime (date, india_vix) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET india_vix = excluded.india_vix",
            (today, vix_value),
        )
        conn.commit()
        conn.close()

        logger.info("VIX sync: upserted %s = %.2f", today, vix_value)
        return {"success": True, "date": today, "india_vix": vix_value}

    except Exception as exc:
        logger.error("VIX sync failed: %s", exc)
        return {"success": False, "error": str(exc)}


@router.post("/sync/fii")
def sync_fii_today():
    """
    Sync FII/DII cash flows into market_regime.

    Two steps, because NSE's live endpoint only ever returns the current
    trading day and ignores date params — one missed run used to mean that
    day's flows were lost for good:
      1. NSE live (authoritative) for today.
      2. Mirror gap-fill for any earlier date in the rolling window that has
         no FII value yet. This is what makes the step self-healing after an
         outage, and what keeps it working from GitHub Actions runners, whose
         datacenter IPs NSE intermittently blocks.

    Reports success if either step wrote a row.
    """
    from quant_engine.data.backfill_fii_dii import fetch_recent, fetch_today
    from quant_engine.data.turso_client import connect

    conn = None
    live_rows = filled_rows = 0
    mirror_ok = False
    warnings = []

    try:
        conn = connect()

        try:
            live_rows = fetch_today(conn)
            conn.commit()
            if not live_rows:
                warnings.append("live: NSE returned no FII/DII data (market may be closed)")
        except Exception as exc:            # never let the primary sink the gap-fill
            logger.warning("FII live fetch failed: %s", exc)
            warnings.append(f"live: {exc}")

        try:
            filled_rows = fetch_recent(conn)
            mirror_ok = True
        except Exception as exc:
            logger.warning("FII mirror gap-fill failed: %s", exc)
            warnings.append(f"mirror: {exc}")

        # A clean mirror pass that wrote nothing means the whole rolling window
        # is already stored — that is up-to-date, not a failure.
        if live_rows or filled_rows or mirror_ok:
            return {
                "success": True,
                "rows": live_rows + filled_rows,
                "live_rows": live_rows,
                "gap_filled_rows": filled_rows,
                # A working gap-fill after a failed live call is still a
                # success, but the caller should see why it was needed.
                "warnings": warnings or None,
            }
        return {"success": False, "error": "; ".join(warnings) or "no FII/DII rows written"}

    except Exception as exc:
        logger.error("FII sync failed: %s", exc)
        return {"success": False, "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()
