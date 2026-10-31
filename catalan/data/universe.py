"""
Point-in-time NIFTY500 membership — CATALAN's single seam onto the main repo's
survivorship-bias fix.

Reuses ``quant_engine.data.membership.MembershipRegistry`` rather than
re-approximating it: 837 ever-members, 940 spells, 2018-01-01 → 2026-07-16,
already validated with 21 tests. Filtering announcements against *today's*
roster would silently drop exactly the firms that left the index because they
failed — the bias this study cannot afford, since the paper's alpha
concentrates in small caps.

The registry is built once per process and cached. ``members_on()`` is O(n)
over all symbols per call (quant_engine/data/membership.py:273), so building it
per announcement — or calling it per announcement without memoising the day —
would be pathological on a 500k-row corpus.

**catalan-v2 (Amendment 1) uses a different universe for the counted period:**
``study_universe()``, NSE's own constituent list frozen on 2026-10-18. The
point-in-time registry below remains for the pilot and the historical arm.
"""
from __future__ import annotations

import csv
import hashlib
import logging
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

from catalan.config import (
    CATALAN_ROOT,
    UNIVERSE_EXCLUDED,
    UNIVERSE_FILE,
    UNIVERSE_FILE_SHA256,
    UNIVERSE_INDEX,
    UNIVERSE_SERIES,
)
from catalan.data.store import turso_ro

logger = logging.getLogger(__name__)

DateLike = Union[str, date, datetime]


class UniverseFileChanged(RuntimeError):
    """The frozen universe file no longer matches its declared hash."""


@lru_cache(maxsize=1)
def study_universe() -> frozenset:
    """The catalan-v2 universe: the frozen 2026-10-18 NIFTY500 list.

    EQ-series shares only, minus UNIVERSE_EXCLUDED. No database involved, so
    the daily run can apply it with no Turso access. The file's sha256 is
    checked on every load: an edited list must fail loudly, never quietly
    change what the study measures.
    """
    path = Path(CATALAN_ROOT) / UNIVERSE_FILE
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != UNIVERSE_FILE_SHA256:
        raise UniverseFileChanged(
            f"{path} hashes to {digest}, not the declared {UNIVERSE_FILE_SHA256}. "
            "The study universe is frozen by Amendment 1; restore the file.")
    rows = csv.DictReader(data.decode("utf-8-sig").splitlines())
    return frozenset(
        r["Symbol"].strip() for r in rows
        if r["Series"].strip() in UNIVERSE_SERIES and r["Symbol"].strip() not in UNIVERSE_EXCLUDED)


def _as_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


@lru_cache(maxsize=1)
def _registry():
    """Build the PIT registry once per process from Turso (read-only)."""
    from quant_engine.data.membership import MembershipRegistry

    conn = turso_ro()
    reg = MembershipRegistry.from_turso(conn, index_name=UNIVERSE_INDEX)
    logger.info(
        "PIT registry loaded: %d ever-members of %s",
        len(reg.all_symbols(UNIVERSE_INDEX)), UNIVERSE_INDEX,
    )
    return reg


@lru_cache(maxsize=4096)
def members_on(on: DateLike) -> frozenset:
    """Symbols that were NIFTY500 members on ``on``.

    Memoised per date — the corpus has ~1,900 trading days but ~500k rows, so
    without this the join is 500k full scans of the registry.
    """
    return frozenset(_registry().members_on(_as_date(on), index_name=UNIVERSE_INDEX))


def is_member(symbol: str, on: DateLike) -> bool:
    """Was ``symbol`` in the index on ``on``? O(log n) via the registry's bisect."""
    return _registry().contains(symbol, _as_date(on), index_name=UNIVERSE_INDEX)


def ever_members() -> frozenset:
    """Every symbol that was EVER a member — includes delisted, merged, renamed.

    This is the right universe to query ``price_history`` over; ``members_on``
    is the right filter to apply once you have a date.
    """
    return frozenset(_registry().all_symbols(UNIVERSE_INDEX))


def reset_cache() -> None:
    """Drop cached registry + per-date memo. For tests, and after a roster refresh."""
    members_on.cache_clear()
    _registry.cache_clear()


def roster_last_effective_from() -> Optional[date]:
    """Newest ``effective_from`` in the roster — i.e. how fresh the PIT membership is.

    The panel needs this to warn when it is asked to build sessions that run
    past the roster's last known reconstitution. Past that point ``members_on``
    keeps returning the last known roster rather than failing, so a stale
    registry produces a silently wrong ``in_universe`` column instead of an
    error. A warning is the right severity: the roster being a few weeks behind
    the price data is the normal steady state, not a bug.
    """
    intervals = getattr(_registry(), "_all_intervals", None)
    if not intervals:
        return None
    stamps = [iv.effective_from for iv in intervals
              if iv.index_name == UNIVERSE_INDEX]
    return max(stamps) if stamps else None
