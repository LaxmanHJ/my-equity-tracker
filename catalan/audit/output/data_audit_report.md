# CATALAN data audit — final report

**Dataset.** 26,585 NSE corporate announcements, 2026-08-07 → 2026-09-25 (35 day files, 2,370
symbols, 113 categories), plus 36,519 AI score rows. Audit run 2026-09-27.

**Method.** Read-only and reproducible with `python3 -m catalan.audit.data_audit`, which writes
every table and number to `catalan/audit/output/`. No prices or returns were read, so **nothing
here is evidence of predictive value**. AI verdicts were analysed as outputs, not as ground truth.
Details: `data_quality_audit.md`, `data_category_analysis.md`, `data_value_analysis.md`.

**The one-line answer.** Of everything collected:
- **~52% is obvious or probable low-value.** 16.7% is already excluded, and a further 35.4% is
  procedural.
- **~3% is duplicate.**
- **~23% is priority material.** For about a third of that (results, ratings), the value is only
  reachable through the PDF, not the headline.
- **Separately, 8.3% are distinct events the project's own dedup filter wrongly throws away**
  before scoring. That's lost value, not noise.

The record-level data is clean. The problems are in the **pipeline and instrument**, not in what
NSE sent.

---

### 1. Is the 2-month dataset clean enough to continue using?

**Yes as a corpus; not as-is for the study.**

**Record-level integrity is good:**
- 0 key collisions and 0 unparseable announcement timestamps.
- 1.4% exact duplicates and 4.6% of records with any core defect.

**Four issues make the *processed* data unfit to count toward the pre-registered test:**
- **The dedup filter over-matches.** It drops 13.3% of records before scoring, and 63% of those
  drops don't even share the kept record's category. In a hand-read sample, 18 of 22 drops were
  distinct events.
- **Headline input is label-only for 52% of records.** The model sees no company-specific
  information, yet still gives a direction to 54% of those.
- **Verdicts are unstable at default temperature.**
  - The scorer sets no temperature, so Haiku runs at 1.0.
  - Of 1,334 announcements scored about 15 times each, **42% changed verdict** and 222 received
    both YES and NO for the identical input.
- **Coverage gaps and an ambiguous clock:**
  - 13 of 14 weekend days and two Fridays are missing.
  - `ingested_at` is UTC for 34 of 35 files but stored without a zone next to IST timestamps.

**Scope limits of this window:**
- Only 26.6% of records are NIFTY500 companies, and 63% are symbols with no price data at all.
- Collection began after most large caps had reported Q1: only 145 NIFTY500 results filings are
  in the log.

**Verdict:** keep it as the declared **pilot**, and start the counted period fresh after the fixes
in Q10.

### 2. What percentage of records have quality problems?

**4.6% (1,226 records) have a record-level defect:**
- 480 records lack the attachment fields. All are in the 08-08 file, which was archived before
  those columns existed.
- 380 exact duplicates.
- 367 ZIP attachments instead of PDFs, 331 of them director/KMP resignations.
- 19 headlines are empty (e.g. `regarding ''`).
- Timestamp defects: 0. Missing ISIN: 0. Invalid ISIN: 27 (22 placeholders).

**Field-level (not a record defect):** `industry` is empty for 62.4% (NSE supplies it for ~40% of
records every day), and `has_xbrl` duplicates "has attachment" exactly.

**Pipeline-level:** 2,218+ distinct announcements (8.3%) are wrongly removed by the dedup filter.

### 3. What percentage is duplicate or repetitive?

- **Duplicate: 3.1% (815).** 380 exact, 180 near-duplicates on the same day, and 255 probable
  re-filings within 3 days (an upper bound).
- **Repetitive (one event, several filings):**
  - 13.1% of scorable records are an extra filing of the same type on the same company-day.
  - 807 records echo a results announcement on the same day (press release, investor
    presentation, other board outcomes).
  - Together: **at most ~17% of scorable records** (the two sets overlap).
- **On the AI side, 55% of all actual calls (19,210 of 34,866) were repeats** of already-scored
  announcements, caused by the rebuild bug. That bug is fixed in the working tree but not yet
  pushed.

