"""
Turso HTTP client — DB-API 2.0 compatible wrapper.

Turso exposes a REST API (POST /v2/pipeline) that accepts SQL statements and
returns typed result rows. This module wraps that API in a cursor/connection
interface compatible with both pd.read_sql_query() and direct execute() calls,
so every loader and backfill script can swap sqlite3 for Turso with a one-line
connection change.

Supports:
  - Positional params with ?   e.g. "WHERE symbol = ?"  params=("RELIANCE",)
  - Named params with :name    e.g. "VALUES (:date, :vix)" params={"date":...}
  - executemany() with lists of tuples or dicts
  - pd.read_sql_query() via cursor.description + fetchall()
"""

import os
import re
from typing import Any, Optional, Sequence, Union

import requests as _requests

from quant_engine.config import TURSO_URL, TURSO_TOKEN


# ── Type helpers ──────────────────────────────────────────────────────────────

def _to_turso_value(v: Any) -> dict:
    """Convert a Python value to a Turso API typed value object.

    Turso hrana protocol types:
      integer → {"type": "integer", "value": "<string>"}   integers as strings (avoids JSON int64 precision loss)
      float   → {"type": "float",   "value": <number>}     floats as JSON numbers (NOT strings — API rejects strings)
      text    → {"type": "text",    "value": "<string>"}
      null    → {"type": "null"}
    """
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}   # must be a JSON number, not string
    return {"type": "text", "value": str(v)}


def _from_turso_value(cell: dict) -> Any:
    """Convert a Turso API typed value object to a Python value."""
    t = cell.get("type", "null")
    v = cell.get("value")
    if t == "null" or v is None:
        return None
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    return v  # text — return as-is


def _build_stmt(sql: str, params: Union[Sequence, dict, None]) -> dict:
    """
    Build a Turso statement dict from SQL + params.
    Handles ? positional and :name named placeholders.
    """
    stmt: dict = {"sql": sql}
    if not params:
        return stmt

    if isinstance(params, dict):
        stmt["named_args"] = [
            {"name": k.lstrip(":"), "value": _to_turso_value(v)}
            for k, v in params.items()
        ]
    else:
        stmt["args"] = [_to_turso_value(p) for p in params]

    return stmt


# ── Cursor ────────────────────────────────────────────────────────────────────

class TursoCursor:
    """DB-API 2.0 cursor backed by Turso's HTTP pipeline endpoint."""

    def __init__(self, http_url: str, token: str):
        self._url = f"{http_url}/v2/pipeline"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self.description: Optional[list] = None
        self._rows: list = []
        self.rowcount: int = -1
        self.arraysize: int = 1

    # ── internal ──────────────────────────────────────────────────────────────

    def _post(self, payload: dict) -> dict:
        resp = _requests.post(self._url, headers=self._headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _parse_result(self, result: dict):
        cols = result.get("cols", [])
        rows = result.get("rows", [])
        self.description = [
            (c["name"], None, None, None, None, None, None) for c in cols
        ]
        self._rows = [
            [_from_turso_value(cell) for cell in row] for row in rows
        ]
        self.rowcount = int(result.get("affected_row_count", len(self._rows)))

    # ── DB-API interface ──────────────────────────────────────────────────────

    def execute(self, sql: str, params=None):
        payload = {"requests": [{"type": "execute", "stmt": _build_stmt(sql, params)}]}
        data = self._post(payload)
        result = data["results"][0]["response"]["result"]
        self._parse_result(result)
        return self

    def executemany(self, sql: str, params_list):
        """Batch all statements into a single HTTP request."""
        batch = [
            {"type": "execute", "stmt": _build_stmt(sql, p)}
            for p in params_list
        ]
        if not batch:
            return self
        data = self._post({"requests": batch})
        # Check every result for errors — Turso returns per-statement status
        for i, res in enumerate(data.get("results", [])):
            if res.get("type") == "error":
                raise RuntimeError(
                    f"executemany statement {i} failed: {res.get('error', {}).get('message', res)}"
                )
        self.rowcount = len(batch)
        return self

    def fetchall(self) -> list:
        return [tuple(row) for row in self._rows]

    def fetchone(self) -> Optional[tuple]:
        return tuple(self._rows[0]) if self._rows else None

    def fetchmany(self, size: int = None) -> list:
        n = size or self.arraysize
        return [tuple(row) for row in self._rows[:n]]

    def close(self):
        pass

    # pandas compatibility: iterate cursor like a sequence of rows
    def __iter__(self):
        return iter(self.fetchall())


# ── Connection ────────────────────────────────────────────────────────────────

class TursoConnection:
    """DB-API 2.0 connection backed by Turso's HTTP API."""

    def __init__(self, db_url: str, token: str):
        self._http_url = db_url.replace("libsql://", "https://")
        self._token = token

    def cursor(self) -> TursoCursor:
        return TursoCursor(self._http_url, self._token)

    def execute(self, sql: str, params=None) -> TursoCursor:
        c = self.cursor()
        c.execute(sql, params or [])
        return c

    def executemany(self, sql: str, params_list) -> TursoCursor:
        c = self.cursor()
        c.executemany(sql, params_list)
        return c

    def commit(self):
        pass  # Turso auto-commits each statement

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ── Public API ────────────────────────────────────────────────────────────────

def connect(db_url: str = None, token: str = None) -> TursoConnection:
    """Return a Turso connection. Reads from env if args not provided."""
    url = db_url or TURSO_URL
    tok = token or TURSO_TOKEN
    if not url or not tok:
        raise RuntimeError(
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set in .env"
        )
    return TursoConnection(url, tok)
