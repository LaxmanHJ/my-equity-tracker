"""
Scores Router — API endpoints for the quant scoring engine.
"""
import logging
from datetime import date

from fastapi import APIRouter
from quant_engine.scoring.composite import score_all_stocks, score_single_stock
from quant_engine.data.loader import load_benchmark
from quant_engine.scoring.ic_weights import get_weight_metadata

router = APIRouter(prefix="/api", tags=["scores"])
logger = logging.getLogger(__name__)


@router.get("/scores")
def get_all_scores():
    """
    Return composite factor scores for all portfolio stocks,
    sorted by score descending (strongest long first).
    """
    results = score_all_stocks()

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
        conn.execute(
            "INSERT OR REPLACE INTO market_regime (date, india_vix) VALUES (?, ?)",
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
    Fetch today's FII/DII cash flows from NSE and upsert into market_regime.

    Uses the Python session-based fetcher which handles NSE cookie requirements
    reliably. Called by the Node.js Force Sync handler.
    """
    from quant_engine.data.backfill_fii_dii import fetch_today
    from quant_engine.data.turso_client import connect

    try:
        conn = connect()
        count = fetch_today(conn)
        conn.commit()
        conn.close()

        if count:
            return {"success": True, "rows": count}
        return {"success": False, "error": "NSE returned no FII/DII data (market may be closed)"}

    except Exception as exc:
        logger.error("FII sync failed: %s", exc)
        return {"success": False, "error": str(exc)}
