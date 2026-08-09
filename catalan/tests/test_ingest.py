"""Forward collector: normalisation and idempotency.

Idempotency is not a nicety — the collector will be re-run on overlapping
windows during catch-up, and a corpus with duplicated announcements would
silently overweight whatever was fetched twice.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from catalan.data.collect_daily import normalise, parse_ts, persist
from catalan.data.store import store


@pytest.fixture
def db(tmp_path):
    conn = store(tmp_path / "test.db")
    yield conn
    conn.close()


def nse_row(**over):
    row = {
        "symbol": "RELIANCE",
        "sm_isin": "INE002A01018",
        "sm_name": "Reliance Industries Limited",
        "an_dt": "06-Aug-2026 18:23:45",
        "attchmntText": "Board approves demerger of financial services business",
        "desc": "Board Meeting Outcome",
        "smIndustry": "Refineries",
        "seqId": "998877",
    }
    row.update(over)
    return row


# ── Timestamp parsing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("06-Aug-2026 18:23:45", "2026-08-06T18:23:45"),
    ("06-Aug-2026 18:23", "2026-08-06T18:23:00"),
    ("2026-08-06 18:23:45", "2026-08-06T18:23:45"),
    ("2026-08-06T18:23:45", "2026-08-06T18:23:45"),
])
def test_parse_ts_accepts_nse_formats(raw, expected):
    assert parse_ts(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "not a date", "-"])
def test_parse_ts_returns_none_rather_than_guessing(raw):
    assert parse_ts(raw) is None


# ── Normalisation ─────────────────────────────────────────────────────────────

def test_normalise_maps_nse_fields():
    out = normalise(nse_row())
    assert out[0] == "RELIANCE"
    assert out[3] == "2026-08-06T18:23:45"
    assert out[4].startswith("Board approves demerger")
    assert out[5] == "Board Meeting Outcome"
    assert out[7] == "998877"


def test_normalise_uppercases_and_strips_symbol():
    assert normalise(nse_row(symbol="  reliance "))[0] == "RELIANCE"


@pytest.mark.parametrize("bad", [
    {"symbol": ""},
    {"attchmntText": "   "},
    {"an_dt": "garbage"},
])
def test_normalise_drops_unusable_rows(bad):
    """The June-2026 probe found zero blank headlines in 6,395 in-universe rows,
    so a blank one means the response shape changed — drop, don't invent."""
    assert normalise(nse_row(**bad)) is None


def test_seq_id_falls_back_to_content_hash():
    """NSE is inconsistent about the identity key across date ranges. The hash
    only has to be stable for the same row across re-fetches."""
    row = nse_row()
    row.pop("seqId")
    first, second = normalise(row), normalise(dict(row))
    assert first[7].startswith("h:")
    assert first[7] == second[7]


def test_different_headlines_get_different_seq_ids():
    a, b = nse_row(), nse_row(attchmntText="Something else entirely")
    a.pop("seqId"); b.pop("seqId")
    assert normalise(a)[7] != normalise(b)[7]


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_reinserting_the_same_rows_adds_nothing(db):
    rows = [normalise(nse_row())]
    assert persist(db, rows) == 1
    assert persist(db, rows) == 0
    assert db.execute("SELECT count(*) FROM announcements").fetchone()[0] == 1


def test_same_seq_id_different_symbol_is_a_distinct_row(db):
    """UNIQUE is (symbol, seq_id) — NSE sequence ids are not globally unique."""
    persist(db, [normalise(nse_row())])
    persist(db, [normalise(nse_row(symbol="TCS"))])
    assert db.execute("SELECT count(*) FROM announcements").fetchone()[0] == 2


def test_persist_handles_an_empty_batch(db):
    assert persist(db, []) == 0
