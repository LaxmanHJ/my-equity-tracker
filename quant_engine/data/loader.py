"""
Data Loader — reads price history from the shared SQLite database.
"""
import sqlite3
from typing import List
import pandas as pd
from quant_engine.config import DB_PATH


def get_connection():
    """Get a read-only SQLite connection."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_price_history(symbol: str, limit: int = 365) -> pd.DataFrame:
    """
    Load OHLCV data for a symbol from the local cache.
    Returns a DataFrame with columns: date, open, high, low, close, volume.
    """
    clean_symbol = symbol.replace(".NS", "").replace(".BO", "")
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            """
            SELECT date, open, high, low, close, volume
            FROM price_history
            WHERE symbol = ?
            ORDER BY date ASC
            """,
            conn,
            params=(clean_symbol,),
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        if limit:
            df = df.tail(limit)
        return df
    finally:
        conn.close()


# Symbols that are benchmarks/indices, not portfolio stocks — exclude from scoring
BENCHMARK_SYMBOLS = {"^NSEI", "NSEI", "NIFTY", "^NSEBANK", "^BSESN", "BSESN", "SENSEX"}


def load_all_symbols() -> List[str]:
    """Return all symbols that have cached price history, excluding benchmarks."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT DISTINCT symbol FROM price_history")
        return [row[0] for row in cursor.fetchall() if row[0] not in BENCHMARK_SYMBOLS]
    finally:
        conn.close()


def load_benchmark(limit: int = 365) -> pd.DataFrame:
    """Load NIFTY 50 benchmark data. Symbol stored as '^NSEI' or 'NSEI'."""
    for sym in ["^NSEI", "NSEI", "NIFTY", "NIFTY 50"]:
        df = load_price_history(sym, limit)
        if not df.empty:
            return df
    return pd.DataFrame()


# Symbol candidates for each index
INDEX_SYMBOL_MAP = {
    "nifty": ["^NSEI", "NSEI", "NIFTY", "NIFTY 50"],
    "sensex": ["^BSESN", "BSESN", "SENSEX"],
}


def load_industry_map() -> dict:
    """
    Returns {symbol: industry} for every stock that has industry data in
    stock_fundamentals.  Used by the trainer to group peers for sector rotation.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT symbol, industry FROM stock_fundamentals WHERE industry IS NOT NULL"
        ).fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def load_analyst_consensus() -> dict:
    """
    Returns {symbol: score} where score ∈ [-1, +1].

    Score = (strong_buy + buy - sell - strong_sell) / total_analysts.
    Positive means more analysts are bullish than bearish; negative means the
    opposite.  Stocks with no analyst coverage get 0 (neutral).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol, strong_buy, buy, hold, sell, strong_sell, total_analysts
            FROM stock_analyst_ratings
            WHERE total_analysts > 0
            """
        ).fetchall()
        result = {}
        for row in rows:
            sym, sb, b, h, s, ss, total = row
            net_bullish = (sb + b) - (s + ss)
            result[sym] = round(float(net_bullish) / float(total), 4)
        return result
    except Exception:
        return {}
    finally:
        conn.close()


def load_index_data(index_name: str, limit: int = 365) -> pd.DataFrame:
    """
    Load index price data from SQLite.
    index_name: 'nifty' or 'sensex'
    """
    candidates = INDEX_SYMBOL_MAP.get(index_name.lower(), [index_name])
    for sym in candidates:
        df = load_price_history(sym, limit)
        if not df.empty:
            return df
    return pd.DataFrame()
