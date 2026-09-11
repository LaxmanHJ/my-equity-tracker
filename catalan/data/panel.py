"""
Phase 2 — the returns panel, and the session classifier the whole study hinges on.

Two return legs, per the India-adapted definitions in config.py:

  init_ret  = close(t-1) → open(t)   the market's immediate reaction. NOT tradable.
  drift_ret = open(t)    → close(t)  the tradable leg. This is the study.

``build_panel`` reads Turso ``price_history`` read-only, computes both legs on a
**global session grid**, assigns within-date liquidity quintiles by 20-day rupee
ADV, and upserts into the ``panel`` table. Every measurement rule it applies is
frozen in ``config.py`` and declared in ``PREREGISTRATION.md`` — read the
"Panel construction — declared consequences" section there before changing any
threshold in this file.

**It returns a report, not a frame.** Downstream consumers read the *table* via
``load_panel`` so that nobody writes their own panel SQL and quietly disagrees
about which rows are eligible. The report exists so that a build's drops are
visible: silent row loss is this study's stated main failure mode, which is also
why the function ends in an arithmetic conservation check.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from catalan.config import (
    ADV_WINDOW_DAYS,
    HISTORY_START,
    LIQUIDITY_BUCKETS,
    MARKET_CLOSE,
    NEWS_CUTOFF,
    PANEL_CA_GAP_LIMIT,
    PANEL_CADENCE_MAX_MEDIAN_GAP,
    PANEL_CADENCE_MIN_PERIODS,
    PANEL_CADENCE_WINDOW,
    PANEL_MIN_SESSION_BREADTH_ABS,
    PANEL_MIN_SESSION_BREADTH_REL,
    PANEL_WARMUP_SESSIONS,
)

logger = logging.getLogger(__name__)

# Announcement → which session it can first be traded in.
OVERNIGHT = "overnight"   # tradable at the open of the attributed session
INTRADAY = "intraday"     # landed mid-session; the paper's separate (blocked) arm

SOURCE = "panel"

# Columns read from Turso. `high` and `low` are fetched but never stored: they
# exist solely for the flat-bar guard, which needs all four OHLC fields to
# recognise a RapidAPI-era row that copied one price into every field.
_PRICE_COLS = ("symbol", "date", "open", "high", "low", "close", "volume")

# Columns written to `panel`. `shortable` is deliberately absent from BOTH the
# insert and the update list — see _upsert.
_PANEL_COLS = ("symbol", "date", "prev_close", "open", "close", "init_ret",
               "drift_ret", "in_universe", "adv_20d", "liquidity_bucket")

# Row-level drop reasons, in the order they are applied. FIRST MATCH WINS and
# every dropped row is counted exactly once, which is what makes the
# conservation check at the end of build_panel meaningful.
DROP_REASONS = ("phantom_session", "duplicate_bar", "no_usable_price",
                "flat_bar", "weekly_cadence")

# Field-level suppressions. The row is still WRITTEN; one or two of its columns
# are nulled. These do NOT enter the conservation check.
SUPPRESS_REASONS = ("corporate_action", "no_prev_close", "adv_short")


def classify_session(announced_at: datetime) -> str:
    """OVERNIGHT if the news cannot have been traded before the next open.

    Overnight = after MARKET_CLOSE (15:30 IST) on t-1, or before NEWS_CUTOFF
    (09:00 IST) on t. The 09:00 cutoff leaves 15 minutes before the 09:15
    pre-open auction clears, mirroring the buffer the paper leaves before the
    US open. IST has no DST, so there are no ambiguous local times to handle.

    Boundaries: exactly 15:30:00 is still intraday (the close is a 15:00-15:30
    VWAP, so news at the bell is inside the session); exactly 09:00:00 is
    already too late to count as overnight.
    """
    t = announced_at.time()
    if t > MARKET_CLOSE:
        return OVERNIGHT
    if t < NEWS_CUTOFF:
        return OVERNIGHT
    return INTRADAY


def attribution_date(announced_at: datetime,
                     trading_days: Optional[Sequence[date]] = None) -> date:
    """The session whose open→close drift this announcement is attributed to.

    News after the close rolls to the next day; news before 09:00 belongs to the
    same day. Both then roll forward to the next actual trading day, so Friday
    evening and weekend news land on Monday and holidays are skipped.

    ``trading_days`` must be a sorted sequence of session dates (from
    price_history). Omit it only in tests: without it, calendar days are used
    and weekends/holidays are NOT skipped.
    """
    d = announced_at.date()
    if announced_at.time() > MARKET_CLOSE:
        d = d + timedelta(days=1)

    if not trading_days:
        return d

    for session in trading_days:
        if session >= d:
            return session
    raise ValueError(
        f"{d} is beyond the last known trading day ({trading_days[-1]}) — "
        "the panel's calendar needs extending before attributing this news."
    )


def attribution_dates(stamps: Iterable[datetime],
                      trading_days: Sequence[date]) -> List[date]:
    """Bulk ``attribution_date``. Same rule, O(log n) per stamp instead of O(n).

    ``attribution_date`` scans the session list linearly, which is fine for one
    call and quadratic over a 500k-row corpus. This is an additive sibling, not
    a replacement: the single-row function keeps its exact semantics and its
    pinned ValueError text.
    """
    days = list(trading_days)
    if not days:
        raise ValueError("attribution_dates needs a non-empty trading-day list")

    out: List[date] = []
    for stamp in stamps:
        d = stamp.date()
        if stamp.time() > MARKET_CLOSE:
            d = d + timedelta(days=1)
        i = bisect_left(days, d)
        if i >= len(days):
            raise ValueError(
                f"{d} is beyond the last known trading day ({days[-1]}) — "
                "the panel's calendar needs extending before attributing this news."
            )
        out.append(days[i])
    return out


# ── Report ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PanelReport:
    """What one build did, in enough detail to argue with.

    ``dropped`` is ROW-level and enters the conservation check; ``suppressed``
    is FIELD-level (the row is still written with one or two columns nulled)
    and deliberately does not.
    """
    start: str
    end: str
    sessions: int
    sessions_rejected: List[Tuple[str, int]]
    symbols: int
    rows_fetched: int
    rows_warmup: int
    rows_written: int
    rows_in_universe: int
    dropped: Dict[str, int]
    suppressed: Dict[str, int]
    ca_suspects: List[Dict[str, Any]] = field(default_factory=list)
    thin_dates: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> Dict[str, Any]:
        """JSON-dumpable, matching what collect_daily and archive return."""
        return asdict(self)


# ── Small helpers ─────────────────────────────────────────────────────────────

def _as_date(d: Union[str, date, datetime]) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def _f(v: Any) -> Optional[float]:
    """Coerce to a plain Python float, or None.

    Every REAL column goes through this. numpy scalars bind to sqlite3 as
    8-byte BLOBs rather than numbers, and NaN/inf must become NULL rather than
    being stored as a value no SQL comparison will ever match.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _i(v: Any) -> Optional[int]:
    """Coerce to a plain Python int, or None.

    THE SILENT-FAILURE TRAP: np.int64 binds to sqlite3 as an 8-byte BLOB, so a
    panel written without this passes every insert and then returns zero rows
    for `WHERE in_universe = 1` — with no error anywhere. tests/test_panel.py
    pins `SELECT DISTINCT typeof(in_universe)` to 'integer' for this reason.
    """
    f = _f(v)
    return None if f is None else int(f)


