"""
Point-in-time Nifty 200 (or any index) membership — survivorship-bias fix
(audit item S8, audit-doc P2-c).

The problem
-----------
The Turso ``price_history`` table holds whatever symbols our ingestion has
seen. As of 2026-05, that is the union of today's Nifty 200 + a handful of
retired symbols we never cleaned out — 208 in total. Every backtest that
filters "give me Nifty 200 stocks" implicitly uses **today's** roster,
silently excluding the stocks that were members in 2018-2024 but have
since been removed (often *because* they failed). The published estimate
for EM equities is 2-5 %/year of overstated IR.

The fix
-------
Maintain a separate ``index_membership`` table with one row per
(symbol, effective_from, effective_to) interval, and replace every
``load_all_symbols()`` call in the research path with a PIT query
``members_on(date, index='NIFTY200')``.

This module ships the **infrastructure** for that fix:

* the table schema (idempotent CREATE)
* a ``MembershipRegistry`` that answers PIT queries in O(log n)
* a CSV loader so the user can ingest historical membership from
  CMIE Prowess / NSE corporate-actions archive / BSE delisted list
* a validator that surfaces overlapping intervals, inverted dates, dupes
* a Turso reader/writer

The *data sourcing* is a separate operational task — see
``quant_engine/data/backfill_membership.py`` for the ingest CLI and
the wiki page ``wiki/concepts/survivorship_pit_universe.md`` for the
end-to-end procedure.
"""
from __future__ import annotations

import csv
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional


__all__ = [
    "MembershipInterval",
    "MembershipRegistry",
    "CREATE_TABLE_SQL",
    "apply_pit_row_filter",
    "load_membership_from_csv",
    "validate_intervals",
]


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS index_membership (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    index_name      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    source          TEXT,
    notes           TEXT,
    UNIQUE(index_name, symbol, effective_from)
)
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_index_membership_lookup
    ON index_membership(index_name, symbol, effective_from)
