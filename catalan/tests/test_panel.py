"""Phase 2 — the returns panel.

Every test here runs offline: an in-memory sqlite ``price_history`` stands in
for Turso, wrapped in the REAL ``ReadOnlyConnection`` so that the actual SQL and
the write guard are both exercised, and a hand-built ``MembershipRegistry``
stands in for the PIT roster.

The tests that matter most are not the happy path. They are:

  • the 1:10 split — the corporate-action guard must protect the untradable leg
    and leave the TRADABLE leg (drift_ret) completely alone. If someone
    "simplifies" that guard into dropping the row, this study loses drift
    observations on exactly its highest-news-intensity days;
  • ``typeof(in_universe)`` — np.int64 binds to sqlite3 as a BLOB, so a panel
    written without coercion passes every insert and then silently returns zero
    rows for `WHERE in_universe = 1`;
  • row conservation — silent row loss is this study's stated main failure mode.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from functools import lru_cache

import pytest

from catalan.config import PANEL_WARMUP_SESSIONS
from catalan.data import panel, universe
from catalan.data.panel import build_panel, load_panel, PanelReport
from catalan.data.store import ReadOnlyConnection, store

pd = pytest.importorskip("pandas")


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _weekdays(frm: date, to: date):
    d, out = frm, []
    while d <= to:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# Two months of weekdays (~44 sessions). Comfortably more than ADV_WINDOW_DAYS,
# so the last sessions carry a full 20-bar ADV and the quintile path is reached,
# and long enough that a weekly-cadence symbol accumulates the
# PANEL_CADENCE_MIN_PERIODS gap observations its rolling median needs.
SESSIONS = _weekdays(date(2026, 3, 2), date(2026, 4, 30))

# 25 symbols: enough to clear the production phantom-session floor
# (PANEL_MIN_SESSION_BREADTH_ABS = 20 bars) so these tests exercise the real
# constant rather than a relaxed one, and an exact multiple of
# LIQUIDITY_BUCKETS so each quintile holds exactly 5 names.
SYMBOLS = [f"SYM{i:02d}" for i in range(1, 26)]
MOST_LIQUID = SYMBOLS[-1]


class _FakeTurso(ReadOnlyConnection):
    """A real ReadOnlyConnection over an in-memory sqlite price_history.

    Subclassed rather than mocked so the read-only guard, the SQL text and the
    DB-API surface are all the production ones. The only accommodation is
    ``params or ()``: ReadOnlyConnection forwards params verbatim and
    ``sqlite3.execute(sql, None)`` raises, where Turso's client tolerates None.
    """

    def execute(self, sql, params=None):
        return super().execute(sql, params or ())


def _price_db(rows):
    """rows: iterable of (symbol, date, open, high, low, close, volume)."""
    raw = sqlite3.connect(":memory:")
    raw.execute(
        "CREATE TABLE price_history (symbol TEXT, date TEXT, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL)"
    )
    raw.executemany(
        "INSERT INTO price_history VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(s, d.isoformat() if hasattr(d, "isoformat") else d, o, h, l, c, v)
         for s, d, o, h, l, c, v in rows],
    )
    raw.commit()
    return _FakeTurso(raw)


def _bar(sym, d, close, *, open_=None, high=None, low=None, volume=100_000.0):
    """A well-formed daily bar with a real intraday range.

    The range matters: open == high == low == close is the flat-bar signature
    the RapidAPI era left behind, so an accidentally flat fixture bar would be
    dropped and the test would fail for the wrong reason.
    """
    o = close * 0.99 if open_ is None else open_
    hi = max(o, close) * 1.01 if high is None else high
    lo = min(o, close) * 0.99 if low is None else low
    return (sym, d, o, hi, lo, close, volume)


def _clean_rows(symbols=SYMBOLS, sessions=SESSIONS):
    """A clean panel: every symbol trades every session, prices drift upward.

    Volume varies by symbol so ADV is strictly ordered and quintiles are
    unambiguous — SYM01 least liquid, SYM10 most.
    """
    rows = []
    for i, sym in enumerate(symbols, start=1):
        for j, d in enumerate(sessions):
            rows.append(_bar(sym, d, close=100.0 + i + j, volume=1_000.0 * i))
    return rows


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    """All SYMBOLS are NIFTY500 members throughout. No Turso, no network."""
    from quant_engine.data.membership import MembershipInterval, MembershipRegistry

    intervals = [
        MembershipInterval("NIFTY500", s, date(2018, 1, 1), None) for s in SYMBOLS
    ] + [MembershipInterval("NIFTY500", "OUTSIDER", date(2018, 1, 1), date(2018, 6, 30))]
    reg = MembershipRegistry.from_iterable(intervals)

    @lru_cache(maxsize=1)
    def _fake():
        return reg

    universe.reset_cache()
    monkeypatch.setattr(universe, "_registry", _fake)
    universe.members_on.cache_clear()
    yield
    universe.reset_cache()


@pytest.fixture
def db(tmp_path):
    conn = store(tmp_path / "panel.db")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _history_start(monkeypatch):
    """HISTORY_START defaults to 2019 and the fixtures live in 2026.

    The session grid is built from HISTORY_START so median breadth is measured
    over the whole study era; in tests that would just scan an empty range.
    """
    monkeypatch.setattr(panel, "HISTORY_START", "2026-01-01")


def _build(db, rows, start=SESSIONS[-2], end=SESSIONS[-1], **kw):
    return build_panel(start, end, conn=db, turso=_price_db(rows), **kw)


# ── Report shape and the happy path ───────────────────────────────────────────

def test_clean_build_writes_every_symbol_for_every_session(db):
    report = _build(db, _clean_rows())
    assert isinstance(report, PanelReport)
    assert report.sessions == 2
    assert report.rows_written == len(SYMBOLS) * 2
    assert report.rows_in_universe == len(SYMBOLS) * 2
    assert sum(report.dropped.values()) == 0
    assert report.as_dict()["rows_written"] == report.rows_written


def test_warmup_rows_are_reported_separately_from_drops(db):
    """Warm-up rows are neither written nor dropped — they are a third bucket.

    Counting them as drops would fail the conservation check on every run, and
    a check that always fails gets disabled.
    """
    report = _build(db, _clean_rows())
    # Exactly PANEL_WARMUP_SESSIONS deep, counted in SESSIONS — not every bar
    # that happens to precede the window, and not a calendar-day guess.
    assert report.rows_warmup == len(SYMBOLS) * PANEL_WARMUP_SESSIONS
    assert report.rows_written + report.rows_warmup + sum(report.dropped.values()) \
        == report.rows_fetched


def test_return_legs_are_computed_from_the_right_prices(db):
    _build(db, _clean_rows())
    row = db.execute(
        "SELECT prev_close, open, close, init_ret, drift_ret FROM panel "
        "WHERE symbol = 'SYM01' AND date = ?", (SESSIONS[-1].isoformat(),)
    ).fetchone()
    assert row["init_ret"] == pytest.approx(row["open"] / row["prev_close"] - 1)
    assert row["drift_ret"] == pytest.approx(row["close"] / row["open"] - 1)


# ── The corporate-action guard: THE SHIP-GATE TEST ────────────────────────────

def test_ten_for_one_split_nulls_the_untradable_leg_and_keeps_the_tradable_one(db):
    """A 1:10 split: prev_close 1000 → open 100.

    init_ret and prev_close must be nulled; open, close and **drift_ret** must
    survive untouched. On an ex-date all four prices are already quoted in the
    post-action basis, so only the comparison to the previous close crosses the
    basis change — the guard costs the C2 ship gate nothing.
    """
    split_day = SESSIONS[-1]
    rows = [r for r in _clean_rows() if r[0] != "SYM01"]
    # SYM01 sits flat at 1,000 through the whole warm-up, so the ONLY >20% move
    # in the fixture is the split itself. Any drift in the pre-split series
    # would trip the guard a second time and make the count meaningless.
    rows += [_bar("SYM01", d, close=1000.0, open_=990.0, volume=1_000.0)
             for d in SESSIONS[:-1]]
    rows.append(_bar("SYM01", split_day, close=104.0, open_=100.0, volume=1_000.0))

    report = _build(db, rows)

    row = db.execute(
        "SELECT prev_close, open, close, init_ret, drift_ret FROM panel "
        "WHERE symbol = 'SYM01' AND date = ?", (split_day.isoformat(),)
    ).fetchone()
    assert row["init_ret"] is None, "the untradable leg must be suppressed"
    assert row["prev_close"] is None, "prev_close crosses the basis change too"
    assert row["open"] == pytest.approx(100.0), "prices are retained"
    assert row["close"] == pytest.approx(104.0)
    assert row["drift_ret"] == pytest.approx(104.0 / 100.0 - 1), \
        "THE TRADABLE LEG MUST SURVIVE — this is the C2 gate's only input"

    assert report.suppressed["corporate_action"] == 1
    assert report.ca_suspects[0]["symbol"] == "SYM01"
    assert report.ca_suspects[0]["nearest_ratio"] == "10:1", \
        "the report should show a 1:10 split as a clean ratio"


def test_corporate_action_suppression_is_a_field_drop_not_a_row_drop(db):
    """The suspect row is still WRITTEN. It must never enter `dropped`."""
    rows = [r for r in _clean_rows() if r[0] != "SYM01"]
    rows += [_bar("SYM01", d, close=100.0, open_=99.0, volume=1_000.0)
             for d in SESSIONS[:-1]]
    rows.append(_bar("SYM01", SESSIONS[-1], close=500.0, open_=480.0, volume=1_000.0))

    report = _build(db, rows)
    assert sum(report.dropped.values()) == 0
    assert report.rows_written == len(SYMBOLS) * 2
    assert report.suppressed["corporate_action"] == 1


# ── prev_close on the global grid ─────────────────────────────────────────────

def test_prev_close_is_the_previous_global_session_not_the_symbols_own_bar(db):
    """A symbol that skips a session gets NULL, specifically not the bar before.

    Forward-filling here would turn open(t)/close(t-2)-1 into a two-day return
    wearing an overnight label.
    """
    skipped, target = SESSIONS[-2], SESSIONS[-1]
    rows = [r for r in _clean_rows() if not (r[0] == "SYM01" and r[1] == skipped)]

    _build(db, rows)
    row = db.execute(
        "SELECT prev_close, init_ret, close FROM panel "
        "WHERE symbol = 'SYM01' AND date = ?", (target.isoformat(),)
    ).fetchone()
    assert row["prev_close"] is None
    assert row["init_ret"] is None
    assert row["close"] is not None, "the row itself is still written"

    # And the value it must NOT have picked up: SYM01's close two sessions back.
    two_back = [r for r in rows if r[0] == "SYM01" and r[1] == SESSIONS[-3]][0][5]
    assert row["prev_close"] != two_back


def test_prev_close_uses_the_grid_even_when_no_symbol_traded_a_gap(db):
    report = _build(db, _clean_rows())
    assert report.suppressed["no_prev_close"] == 0


# ── Degenerate prices ─────────────────────────────────────────────────────────

def test_zero_open_nulls_both_legs_rather_than_dividing(db):
    """open == 0 must give NULL, never 0.0 and never inf."""
    target = SESSIONS[-1]
    rows = [r for r in _clean_rows() if not (r[0] == "SYM01" and r[1] == target)]
    rows.append(("SYM01", target, 0.0, 110.0, 0.0, 105.0, 1_000.0))

    _build(db, rows)
    row = db.execute(
        "SELECT open, init_ret, drift_ret FROM panel "
        "WHERE symbol = 'SYM01' AND date = ?", (target.isoformat(),)
    ).fetchone()
    assert row["init_ret"] is None and row["drift_ret"] is None
    n_inf = db.execute(
        "SELECT COUNT(*) c FROM panel WHERE drift_ret > 1e300 OR drift_ret < -1e300"
    ).fetchone()["c"]
    assert n_inf == 0


def test_row_with_neither_open_nor_close_is_dropped(db):
    target = SESSIONS[-1]
    rows = [r for r in _clean_rows() if not (r[0] == "SYM01" and r[1] == target)]
    rows.append(("SYM01", target, None, None, None, None, 1_000.0))

    report = _build(db, rows)
    assert report.dropped["no_usable_price"] == 1
    assert db.execute(
        "SELECT COUNT(*) c FROM panel WHERE symbol='SYM01' AND date=?",
        (target.isoformat(),)).fetchone()["c"] == 0


def test_flat_bar_is_dropped_and_leaves_no_exact_zero_drift(db):
    """o==h==l==c gives drift_ret an EXACT 0.0 — a fake zero in the tradable leg.

    Worse than noise, because it shrinks the estimated mean toward zero rather
    than merely widening the error bar.
    """
    target = SESSIONS[-1]
    rows = [r for r in _clean_rows() if not (r[0] == "SYM01" and r[1] == target)]
    rows.append(("SYM01", target, 120.0, 120.0, 120.0, 120.0, 1_000.0))

    report = _build(db, rows)
    assert report.dropped["flat_bar"] == 1
    assert db.execute(
        "SELECT COUNT(*) c FROM panel WHERE drift_ret = 0.0").fetchone()["c"] == 0


# ── Cadence and phantom sessions ──────────────────────────────────────────────

def test_weekly_cadence_rows_are_dropped_while_daily_era_rows_survive(db):
    """A symbol bar-for-bar on a weekly grid is contaminated history, not data.

    Pre-2025-03-17 rows were weekly AGGREGATED OHLC, so BOTH legs are weekly
    returns wearing a session label.
    """
    weekly_sessions = SESSIONS[::5]        # one bar per week over two months
    rows = _clean_rows()
    rows = [r for r in rows if r[0] != "SYM01"]
    rows += [_bar("SYM01", d, close=100.0 + k, volume=1_000.0)
             for k, d in enumerate(weekly_sessions)]

    report = _build(db, rows, start=SESSIONS[0], end=SESSIONS[-1])
    assert report.dropped["weekly_cadence"] > 0
    survivors = db.execute(
        "SELECT COUNT(*) c FROM panel WHERE symbol = 'SYM02'").fetchone()["c"]
    assert survivors == len(SESSIONS), "the daily-era symbols are untouched"


def test_a_thin_date_is_rejected_as_a_phantom_session(db):
    """A date carrying a couple of bars is a partial write, not a trading day."""
    phantom = date(2026, 3, 21)            # a Saturday; two bars only
    rows = _clean_rows()
    rows.append(_bar("SYM01", phantom, close=999.0))
    rows.append(_bar("SYM02", phantom, close=999.0))

    report = _build(db, rows)
    assert (phantom.isoformat(), 2) in report.sessions_rejected
    assert report.dropped["phantom_session"] == 2
    assert db.execute(
        "SELECT COUNT(*) c FROM panel WHERE date = ?",
        (phantom.isoformat(),)).fetchone()["c"] == 0


def test_duplicate_bars_are_dropped_not_upserted_over(db):
    """price_history duplicates must be counted, not silently collapsed by the PK."""
    rows = _clean_rows()
    rows.append(_bar("SYM01", SESSIONS[-1], close=777.0, volume=1_000.0))

    report = _build(db, rows)
    assert report.dropped["duplicate_bar"] == 1
    assert report.rows_written == len(SYMBOLS) * 2


# ── ADV and quintiles ─────────────────────────────────────────────────────────

def test_adv_needs_the_full_window_and_is_null_before_it(db):
    """min_periods is the FULL 20 bars deliberately.

    A 6-bar and a 20-bar ADV are not comparable within one cross-section, and
    partials would mis-bucket freshly listed names — the very population the
    paper's alpha concentrates in.
    """
    report = build_panel(SESSIONS[0], SESSIONS[-1], conn=db,
                         turso=_price_db(_clean_rows()))
    early = db.execute(
        "SELECT adv_20d, liquidity_bucket FROM panel "
        "WHERE symbol='SYM01' AND date=?", (SESSIONS[0].isoformat(),)).fetchone()
    late = db.execute(
        "SELECT adv_20d, liquidity_bucket FROM panel "
        "WHERE symbol='SYM01' AND date=?", (SESSIONS[-1].isoformat(),)).fetchone()
    assert early["adv_20d"] is None and early["liquidity_bucket"] is None
    assert late["adv_20d"] is not None and late["liquidity_bucket"] is not None
    assert report.suppressed["adv_short"] > 0


def test_bucket_one_is_the_least_liquid_name(db):
    """Orientation is a declared study parameter, not an implementation detail.

    PREREGISTRATION reads its negative-result verdict off "the most illiquid
    bucket". SYM01 has the smallest volume, SYM10 the largest.
    """
    _build(db, _clean_rows())
    d = SESSIONS[-1].isoformat()
    lo = db.execute("SELECT liquidity_bucket b FROM panel WHERE symbol='SYM01' "
                    "AND date=?", (d,)).fetchone()["b"]
    hi = db.execute("SELECT liquidity_bucket b FROM panel WHERE symbol=? "
                    "AND date=?", (MOST_LIQUID, d)).fetchone()["b"]
    assert lo == 1, "least liquid must be bucket 1"
    assert hi == 5, "most liquid must be bucket 5"


def test_quintiles_are_deterministic_under_input_row_order(db, tmp_path):
    """Shuffled Turso row order must produce byte-identical buckets.

    rank(method="first") breaks ties by position, so without an explicit sort
    the buckets would be a function of the database's response order.
    """
    import random

    rows = _clean_rows()
    _build(db, rows)
    a = db.execute("SELECT symbol, date, liquidity_bucket FROM panel "
                   "ORDER BY symbol, date").fetchall()

    shuffled = list(rows)
    random.Random(11).shuffle(shuffled)
    other = store(tmp_path / "shuffled.db")
    _build(other, shuffled)
    b = other.execute("SELECT symbol, date, liquidity_bucket FROM panel "
                      "ORDER BY symbol, date").fetchall()
    other.close()

    assert [tuple(r) for r in a] == [tuple(r) for r in b]


def test_fewer_than_five_eligible_names_gives_null_buckets_not_one_to_four(db, monkeypatch):
    """Assigning 1..4 would file a mid-cap under "bucket 1 = most illiquid".

    Thins the ELIGIBLE set (the PIT roster), not the raw feed — a thin feed
    would trip the phantom-session floor first and never reach the quintiles.
    """
    four = frozenset(SYMBOLS[:4])
    monkeypatch.setattr(universe, "members_on", lru_cache(maxsize=1)(lambda d: four))
    report = build_panel(SESSIONS[-2], SESSIONS[-1], conn=db,
                         turso=_price_db(_clean_rows()))
    buckets = [r["liquidity_bucket"] for r in
               db.execute("SELECT liquidity_bucket FROM panel "
                          "WHERE in_universe = 1").fetchall()]
    assert set(buckets) == {None}, "no partial bucketing on a thin cross-section"
    assert SESSIONS[-1].isoformat() in report.thin_dates


# ── Persistence invariants (gate G4) ──────────────────────────────────────────

def test_in_universe_is_stored_as_an_integer_not_a_numpy_blob(db):
    """np.int64 binds to sqlite3 as an 8-byte BLOB.

    Nothing errors; `WHERE in_universe = 1` just returns zero rows forever.
    This test is the only thing standing between that bug and production.
    """
    _build(db, _clean_rows())
    types = {r[0] for r in db.execute(
        "SELECT DISTINCT typeof(in_universe) FROM panel").fetchall()}
    assert types == {"integer"}
    assert db.execute(
        "SELECT COUNT(*) c FROM panel WHERE in_universe = 1").fetchone()["c"] > 0

    for col in ("prev_close", "open", "close", "init_ret", "drift_ret", "adv_20d"):
        bad = db.execute(
            f"SELECT DISTINCT typeof({col}) t FROM panel WHERE {col} IS NOT NULL"
        ).fetchall()
        assert {r["t"] for r in bad} <= {"real", "integer"}, f"{col} bound as a blob"


def test_no_nan_is_ever_stored(db):
    """NaN stored as a value matches no SQL comparison, including `IS NULL`."""
    rows = _clean_rows()
    rows = [r for r in rows if not (r[0] == "SYM01" and r[1] == SESSIONS[-1])]
    rows.append(("SYM01", SESSIONS[-1], 0.0, 110.0, 0.0, 105.0, 1_000.0))
    _build(db, rows)
    for col in ("init_ret", "drift_ret", "adv_20d", "prev_close"):
        assert db.execute(
            f"SELECT COUNT(*) c FROM panel WHERE {col} != {col}").fetchone()["c"] == 0


def test_shortable_survives_a_rebuild(db):
    """ON CONFLICT DO UPDATE, never INSERT OR REPLACE.

    REPLACE is a DELETE+INSERT in SQLite and would reset `shortable` to NULL on
    every rebuild, silently disarming the short-leg filter — and the short leg
    is where the paper finds most of its alpha.
    """
    rows = _clean_rows()
    _build(db, rows)
    db.execute("UPDATE panel SET shortable = 0 WHERE symbol = 'SYM01'")
    db.commit()

    _build(db, rows)
    still = db.execute(
        "SELECT COUNT(*) c FROM panel WHERE symbol='SYM01' AND shortable = 0"
    ).fetchone()["c"]
    assert still == 2, "a separate job owns `shortable`; the panel must not clear it"


def test_rebuild_is_idempotent(db):
    rows = _clean_rows()
    first = _build(db, rows)
    before = db.execute("SELECT COUNT(*) c FROM panel").fetchone()["c"]
    snapshot = [tuple(r) for r in db.execute(
        "SELECT * FROM panel ORDER BY symbol, date").fetchall()]

    second = _build(db, rows)
    after = db.execute("SELECT COUNT(*) c FROM panel").fetchone()["c"]
    assert before == after, "overlapping rebuilds must not duplicate primary keys"
    assert snapshot == [tuple(r) for r in db.execute(
        "SELECT * FROM panel ORDER BY symbol, date").fetchall()]
    assert first.rows_written == second.rows_written


def test_overlapping_windows_do_not_duplicate_rows(db):
    rows = _clean_rows()
    build_panel(SESSIONS[-4], SESSIONS[-2], conn=db, turso=_price_db(rows))
    build_panel(SESSIONS[-3], SESSIONS[-1], conn=db, turso=_price_db(rows))
    total = db.execute("SELECT COUNT(*) c FROM panel").fetchone()["c"]
    distinct = db.execute(
        "SELECT COUNT(*) c FROM (SELECT DISTINCT symbol, date FROM panel)"
    ).fetchone()["c"]
    assert total == distinct == len(SYMBOLS) * 4


# ── Conservation and guards ───────────────────────────────────────────────────

def test_conservation_holds_with_one_of_every_drop_reason(db):
    """Every fetched row is written, held as warm-up, or dropped for one reason."""
    rows = _clean_rows()
    target = SESSIONS[-1]
    rows.append(_bar("SYM02", date(2026, 3, 21), close=999.0))        # phantom
    rows.append(_bar("SYM03", target, close=777.0))                    # duplicate
    rows = [r for r in rows if not (r[0] == "SYM04" and r[1] == target)]
    rows.append(("SYM04", target, None, None, None, None, 1_000.0))    # no price
    rows = [r for r in rows if not (r[0] == "SYM05" and r[1] == target)]
    rows.append(("SYM05", target, 120.0, 120.0, 120.0, 120.0, 1_000.0))  # flat

    report = _build(db, rows)
    assert report.dropped["phantom_session"] == 1
    assert report.dropped["duplicate_bar"] == 1
    assert report.dropped["no_usable_price"] == 1
    assert report.dropped["flat_bar"] == 1
    assert (report.rows_written + report.rows_warmup
            + sum(report.dropped.values())) == report.rows_fetched


def test_a_session_with_zero_index_members_raises(db, monkeypatch):
    """An all-zero in_universe panel reads as a genuine null result.

    That is the worst possible outcome — it looks like an answer. members_on
    returns an empty set rather than raising when the roster does not cover a
    date, so the panel has to fail loudly itself.
    """
    monkeypatch.setattr(universe, "members_on",
                        lru_cache(maxsize=1)(lambda d: frozenset()))
    with pytest.raises(ValueError, match="zero NIFTY500 members"):
        _build(db, _clean_rows())


def test_dry_run_computes_everything_and_writes_nothing(db):
    """The intended way to inspect the contaminated pre-2025 history."""
    report = _build(db, _clean_rows(), dry_run=True)
    assert report.dry_run is True
    assert report.rows_written == len(SYMBOLS) * 2
    assert db.execute("SELECT COUNT(*) c FROM panel").fetchone()["c"] == 0
    assert db.execute(
        "SELECT COUNT(*) c FROM ingest_runs WHERE source='panel'").fetchone()["c"] == 0


def test_a_successful_build_is_logged_to_ingest_runs(db):
    """A silently-dead builder is the failure mode the run log exists to expose."""
    _build(db, _clean_rows())
    run = db.execute(
        "SELECT status, rows_fetched, rows_inserted FROM ingest_runs "
        "WHERE source='panel' ORDER BY id DESC LIMIT 1").fetchone()
    assert run["status"] == "ok"
    assert run["rows_inserted"] == len(SYMBOLS) * 2


def test_start_after_end_is_rejected(db):
    with pytest.raises(ValueError, match="after end"):
        _build(db, _clean_rows(), start=SESSIONS[-1], end=SESSIONS[-2])


# ── load_panel ────────────────────────────────────────────────────────────────

def test_load_panel_filters_to_the_universe_by_default(db):
    """The panel stores all ever-members by design — filter at query time."""
    rows = _clean_rows()
    rows += [_bar("OUTSIDER", d, close=50.0, volume=500.0) for d in SESSIONS]
    _build(db, rows)

    assert db.execute(
        "SELECT COUNT(*) c FROM panel WHERE symbol='OUTSIDER'").fetchone()["c"] == 2, \
        "non-members are still stored — the panel filters at query time"

    frame = load_panel(SESSIONS[-2], SESSIONS[-1], conn=db)
    assert "OUTSIDER" not in set(frame["symbol"])
    everything = load_panel(SESSIONS[-2], SESSIONS[-1], conn=db, in_universe_only=False)
    assert "OUTSIDER" in set(everything["symbol"])
    assert everything["date"].iloc[0] == SESSIONS[-2]


# ── Session helpers ───────────────────────────────────────────────────────────

def test_trading_sessions_excludes_phantom_dates():
    rows = _clean_rows()
    rows.append(_bar("SYM01", date(2026, 3, 21), close=999.0))
    got = panel.trading_sessions(SESSIONS[0], SESSIONS[-1], turso=_price_db(rows))
    assert got == SESSIONS
    assert date(2026, 3, 21) not in got


def test_attribution_dates_matches_the_single_row_function():
    stamps = [
        datetime(2026, 3, 5, 18, 30),   # after close → next session
        datetime(2026, 3, 5, 8, 30),    # before cutoff → same session
        datetime(2026, 3, 6, 20, 0),    # Friday evening → Monday
    ]
    bulk = panel.attribution_dates(stamps, SESSIONS)
    one_at_a_time = [panel.attribution_date(s, SESSIONS) for s in stamps]
    assert bulk == one_at_a_time
    assert bulk == [date(2026, 3, 6), date(2026, 3, 5), date(2026, 3, 9)]


def test_attribution_dates_raises_past_the_calendar():
    with pytest.raises(ValueError, match="beyond the last known trading day"):
        panel.attribution_dates([datetime(2027, 1, 1, 10, 0)], SESSIONS)
