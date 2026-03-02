"""
Scores Router — API endpoints for the quant scoring engine.
"""
from fastapi import APIRouter
from quant_engine.scoring.composite import score_all_stocks, score_single_stock
from quant_engine.data.loader import load_benchmark

router = APIRouter(prefix="/api", tags=["scores"])


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
