"""
Fundamentals Loader — reads cached fundamental data from the Turso cloud database.
This data is fetched from RapidAPI by the Node.js service and stored in normalised tables.
"""
from typing import Optional, Dict, Any
from quant_engine.data.turso_client import connect


def load_fundamentals(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Load fundamental metrics for a single stock from the cloud cache.
    Returns a dict with all stored metrics, or None if no data available.
    """
    clean = symbol.replace(".NS", "").replace(".BO", "")
    conn = connect()
    try:
        cursor = conn.execute(
            "SELECT * FROM stock_fundamentals WHERE symbol = ?",
            (clean,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    except Exception:
        return None
    finally:
        conn.close()


def load_fundamentals_for_all() -> Dict[str, Dict[str, Any]]:
    """
    Load fundamental metrics for all stocks that have cached data.
    Returns a dict keyed by symbol.
    """
    conn = connect()
    try:
        cursor = conn.execute("SELECT * FROM stock_fundamentals")
        cols = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        return {row[cols.index("symbol")]: dict(zip(cols, row)) for row in rows}
    except Exception:
        return {}
    finally:
        conn.close()
