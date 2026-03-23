"""
Fundamentals Loader — reads cached fundamental data from the shared SQLite database.
This data is fetched from RapidAPI by the Node.js service and stored in normalised tables.
"""
import sqlite3
from typing import Optional, Dict, Any
from quant_engine.config import DB_PATH


def _get_connection():
    """Get a read-only SQLite connection."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_fundamentals(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Load fundamental metrics for a single stock from the local cache.
    Returns a dict with all stored metrics, or None if no data available.
    """
    clean = symbol.replace(".NS", "").replace(".BO", "")
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM stock_fundamentals WHERE symbol = ?",
            (clean,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    except Exception:
        return None
    finally:
        conn.close()


def load_fundamentals_for_all() -> Dict[str, Dict[str, Any]]:
    """
    Load fundamental metrics for all stocks that have cached data.
    Returns a dict keyed by symbol.
    """
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM stock_fundamentals").fetchall()
        return {dict(r)["symbol"]: dict(r) for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()
