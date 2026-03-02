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


def load_all_symbols() -> List[str]:
    """Return all symbols that have cached price history."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT DISTINCT symbol FROM price_history")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def load_benchmark(limit: int = 365) -> pd.DataFrame:
    """Load NIFTY 50 benchmark data. Symbol stored as '^NSEI' or 'NSEI'."""
    for sym in ["^NSEI", "NSEI", "NIFTY"]:
        df = load_price_history(sym, limit)
        if not df.empty:
            return df
    return pd.DataFrame()
