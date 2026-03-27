"""
Sector Indices Loader — reads NSE sector index closing prices from Turso.
"""
import pandas as pd
from quant_engine.data.turso_client import connect


def load_sector_series(index_name: str, limit: int = 500) -> pd.Series:
    """
    Load closing price history for an NSE index.

    Returns pd.Series indexed by date (datetime), values = closing price.
    Empty series if the index has no data.
    """
    conn = connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT date, close
            FROM sector_indices
            WHERE index_name = ?
            ORDER BY date ASC
            """,
            conn,
            params=(index_name,),
        )
        if df.empty:
            return pd.Series(dtype=float, name=index_name)
        df["date"] = pd.to_datetime(df["date"])
        series = df.set_index("date")["close"].sort_index()
        if limit:
            series = series.tail(limit)
        return series
    finally:
        conn.close()


def load_sector_pe(index_name: str, limit: int = 500) -> pd.Series:
    """
    Load P/E ratio history for an NSE index.

    Returns pd.Series indexed by date (datetime), values = P/E ratio.
    """
    conn = connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT date, pe_ratio
            FROM sector_indices
            WHERE index_name = ?
              AND pe_ratio IS NOT NULL
            ORDER BY date ASC
            """,
            conn,
            params=(index_name,),
        )
        if df.empty:
            return pd.Series(dtype=float, name=index_name)
        df["date"] = pd.to_datetime(df["date"])
        series = df.set_index("date")["pe_ratio"].sort_index()
        if limit:
            series = series.tail(limit)
        return series
    finally:
        conn.close()
