# Point-in-time universe — survivorship-bias correction (P2-c)

**Status**: infrastructure landed; NIFTY200 ingested (367 rows, hand-built);
automated scraper landed 2026-08-03 but its NIFTY200 control run does **not**
yet reproduce the hand-built data — see § Automated sourcing below. NIFTY500
(research item R2) is blocked on closing that gap.
**Branch**: `claude/audit-ml-pipeline-afml-37M7V` commits
`quant_engine/data/membership.py`, `quant_engine/data/backfill_membership.py`,
`quant_engine/tests/test_membership.py`.

## The problem

`price_history` holds whatever symbols ingestion has seen — today that's
208 (NIFTY200 + 8 legacy). Every research path that filters "Nifty 200"
implicitly uses **today's** roster, silently excluding the stocks that
were members in 2018–2024 but have since been removed (often *because*
they failed). For Indian equities the published estimate is **2–5 %/year
of overstated IR** on EM-equity backtests.

The ML audit (`wiki/concepts/ml_audit_2026_05_21.md`) listed this as S8
and as P2-c on the rewrite plan. Until the PIT universe is in place, no
backtested Sharpe in this codebase is fully honest.

## What landed

A separate `index_membership` table (one row per
`(symbol, effective_from, effective_to)` interval) with:

* `quant_engine/data/membership.py` — schema + `MembershipRegistry` with
  O(log n) PIT queries (`contains`, `members_on`, `all_symbols`).
* `quant_engine/data/backfill_membership.py` — CLI ingest. Reads a CSV,
  validates intervals (inverted dates, dupes, overlaps), writes to Turso.
* 21 assertion tests covering boundary inclusion, multi-spell rejoiners,
  index-name namespacing, CSV format tolerance, end-to-end CSV → PIT query.

The infrastructure works; the **data is what's missing**.

## Automated sourcing (2026-08-03)

`quant_engine/data/scrape_niftyindices.py` reconstructs membership from NSE
Indices press releases, so the CSV stops being a hand-built artifact with no
provenance. Written for R2 (NIFTY500), validated against NIFTY200.

**The source is far better than the "~30 PDFs, manual scraping, tedious" note
below suggested.** `https://www.niftyindices.com/media` returns the **entire**
press-release archive — 1440 PDFs back to 1998 — in one static HTML response.
No pagination, no auth, no XHR; the month dropdown filters client-side. PDF URLs
are `/Press_Release/ind_prs<DDMMYYYY>.pdf`. Current rosters are published exactly
at `IndexConstituent/ind_nifty{500,200}list.csv`. (`/reports/press-release` is a
404 — the archive lives under `/media`.)

**Method.** Press releases carry change *events*, not rosters, so the scraper
anchors on today's published roster and walks **backward**:

    roster_before(review) = roster_after(review) − included + excluded

Inter-event periods then collapse into `(effective_from, effective_to)` spells.
Walking back from a known endpoint beats walking forward from a guessed base —
there is no authoritative historical roster to start from.

**Parsing gotcha worth remembering.** Section markers use two conventions:
`2) Nifty 500` (index numbered directly) and `1) Replacements on account of
semi-annual review…` → `l) Nifty 200` (index as a lettered child). Matching only
the numeric form dropped five semi-annual reviews, and since the walk runs
backward, **each missed event corrupts every earlier period** — the signature is
disagreement that grows the further back you look.

### Control run — NOT yet clean

NIFTY200 is the control: it was assembled independently, so the scraper must
reproduce it before NIFTY500 output can be trusted.

| | built | stored |
|---|---|---|
| ever-members | 332 | 337 |
| spells | 363 | 367 |

~224 symbol-date disagreements across 15 probe dates (**≈4.5%**), concentrated
before 2022 (32 in 2018 → 2 in 2024; 2025 clean). Known causes:

- **Renames are invisible to the method.** GMRINFRA→GMRAIRPORT,
  MOTHERSUMI→MOTHERSON are symbol changes with no index event, so the walk sees
  a stock that vanishes and one that appears.
- **The stored data is not ground truth either** — it contains `DUMMYVEDL1-4`
  placeholder rows, so a diff is not automatically a scraper bug.
- Some events remain unparsed; the pre-2022 residual is the evidence.

**Before shipping any NIFTY500 result**: either close the pre-2022 gap or scope
the study to 2022+, where the reconstruction is near-exact. Re-run
`--index NIFTY200 --verify` after every parser change.

    python -m quant_engine.data.scrape_niftyindices --index NIFTY200 --verify
    python -m quant_engine.data.scrape_niftyindices --index NIFTY500