### 4. What percentage appears to be obvious low-value or junk content?

- **Already excluded: 16.7% (4,434).** Newspaper copies, trading-window closures, takeover (SAST)
  disclosures.
- **Obvious, beyond that: 20.4% (5,433).** Procedural types *and* a label-only headline. Of their
  YES/NO verdicts, 81% have a rationale admitting no information.
- **Probable: up to 35.4% (9,417)** for all procedural types: analyst-meet notices and
  recordings, AGM/shareholder filings, routine compliance. 76% no-information verdicts.
  - It contains real exceptions: see Q11.

So: **37% obvious, up to 52% including the probable set.**

### 5. Which categories contain the most useful information?

**For the headline arm**, ranked by headline specificity, the rate of verdicts that don't admit
ignorance, and stability:

| Category | Specific headlines | Informative verdicts | Stability | Other |
|---|---|---|---|---|
| **Press releases** | 94% | 67% | 76% | 13% name an amount |
| **Orders / contracts / capacity / launches** | 63% | — | — | 3% no-information verdicts |
| **M&A and restructuring** | 87% | — | — | 7% no-information verdicts |
| **Fund raising** | 74% | — | — | 7% no-information verdicts |
| **Regulatory / legal** | 52% | — | — | 9% no-information verdicts |

Management changes help only when the headline names the person (Appointment and Resignation are
~100% specific; the generic "Resignation of Director/KMP/SMP" is 0%).

**For the PDF arm**, also **financial results** and **credit-rating actions**. Their headlines are
64% and 83% label-only, but the substance is in the document. They're the worst headline inputs
and among the best document inputs.

### 6. Which categories appear to be mostly noise?

- **Already excluded:** newspaper copies (3,171) and trading-window closures (1,147).
- **Analyst/investor-meet schedule notices** (2,884). 67% are label-only, and the sampled PDFs
  are 1-page covering letters (19 of 19).
- **AGM / shareholder-meeting procedural filings** (4,137): notices, voting results, scrutiniser
  reports, proceedings. 85% of their YES/NO verdicts admit no information.
- **Routine compliance still being scored** (1,199 non-excluded): monitoring-agency reports,
  statements of deviation, RTA / BRSR notices.
- **Investor-presentation headlines.** 100% label-only, and only 16% of re-scored ones kept their
  verdict.

### 7. Which categories consume AI processing without producing much useful output?

| Type | Processed | Actual calls | Signal % | No-info signal % | Signals on label-only % |
|---|---|---|---|---|---|
| Shareholder meetings | 2,691 | 5,398 | 81.3 | **85.4** | 57.7 |
| Analyst-meet notices + recordings | 2,813 | 7,229 | 16–31 | **58–76** | 20–24 |
| Financial results (headline only) | 1,585 | 3,294 | 79.0 | **66.1** | **67.7** |
| Board-meeting outcomes (non-results) | 552 | 988 | 24.8 | 54.7 | 43.8 |
| Routine compliance (non-excluded) | 946 | 2,209 | 62.8 | 56.2 | 27.9 |
| **Re-scoring repeats (all types)** | — | **19,210** | — | — | — |

The model does not abstain on empty inputs. It returns **NO** on shareholder-meeting filings 71% of the time,
while its own rationale calls them "routine" and "neutral". That's a systematic negative bias on
procedural filings (a measured fact about outputs; its market meaning is untested).

### 8. Are there specific document types that should be prioritised for AI processing?

**Yes.** For the PDF arm, first priority:
- financial results
- press releases
- orders / contracts / operations
- M&A and restructuring
- fund raising
- credit-rating actions (new or revised)
- regulatory and legal
- corporate actions with amounts (dividends, buybacks, bonus, splits)

These are 23.4% of records.

For the headline arm, prioritise company-specific headlines (44% of records) and treat label-only
ones as "no input".

Whether to restrict to the NIFTY500 is a **scope** decision for the amendment, not a quality
finding:
- 74% of actual calls went to non-NIFTY500 companies.
- 63% of records are symbols with no price data, which can never be matched to a return.

### 9. How much of the current AI workload could potentially be reduced?

