"""
Relative Strength Factor
Compares stock's return to the NIFTY 50 benchmark over the same period.
Outperforming market → Long (+1), Underperforming → Short (-1).
"""
import numpy as np
import pandas as pd


def calculate(df: pd.DataFrame, benchmark_df: pd.DataFrame, period: int = 63) -> dict:
    """
    Calculate relative strength vs benchmark over the lookback period.
    """
    close = df["close"]

    if len(close) < period or benchmark_df.empty or len(benchmark_df) < period:
        return {"stock_return": None, "benchmark_return": None, "excess_return": None, "score": 0.0}

    stock_denom = close.iloc[-period]
    if not np.isfinite(stock_denom) or stock_denom == 0:
        return {"stock_return": None, "benchmark_return": None, "excess_return": None, "score": 0.0}
    stock_ret = float(close.iloc[-1] / stock_denom - 1)

    bench_close = benchmark_df["close"]
    bench_denom = bench_close.iloc[-period]
    if not np.isfinite(bench_denom) or bench_denom == 0:
        return {"stock_return": round(stock_ret * 100, 2), "benchmark_return": None, "excess_return": None, "score": 0.0}
    bench_ret = float(bench_close.iloc[-1] / bench_denom - 1)

    excess_return = stock_ret - bench_ret

    # Normalize: excess return of ±20% maps to ±1.0
    score = np.clip(excess_return / 0.20, -1.0, 1.0)

    return {
        "stock_return": round(stock_ret * 100, 2),
        "benchmark_return": round(bench_ret * 100, 2),
        "excess_return": round(excess_return * 100, 2),
        "outperforming": excess_return > 0,
        "score": round(float(score), 4),
    }