def _month_chunks(frm: date, to: date) -> List[Tuple[date, date]]:
    """Calendar-month windows over [frm, to].

    Turso has no server-side cursor and a fixed 30s HTTP timeout
    (turso_client.py:126-131). A quarter of price_history (~52k rows) times
    out; a month (~17k) does not.
    """
    out: List[Tuple[date, date]] = []
    cur = frm.replace(day=1)
    while cur <= to:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((max(cur, frm), min(nxt - timedelta(days=1), to)))
        cur = nxt
    return out


def _rows_to_frame(rows: Iterable[Any], columns: Sequence[str]):
    """Build a DataFrame from a DB-API result.

    Deliberately not ``pd.read_sql_query``: it inspects the connection for a
    DB-API2 or SQLAlchemy interface, and ReadOnlyConnection is a proxy that
    satisfies neither cleanly. fetchall() + an explicit column list is the
    boring path that works against Turso, sqlite3 and the test double alike.
    """
    import pandas as pd

    return pd.DataFrame([tuple(r) for r in rows], columns=list(columns))


def _nearest_action_ratio(prev_close: float, open_: float) -> Optional[str]:
    """Report-only: does this suspect's price ratio look like a clean split/bonus?

    A 1:2 split leaves prev_close/open ≈ 2/1 and a 5:1 bonus ≈ 1/6. Showing the
    nearest small-denominator fraction lets a reader see at a glance how many
    CA suspects are textbook corporate actions rather than genuine 20%+ moves.

    This is a DIAGNOSTIC, never part of the rule. As a rule, "null it only if
    the ratio is close to a nice fraction" would be a fitted threshold on the
    same data the study is measuring.
    """
    if not prev_close or not open_ or open_ <= 0 or prev_close <= 0:
        return None
    try:
        fr = Fraction(prev_close / open_).limit_denominator(20)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return f"{fr.numerator}:{fr.denominator}"