PDFs cache under `data/membership_sources/press_release_cache/`, so re-parses
cost nothing.

## What's still needed

The CSV that the CLI ingests. The data has to come from external sources;
there is no API that gives us "Nifty 200 constituents on day X" for free.

### Recommended sources (in order of completeness)

1. **CMIE Prowess** (paid) — "Index constituents history" export gives
   every addition / deletion with reason. Single best source for Indian
   index history. If you have a Prowess subscription this is a one-day
   job: export and clean.
2. **NSE corporate-actions archive** —
   `https://nsearchives.nseindia.com/archives/equities/CAS/` publishes
   daily files with delistings, suspensions, mergers. Use to derive
   `effective_to` for retired symbols. Free but tedious.
3. **Nifty Indices methodology PDFs** — semi-annual index review press
   releases at <niftyindices.com> list additions / deletions at each
   rebalance. Manual scraping ~30 PDFs covers 2018-2026.
4. **BSE delisted list** (free) —
   `https://www.bseindia.com/Markets/equity/EQReports/delistdetails.aspx`
   — cross-check that you haven't missed any failed companies.

### CSV format

```
symbol,effective_from,effective_to,source,notes
RELIANCE,2018-01-01,,nifty_indices,
DHFL,2018-01-01,2019-09-25,NSE_CA_archive,delisted post bankruptcy
YESBANK,2018-01-01,2024-09-30,NSE_CA_archive,removed at semi-annual review
```

`effective_to` blank means "still a member as of today." Date format
`YYYY-MM-DD` preferred; `YYYY/MM/DD`, `DD-Mon-YYYY`, `DD-MM-YYYY` also
accepted.

`source` and `notes` are free-form — record where you got it for audit.

### Run

```bash
# 1. Dry-run: validate the CSV without writing
python -m quant_engine.data.backfill_membership --from-csv nifty200_history.csv

# 2. Apply to Turso
python -m quant_engine.data.backfill_membership --from-csv nifty200_history.csv --apply

# 3. Verify
python -m quant_engine.data.backfill_membership --status
```

## Hooking it into the research path

Once the table is populated, the diagnostic / trainer / meta-labeler need
to swap their `load_all_symbols()` calls (which today returns today's
roster) for `MembershipRegistry.members_on(date)` (PIT query). That's a
followup commit — keeping it separate keeps the infrastructure PR
independent of the data PR.

Suggested call sites to update:

* `quant_engine/ml/trainer.py :: build_training_dataset` — iterate per
  date, restrict the cross-section to that date's PIT members.
* `quant_engine/ml/diagnostic.py :: build_dataset_with_horizons` — same.
* `quant_engine/ml/meta_labeler.py :: run_meta_diagnostic` — inherits via
  `build_dataset_with_horizons`.
* `quant_engine/data/loader.py :: load_all_symbols` — add a
  `as_of: date | None = None` parameter; when set, return
  PIT membership.

## Acceptance criteria

The fix is "done" when:

1. `index_membership` has rows for every Nifty 200 spell from 2018-01-01
   to today, including delistings.
2. `diagnostic.py` produces an updated `ml_diagnostic.json` with a
   visibly different aggregate IC vs. the current (survivorship-biased)
   version. The published wisdom is the new number should drop by
   roughly the survivorship premium (2-5 %/year ⇒ ~0.001-0.003 IC).
3. The wiki paper `wiki/papers/lopez_de_prado_afml_2018.md` records the
   pre / post numbers under "Project Usage."

## Expected impact

The ml_regression track's pre-PIT IC is +0.0122 at 20d. If the
survivorship premium for our universe-period is ~2 %/year that translates
to roughly 0.002 IC of bias — meaning the honest IC is likely ~0.010.
That changes the deflated-Sharpe verdict at the margin:

| | Pre-PIT (today) | Post-PIT (estimate) | Decision |
|---|---|---|---|
| ml_regression deflated SR | 1.53 | ~1.2 | Borderline → still promotable |
| linear deflated SR | 0.96 | ~0.7 | Not significant → keep as primary |
| ml deflated SR | 0.88 | ~0.6 | Not significant → telemetry only |

The order doesn't change; the absolute numbers move down. The PIT fix
won't change *which* track wins, but it will move the absolute deflated
Sharpe of the leader from "comfortable" to "borderline" — exactly the
direction that should make us more conservative about sizing.
