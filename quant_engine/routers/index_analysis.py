"""
Index Analysis Router — Markov Chain & Mean Reversion strategies for NIFTY and SENSEX.
"""
from fastapi import APIRouter
from quant_engine.data.loader import load_index_data
from quant_engine.strategies import markov_regime, mean_reversion_index

router = APIRouter(tags=["Index Analysis"])


def _analyse_index(index_name: str, display_name: str) -> dict:
    """Run both strategies on a single index."""
    df = load_index_data(index_name, limit=365)

    if df.empty:
        return {
            "name": display_name,
            "error": f"No data available for {display_name}",
            "price": None,
            "data_points": 0,
            "markov": {"current_regime": "Unknown", "error": "No data"},
            "mean_reversion": {"signal": "NO_DATA", "error": "No data"},
        }

    price = round(float(df["close"].iloc[-1]), 2)
    data_points = len(df)

    markov_result = markov_regime.calculate(df)
    mr_result = mean_reversion_index.calculate(df)

    return {
        "name": display_name,
        "price": price,
        "data_points": data_points,
        "last_date": df.index[-1].strftime("%Y-%m-%d"),
        "markov": markov_result,
        "mean_reversion": mr_result,
    }


@router.get("/index-analysis")
def get_index_analysis():
    """Get Markov Chain & Mean Reversion analysis for NIFTY and SENSEX."""
    return {
        "nifty": _analyse_index("nifty", "NIFTY 50"),
        "sensex": _analyse_index("sensex", "SENSEX"),
    }
