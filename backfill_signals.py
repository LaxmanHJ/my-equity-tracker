"""
One-time backfill: replay Sicilian engine over all historical price data
and write signals + composite scores into signals_log table.
"""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import numpy as np
import pandas as pd
from quant_engine.config import FACTOR_WEIGHTS, LONG_THRESHOLD, SHORT_THRESHOLD
from quant_engine.strategies.sicilian_strategy import SicilianStrategy
from quant_engine.data.loader import load_all_symbols, load_price_history, load_benchmark
from quant_engine.data.turso_client import connect

SKIP_SYMBOLS = {'^BSESN', '^NSEI'}  # benchmark indices, not tradeable stocks


def compute_score_series(df: pd.DataFrame, benchmark_df: pd.DataFrame) -> pd.Series:
    """Compute per-bar Sicilian composite score (same logic as generate_signals)."""
    strat = SicilianStrategy("_backfill")
    close = df["close"]
    volume = df["volume"]

    factor_scores = {
        "momentum":          strat._rolling_momentum_score(close),
        "mean_reversion":    strat._rolling_mean_reversion_score(close),
        "rsi":               strat._rolling_rsi_score(close),
        "macd":              strat._rolling_macd_score(close),
        "volatility":        strat._rolling_volatility_score(close),
        "volume":            strat._rolling_volume_score(close, volume),
        "relative_strength": strat._rolling_relative_strength_score(close, benchmark_df),
    }

    return sum(FACTOR_WEIGHTS[name] * scores for name, scores in factor_scores.items()).clip(-1.0, 1.0)


def signal_label(score: float) -> str:
    threshold = LONG_THRESHOLD / 100  # 0.40
    if score >= threshold:
        return "LONG"
    elif score <= -threshold:
        return "SHORT"
    return "HOLD"


SQL = """INSERT INTO signals_log (signal_date, symbol, signal, composite_score, recorded_at)
         VALUES (?, ?, ?, ?, datetime('now'))
         ON CONFLICT(signal_date, symbol) DO UPDATE SET
           signal          = excluded.signal,
           composite_score = excluded.composite_score,
           recorded_at     = excluded.recorded_at"""


def main():
    conn = connect()
    symbols = [s for s in load_all_symbols() if s not in SKIP_SYMBOLS]
    benchmark_df = load_benchmark(limit=3000)

    print(f"Backfilling {len(symbols)} symbols...")
    total_rows = 0

    for symbol in symbols:
        df = load_price_history(symbol, limit=3000)
        if df.empty or len(df) < 30:
            print(f"  {symbol}: skipped (only {len(df)} bars)")
            continue

        scores = compute_score_series(df, benchmark_df)

        params = []
        for date, score in scores.items():
            if pd.isna(score):
                continue
            date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)[:10]
            label = signal_label(float(score))
            composite = round(float(score) * 100, 2)  # scale to -100..+100 like live engine
            params.append((date_str, symbol, label, composite))

        if params:
            conn.executemany(SQL, params)

        print(f"  {symbol}: {len(params)} bars written")
        total_rows += len(params)

    print(f"\nDone. {total_rows} total rows written to signals_log.")


if __name__ == "__main__":
    main()