# ── Session grid ──────────────────────────────────────────────────────────────

def _session_grid(turso, frm: date, to: date) -> Tuple[List[date], List[Tuple[str, int]]]:
    """Real trading sessions in [frm, to], plus the dates rejected as phantoms.

    A date in price_history carrying a handful of bars is a partial write, not a
    trading day. Admitting one is not cosmetic: it shifts EVERY symbol's
    prev_close back by a session, and it becomes a date attribution_date() can
    return — so an announcement would be attributed to a day the market did not
    trade.

    The floor is relative with an absolute backstop. A fixed constant tuned for
    2026's ~849-symbol coverage would reject most of 2019, when coverage was
    genuinely thinner; a purely relative floor would accept everything on a
    corpus that was uniformly thin.
    """
    cur = turso.execute(
        "SELECT date, COUNT(*) AS n FROM price_history "
        "WHERE date >= ? AND date <= ? GROUP BY date ORDER BY date",
        [frm.isoformat(), to.isoformat()],
    )
    rows = [(str(r[0])[:10], int(r[1])) for r in cur.fetchall()]
    if not rows:
        raise ValueError(
            f"price_history has no rows between {frm} and {to} — the panel "
            "cannot build a session grid. Check the Turso connection and that "
            "the price backfill covers this window."
        )

    breadths = [n for _, n in rows]
    floor = max(PANEL_MIN_SESSION_BREADTH_ABS,
                PANEL_MIN_SESSION_BREADTH_REL * median(breadths))

    sessions = [_as_date(d) for d, n in rows if n >= floor]
    rejected = [(d, n) for d, n in rows if n < floor]

    if not sessions:
        raise ValueError(
            f"every date between {frm} and {to} fell below the phantom-session "
            f"floor of {floor:.0f} bars. price_history looks broken, not thin."
        )
    return sessions, rejected


def trading_sessions(start: Union[str, date], end: Union[str, date],
                     *, turso=None) -> List[date]:
    """Real NSE sessions in [start, end], derived from price_history breadth.

    There is no holiday calendar anywhere in this repo, so the session list is
    inferred from the data. Exposed because ``attribution_date`` needs exactly
    this list and every caller was otherwise going to invent its own.
    """
    own = turso is None
    if own:
        from catalan.data.store import turso_ro
        turso = turso_ro()
    try:
        sessions, _ = _session_grid(turso, _as_date(start), _as_date(end))
        return sessions
    finally:
        if own:
            turso.close()


def _fetch_prices(turso, frm: date, to: date):
    """All price_history bars in [frm, to], month by month.

    Deliberately NOT filtered to the NIFTY500 ever-members list in SQL. Two
    reasons: ever_members() is 837 of ~849 symbols so the filter saves almost
    nothing, and an `IN (…×837)` clause would also have to be applied to the
    breadth query — where it would corrupt the phantom-session check, which must
    measure the raw feed.
    """
    import pandas as pd

    frames = []
    for c_from, c_to in _month_chunks(frm, to):
        cur = turso.execute(
            "SELECT symbol, date, open, high, low, close, volume FROM price_history "
            "WHERE date >= ? AND date <= ? ORDER BY symbol, date",
            [c_from.isoformat(), c_to.isoformat()],
        )
        rows = cur.fetchall()
        logger.debug("price_history %s→%s: %d rows", c_from, c_to, len(rows))
        if rows:
            frames.append(_rows_to_frame(rows, _PRICE_COLS))

    if not frames:
        return pd.DataFrame(columns=list(_PRICE_COLS))
    return pd.concat(frames, ignore_index=True)


