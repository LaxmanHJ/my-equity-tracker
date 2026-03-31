from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pandas as pd
from typing import List

from quant_engine.data.turso_client import connect
from quant_engine.backtest.engine import VectorizedBacktester
from quant_engine.backtest.metrics import calculate_metrics
from quant_engine.strategies.base import BaseStrategy
from quant_engine.strategies.sicilian_strategy import SicilianStrategy
from quant_engine.data.loader import load_benchmark

router = APIRouter(
    prefix="/api/quant/backtest",
    tags=["backtest"]
)

class BacktestRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    strategy: str # e.g. "buy_and_hold", "momentum"
    initial_capital: float = 10000.0

class BuyAndHoldStrategy(BaseStrategy):
    def generate_signals(self, data: pd.DataFrame, **kwargs) -> pd.Series:
        # Always hold 1 unit
        return pd.Series(1, index=data.index)

def fetch_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch OHLCV data from the SQLite DB and format it for the backtester."""
    try:
        # DB stores 'RELIANCE', not 'RELIANCE.NS'
        db_symbol = symbol.replace('.NS', '').replace('.BO', '')
        
        conn = connect()
        query = """
            SELECT date, open, high, low, close, volume
            FROM price_history
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
        """
        df = pd.read_sql_query(query, conn, params=(db_symbol, start_date, end_date), parse_dates=['date'])
        conn.close()
        
        if df.empty:
            return df
            
        df.set_index('date', inplace=True)
        return df
    except Exception as e:
        print(f"DB Error: {e}")
        return pd.DataFrame()

@router.post("/")
async def run_backtest(req: BacktestRequest):
    db_symbol = req.symbol.replace('.NS', '').replace('.BO', '')
    df = fetch_data(req.symbol, req.start_date, req.end_date)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {req.symbol} in given date range.")
        
    # Standardize strategy names
    strategies = {
        "buy_and_hold": BuyAndHoldStrategy("Buy & Hold"),
        "sicilian": SicilianStrategy("The Sicilian")
    }
    
    strategy = strategies.get(req.strategy.lower())
    if not strategy:
        raise HTTPException(status_code=400, detail=f"Strategy '{req.strategy}' not found.")
    
    # Load benchmark for strategies that need it (e.g. Sicilian relative strength)
    benchmark_df = load_benchmark(limit=len(df) + 200)
        
    # 1. Generate signals — pass symbol so ML path can load delivery/sector data
    signals = strategy.generate_signals(df, benchmark_df=benchmark_df, symbol=db_symbol)
    
    # 2. Run simulation
    engine = VectorizedBacktester(df, initial_capital=req.initial_capital)
    result = engine.run(signals)
    equity_curve = result['strategy']
    baseline_curve = result['baseline']
    
    # 3. Calculate metrics
    metrics = calculate_metrics(equity_curve, initial_capital=req.initial_capital)
    if not metrics:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough trading data in the selected range ({len(df)} bars). "
                   "Try a wider date range — at least 3 months is recommended."
        )

    # Format timeseries for frontend Chart.js (needs list of {x: date, y: value})
    chart_data = [{"x": date.strftime('%Y-%m-%d'), "y": round(val, 2)} for date, val in equity_curve.items()]
    baseline_data = [{"x": date.strftime('%Y-%m-%d'), "y": round(val, 2)} for date, val in baseline_curve.items()]
    
    return {
        "symbol": req.symbol,
        "strategy": strategy.name,
        "metrics": metrics,
        "chart_data": chart_data,
        "baseline_data": baseline_data
    }
