"""Turso is read-only from CATALAN.

Turso's free-tier write quota was exhausted repo-wide on 2026-08-05 and the
eod-sync crons are commented out. A stray write from a research script is a
real hazard here, not a hypothetical one, so store.turso_ro() rejects anything
that is not a read and these tests hold it to that.

No network required — the guard is checked against a fake inner connection.
"""
from __future__ import annotations

import pytest

from catalan.data.store import ReadOnlyConnection, TursoWriteAttempt


class _FakeCursor:
    def __init__(self):
        self.seen = []

    def execute(self, sql, params=None):
        self.seen.append(sql)
        return self

    def executemany(self, sql, params_list):
        self.seen.append(sql)
        return self

    def fetchone(self):
        return (1,)


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()

    def cursor(self):
        return self.cur

    def execute(self, sql, params=None):
        return self.cur.execute(sql, params)

    def executemany(self, sql, params_list):
        return self.cur.executemany(sql, params_list)

    def close(self):
        pass


@pytest.fixture
def ro():
    return ReadOnlyConnection(_FakeConn())


@pytest.mark.parametrize("sql", [
    "SELECT * FROM price_history LIMIT 1",
    "  select symbol from index_membership",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "PRAGMA table_info(price_history)",
    "EXPLAIN SELECT 1",
])
def test_reads_pass_through(ro, sql):
    assert ro.execute(sql) is not None


@pytest.mark.parametrize("sql", [
    "INSERT INTO price_history VALUES (1)",
    "UPDATE market_regime SET vix = 1",
    "DELETE FROM earnings_events",
    "DROP TABLE price_history",
    "CREATE TABLE catalan_scratch (a INT)",
    "REPLACE INTO market_regime VALUES (1)",
])
def test_writes_are_rejected(ro, sql):
    with pytest.raises(TursoWriteAttempt):
        ro.execute(sql)


def test_comment_prefix_cannot_smuggle_a_write(ro):
    """`-- harmless\\nDELETE ...` must not read as a comment-led no-op."""
    with pytest.raises(TursoWriteAttempt):
        ro.execute("-- just looking\nDELETE FROM price_history")

    with pytest.raises(TursoWriteAttempt):
        ro.execute("/* block */ UPDATE price_history SET close = 0")


def test_executemany_is_unavailable_on_both_surfaces(ro):
    with pytest.raises(TursoWriteAttempt):
        ro.executemany("INSERT INTO x VALUES (?)", [(1,)])
    with pytest.raises(TursoWriteAttempt):
        ro.cursor().executemany("INSERT INTO x VALUES (?)", [(1,)])


def test_cursor_writes_are_rejected_too(ro):
    with pytest.raises(TursoWriteAttempt):
        ro.cursor().execute("INSERT INTO price_history VALUES (1)")
