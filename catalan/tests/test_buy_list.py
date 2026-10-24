"""The daily buy list — what the model said YES to, recorded once, before the outcome."""
from __future__ import annotations

import pytest

from catalan import buy_list
from catalan.data.store import store
from catalan.scoring import scorer

MODEL = "claude-haiku-4-5-20251001"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(buy_list, "BUY_LIST_DIR", tmp_path / "buy_list")
    monkeypatch.setattr(buy_list, "_members", lambda day: frozenset({"TCS", "INFY", "SBIN"}))
    conn = store(tmp_path / "b.db")
    rows = [  # id, symbol, time, category
        (1, "TCS", "2026-10-01T18:05:00", "Press Release"),     # PDF YES, overnight, NIFTY500
        (2, "INFY", "2026-10-01T19:00:00", "Press Release"),    # headline-only YES, overnight
        (3, "SBIN", "2026-10-01T11:00:00", "Press Release"),    # YES but market hours
        (4, "TINYCO", "2026-10-01T20:00:00", "Press Release"),  # YES overnight, not NIFTY500
        (5, "TCS", "2026-10-01T18:10:00", "Trading Window"),    # excluded category
        (6, "INFY", "2026-10-01T20:30:00", "Updates"),          # PDF NO overrides headline YES
    ]
    for i, sym, at, cat in rows:
        conn.execute(
            "INSERT INTO announcements (id, symbol, company, announced_at, headline, category, "
            "seq_id, source, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'nse', 'x')",
            (i, sym, sym + " Ltd", at, f"{sym} filing {i}", cat, f"s{i}"))
    hl, pdf = scorer.arm_hash(scorer.ARM_HEADLINE), scorer.arm_hash(scorer.ARM_PDF)
    verdicts = [(1, pdf, 1), (1, hl, -1), (2, hl, 1), (3, hl, 1), (4, hl, 1), (5, hl, 1),
                (6, hl, 1), (6, pdf, -1)]
    for ann, h, sc in verdicts:
        conn.execute(
            "INSERT INTO scores (announcement_id, model_id, prompt_hash, score, rationale, "
            "run_id, scored_at, temperature) VALUES (?, ?, ?, ?, 'why', 'r', 'x', 0.0)",
            (ann, MODEL, h, sc))
    conn.commit()
    yield conn
    conn.close()


def test_the_pdf_verdict_wins_over_the_headline_verdict(db):
    ids = {e["id"]: e for e in buy_list.entries("2026-10-01", db, MODEL)}
    assert ids[1]["source"] == "filing", "PDF YES kept although the headline said NO"
    assert 6 not in ids, "a PDF NO must override a headline YES"
    assert ids[2]["source"] == "headline only"


def test_excluded_categories_never_reach_the_list(db):
    assert 5 not in {e["id"] for e in buy_list.entries("2026-10-01", db, MODEL)}


def test_the_study_basket_is_overnight_nifty500_only(db):
    text = buy_list.render("2026-10-01", db, MODEL)
    basket = text.split("## Study basket")[1].split("## Overnight news, other")[0]
    assert "| TCS |" in basket and "| INFY |" in basket
    assert "SBIN" not in basket and "TINYCO" not in basket


def test_the_list_is_written_once_and_never_rewritten(db):
    first = buy_list.write("2026-10-01", conn=db, model_id=MODEL)
    assert first["written"]
    second = buy_list.write("2026-10-01", conn=db, model_id=MODEL)
    assert not second["written"] and second["reason"] == "already exists"


def test_a_missing_roster_is_said_out_loud(db, monkeypatch):
    monkeypatch.setattr(buy_list, "_members", lambda day: None)
    assert "universe unavailable" in buy_list.render("2026-10-01", db, MODEL)


# ── Verifier S1: a list is written only for a complete, counted day ──────────

def _day(conn, ann_id, day, symbol="TCS"):
    conn.execute(
        "INSERT INTO announcements (id, symbol, company, announced_at, headline, category, "
        "seq_id, source, ingested_at) VALUES (?, ?, ?, ?, ?, 'Press Release', ?, 'nse', 'x')",
        (ann_id, symbol, symbol, f"{day}T18:00:00", f"{symbol} news {ann_id}", f"q{ann_id}"))
    conn.commit()


def test_a_day_with_unscored_filings_is_not_ready(db):
    _day(db, 50, "2026-10-21")                     # no verdict in either arm yet
    assert "2026-10-21" not in buy_list.ready_days(db, MODEL, since="2026-10-19", until="2026-10-21",
                                                   today="2026-10-25")


def test_a_day_becomes_ready_once_both_arms_have_scored_it(db):
    _day(db, 51, "2026-10-22")
    for h in (scorer.arm_hash(scorer.ARM_HEADLINE),):
        db.execute("INSERT INTO scores (announcement_id, model_id, prompt_hash, score, rationale, "
                   "run_id, scored_at, temperature) VALUES (51, ?, ?, 1, 'why', 'r', 'x', 0.0)", (MODEL, h))
    db.commit()
    # no pdf_texts row → nothing pending in the PDF arm, and no attachment to wait for
    assert "2026-10-22" in buy_list.ready_days(db, MODEL, since="2026-10-19", until="2026-10-22",
                                               today="2026-10-25")


def test_today_is_never_ready_because_it_is_still_being_filed(db):
    _day(db, 52, "2026-10-23")
    for h in (scorer.arm_hash(scorer.ARM_HEADLINE),):
        db.execute("INSERT INTO scores (announcement_id, model_id, prompt_hash, score, rationale, "
                   "run_id, scored_at, temperature) VALUES (52, ?, ?, 1, 'why', 'r', 'x', 0.0)", (MODEL, h))
    db.commit()
    assert "2026-10-23" not in buy_list.ready_days(db, MODEL, since="2026-10-19", until="2026-10-23",
                                                   today="2026-10-23")


def test_pilot_days_never_get_a_list(db):
    """The fixture's 2026-10-01 day is fully scored, but it predates the clock."""
    out = buy_list.write_ready(conn=db, model_id=MODEL, since="2026-10-19", until="2026-10-25",
                               today="2026-10-26")
    assert "2026-10-01" not in out["written"]


def test_a_day_blocked_by_failing_scoring_is_written_after_the_wait(db):
    _day(db, 53, "2026-10-21")                     # never scored
    assert "2026-10-21" not in buy_list.ready_days(db, MODEL, since="2026-10-19", until="2026-10-22",
                                                   today="2026-10-23")
    assert "2026-10-21" in buy_list.ready_days(db, MODEL, since="2026-10-19", until="2026-10-28",
                                               today="2026-10-29")
    assert "still missing" in buy_list.render("2026-10-21", db, MODEL)
