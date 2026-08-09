"""
Phase 2 — the returns panel, and the session classifier the whole study hinges on.

Two return legs, per the India-adapted definitions in config.py:

  init_ret  = close(t-1) → open(t)   the market's immediate reaction. NOT tradable.
  drift_ret = open(t)    → close(t)  the tradable leg. This is the study.

The classifier below is fully implemented and tested; ``build_panel`` is Phase 2
and still to write.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

from catalan.config import MARKET_CLOSE, NEWS_CUTOFF

# Announcement → which session it can first be traded in.
OVERNIGHT = "overnight"   # tradable at the open of the attributed session
INTRADAY = "intraday"     # landed mid-session; the paper's separate (blocked) arm


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


def build_panel(start: date, end: date):
    """Build the returns panel over the PIT universe for [start, end].

    Phase 2. Reads Turso ``price_history`` read-only via store.turso_ro(),
    filters each date through universe.members_on(), computes init_ret and
    drift_ret, assigns within-date liquidity quintiles by 20-day ADV, and sets
    the ``shortable`` flag from the T2T/ASM/GSM lists (open item 4 — the source
    for those lists is not yet settled, and until it is, the short leg cannot be
    claimed as executable).
    """
    raise NotImplementedError("Phase 2 — see catalan/README.md")
