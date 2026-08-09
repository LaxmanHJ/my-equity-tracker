"""
Phase 1b — historical backfill of NSE corporate announcements, 2019 → now.

Reuses the client in ``collect_daily`` (session warm-up, quarter chunking,
normalisation, INSERT OR IGNORE) — the only difference from the forward
collector is scale and resumability, not mechanics.

Projected corpus: ~500k in-universe rows 2019→2026, ~250k after category
filtering and dedup. A 3-month window returns in a single request, so chunk by
quarter (config.INGEST_CHUNK_DAYS).

**The historical arm is exploratory only.** Claude models' training cutoffs fall
inside this window, so scores over these headlines are contaminated by recall.
Its job is to *measure* that contamination (analysis/contamination.py), not to
support a trading claim. Only the forward log clears C2.

Run:
    python3 -m catalan.data.ingest_nse --from 2019-01-01
"""
from __future__ import annotations

from datetime import date


def backfill(start: date, end: date, resume: bool = True) -> dict:
    """Quarter-chunked, resumable historical ingest.

    Resume works off the ``ingest_runs`` table: skip windows that already have
    a status='ok' row covering them, so an interrupted run restarts at the first
    incomplete quarter instead of refetching six years.

    Phase 1b. Build on collect_daily.nse_session / fetch_window / normalise /
    persist rather than reimplementing them.
    """
    raise NotImplementedError("Phase 1b — see catalan/README.md")
