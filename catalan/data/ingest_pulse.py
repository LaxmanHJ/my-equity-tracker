"""
Secondary corpus — Zerodha Pulse, forward-only.

Pulse (``/feed.php``) returns a general market RSS with **no ticker tag, no
company entity, and no archive**; the live feed also carried non-financial
items (cricket schedules). It therefore cannot anchor the historical arm and
cannot be entity-linked without a resolution step of its own.

It is collected forward-only as a *secondary* corpus: useful for checking
whether the NSE announcement spine misses market-moving news that a general
feed catches, and nothing more. No CATALAN gate may rest on it.
"""
from __future__ import annotations

from datetime import date

PULSE_FEED_URL = "https://pulse.zerodha.com/feed.php"


def collect(on: date) -> dict:
    """Pull today's Pulse items into the local store with source='pulse'.

    Entity linkage is the hard part and is deliberately deferred: store the raw
    item with a NULL symbol rather than guessing, and resolve later against the
    PIT roster. A wrong symbol here is worse than a missing one.
    """
    raise NotImplementedError("secondary corpus — not on the critical path")
