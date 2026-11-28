"""
One clock for every timestamp CATALAN writes: IST, stored without a zone suffix.

``announced_at`` comes from NSE in IST. Until catalan-v2 (2026-10-18) the
timestamps CATALAN wrote itself (``ingested_at``, ``scored_at``, ``extracted_at``,
``run_id``) used the machine clock, which is UTC on the CI runner and IST on a
laptop. The 2026-09-27 audit found UTC-naive values sitting next to IST-naive
ones. Everything written from v2 on goes through here.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist_dt() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def now_ist() -> str:
    """'YYYY-MM-DDTHH:MM:SS' in IST, no zone suffix (same shape as announced_at)."""
    return now_ist_dt().isoformat(timespec="seconds")


def today_ist() -> str:
    return now_ist()[:10]