Call counts are actual; dollar amounts are **estimates** from text length (no per-call usage was
recorded).

| Scenario | Calls | vs actual | Est. cost |
|---|---|---|---|
| Actual | 34,866 | — | $14.49 |
| Score each announcement once (bug fix, already written) | 15,656 | −55.1% | $6.50 |
| …minus strict duplicates | 15,323 | −56.1% | $6.36 |
| …minus procedural types (T2) | 9,206 | −73.6% | $3.87 |
| …minus label-only headlines (**headline arm only**) | 7,711 | −77.9% | $3.34 |
| …restricted to NIFTY500 (**scope decision**) | 2,240 | −93.6% | $0.97 |

**The bug fix alone halves the workload.** Deprioritising procedural filings takes it to roughly
a quarter.

### 10. What data-quality issues need to be fixed before continuing?

Ordered by impact.

1. **Push the re-scoring fix.** `archive.rebuild` now replays through `_write_scores`. Without it,
   each daily run pays for about 1,300 repeats.
2. **Replace the dedup rule.** It's a frozen study setting, so the change belongs in the
   amendment. Compare company-stripped text, require the same category, use a ~0.9 threshold, and
   tell templated headlines apart by attachment size or hash.
3. **Pin the instrument's randomness.**
   - Set `temperature=0` for Haiku, which accepts it; the paper used 0. Or score k times and take
     the majority.
   - 42% of re-scored headlines flipped, and 222 went YES↔NO.
   - This changes how scoring works, so it goes in the amendment as a new arm.
4. **Close the collection gaps:**
   - Put the weekend cron on `main`.
   - Make a failed collection *alert* someone (09-04 and 09-11 failed silently).
   - Run before 09:15 IST.
5. **Record `ingested_at` with an explicit zone**, or convert it to IST. It's UTC-naive today, next
   to IST-naive timestamps.
6. **Handle ZIP attachments** (367, mostly resignations) in the PDF extractor, or declare them
   headline-only.
7. **Keep headline-arm and PDF-arm rules separate.** "Drop label-only headlines" is correct for
   one and wrong for the other.
8. **Decide the universe scope in the amendment.** Stop scoring companies that can never be
   matched to a return, or declare why they're scored anyway.

### 11. What should remain untouched because the evidence is insufficient?

- **Rare categories.** 54 categories have fewer than 20 records (354 in all): fraud / default /
  arrest, delisting, buyback, bonus, split, open offer, strikes, closure of operations, and more.
  Also the Corporate Insolvency process category (96). Too few to judge, and often highly
  material. **Keep collecting and scoring.**
- **Earnings-call transcripts.** Substantive but stale, and their content lies beyond a 3-page
  window. Don't drop them until the PDF arm shows whether they add anything.
- **AGM notices.** Mostly procedural, but some carry special business (fund raising,
  related-party approvals) that the headline can't reveal.
- **Exchange queries** (price movement, spurt in volume, news verification). The text is
  specific, but the information is in the company's *reply*, a separate filing. Their value is
  unclear.
- **"General Updates" / "Updates"** (18% of records). Heterogeneous and 72–96% specific.
  They can't be judged as a class.
- **Per-type PDF conclusions.** The sample is 145 documents, with n < 10 for most types. It
  supports corpus-level statements only.
- **The tier boundaries and the no-information proxy.** Both are defined from measurements but
  involve judgement. The proxy is a lower bound: 25 of 25 flagged verdicts were correct, and it
  missed at least 2 of 35.
- **Anything about predictive value.** No returns were examined.

---

## Facts vs hypotheses

**Measured facts:**
- all counts and percentages
- timestamp semantics (from the upload times embedded in URLs)
- the dedup filter's category-mismatch rate
- verdict flip rates
- label-only shares
- call volumes

**Sample-based:**
- the dedup filter's precision (22 pairs)
- classifier accuracy (150)
- the no-information proxy's precision (60)
- everything about PDF content (145)

**Hypotheses:**
- why the dedup rule was set at 0.6 (US newswire calibration)
- that label-only verdicts are category priors (strongly implied, but not proven per record)
- the redundancy estimate (it depends on the analytical typing)
- all dollar figures (token estimates)