def _quintiles(frame):
    """Within-date liquidity quintiles over in-universe rows with a full ADV.

    Orientation is a declared study parameter: rank is ASCENDING on ADV, so
    **bucket 1 = least liquid** and bucket 5 = most liquid. PREREGISTRATION
    reads its negative-result verdict off "the most illiquid bucket"; which
    integer that is cannot be an implementation detail.

    Not ``pd.qcut``: qcut raises on duplicate bin edges, and real ADV ties among
    illiquid names produce exactly that. ``duplicates="drop"`` would instead
    silently return fewer than 5 buckets, which is worse.

    Fewer than LIQUIDITY_BUCKETS eligible names on a date → that date gets NULL
    buckets, never 1..k for k<5. Assigning 1..4 would file a mid-cap under
    "bucket 1 = most illiquid", and bucket 1 is the cell that decides a negative
    result.
    """
    import numpy as np
    import pandas as pd

    out = pd.Series(np.nan, index=frame.index, dtype=float)
    eligible = frame[(frame["in_universe"] == 1) & frame["adv_20d"].notna()]

    thin: List[str] = []
    for d, grp in eligible.groupby("date", sort=True):
        if len(grp) < LIQUIDITY_BUCKETS:
            thin.append(d.isoformat() if hasattr(d, "isoformat") else str(d))
            continue
        # Sort by symbol first so that rank(method="first") breaks ADV ties
        # deterministically rather than by whatever order Turso returned rows in.
        g = grp.sort_values("symbol", kind="mergesort")
        ranks = g["adv_20d"].rank(method="first", ascending=True)
        buckets = np.ceil(ranks * LIQUIDITY_BUCKETS / len(g))
        out.loc[g.index] = np.clip(buckets, 1, LIQUIDITY_BUCKETS)

    return out, thin


# ── The build ─────────────────────────────────────────────────────────────────

