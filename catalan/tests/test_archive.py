"""The committed archive — the system of record.

GitHub Actions runners are ephemeral and Turso writes are blocked, so the
NDJSON under ``catalan/data/forward_log/`` is the only durable copy of the
forward log. If the round-trip loses or reorders rows, the study loses evidence
it cannot recollect, so these tests are about durability rather than features.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime

import pytest

from catalan.data import archive
from catalan.data.store import store


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated archive dirs + store, so tests never touch the real corpus."""
    monkeypatch.setattr(archive, "ANNOUNCEMENTS_DIR", tmp_path / "announcements")
    monkeypatch.setattr(archive, "SCORES_DIR", tmp_path / "scores")
    conn = store(tmp_path / "t.db")
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO announcements (symbol, isin, company, announced_at, headline, "
        "category, industry, seq_id, source, ingested_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'nse_announcements', ?)",
        [
            ("RELIANCE", "INE002A01018", "Reliance Industries Limited",
             "2026-08-07T18:00:00", "Board approves demerger", "Board Meeting Outcome",
             "Refineries", "s1", now),
            ("TCS", "INE467B01029", "Tata Consultancy Services Limited",
             "2026-08-07T19:00:00", "Q1 revenue up 12 percent", "Board Meeting Outcome",
             "IT", "s2", now),
        ],
    )
    conn.execute(
        "INSERT INTO scores (announcement_id, model_id, prompt_hash, effort, score, "
        "rationale, run_id, scored_at) VALUES (1, 'm1', 'h1', NULL, 1, 'good', "
        "'run-1', '2026-08-10T09:00:00')"
    )
    conn.commit()
    yield conn, tmp_path
    conn.close()


def test_export_writes_gzipped_ndjson(env):
    conn, tmp = env
    assert archive.export_announcements("2026-08-07", conn=conn) == 2

    path = tmp / "announcements" / "2026-08-07.ndjson.gz"
    assert path.exists()
    with gzip.open(path, "rt") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    assert {r["symbol"] for r in rows} == {"RELIANCE", "TCS"}
    assert rows[0]["headline"] == "Board approves demerger"


def test_export_is_idempotent(env):
    conn, _ = env
    assert archive.export_announcements("2026-08-07", conn=conn) == 2
    assert archive.export_announcements("2026-08-07", conn=conn) == 0


def test_repeat_export_is_byte_identical(env):
    """A no-op re-run must not produce a spurious git diff — otherwise every
    scheduled run commits noise and the commit timestamps stop meaning anything."""
    conn, tmp = env
    archive.export_announcements("2026-08-07", conn=conn)
    path = tmp / "announcements" / "2026-08-07.ndjson.gz"
    before = path.read_bytes()
    archive.export_announcements("2026-08-07", conn=conn)
    assert path.read_bytes() == before


def test_scores_are_keyed_by_symbol_and_seq_not_row_id(env):
    """Local integer ids are an artefact of one machine's ingest order and
    would not survive a rebuild elsewhere."""
    conn, tmp = env
    archive.export_scores("2026-08-10", conn=conn)
    with gzip.open(tmp / "scores" / "2026-08-10.ndjson.gz", "rt") as fh:
        row = json.loads(fh.readline())
    assert row["symbol"] == "RELIANCE"
    assert row["seq_id"] == "s1"
    assert "announcement_id" not in row


def test_no_file_is_created_for_an_empty_day(env):
    conn, tmp = env
    assert archive.export_announcements("2026-01-01", conn=conn) == 0
    assert not (tmp / "announcements" / "2026-01-01.ndjson.gz").exists()


def test_round_trip_reconstructs_the_store(env, tmp_path):
    """The disaster-recovery path: archive alone must rebuild everything."""
    conn, _ = env
    archive.export_announcements("2026-08-07", conn=conn)
    archive.export_scores("2026-08-10", conn=conn)

    fresh = store(tmp_path / "rebuilt.db")
    try:
        archive.rebuild(conn=fresh)
        assert fresh.execute("SELECT count(*) FROM announcements").fetchone()[0] == 2
        assert fresh.execute("SELECT count(*) FROM scores").fetchone()[0] == 1
        # The score must re-attach to the right announcement after renumbering.
        sym = fresh.execute(
            "SELECT a.symbol FROM scores s JOIN announcements a ON a.id = s.announcement_id"
        ).fetchone()[0]
        assert sym == "RELIANCE"
    finally:
        fresh.close()


def test_rebuild_is_idempotent(env, tmp_path):
    conn, _ = env
    archive.export_announcements("2026-08-07", conn=conn)
    fresh = store(tmp_path / "r2.db")
    try:
        archive.rebuild(conn=fresh)
        archive.rebuild(conn=fresh)
        assert fresh.execute("SELECT count(*) FROM announcements").fetchone()[0] == 2
    finally:
        fresh.close()


def test_orphan_score_is_skipped_not_crashed(env, tmp_path, caplog):
    """An archived score whose announcement is missing must warn, not abort the
    whole rebuild — one bad row cannot cost the rest of the corpus."""
    conn, tmp = env
    archive.export_scores("2026-08-10", conn=conn)   # announcements NOT exported

    fresh = store(tmp_path / "r3.db")
    try:
        result = archive.rebuild(conn=fresh)
        assert result["scores_in_store"] == 0
    finally:
        fresh.close()
