"""Scoring: response parsing, cross-sectional selection, filters, provenance.

No network — the Anthropic transport is stubbed. What is tested here is
everything that decides *which* headlines get scored and *how the verdict is
read*, which is where a silent bug would corrupt the study rather than just
crash it.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from catalan.scoring import filters, scorer
from catalan.scoring.prompt import prompt_hash
from catalan.data.store import store


# ── Response parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,score", [
    ("YES\nThe demerger unlocks value.", 1),
    ("NO\nThe fine is material.", -1),
    ("UNKNOWN\nRoutine disclosure.", 0),
    ('"YES"\nGood for margins.', 1),
    ("yes\nlowercase still parses.", 1),
    ("YES.\nWith punctuation.", 1),
])
def test_parses_the_first_line_verdict(raw, score):
    assert scorer.parse_response(raw)[0] == score


def test_rationale_is_the_following_lines():
    score, rationale = scorer.parse_response("NO\nThe penalty exceeds quarterly profit.")
    assert score == -1
    assert rationale == "The penalty exceeds quarterly profit."


def test_verdict_is_read_from_the_first_line_only():
    """A rationale mentioning another verdict must not flip the score — this is
    the parse bug that would quietly invert part of the cross-section."""
    score, _ = scorer.parse_response("YES\nThis is not a NO for the stock price.")
    assert score == 1


@pytest.mark.parametrize("raw", ["", None, "   ", "I cannot help with that."])
def test_unparseable_responses_abstain_rather_than_default(raw):
    """None is a parse miss; 0 is a real UNKNOWN verdict. Collapsing the two
    would silently pad the neutral bucket with API failures."""
    assert scorer.parse_response(raw)[0] is None


def test_abstention_is_distinguishable_from_unknown():
    assert scorer.parse_response("UNKNOWN\nNo view.")[0] == 0
    assert scorer.parse_response("banana")[0] is None


# ── Filters ───────────────────────────────────────────────────────────────────

def _row(symbol, headline, category="Board Meeting Outcome", at="2026-08-07T10:00:00"):
    return {"symbol": symbol, "headline": headline, "category": category,
            "announced_at": at}


def test_excluded_categories_are_dropped():
    rows = [_row("A", "Real news"), _row("B", "Notice", "Trading Window")]
    kept, counts = filters.apply_all(rows)
    assert counts["excluded_category"] == 1
    assert [r["symbol"] for r in kept] == ["A"]


def test_newspaper_publication_is_dropped_on_headline_text_alone():
    """The row is the same non-event whatever `desc` NSE filed it under —
    the category filter must not be the only line of defence."""
    rows = [_row("A", "Real news"),
            _row("OLAELEC",
                 "Ola Electric Mobility Limited has informed the Exchange about "
                 "Copy of Newspaper Publication",
                 category="General Updates")]        # miscategorised on purpose
    kept, counts = filters.apply_all(rows)
    assert counts["excluded_headline"] == 1
    assert counts["excluded_category"] == 0
    assert [r["symbol"] for r in kept] == ["A"]


def test_headline_exclusion_is_case_insensitive():
    rows = [_row("X", "COPY OF NEWSPAPER PUBLICATION submitted", category="Updates")]
    assert filters.apply_all(rows)[1]["excluded_headline"] == 1


def test_the_two_exclusion_rules_are_counted_separately():
    """If the headline rule ever starts catching a lot, that is NSE's `desc`
    drifting — it must be visible in the log, not silently absorbed."""
    rows = [
        _row("A", "Real news"),
        _row("B", "Notice", category="Trading Window"),
        _row("C", "has informed the Exchange about Copy of Newspaper Publication",
             category="General Updates"),
    ]
    kept, counts = filters.apply_all(rows)
    assert counts["excluded_category"] == 1
    assert counts["excluded_headline"] == 1
    assert counts["kept"] == 1


def test_a_row_hit_by_both_rules_is_not_double_counted():
    rows = [_row("D", "has informed the Exchange about Copy of Newspaper Publication",
                 category="Copy of Newspaper Publication")]
    counts = filters.apply_all(rows)[1]
    assert counts["excluded_category"] == 1
    assert counts["excluded_headline"] == 0     # category already claimed it
    assert counts["kept"] == 0


def test_near_duplicates_keep_the_earliest():
    """The first announcement carried the information; a later restatement
    would attribute the reaction to the wrong timestamp."""
    rows = [
        _row("REL", "Board approves demerger of financial services business",
             at="2026-08-07T18:00:00"),
        _row("REL", "Board approves demerger of financial services businesses",
             at="2026-08-07T19:30:00"),
    ]
    kept, counts = filters.apply_all(rows)
    assert counts["excluded_duplicate"] == 1
    assert kept[0]["announced_at"] == "2026-08-07T18:00:00"


def test_dedup_is_scoped_to_same_symbol_same_day():
    same_text = "Intimation of board meeting"
    rows = [
        _row("A", same_text, at="2026-08-07T10:00:00"),
        _row("B", same_text, at="2026-08-07T10:00:00"),   # different symbol
        _row("A", same_text, at="2026-08-08T10:00:00"),   # different day
    ]
    kept, counts = filters.apply_all(rows)
    assert counts["excluded_duplicate"] == 0
    assert len(kept) == 3


def test_distinct_headlines_survive():
    rows = [
        _row("REL", "Board approves demerger", at="2026-08-07T10:00:00"),
        _row("REL", "Q1 results: revenue up 12%", at="2026-08-07T11:00:00"),
    ]
    kept, _ = filters.apply_all(rows)
    assert len(kept) == 2


def test_normalise_headline_is_comparison_only():
    assert filters.normalise_headline("Board  Approves, Demerger!") == "board approves demerger"


# ── Cross-sectional selection ─────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    conn = store(tmp_path / "score.db")
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        ("RELIANCE", "Board approves demerger", "Board Meeting Outcome", "s1"),
        ("TCS", "Q1 revenue up 12 percent", "Board Meeting Outcome", "s2"),
        ("INFY", "Newspaper copy", "Copy of Newspaper Publication", "s3"),
    ]
    conn.executemany(
        "INSERT INTO announcements (symbol, company, announced_at, headline, "
        "category, seq_id, source, ingested_at) VALUES (?, ?, ?, ?, ?, ?, 'test', ?)",
        [(s, s, "2026-08-07T18:00:00", h, c, q, now) for s, h, c, q in rows],
    )
    conn.commit()
    yield conn
    conn.close()


def test_pending_spans_the_whole_cross_section(db):
    """Scoring is never scoped to a symbol — pending() takes no symbol at all."""
    rows = scorer.pending("test-model", conn=db, apply_filters=False)
    assert {r["symbol"] for r in rows} == {"RELIANCE", "TCS", "INFY"}


def test_pending_applies_filters_by_default(db):
    """Filtering here is a cost control too — no point paying to score a
    newspaper copy."""
    rows = scorer.pending("test-model", conn=db)
    assert {r["symbol"] for r in rows} == {"RELIANCE", "TCS"}


def test_scored_rows_stop_being_pending(db):
    scorer._write_scores(db, [(1, "test-model", prompt_hash(), None, 1,
                               "why", "run-1", "2026-08-10T09:00:00")])
    assert {r["symbol"] for r in scorer.pending("test-model", conn=db)} == {"TCS"}


def test_a_different_model_re_opens_everything(db):
    """Each arm scores the full corpus independently — that is what makes the
    model-size comparison a like-for-like one."""
    scorer._write_scores(db, [(1, "model-a", prompt_hash(), None, 1,
                               "why", "run-1", "2026-08-10T09:00:00")])
    assert len(scorer.pending("model-b", conn=db)) == 2


def test_scores_are_append_only(db):
    """A re-run under identical provenance must not overwrite the first verdict."""
    row = (1, "m", "h", None, 1, "first", "run-1", "2026-08-10T09:00:00")
    assert scorer._write_scores(db, [row]) == 1
    assert scorer._write_scores(db, [(1, "m", "h", None, -1, "second",
                                      "run-2", "2026-08-10T10:00:00")]) == 0
    stored = db.execute("SELECT score, rationale FROM scores").fetchone()
    assert (stored[0], stored[1]) == (1, "first")