def build_panel(start: Union[str, date], end: Union[str, date], *,
                conn: Optional[sqlite3.Connection] = None,
                turso=None,
                dry_run: bool = False) -> PanelReport:
    """Build the returns panel over the PIT universe for [start, end].

    Reads Turso ``price_history`` read-only, computes ``init_ret`` and
    ``drift_ret`` on a global session grid, assigns within-date liquidity
    quintiles by 20-day rupee ADV, flags PIT NIFTY500 membership, and upserts
    into ``panel``.

    ``shortable`` is NOT set here — open item 4, the T2T/ASM/GSM source is
    unsettled, and until it is the short leg cannot be claimed executable. The
    column is left for a separate job to own, which is why this function uses
    ON CONFLICT DO UPDATE with an explicit column list rather than
    INSERT OR REPLACE.

    ``dry_run=True`` computes everything and writes nothing — the intended way
    to inspect drop counts on the contaminated pre-2025 history before deciding
    whether that arm is usable at all.
    """
    import numpy as np
    import pandas as pd

    from catalan.data import universe
    from catalan.data.store import store, turso_ro

    start_d, end_d = _as_date(start), _as_date(end)
    if start_d > end_d:
        raise ValueError(f"start {start_d} is after end {end_d}")

    own_turso = turso is None
    own_conn = conn is None
    turso = turso_ro() if own_turso else turso
    conn = store() if own_conn else conn

    warnings: List[str] = []
    started = datetime.now().isoformat(timespec="seconds")
    run_id = None
    if not dry_run:
        run_id = conn.execute(
            "INSERT INTO ingest_runs (source, window_from, window_to, status, started_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (SOURCE, start_d.isoformat(), end_d.isoformat(), started),
        ).lastrowid
        conn.commit()

    try:
        # ── 1. Session grid ───────────────────────────────────────────────────
        # Built from HISTORY_START so the median breadth is measured over the
        # whole study era rather than over whatever short window was requested —
        # a two-week build must not get its own, differently-calibrated floor.
        all_sessions, rejected = _session_grid(turso, _as_date(HISTORY_START), end_d)

        lo = bisect_left(all_sessions, start_d)
        hi = bisect_right(all_sessions, end_d)
        in_window = all_sessions[lo:hi]
        if not in_window:
            raise ValueError(
                f"no trading sessions between {start_d} and {end_d}. Either the "
                "window is entirely holidays/weekend, or price_history has not "
                "been backfilled that far."
            )

        # ── 2. Warm-up, counted in SESSIONS and never calendar days ───────────
        # A calendar-day guess under-fetches across holiday clusters (Diwali,
        # year-end) and ADV then silently returns NULL for the first week of
        # every build — a hole nobody notices because NULL ADV just means NULL
        # bucket, and NULL buckets look like ordinary missing data.
        fetch_lo = max(0, lo - PANEL_WARMUP_SESSIONS)
        grid = all_sessions[fetch_lo:hi]
        fetch_from = grid[0]
        if lo - PANEL_WARMUP_SESSIONS < 0:
            warnings.append(
                f"only {lo} sessions of warm-up available before {start_d} "
                f"(wanted {PANEL_WARMUP_SESSIONS}); adv_20d will be NULL for the "
                "earliest rows and their liquidity buckets with it."
            )

        # ── 3. Universe guards ────────────────────────────────────────────────
        # members_on returns an EMPTY SET rather than raising when the roster
        # does not cover a date. An all-zero in_universe panel then reads as a
        # genuine null result — the single worst outcome this study can produce,
        # because it looks like an answer. Fail loudly instead.
        empty_sessions = [d for d in in_window if not universe.members_on(d)]
        if empty_sessions:
            raise ValueError(
                f"{len(empty_sessions)} in-window session(s) have zero NIFTY500 "
                f"members (first: {empty_sessions[0]}). The PIT roster does not "
                "cover this window. Refusing to build a panel whose in_universe "
                "column would be uniformly zero — that reads as a null result "
                "rather than as the missing-data error it actually is."
            )

        newest = universe.roster_last_effective_from()
        if newest is not None and (end_d - newest).days > 90:
            warnings.append(
                f"roster's newest effective_from is {newest}, {(end_d - newest).days} "
                f"days before the requested end {end_d}. in_universe past that date "
                "is the last known roster carried forward, not point-in-time truth."
            )

        # ── 4. Fetch ──────────────────────────────────────────────────────────
        raw = _fetch_prices(turso, fetch_from, end_d)
        rows_fetched = len(raw)
        if rows_fetched == 0:
            raise ValueError(
                f"price_history returned no bars between {fetch_from} and {end_d}, "
                "even though the session grid found trading days there."
            )

        raw["date"] = raw["date"].map(lambda v: _as_date(v))
        raw["symbol"] = raw["symbol"].astype(str)
        for col in ("open", "high", "low", "close", "volume"):
            raw[col] = pd.to_numeric(raw[col], errors="coerce")

        # Deterministic order. Everything downstream — duplicate detection,
        # positional rolling, tie-breaking in the quintiles — depends on the row
        # order being a property of the data rather than of Turso's response.
        raw = raw.sort_values(["symbol", "date"], kind="mergesort").reset_index(drop=True)

        # ── 5. Row-level drops, fixed order, first match wins ─────────────────
        reason = pd.Series([None] * len(raw), index=raw.index, dtype=object)

        def mark(mask, label: str) -> None:
            m = mask.fillna(False).astype(bool) & reason.isna()
            reason[m] = label

        grid_set = set(grid)
        mark(~raw["date"].isin(grid_set), "phantom_session")
        mark(raw.duplicated(subset=["symbol", "date"], keep="first"), "duplicate_bar")

        def usable(s):
            return s.notna() & np.isfinite(s.fillna(0.0)) & (s > 0)

        ok_open, ok_close = usable(raw["open"]), usable(raw["close"])
        mark(~(ok_open | ok_close), "no_usable_price")

        # Flat bar: the RapidAPI era copied one price into all four OHLC fields
        # (av_weekly_backfill.py:9-12). That gives drift_ret an EXACT 0.0 — a
        # fake zero in the tradable leg, which is worse than noise because it
        # shrinks the estimated mean toward zero instead of just widening it.
        # Dropped entirely, unlike the corporate-action guard: there is no
        # salvageable leg in a bar with no intraday range.
        flat = (raw["open"] == raw["high"]) & (raw["high"] == raw["low"]) \
            & (raw["low"] == raw["close"]) & raw["open"].notna()
        mark(flat, "flat_bar")

        # Weekly cadence: pre-2025-03-17 rows were weekly AGGREGATED OHLC
        # (av_weekly_backfill.py:20-22), so `open` is the week's open and
        # `close` the week's close — BOTH legs are weekly returns wearing a
        # session label. Also dropped entirely, for the same reason.
        pos_of = {d: i for i, d in enumerate(grid)}
        alive = raw[reason.isna()]
        if len(alive):
            pos = alive["date"].map(pos_of)
            gaps = pos.groupby(alive["symbol"]).diff()
            med = gaps.groupby(alive["symbol"]).transform(
                lambda s: s.rolling(PANEL_CADENCE_WINDOW,
                                    min_periods=PANEL_CADENCE_MIN_PERIODS).median()
            )
            # NaN median (a symbol with too few bars to judge) → keep. A newly
            # listed name must not be mistaken for a weekly-cadence artefact.
            bad = (med > PANEL_CADENCE_MAX_MEDIAN_GAP).reindex(raw.index, fill_value=False)
            mark(bad, "weekly_cadence")

        dropped = {r: int((reason == r).sum()) for r in DROP_REASONS}
        kept = raw[reason.isna()].copy()

        suppressed = {r: 0 for r in SUPPRESS_REASONS}
        ca_suspects: List[Dict[str, Any]] = []
        thin_dates: List[str] = []

        if len(kept):
            # ── 6/7. ADV, positional on the symbol's own bars ──────────────────
            # AFTER the cadence mask, so the 20 bars are 20 daily bars and not
            # 20 weeks. Positional rather than on the grid, because rolling on
            # the grid would null the ADV of any stock that was halted once in
            # 20 sessions. min_periods is the FULL window deliberately: a 6-bar
            # and a 20-bar ADV are not comparable within one cross-section, and
            # partials would mis-bucket freshly listed names — the very
            # population the paper's alpha concentrates in.
            turnover = kept["close"] * kept["volume"]
            kept["adv_20d"] = turnover.groupby(kept["symbol"]).transform(
                lambda s: s.rolling(ADV_WINDOW_DAYS, min_periods=ADV_WINDOW_DAYS).mean()
            )

            # ── prev_close on the GLOBAL grid ─────────────────────────────────
            # A per-symbol positional lag is WRONG — it would silently reach
            # across a halt. prev_close is defined against the session grid:
            # look up the same symbol's close on the immediately preceding
            # GRID session, whatever that symbol did in between.
            #
            # DECLARED RULE: the immediately preceding session's close, or NULL.
            # NO FORWARD-FILL. If a stock was halted on t-1 then
            # open(t)/close(t-2)-1 is a two-day return wearing an overnight
            # label. Losing an observation is cheap; a mislabelled horizon in
            # the C0 leg is not. The left-merge below yields NaN exactly when
            # the symbol had no bar on the previous grid session, which is the
            # rule stated directly rather than emerging from NaN propagation.
            #
            # Implemented as a self-merge rather than pivot -> shift -> stack.
            # That round-trip materialised a full sessions x symbols matrix
            # (1,880 x 840 on the historical build) to read back the ~0.1% of
            # cells that exist, and it depended on `stack(dropna=True)` — an
            # argument pandas 3 rejects outright. Simply DELETING that
            # argument would not have been a drop-in fix either: the newer
            # stack implementation keeps pre-existing NaN, so the stacked frame
            # would have grown from "cells that exist" to the entire matrix.
            # Both problems disappear when there is no pivot at all.
            prev_session = {d: grid[i - 1] for i, d in enumerate(grid) if i > 0}
            prev_src = kept[["date", "symbol", "close"]].rename(
                columns={"date": "_prev_date", "close": "prev_close"})
            kept["_prev_date"] = kept["date"].map(prev_session)
            # (symbol, date) is already unique — duplicate_bar rows were dropped
            # above — so this merge cannot multiply rows.
            kept = kept.merge(prev_src, on=["_prev_date", "symbol"], how="left")
            kept = kept.drop(columns=["_prev_date"])

            # ── 8. Return legs. Gate BEFORE dividing ──────────────────────────
            # open missing or zero → BOTH legs NULL, never 0.0 and never inf.
            # Prices are still written so a later phase can audit the row.
            o, c, pc = kept["open"], kept["close"], kept["prev_close"]
            g_o, g_c, g_pc = usable(o), usable(c), usable(pc)

            kept["init_ret"] = np.where(g_o & g_pc, o / pc - 1.0, np.nan)
            kept["drift_ret"] = np.where(g_o & g_c, c / o - 1.0, np.nan)

            # ── Corporate-action guard ────────────────────────────────────────
            # No corporate-actions table exists in this repo and OHLC is
            # unadjusted (backfill_bhavcopy.py:33), so a split reads as a fake
            # overnight return.
            #
            # Null init_ret AND prev_close; RETAIN open, close and drift_ret
            # unchanged. That retention is a fact, not a compromise: on an
            # ex-date all four prices are already quoted in the post-action
            # basis, so only the comparison to the previous close crosses the
            # basis change. drift_ret = close(t)/open(t)-1 is untouched.
            #
            # THE GUARD THEREFORE COSTS THE C2 SHIP GATE NOTHING and protects
            # only the untradable C0 leg. Any future refactor that "simplifies"
            # this into dropping the row would delete drift observations on
            # exactly the highest-news-intensity days.
            suspect = kept["init_ret"].abs() > PANEL_CA_GAP_LIMIT
            # Stash the pre-suppression values: the report has to show WHAT was
            # suppressed, and the columns are about to be nulled. Reported after
            # the window slice, so the counts describe rows that were actually
            # written rather than warm-up rows nobody will ever see.
            kept["_no_pc"] = ~g_pc          # BEFORE the guard nulls more of them
            kept["_ca_suspect"] = suspect
            kept["_ca_prev_close"] = kept["prev_close"]
            kept["_ca_init_ret"] = kept["init_ret"]
            kept.loc[suspect, "init_ret"] = np.nan
            kept.loc[suspect, "prev_close"] = np.nan

            # ── 9. PIT membership, memoised per session ───────────────────────
            member_sets = {d: universe.members_on(d) for d in kept["date"].unique()}
            kept["in_universe"] = [
                1 if s in member_sets[d] else 0
                for s, d in zip(kept["symbol"], kept["date"])
            ]

            # ── 10. Quintiles ─────────────────────────────────────────────────
            kept["liquidity_bucket"], thin_dates = _quintiles(kept)

            # ── 11. Slice to the requested window ─────────────────────────────
            # Warm-up rows are counted as rows_warmup, NOT as dropped. Counting
            # them as drops would fail the conservation check on every single
            # run, and a check that always fails gets deleted.
            is_window = kept["date"] >= start_d
            rows_warmup = int((~is_window).sum())
            kept = kept[is_window].copy()

            # Field-level suppressions, counted on the written window only.
            # `no_prev_close` over the warm-up would always include the first
            # grid session, where no symbol can have a predecessor — a constant
            # that tells the reader nothing about the data they asked for.
            suppressed["adv_short"] = int(kept["adv_20d"].isna().sum())
            # Counted from the gate, not from the final column: the corporate-action
            # guard nulls prev_close too, and conflating the two would hide how
            # many rows genuinely had no preceding session.
            suppressed["no_prev_close"] = int(kept["_no_pc"].sum())
            suppressed["corporate_action"] = int(kept["_ca_suspect"].sum())
            for _, r in kept[kept["_ca_suspect"]].iterrows():
                pc, op = _f(r["_ca_prev_close"]), _f(r["open"])
                ca_suspects.append({
                    "symbol": r["symbol"],
                    "date": r["date"].isoformat(),
                    "prev_close": pc,
                    "open": op,
                    "init_ret": _f(r["_ca_init_ret"]),
                    "nearest_ratio": _nearest_action_ratio(pc or 0.0, op or 0.0),
                })
            kept = kept.drop(columns=["_no_pc", "_ca_suspect",
                                      "_ca_prev_close", "_ca_init_ret"])
        else:
            rows_warmup = 0
            kept["adv_20d"] = []
            kept["prev_close"] = []
            kept["init_ret"] = []
            kept["drift_ret"] = []
            kept["in_universe"] = []
            kept["liquidity_bucket"] = []

        rows_written = len(kept)
        rows_in_universe = int((kept["in_universe"] == 1).sum()) if rows_written else 0

        # ── 12. Conservation ──────────────────────────────────────────────────
        # The single most valuable line in this function. Silent row loss is the
        # study's stated main failure mode, and the durable fix is an arithmetic
        # invariant that breaks loudly the first time someone adds a filter
        # without adding a counter for it. A plain `assert` would be stripped
        # under `python -O`, so this raises.
        accounted = rows_written + rows_warmup + sum(dropped.values())
        if accounted != rows_fetched:
            raise RuntimeError(
                f"panel row conservation failed: {rows_fetched} fetched but "
                f"{rows_written} written + {rows_warmup} warm-up + "
                f"{sum(dropped.values())} dropped = {accounted}. A filter was "
                "added without a counter — every fetched row must be written, "
                "held as warm-up, or dropped for exactly one named reason."
            )

        if not dry_run and rows_written:
            _upsert(conn, kept)

        report = PanelReport(
            start=start_d.isoformat(),
            end=end_d.isoformat(),
            sessions=len(in_window),
            sessions_rejected=rejected,
            symbols=int(kept["symbol"].nunique()) if rows_written else 0,
            rows_fetched=rows_fetched,
            rows_warmup=rows_warmup,
            rows_written=rows_written,
            rows_in_universe=rows_in_universe,
            dropped=dropped,
            suppressed=suppressed,
            ca_suspects=ca_suspects,
            thin_dates=thin_dates,
            warnings=warnings,
            dry_run=dry_run,
        )

    except Exception as exc:
        if run_id is not None:
            conn.execute(
                "UPDATE ingest_runs SET status='error', detail=?, finished_at=? WHERE id=?",
                (str(exc)[:500], datetime.now().isoformat(timespec="seconds"), run_id),
            )
            conn.commit()
        if own_turso:
            turso.close()
        if own_conn:
            conn.close()
        raise

    if run_id is not None:
        conn.execute(
            "UPDATE ingest_runs SET status='ok', rows_fetched=?, rows_inserted=?, "
            "detail=?, finished_at=? WHERE id=?",
            (report.rows_fetched, report.rows_written,
             f"dropped={report.dropped}",
             datetime.now().isoformat(timespec="seconds"), run_id),
        )
        conn.commit()

    for w in warnings:
        logger.warning("build_panel: %s", w)
    logger.info(
        "panel %s→%s: %d sessions, %d fetched, %d written (%d in-universe), dropped %s",
        report.start, report.end, report.sessions, report.rows_fetched,
        report.rows_written, report.rows_in_universe, report.dropped,
    )

    if own_turso:
        turso.close()
    if own_conn:
        conn.close()
    return report


