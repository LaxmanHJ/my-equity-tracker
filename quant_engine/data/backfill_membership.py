"""
Backfill historical Nifty 200 (or any index) constituents into the
``index_membership`` table — survivorship-bias fix (audit P2-c).

Why
---
``price_history`` holds whatever symbols got ingested; today that's 208
(NIFTY200 + 8 legacy). Every research run that filters "Nifty 200" silently
uses TODAY's roster, excluding the stocks that were members in 2018-2024
but have since been removed (often *because* they failed). The bias is
2-5 %/year of overstated IR on EM-equity backtests.

This CLI ingests a CSV of (symbol, effective_from, effective_to) intervals
and writes them to the ``index_membership`` table. The Python research
path then queries ``MembershipRegistry.members_on(date)`` instead of
``load_all_symbols()``.

CSV format
----------
Header (required, order independent): ``symbol,effective_from,effective_to``
Optional columns: ``index_name`` (default NIFTY200), ``source``, ``notes``.
Date format: ``YYYY-MM-DD`` preferred (also accepts ``YYYY/MM/DD``, ``DD-Mon-YYYY``).
Empty ``effective_to`` means "still a member as of today."

Sourcing the data
-----------------
We can't generate this — it has to come from external sources:

  1. **CMIE Prowess (paid)** — has "Index constituents history" export with
     change reasons; best single source for Indian indices.
  2. **NSE corporate-actions archive** — daily files at
     ``https://nsearchives.nseindia.com/archives/equities/CAS/`` (delistings,
     suspensions, mergers); use to derive ``effective_to`` for retired symbols.
  3. **Nifty Indices methodology PDFs** — semi-annual index review press
     releases list additions/deletions at each rebalance.
  4. **BSE delisted list** (free) — cross-check at
     ``https://www.bseindia.com/Markets/equity/EQReports/delistdetails.aspx``.

Run
---
    # Dry-run: validate the CSV but don't write
    python -m quant_engine.data.backfill_membership --from-csv nifty200_history.csv

    # Apply to Turso
    python -m quant_engine.data.backfill_membership --from-csv nifty200_history.csv --apply

    # Verify what's in Turso right now
    python -m quant_engine.data.backfill_membership --status
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from quant_engine.data.membership import (
    CREATE_INDEX_SQL,
    CREATE_TABLE_SQL,
    MembershipInterval,
    load_membership_from_csv,
    validate_intervals,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _print_summary(intervals: list[MembershipInterval]) -> None:
    if not intervals:
        print("  (empty)")
        return
    by_index: dict[str, int] = {}
    by_symbol: dict[tuple[str, str], int] = {}
    open_intervals = 0
    for iv in intervals:
        by_index[iv.index_name] = by_index.get(iv.index_name, 0) + 1
        by_symbol[(iv.index_name, iv.symbol)] = \
            by_symbol.get((iv.index_name, iv.symbol), 0) + 1
        if iv.effective_to is None:
            open_intervals += 1
    print(f"  total intervals:   {len(intervals)}")
    print(f"  distinct symbols:  {len(by_symbol)}")
    print(f"  open intervals:    {open_intervals} (effective_to is NULL — current members)")
    print("  intervals per index:")
    for idx, n in sorted(by_index.items()):
        print(f"    {idx:<12} {n}")


def _apply_to_turso(intervals: list[MembershipInterval]) -> int:
    """Insert validated intervals into Turso. Returns count of new rows added."""
    from quant_engine.data.turso_client import connect

    conn = connect()
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_SQL)

    inserted = 0
    rows = [
        (
            iv.index_name, iv.symbol,
            iv.effective_from.isoformat(),
            iv.effective_to.isoformat() if iv.effective_to else None,
            iv.source, iv.notes,
        )
        for iv in intervals
    ]
    # Use INSERT OR IGNORE so re-running the CLI doesn't double-insert
    # already-present rows (the UNIQUE constraint enforces idempotency).
    for r in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO index_membership "
            "(index_name, symbol, effective_from, effective_to, source, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            list(r),
        )
        # Turso doesn't expose rowcount portably; we approximate by counting
        # the SELECT below. Conservative: report as "attempted insert".
        inserted += 1
    return inserted


def _print_turso_status() -> None:
    from quant_engine.data.turso_client import connect

    conn = connect()
    conn.execute(CREATE_TABLE_SQL)
    cur = conn.execute(
        "SELECT index_name, COUNT(*), "
        "SUM(CASE WHEN effective_to IS NULL THEN 1 ELSE 0 END), "
        "MIN(effective_from), MAX(COALESCE(effective_to, effective_from)) "
        "FROM index_membership GROUP BY index_name"
    )
    rows = cur.fetchall()
    print("\n=== index_membership status ===")
    if not rows:
        print("  table is empty — run --from-csv ... --apply to ingest")
        return
    print(f"  {'index':<12} {'rows':>6} {'open':>6} {'earliest':>12} {'latest':>12}")
    for r in rows:
        print(f"  {r[0]:<12} {r[1]:>6} {r[2]:>6} {r[3]:>12} {r[4]:>12}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-csv", type=str, default=None,
                   help="Path to membership CSV (see file docstring for format)")
    p.add_argument("--apply", action="store_true",
                   help="Actually write to Turso. Without --apply, dry-runs.")
    p.add_argument("--status", action="store_true",
                   help="Print current Turso state and exit (no writes).")
    p.add_argument("--default-index", type=str, default="NIFTY200",
                   help="Index name to assume when CSV row's index_name is blank")
    p.add_argument("--strict", action="store_true",
                   help="Abort on ANY validation warning. Without --strict, warns "
                        "and continues (use for messy import-then-fix workflows).")
    args = p.parse_args(argv)

    if args.status:
        _print_turso_status()
        return 0
    if not args.from_csv:
        p.error("either --from-csv or --status is required")

    csv_path = Path(args.from_csv).expanduser().resolve()
    if not csv_path.exists():
        logger.error("CSV not found: %s", csv_path)
        return 2

    logger.info("Reading %s …", csv_path)
    intervals = load_membership_from_csv(csv_path, default_index=args.default_index)
    print(f"\n=== Parsed {len(intervals)} rows from {csv_path.name} ===")
    _print_summary(intervals)

    errors = validate_intervals(intervals)
    print(f"\n=== Validation: {len(errors)} issue(s) ===")
    for e in errors[:25]:
        print(f"  • {e}")
    if len(errors) > 25:
        print(f"  … and {len(errors) - 25} more")

    if errors and args.strict:
        logger.error("--strict: aborting because validation surfaced issues")
        return 3
    if errors and not args.apply:
        logger.warning("validation issues present; --apply will proceed anyway "
                       "unless --strict is set")

    if not args.apply:
        print("\n(dry-run — pass --apply to write to Turso)")
        return 0

    logger.info("Writing to Turso …")
    n = _apply_to_turso(intervals)
    print(f"\nInserted/ignored {n} rows into index_membership.")
    _print_turso_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
