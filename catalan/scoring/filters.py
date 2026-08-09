"""
Query-time corpus filters: category exclusions and near-duplicate dedup.

These are applied when *selecting* announcements — never at ingest. The raw
corpus stays complete and auditable, so changing the exclusion list is a
re-query rather than a refetch, and any result can be recomputed under a
different filter without losing data.

They serve two callers with the same rules:
  - the scorer, where filtering is also a cost control (no point paying to
    score 160 "Copy of Newspaper Publication" rows a day);
  - the analysis, where filtering is a study-design decision.

Both must use the same predicate or the corpus that was scored stops matching
the corpus that was analysed.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Tuple

from catalan.config import (
    CATEGORY_EXCLUSIONS,
    DEDUP_SIMILARITY,
    HEADLINE_EXCLUSIONS,
)

try:                                   # optional; stdlib fallback below
    from rapidfuzz.distance import DamerauLevenshtein

    def _similarity(a: str, b: str) -> float:
        return DamerauLevenshtein.normalized_similarity(a, b)

    SIMILARITY_BACKEND = "rapidfuzz"
except ImportError:                     # pragma: no cover - depends on env
    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        # Not Damerau-Levenshtein: SequenceMatcher scores differently, so the
        # 0.6 threshold is not exactly the paper's under this backend. Install
        # rapidfuzz (it is in requirements.txt) for the real thing; this exists
        # so the pipeline degrades rather than dies.
        return SequenceMatcher(None, a, b).ratio()

    SIMILARITY_BACKEND = "difflib"


_WS_RE = re.compile(r"\s+")


def normalise_headline(text: str) -> str:
    """Lowercase, collapse whitespace, drop punctuation — for comparison only.

    Never written back to the store; the raw headline is what gets scored.
    """
    return _WS_RE.sub(" ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def is_excluded_category(category) -> bool:
    """True if this `desc` is on the declared exclusion list."""
    return (category or "") in CATEGORY_EXCLUSIONS


def is_excluded_headline(headline) -> bool:
    """True if the headline TEXT marks this as a declared non-event.

    Complements the `desc` filter rather than replacing it. `desc` is
    vendor-assigned; a row reading "... has informed the Exchange about Copy of
    Newspaper Publication" is the same non-event whatever category it was filed
    under.
    """
    text = (headline or "").lower()
    return any(pattern in text for pattern in HEADLINE_EXCLUSIONS)


def is_excluded(row: Dict) -> bool:
    """Either exclusion rule fires."""
    return (is_excluded_category(row.get("category"))
            or is_excluded_headline(row.get("headline")))


def dedup_key(symbol: str, announced_at: str) -> Tuple[str, str]:
    """Dedup is scoped to the same symbol on the same day, per the paper."""
    return (symbol, (announced_at or "")[:10])


def drop_near_duplicates(
    rows: Sequence[Dict],
    threshold: float = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Split rows into (kept, dropped) on same-symbol same-day similarity.

    The earliest announcement in a near-duplicate cluster is kept — it is the
    one that carried the information first, and keeping a later restatement
    instead would attribute the reaction to the wrong timestamp.

    ``rows`` need ``symbol``, ``announced_at`` and ``headline``. Returns dicts
    from the input untouched.
    """
    thresh = DEDUP_SIMILARITY if threshold is None else threshold

    buckets: Dict[Tuple[str, str], List[Dict]] = {}
    for row in rows:
        buckets.setdefault(dedup_key(row["symbol"], row["announced_at"]), []).append(row)

    kept: List[Dict] = []
    dropped: List[Dict] = []

    for bucket in buckets.values():
        bucket.sort(key=lambda r: r["announced_at"])
        survivors: List[Tuple[str, Dict]] = []
        for row in bucket:
            norm = normalise_headline(row["headline"])
            if any(_similarity(norm, seen) > thresh for seen, _ in survivors):
                dropped.append(row)
            else:
                survivors.append((norm, row))
                kept.append(row)

    kept.sort(key=lambda r: (r["announced_at"], r["symbol"]))
    return kept, dropped


def apply_all(rows: Iterable[Dict], dedup: bool = True) -> Tuple[List[Dict], Dict[str, int]]:
    """Category exclusions then dedup. Returns (kept, counts) for reporting.

    The counts matter: a filter that silently removes most of the corpus should
    be visible in the run log, not discovered later in a coverage plot.
    """
    rows = list(rows)

    # Counted separately so the run log shows which rule is doing the work.
    # If the headline rule ever starts catching a lot, that is NSE's `desc`
    # drifting and worth knowing about rather than silently absorbing.
    by_category = [r for r in rows if is_excluded_category(r.get("category"))]
    by_headline = [r for r in rows
                   if not is_excluded_category(r.get("category"))
                   and is_excluded_headline(r.get("headline"))]
    kept_rows = [r for r in rows if not is_excluded(r)]

    if dedup:
        kept, dupes = drop_near_duplicates(kept_rows)
    else:
        kept, dupes = kept_rows, []

    return kept, {
        "input": len(rows),
        "excluded_category": len(by_category),
        "excluded_headline": len(by_headline),
        "excluded_duplicate": len(dupes),
        "kept": len(kept),
    }
