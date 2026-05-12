"""
The Sicilian Router — API endpoints for the unified decision engine.
"""
from typing import Optional

from fastapi import APIRouter, Query
from quant_engine.sicilian.engine import run_sicilian
from quant_engine.data.loader import load_all_symbols

router = APIRouter(prefix="/api", tags=["sicilian"])


@router.get("/sicilian/{symbol}")
def get_sicilian_verdict(symbol: str):
    """
    Run The Sicilian analysis on a single stock.
    Returns verdict (BUY/SELL/HOLD), score, confidence, target price, and full breakdown.
    """
    result = run_sicilian(symbol.upper())
    return result


@router.get("/sicilian")
def get_all_sicilian_verdicts(symbols: Optional[str] = Query(None)):
    """
    Run The Sicilian on the requested universe (comma-separated `symbols`
    query param). Falls back to every symbol in price_history if omitted —
    Node's proxy passes the portfolio displaySymbols so live runs stay
    bounded after the Nifty 200 training-universe backfill.
    """
    if symbols:
        symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbols = load_all_symbols()
    results = []
    for sym in symbols:
        result = run_sicilian(sym)
        if result and result["verdict"] != "INSUFFICIENT_DATA":
            results.append(result)

    # Sort: BUY first (highest score), then HOLD, then SELL (lowest score)
    results.sort(key=lambda x: x["sicilian_score"], reverse=True)

    summary = {
        "total": len(results),
        "buy": sum(1 for r in results if r["verdict"] == "BUY"),
        "hold": sum(1 for r in results if r["verdict"] == "HOLD"),
        "sell": sum(1 for r in results if r["verdict"] == "SELL"),
    }

    return {"summary": summary, "stocks": results}
