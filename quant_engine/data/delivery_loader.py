"""
Delivery Loader — reads delivery percentage data from Turso.
"""
import pandas as pd
from quant_engine.data.turso_client import connect


def load_delivery_series(symbol: str, limit: int = 365) -> pd.DataFrame:
    """
    Load delivery percentage data for a symbol.
    Returns DataFrame with columns: date (index), delivery_pct, delivery_qty, circuit_hit.
    """
    clean = symbol.replace(".NS", "").replace(".BO", "")
    conn = connect()
    try:
        df = pd.read_sql_query(
            """
            SELECT date, delivery_pct, delivery_qty, circuit_hit
            FROM delivery_data
            WHERE symbol = ?
            ORDER BY date ASC
            """,
            conn,
            params=(clean,),
        )
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        if limit:
            df = df.tail(limit)
        return df
    finally:
        conn.close()


def load_circuit_status(symbol: str) -> int:
    """
    Returns the most recent circuit_hit value for a symbol.
    +1 = upper circuit, -1 = lower circuit, 0 = none.
    """
    clean = symbol.replace(".NS", "").replace(".BO", "")
    conn = connect()
    try:
        cursor = conn.execute(
            "SELECT circuit_hit FROM delivery_data WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (clean,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()