def _upsert(conn: sqlite3.Connection, frame) -> None:
    """Batch-write the panel in one transaction.

    ON CONFLICT DO UPDATE with an explicit column list, NEVER INSERT OR REPLACE.
    REPLACE is a DELETE+INSERT in SQLite, so it would reset ``shortable`` to
    NULL on every rebuild and silently disarm the short-leg filter. That is not
    hypothetical — a separate job will own that column once the T2T/ASM/GSM
    source is settled (open item 4). ``shortable`` appears in neither the insert
    list nor the update list here, by design.
    """
    payload = [
        (
            str(r["symbol"]),
            r["date"].isoformat(),
            _f(r["prev_close"]),
            _f(r["open"]),
            _f(r["close"]),
            _f(r["init_ret"]),
            _f(r["drift_ret"]),
            _i(r["in_universe"]) or 0,
            _f(r["adv_20d"]),
            _i(r["liquidity_bucket"]),
        )
        for _, r in frame.iterrows()
    ]

    with conn:
        conn.executemany(
            "INSERT INTO panel (symbol, date, prev_close, open, close, init_ret, "
            "                   drift_ret, in_universe, adv_20d, liquidity_bucket) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol, date) DO UPDATE SET "
            "  prev_close       = excluded.prev_close, "
            "  open             = excluded.open, "
            "  close            = excluded.close, "
            "  init_ret         = excluded.init_ret, "
            "  drift_ret        = excluded.drift_ret, "
            "  in_universe      = excluded.in_universe, "
            "  adv_20d          = excluded.adv_20d, "
            "  liquidity_bucket = excluded.liquidity_bucket",
            payload,
        )


