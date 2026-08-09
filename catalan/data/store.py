"""
CATALAN's datastore seam.

Two connections, deliberately asymmetric:

  store()    → local SQLite, read/write. Everything CATALAN produces.
  turso_ro() → the main repo's Turso, wrapped so that any statement which is
               not a SELECT/PRAGMA/WITH raises.

The asymmetry is the point. Turso's free-tier write quota was exhausted
repo-wide on 2026-08-05 and the eod-sync crons are commented out; a stray write
from a research script is a real hazard, not a hypothetical one. Enforcing it
here — at the one place CATALAN opens Turso — is cheaper than trusting every
caller. ``tests/test_no_turso_writes.py`` asserts against this guard.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from catalan.config import DB_PATH

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Columns added after the first stores were created. `CREATE TABLE IF NOT
# EXISTS` in schema.sql cannot retrofit these, so they are ALTERed in
# idempotently on every open. Additive only — never drop or rename here, or a
# rebuild from the archive would lose data.
_MIGRATIONS = (
    ("announcements", "attachment_url", "TEXT"),
    ("announcements", "attachment_size", "TEXT"),
    ("announcements", "has_xbrl", "INTEGER"),
    ("announcements", "disseminated_at", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, coltype in _MIGRATIONS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()

# A statement is allowed if its first keyword is one of these. Comments and
# leading whitespace are stripped first so `-- oops\nDELETE ...` cannot sneak by.
_READ_ONLY_VERBS = frozenset({"select", "pragma", "with", "explain"})

_COMMENT_RE = re.compile(r"(--[^\n]*\n)|(/\*.*?\*/)", re.DOTALL)


class TursoWriteAttempt(RuntimeError):
    """Raised when CATALAN tries to write to Turso. Always a bug — see CLAUDE.md."""


def _first_verb(sql: str) -> str:
    stripped = _COMMENT_RE.sub(" ", sql).strip()
    return stripped.split(None, 1)[0].lower() if stripped else ""


def _assert_read_only(sql: str) -> None:
    verb = _first_verb(sql)
    if verb not in _READ_ONLY_VERBS:
        raise TursoWriteAttempt(
            f"CATALAN opened Turso read-only but got a {verb.upper() or 'blank'} "
            f"statement. Turso is read-only from CATALAN — write to the local "
            f"store instead (catalan/data/catalan.db). Statement: {sql[:120]!r}"
        )


# ── Local store ───────────────────────────────────────────────────────────────

def store(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Open (creating if needed) the local CATALAN store with schema applied.

    Idempotent: schema.sql is all CREATE ... IF NOT EXISTS, so this is safe to
    call on every process start.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_PATH.read_text())
    conn.commit()
    _migrate(conn)
    return conn


# ── Turso, read-only ──────────────────────────────────────────────────────────

class ReadOnlyCursor:
    """Wraps a Turso cursor, rejecting anything that is not a read."""

    def __init__(self, inner: Any):
        self._inner = inner

    def execute(self, sql: str, params: Union[Sequence, dict, None] = None):
        _assert_read_only(sql)
        self._inner.execute(sql, params)
        return self

    def executemany(self, sql: str, params_list) -> None:
        raise TursoWriteAttempt(
            "executemany() is a write path and is not available on CATALAN's "
            "read-only Turso connection."
        )

    def __getattr__(self, name: str) -> Any:
        # fetchall / fetchone / description / rowcount pass straight through
        return getattr(self._inner, name)

    def __iter__(self):
        return iter(self._inner)


class ReadOnlyConnection:
    """Read-only proxy over a TursoConnection. Also satisfies pd.read_sql_query."""

    def __init__(self, inner: Any):
        self._inner = inner

    def cursor(self) -> ReadOnlyCursor:
        return ReadOnlyCursor(self._inner.cursor())

    def execute(self, sql: str, params: Union[Sequence, dict, None] = None) -> ReadOnlyCursor:
        _assert_read_only(sql)
        return ReadOnlyCursor(self._inner.execute(sql, params))

    def executemany(self, sql: str, params_list) -> None:
        raise TursoWriteAttempt(
            "executemany() is a write path and is not available on CATALAN's "
            "read-only Turso connection."
        )

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> "ReadOnlyConnection":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def turso_ro() -> ReadOnlyConnection:
    """Read-only Turso connection for price_history / index_membership / earnings_events.

    This is the only place CATALAN reaches into the main project's database.
    """
    from quant_engine.data.turso_client import connect

    return ReadOnlyConnection(connect())


def turso_reachable() -> bool:
    """Cheap liveness probe for /health. Never raises."""
    try:
        cur = turso_ro().execute("SELECT 1")
        return cur.fetchone() is not None
    except Exception:
        return False
