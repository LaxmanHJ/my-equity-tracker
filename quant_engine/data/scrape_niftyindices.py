"""
Scrape historical index membership from NSE Indices press releases.

Why this exists
---------------
``index_membership`` is the survivorship-bias fix (audit P2-c): every research
run that filters "Nifty 200" must use the roster as it stood on the bar date,
not today's roster. The infrastructure for that landed in ``membership.py`` /
``backfill_membership.py``, but the *data* was assembled by hand and the source
CSV was never committed — so the 367 NIFTY200 rows in Turso have no reproducible
provenance and NIFTY500 (research item R2) had no path at all.

This module is that path. It is the tool; the CSV it writes is the artifact, and
both belong in git.

How it works
------------
NSE Indices publishes every index change as a press-release PDF, and
``https://www.niftyindices.com/media`` embeds the WHOLE archive (1400+ releases
back to 1998) in one static HTML response — no pagination, no auth, no XHR. The
month dropdown filters client-side.

Press releases give *change events*, not rosters. The current roster, however, is
published exactly (``IndexConstituent/ind_nifty500list.csv``). So we anchor on
today's roster and walk **backward**:

    roster_before(review) = roster_after(review) - included + excluded

which reconstructs the roster over each inter-review period, and those periods
collapse into the ``(effective_from, effective_to)`` spells the registry wants.
Walking backward from a known endpoint beats walking forward from a guessed
historical base — there is no authoritative old roster to start from.

Correctness check
-----------------
Run with ``--index NIFTY200`` and diff against the 367 hand-built rows already in
Turso (``--verify``). NIFTY200 is the control: if the reconstruction can't
reproduce a roster that was assembled independently, the NIFTY500 output isn't
trustworthy either.

Caveats
-------
* Reconstruction is only as complete as the releases we parse. A change
  communicated some other way leaves a permanent skew in every earlier period.
  ``--verify`` is what bounds that risk.
* The walk cannot see further back than the oldest release parsed; the earliest
  period is left open at ``FROM_DATE`` and is the least trustworthy.
* Symbols are NSE trading symbols as printed. Renames (symbol changes with no
  index event) are NOT tracked — a rename looks like an exclusion plus an
  inclusion only if NSE published it as one.

Run
---
    # Scrape + write CSV (NIFTY500), using the on-disk PDF cache
    python -m quant_engine.data.scrape_niftyindices --index NIFTY500

    # Control run: rebuild NIFTY200 and diff against what's in Turso
    python -m quant_engine.data.scrape_niftyindices --index NIFTY200 --verify

    # Then ingest (dry-run first, no --apply)
    python -m quant_engine.data.backfill_membership \\
        --from-csv data/membership_sources/nifty500_history.csv \\
        --default-index NIFTY500 --apply
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# --verify reads index_membership from Turso; the credentials live in .env.
load_dotenv(PROJECT_ROOT / ".env")
SOURCES_DIR = PROJECT_ROOT / "data" / "membership_sources"
PDF_CACHE = SOURCES_DIR / "press_release_cache"

BASE = "https://www.niftyindices.com"
MEDIA_URL = f"{BASE}/media"
CONSTITUENT_URL = "https://niftyindices.com/IndexConstituent/ind_{slug}list.csv"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Earliest period boundary. The registry's NIFTY200 rows start here, and the
# ML panel has no usable price history before it either.
FROM_DATE = "2018-01-01"

THROTTLE_SECONDS = 0.3

# Index name in our namespace -> (section header in the PDF, constituent-CSV slug).
# PDF headers are matched case-insensitively with flexible internal spacing, so
# "Nifty 500" also matches "NIFTY500".
INDEX_SPECS = {
    "NIFTY500": (r"Nifty\s*500", "nifty500"),
    "NIFTY200": (r"Nifty\s*200", "nifty200"),
    "NIFTY100": (r"Nifty\s*100", "nifty100"),
    "NIFTY50": (r"Nifty\s*50", "nifty50"),
}

# Press-release titles that can never contain an equity broad-market change.
# Skipping these avoids downloading ~700 irrelevant PDFs (fixed income and SDL
# releases alone are a third of the archive).
SKIP_TITLE = re.compile(
    r"fixed\s+income|SDL\b|\bG-?Sec|\bT-?Bill|SME\s+EMERGE|Nifty\s+IPO\b"
    r"|REIT|InvIT|methodology|consultation|launch|riskometer|tracking\s+error",
    re.I,
)


# ── HTTP ────────────────────────────────────────────────────────────────────
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": MEDIA_URL})
    return s


def _get(sess: requests.Session, url: str, *, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = sess.get(url, timeout=60)
            if r.status_code == 200:
                return r.content
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


# ── Archive index ───────────────────────────────────────────────────────────
_ANCHOR = re.compile(
    r'<a[^>]+href="(/Press_Release/[^"]+\.pdf)\s*"[^>]*>(.*?)</a>', re.S | re.I
)
_PRDATE = re.compile(r"ind_prs(\d{2})(\d{2})(\d{4})", re.I)


def fetch_archive(sess: requests.Session) -> list[dict]:
    """Every press release on /media as {url, title, pr_date}, newest first."""
    html_text = _get(sess, MEDIA_URL).decode("utf-8", errors="replace")
    seen: set[str] = set()
    rows: list[dict] = []
    for href, raw in _ANCHOR.findall(html_text):
        href = href.strip()
        if href in seen:
            continue
        seen.add(href)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip()
        m = _PRDATE.search(href)
        if not m:
            continue
        dd, mm, yyyy = m.groups()
        try:
            pr_date = date(int(yyyy), int(mm), int(dd))
        except ValueError:
            continue
        rows.append({"url": BASE + href, "title": title, "pr_date": pr_date})
    rows.sort(key=lambda r: r["pr_date"], reverse=True)
    logger.info("Archive: %d press releases (%s .. %s)",
                len(rows), rows[-1]["pr_date"], rows[0]["pr_date"])
    return rows


def candidate_releases(archive: list[dict], since: date) -> list[dict]:
    """Releases that could plausibly carry a broad-market equity change."""
    out = [r for r in archive
           if r["pr_date"] >= since - timedelta(days=90)
           and not SKIP_TITLE.search(r["title"])]
    logger.info("Candidates after title filter: %d of %d", len(out), len(archive))
    return out


def fetch_pdf_text(sess: requests.Session, url: str) -> str:
    """Download (or read from cache) a press-release PDF and extract its text."""
    from pypdf import PdfReader

    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    pdf_path = PDF_CACHE / name
    txt_path = PDF_CACHE / (name + ".txt")

    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8")

    if not pdf_path.exists():
        pdf_path.write_bytes(_get(sess, url))
        time.sleep(THROTTLE_SECONDS)

    try:
        reader = PdfReader(str(pdf_path))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unparseable PDF %s: %s", name, exc)
        text = ""
    txt_path.write_text(text, encoding="utf-8")
    return text


# ── PDF parsing ─────────────────────────────────────────────────────────────
_EFFECTIVE = re.compile(
    r"effective\s+from\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})\s*,?\s*(\d{4})",
    re.I,
)
# Table rows look like:  "12 Godrej Agrovet Ltd. GODREJAGRO"
_ROW = re.compile(r"^\s*\d{1,3}[\s.)]+(.+?)\s+([A-Z0-9&\-]{2,20})\s*$")
# Section markers come in two conventions and BOTH must be matched:
#   2026-style:  "2) Nifty 500"                     — index directly numbered
#   2025-style:  "1) Replacements on account of …"  — category numbered, with
#                "l) Nifty 200"                       the index as a lettered child
# Matching only the numeric form silently lost every review that used the
# lettered layout (Sep-2020, Sep-2021, Sep-2023, Sep-2024, Mar-2025), and because
# the roster walk goes backward, each missed event corrupted every EARLIER period
# too — which is precisely what the NIFTY200 control run surfaced.
# Only ")" is accepted as the terminator: "1." would also match numbered footnotes
# ("Note: 1. Zomato Ltd. …") and cut a section short.
_SECTION = re.compile(r"^\s*(?:\d{1,3}|[a-zA-Z]{1,2})\)\s*(.+?)\s*$", re.M)
_INCLUDED = re.compile(r"being\s+included|following.{0,40}included|inclusion", re.I)
_EXCLUDED = re.compile(r"being\s+excluded|following.{0,40}excluded|exclusion", re.I)
_NOCHANGE = re.compile(r"no\s+change", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def parse_effective_date(text: str) -> date | None:
    m = _EFFECTIVE.search(text)
    if not m:
        return None
    mon, day, year = m.group(1).lower(), int(m.group(2)), int(m.group(3))
    try:
        return date(year, _MONTHS[mon], day)
    except (KeyError, ValueError):
        return None


def _index_section(text: str, header_pattern: str) -> str | None:
    """
    Slice out the block of text belonging to one index's numbered section.

    Sections look like "2) Nifty 500" and run until the next "N) <index>"
    header. Returns None when the release doesn't mention this index at all.
    """
    heads = list(_SECTION.finditer(text))
    for i, h in enumerate(heads):
        if re.fullmatch(header_pattern, h.group(1).strip(), re.I):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            return text[h.end():end]
    return None


def parse_changes(text: str, header_pattern: str) -> tuple[set[str], set[str]]:
    """
    (included, excluded) symbols for one index in one press release.

    The PDF prints an "excluded" table then an "included" table under the index
    header; we attribute each symbol row to whichever caption most recently
    preceded it.
    """
    section = _index_section(text, header_pattern)
    if section is None or _NOCHANGE.search(section[:200]):
        return set(), set()

    included: set[str] = set()
    excluded: set[str] = set()
    bucket: set[str] | None = None

    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _EXCLUDED.search(stripped):
            bucket = excluded
            continue
        if _INCLUDED.search(stripped):
            bucket = included
            continue
        if stripped.lower().startswith("sr. no"):
            continue
        m = _ROW.match(stripped)
        if m and bucket is not None:
            symbol = m.group(2).strip().upper()
            # "Ltd" / "Ltd." tails and stray words are not symbols.
            if symbol in {"LTD", "LIMITED", "SYMBOL", "NO", "INDIA"}:
                continue
            bucket.add(symbol)

    return included, excluded


# ── Roster reconstruction ───────────────────────────────────────────────────
def fetch_current_roster(sess: requests.Session, slug: str) -> set[str]:
    raw = _get(sess, CONSTITUENT_URL.format(slug=slug)).decode("utf-8", errors="replace")
    rows = list(csv.DictReader(raw.splitlines()))
    symbols = {r["Symbol"].strip().upper() for r in rows if r.get("Symbol")}
    logger.info("Current %s roster: %d symbols", slug, len(symbols))
    return symbols


def build_intervals(
    current: set[str],
    events: list[dict],
    from_date: date,
    today: date,
) -> list[dict]:
    """
    Walk backward through change events to reconstruct membership spells.

    `events` must be ascending by effective date, each {date, included, excluded}.
    Returns rows of {symbol, effective_from, effective_to} with BOTH endpoints
    inclusive (the convention MembershipRegistry.contains uses).
    """
    events = sorted(events, key=lambda e: e["date"])
    # Period i spans [bounds[i], bounds[i+1]-1]; the last runs to `today`.
    bounds = [from_date] + [e["date"] for e in events]

    rosters: list[set[str]] = [set(current)] * 1
    roster = set(current)
    # Step back over each event, newest first, rebuilding the earlier roster.
    for e in reversed(events):
        roster = (roster - e["included"]) | e["excluded"]
        rosters.append(set(roster))
    rosters.reverse()  # rosters[i] is the roster during period i

    assert len(rosters) == len(bounds), (len(rosters), len(bounds))

    # Collapse consecutive periods of membership into spells.
    rows: list[dict] = []
    all_symbols = set().union(*rosters) if rosters else set()
    for sym in sorted(all_symbols):
        run_start: date | None = None
        for i, r in enumerate(rosters):
            member = sym in r
            if member and run_start is None:
                run_start = bounds[i]
            elif not member and run_start is not None:
                rows.append({
                    "symbol": sym,
                    "effective_from": run_start,
                    "effective_to": bounds[i] - timedelta(days=1),
                })
                run_start = None
        if run_start is not None:
            rows.append({"symbol": sym, "effective_from": run_start, "effective_to": None})
    return rows


# ── Output ──────────────────────────────────────────────────────────────────
def write_csv(rows: list[dict], index_name: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "effective_from", "effective_to", "index_name", "source", "notes"])
        for r in sorted(rows, key=lambda r: (r["symbol"], r["effective_from"])):
            w.writerow([
                r["symbol"],
                r["effective_from"].isoformat(),
                r["effective_to"].isoformat() if r["effective_to"] else "",
                index_name,
                "niftyindices_pr_scraped",
                r.get("notes", ""),
            ])
    logger.info("Wrote %d rows to %s", len(rows), out_path)


def verify_against_turso(rows: list[dict], index_name: str) -> int:
    """
    Diff the reconstruction against what's already in index_membership.

    This is the control experiment: NIFTY200 was assembled by hand from the same
    press releases, so a clean diff is evidence the parser and the backward walk
    are both right. Returns the number of discrepancies.
    """
    from quant_engine.data.membership import MembershipRegistry
    from quant_engine.data.turso_client import connect

    with connect() as conn:
        reg = MembershipRegistry.from_turso(conn, index_name)
    stored = reg.all_symbols(index_name)
    built = {r["symbol"] for r in rows}

    only_stored = sorted(stored - built)
    only_built = sorted(built - stored)

    print(f"\n=== Verify vs Turso ({index_name}) ===")
    print(f"ever-members stored : {len(stored)}")
    print(f"ever-members built  : {len(built)}")
    print(f"in Turso only ({len(only_stored)}): {', '.join(only_stored[:40])}")
    print(f"in build only ({len(only_built)}): {', '.join(only_built[:40])}")

    # Spot-check PIT agreement on a few dates across the range.
    mismatches = 0
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    def built_contains(sym: str, on: date) -> bool:
        for spell in by_symbol.get(sym, []):
            if spell["effective_from"] <= on and (spell["effective_to"] is None or on <= spell["effective_to"]):
                return True
        return False

    probe_dates = [date(y, m, 15) for y in range(2018, 2027) for m in (3, 9)]
    shared = sorted(stored & built)
    for d in probe_dates:
        if d > date.today():
            continue
        diff = [s for s in shared if reg.contains(s, d, index_name) != built_contains(s, d)]
        if diff:
            mismatches += len(diff)
            print(f"  {d}: {len(diff)} symbol-level disagreements e.g. {', '.join(diff[:8])}")
    print(f"total PIT disagreements across probe dates: {mismatches}")
    return mismatches + len(only_stored) + len(only_built)


# ── CLI ─────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", default="NIFTY500", choices=sorted(INDEX_SPECS),
                   help="Index to reconstruct (default NIFTY500).")
    p.add_argument("--from-date", default=FROM_DATE,
                   help=f"Earliest period boundary (default {FROM_DATE}).")
    p.add_argument("--out", default=None,
                   help="Output CSV path (default data/membership_sources/<index>_history.csv).")
    p.add_argument("--verify", action="store_true",
                   help="Diff the result against index_membership in Turso.")
    p.add_argument("--limit", type=int, default=None,
                   help="Only process the N most recent candidate releases (smoke test).")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    header_pattern, slug = INDEX_SPECS[args.index]
    from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    today = date.today()

    sess = _session()
    archive = fetch_archive(sess)
    candidates = candidate_releases(archive, from_date)
    if args.limit:
        candidates = candidates[: args.limit]

    events: list[dict] = []
    for i, rel in enumerate(candidates, 1):
        if i % 25 == 0:
            logger.info("  ... %d/%d releases", i, len(candidates))
        text = fetch_pdf_text(sess, rel["url"])
        if not text:
            continue
        inc, exc = parse_changes(text, header_pattern)
        if not inc and not exc:
            continue
        eff = parse_effective_date(text) or rel["pr_date"]
        if eff < from_date or eff > today:
            continue
        events.append({"date": eff, "included": inc, "excluded": exc,
                       "title": rel["title"], "url": rel["url"]})

    logger.info("Change events affecting %s: %d", args.index, len(events))
    for e in sorted(events, key=lambda e: e["date"])[-8:]:
        logger.info("  %s  +%d/-%d  %s", e["date"], len(e["included"]),
                    len(e["excluded"]), e["title"][:60])

    current = fetch_current_roster(sess, slug)
    rows = build_intervals(current, events, from_date, today)

    out = Path(args.out) if args.out else SOURCES_DIR / f"{args.index.lower()}_history.csv"
    write_csv(rows, args.index, out)

    open_spells = sum(1 for r in rows if r["effective_to"] is None)
    print(f"\n{args.index}: {len(rows)} spells, {len({r['symbol'] for r in rows})} distinct symbols, "
          f"{open_spells} still current (expect ~{len(current)})")

    if args.verify:
        verify_against_turso(rows, args.index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
