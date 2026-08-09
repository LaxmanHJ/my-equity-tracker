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
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from functools import lru_cache
from typing import Optional, Union

from catalan.config import UNIVERSE_INDEX
from catalan.data.store import turso_ro

logger = logging.getLogger(__name__)

DateLike = Union[str, date, datetime]


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