def load_panel(start: Union[str, date], end: Union[str, date], *,
               in_universe_only: bool = True,
               conn: Optional[sqlite3.Connection] = None):
    """Read the panel back as a DataFrame. THE consumer entry point.

    Everything downstream (analysis/portfolio.py, analysis/costs.py) reads the
    table through here rather than writing its own SQL, so that "which rows are
    eligible" is decided in exactly one place. ``in_universe_only`` defaults to
    True because the panel stores all ever-members by design — filtering at
    query time, never at ingest (schema.sql:12-14).
    """
    import pandas as pd

    from catalan.data.store import store

    own = conn is None
    conn = store() if own else conn
    try:
        sql = ("SELECT symbol, date, prev_close, open, close, init_ret, drift_ret, "
               "in_universe, adv_20d, liquidity_bucket, shortable FROM panel "
               "WHERE date >= ? AND date <= ?")
        params = [_as_date(start).isoformat(), _as_date(end).isoformat()]
        if in_universe_only:
            sql += " AND in_universe = 1"
        sql += " ORDER BY date, symbol"

        rows = conn.execute(sql, params).fetchall()
        frame = _rows_to_frame(rows, (
            "symbol", "date", "prev_close", "open", "close", "init_ret",
            "drift_ret", "in_universe", "adv_20d", "liquidity_bucket", "shortable"))
        if len(frame):
            frame["date"] = pd.to_datetime(frame["date"]).dt.date
        return frame
    finally:
        if own:
            conn.close()