"""


@dataclass(frozen=True)
class MembershipInterval:
    """One contiguous spell of index membership for a symbol.

    ``effective_to`` is None when the symbol is still a member as of today.
    Both dates are inclusive — a symbol active on its ``effective_to`` is
    considered a member that day; it ceases the next day.
    """
    index_name: str
    symbol: str
    effective_from: date
    effective_to: Optional[date]
    source: str = ""
    notes: str = ""

    def contains(self, d: date) -> bool:
        if d < self.effective_from:
            return False
        if self.effective_to is not None and d > self.effective_to:
            return False
        return True


# ── CSV loading ─────────────────────────────────────────────────────────────
def _parse_date(s: str) -> Optional[date]:
    s = s.strip()
    if not s:
        return None
    # Accept YYYY-MM-DD or YYYY/MM/DD.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"unparseable date {s!r}; supported formats: YYYY-MM-DD, YYYY/MM/DD, "
        f"DD-Mon-YYYY, DD-MM-YYYY"
    )


def load_membership_from_csv(
    path: Path | str,
    default_index: str = "NIFTY200",
) -> list[MembershipInterval]:
    """Read a CSV with header ``index_name,symbol,effective_from,effective_to[,source[,notes]]``.

    If ``index_name`` is absent or blank, falls back to ``default_index``.
    Blank ``effective_to`` means "still a member as of today."

    Sourcing
    --------
    The CSV is meant to be assembled from one or more of:
      • CMIE Prowess "Index constituents history" export
      • NSE corporate-actions archive (delisting / suspension dates)
      • BSE delisted-stocks public list (cross-check)
      • Nifty Indices methodology documents (semi-annual reviews)

    See ``wiki/concepts/survivorship_pit_universe.md`` for the assembly recipe.
    """
    rows: list[MembershipInterval] = []
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for lineno, row in enumerate(reader, start=2):
            try:
                idx = (row.get("index_name") or default_index).strip()
                sym = (row["symbol"] or "").strip()
                if not sym:
                    raise ValueError("symbol column is empty")
                eff_from = _parse_date(row["effective_from"])
                eff_to = _parse_date(row.get("effective_to") or "")
                rows.append(MembershipInterval(
                    index_name=idx,
                    symbol=sym,
                    effective_from=eff_from,
                    effective_to=eff_to,
                    source=(row.get("source") or "").strip(),
                    notes=(row.get("notes") or "").strip(),
                ))
            except (KeyError, ValueError) as exc:
                raise ValueError(f"CSV line {lineno}: {exc}") from exc
    return rows


# ── Validation ──────────────────────────────────────────────────────────────
def validate_intervals(intervals: Iterable[MembershipInterval]) -> list[str]:
    """Return a list of validation-error messages — empty list if OK.

    Checks
    ------
    * ``effective_from > effective_to`` (inverted interval) — invalid.
    * Duplicate ``(index, symbol, effective_from)`` keys — definitional dupe.
    * Overlapping intervals for the same (index, symbol) — a symbol cannot be
      a member of the same index across two simultaneous spells.

    Returns a list of human-readable error strings so the caller can decide
    whether to abort the ingest or just warn.
    """
    errors: list[str] = []
    seen: set[tuple[str, str, date]] = set()

    by_symbol: dict[tuple[str, str], list[MembershipInterval]] = {}
    for iv in intervals:
        if iv.effective_to is not None and iv.effective_to < iv.effective_from:
            errors.append(
                f"inverted interval: {iv.index_name}/{iv.symbol} "
                f"{iv.effective_from} → {iv.effective_to}"
            )
            continue
        key = (iv.index_name, iv.symbol, iv.effective_from)
        if key in seen:
            errors.append(
                f"duplicate (index,symbol,effective_from) key: "
                f"{iv.index_name}/{iv.symbol} starting {iv.effective_from}"
            )
            continue
        seen.add(key)
        by_symbol.setdefault((iv.index_name, iv.symbol), []).append(iv)

    for (idx, sym), ivs in by_symbol.items():
        ivs_sorted = sorted(ivs, key=lambda iv: iv.effective_from)
        for prev, curr in zip(ivs_sorted, ivs_sorted[1:]):
            prev_end = prev.effective_to or date.max
            if prev_end >= curr.effective_from:
                errors.append(
                    f"overlapping intervals for {idx}/{sym}: "
                    f"[{prev.effective_from}..{prev_end}] overlaps "
                    f"[{curr.effective_from}..{curr.effective_to or 'open'}]"
                )
    return errors


# ── Registry: O(log n) PIT queries ──────────────────────────────────────────
class MembershipRegistry:
    """In-memory PIT registry. Built once from a list of intervals; serves
    ``contains(symbol, date)`` and ``members_on(date)`` in O(log n) and O(n)
    respectively.

    Designed to be loaded from CSV at ingest time AND from Turso at research
    time — see ``from_iterable`` and ``from_turso``.
    """

    def __init__(self, intervals: Iterable[MembershipInterval]):
        ivs = list(intervals)
        errors = validate_intervals(ivs)
        if errors:
            raise ValueError(
                "MembershipRegistry: cannot build from invalid intervals — "
                "fix the source data or call validate_intervals() first. "
                f"First error: {errors[0]}"
            )
        # Build sorted-by-from index per (index_name, symbol) for O(log n) lookup.
        self._by_symbol: dict[tuple[str, str], list[MembershipInterval]] = {}
        for iv in ivs:
            key = (iv.index_name, iv.symbol)
            self._by_symbol.setdefault(key, []).append(iv)
        for key in self._by_symbol:
            self._by_symbol[key].sort(key=lambda iv: iv.effective_from)
        self._all_intervals = ivs

    @classmethod
    def from_iterable(cls, intervals: Iterable[MembershipInterval]) -> "MembershipRegistry":
        return cls(intervals)

    @classmethod
    def from_turso(cls, conn, index_name: str = "NIFTY200") -> "MembershipRegistry":
        cur = conn.execute(
            "SELECT index_name, symbol, effective_from, effective_to, "
            "COALESCE(source, ''), COALESCE(notes, '') "
            "FROM index_membership WHERE index_name = ?",
            [index_name],
        )
        rows = cur.fetchall()
        ivs = [
            MembershipInterval(
                index_name=r[0], symbol=r[1],
                effective_from=_parse_date(r[2]),
                effective_to=_parse_date(r[3]) if r[3] else None,
                source=r[4], notes=r[5],
            )
            for r in rows
        ]
        return cls(ivs)

    def contains(self, symbol: str, on: date, index_name: str = "NIFTY200") -> bool:
        """Was ``symbol`` a member of ``index_name`` on date ``on``?

        O(log n) — binary-searches the sorted spells by effective_from for
        the latest spell that started on/before ``on``, then checks the end.
        """
        ivs = self._by_symbol.get((index_name, symbol))
        if not ivs:
            return False
        starts = [iv.effective_from for iv in ivs]
        pos = bisect_right(starts, on) - 1
        if pos < 0:
            return False
        return ivs[pos].contains(on)

    def members_on(self, on: date, index_name: str = "NIFTY200") -> set[str]:
        """Set of symbols that were members of ``index_name`` on date ``on``."""
        out: set[str] = set()
        for (idx, sym), ivs in self._by_symbol.items():
            if idx != index_name:
                continue
            if any(iv.contains(on) for iv in ivs):
                out.add(sym)
        return out

    def all_symbols(self, index_name: str = "NIFTY200") -> set[str]:
        """All symbols that have *ever* been members — covers delisted, merged,
        renamed. This is the right universe to query ``price_history`` over.
        """
        return {sym for (idx, sym) in self._by_symbol if idx == index_name}


# ── Per-symbol PIT filter for panel-style feature frames ────────────────────
def apply_pit_row_filter(
    features,
    symbol: str,
    registry: Optional["MembershipRegistry"],
    index_name: str = "NIFTY200",
):
    """Drop rows where ``symbol`` was NOT an ``index_name`` member on that date.

    This is the *survivorship-bias-correction* hook the ML diagnostic / trainer
    call after building each symbol's feature frame. With ``registry=None`` it
    is a no-op — preserving bit-identical legacy behaviour when PIT is disabled.

    Parameters
    ----------
    features : pd.DataFrame with a DatetimeIndex (one row per trading day).
    symbol : the symbol whose membership we are checking.
    registry : MembershipRegistry to consult, or None to skip the filter.
    index_name : which index the registry should be queried for.

    Returns
    -------
    pd.DataFrame restricted to dates on which ``symbol`` was a member.
    """
    if registry is None or features is None or len(features) == 0:
        return features
    import pandas as pd  # local — keep module import-light for callers that don't need pandas
    import numpy as np
    dates = pd.DatetimeIndex(features.index).date  # np.ndarray of python date objects
    mask = np.fromiter(
        (registry.contains(symbol, d, index_name) for d in dates),
        dtype=bool,
        count=len(dates),
    )
    return features.loc[mask]
