# CATALAN data value — information types, redundancy, scorecard

**Scope.** Read-only. Nothing was excluded or changed. The value judgements below are about
*information content and processing efficiency*, **not** about whether any type predicts returns.
No returns were examined (NO-PEEK rule), and AI verdicts are not ground truth.

## 1. Analytical information types (Step 7)

Every record gets one of 20 analytical types. These are analysis labels only, not a schema
change.
- **How:** categories with a specific meaning map directly. The generic ones (General Updates,
  Updates, Shareholders meeting, Outcome of Board Meeting, Con. Call Updates) are typed from
  headline keywords (rules in `data_audit.py`).
- **Validation:** a hand-read, random *sample* of 150 records, read before the final rule tweak.
  - About 95% of classified records were typed plausibly (about 6 clear mistypes).
  - 15 (10%) were unclassified. 9 of those are genuinely uninformative headlines ("General
    Updates", bare "Disclosure under Reg 30"), and 6 were routine items the rules missed (BRSR
    reports, RTA notices, interest payments).
  - Rules for those routine patterns were then added, so accuracy on that sample is slightly
    flattered from here on.

| Info type | Count | % total | Dup % | Same symbol-day extra % | Label-only % | Specific headline % | Amount in headline % | AI processed | AI calls | Signal % | No-info signal % | Signals on label-only % | YES % | NO % | Flip % (re-scored) | Re-scored n | PDF sample n | PDF median pages | PDF unreadable % | Est. cost $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| routine_compliance | 5,517 | 20.8 | 1.2 | 10.4 | 59.5 | 39.8 | 0.1 | 946 | 2,209 | 62.8 | 56.2 | 27.9 | 6.4 | 56.3 | 32.6 | 86 | 14 | 11.0 | 7.1 | 1.0 |
| shareholder_meeting | 4,137 | 15.6 | 0.4 | 19.0 | 54.8 | 33.6 | 0.0 | 2,691 | 5,398 | 81.3 | 85.4 | 57.7 | 10.0 | 71.4 | 49.2 | 189 | 28 | 13.0 | 21.4 | 2.2 |
| analyst_meet_notice | 2,884 | 10.8 | 11.2 | 13.1 | 66.7 | 32.5 | 0.0 | 1,692 | 3,211 | 30.5 | 57.8 | 20.5 | 7.6 | 22.9 | 43.9 | 107 | 19 | 1.0 | 0.0 | 1.3 |
| management_change | 2,242 | 8.4 | 3.9 | 36.4 | 43.4 | 56.6 | 0.1 | 1,444 | 2,855 | 93.2 | 9.3 | 44.2 | 21.3 | 71.9 | 29.9 | 97 | 15 | 10.5 | 6.7 | 1.2 |
| generic_unclassified | 2,029 | 7.6 | 3.5 | 8.6 | 43.4 | 55.6 | 0.8 | 1,391 | 3,296 | 52.9 | 46.6 | 25.4 | 9.8 | 43.1 | 49.6 | 133 | 11 | 2.0 | 18.2 | 1.3 |
| financial_results | 1,683 | 6.3 | 1.2 | 7.8 | 63.8 | 35.6 | 0.1 | 1,585 | 3,294 | 79.0 | 66.1 | 67.7 | 10.5 | 68.5 | 56.5 | 115 | 12 | 11.0 | 8.3 | 1.4 |
| analyst_meet_record | 1,199 | 4.5 | 0.3 | 1.3 | 74.4 | 25.2 | 0.2 | 1,121 | 4,018 | 15.7 | 75.6 | 23.9 | 2.6 | 13.1 | 43.8 | 203 | 11 | 16.0 | 0.0 | 1.6 |
| press_release | 1,074 | 4.0 | 0.4 | 3.0 | 2.7 | 93.9 | 13.2 | 819 | 1,863 | 87.7 | 22.8 | 2.5 | 58.7 | 28.9 | 23.6 | 72 | 8 | 3.0 | 0.0 | 0.8 |
| corporate_action | 763 | 2.9 | 1.4 | 11.1 | 38.1 | 39.2 | 7.7 | 615 | 1,349 | 96.7 | 27.4 | 41.8 | 60.8 | 35.9 | 21.6 | 51 | 3 | 1.0 | 0.0 | 0.6 |
| board_meeting_outcome | 736 | 2.8 | 5.3 | 9.0 | 73.4 | 24.3 | 1.5 | 552 | 988 | 24.8 | 54.7 | 43.8 | 6.0 | 18.8 | 50.0 | 30 | 5 | 4.0 | 0.0 | 0.4 |
| business_operations | 719 | 2.7 | 3.1 | 4.6 | 37.3 | 62.7 | 6.5 | 474 | 1,091 | 89.7 | 3.3 | 35.1 | 68.8 | 20.9 | 11.6 | 43 | 2 | 3.5 | 0.0 | 0.4 |
| m_and_a | 641 | 2.4 | 3.7 | 8.0 | 13.4 | 86.6 | 6.6 | 418 | 1,104 | 86.8 | 7.4 | 15.2 | 46.2 | 40.7 | 37.5 | 48 | 1 | 3.0 | 0.0 | 0.5 |
| investor_presentation | 547 | 2.1 | 2.7 | 3.3 | 100.0 | 0.0 | 0.0 | 483 | 1,019 | 53.0 | 41.0 | 100.0 | 25.9 | 27.1 | 83.8 | 37 | 7 | 31.0 | 42.9 | 0.4 |
| fund_raising | 466 | 1.8 | 3.6 | 8.2 | 7.5 | 73.8 | 8.8 | 290 | 674 | 94.5 | 6.6 | 6.6 | 49.3 | 45.2 | 55.6 | 27 | 1 | 5.0 | 0.0 | 0.3 |
| credit_rating | 459 | 1.7 | 0.7 | 5.4 | 83.4 | 16.6 | 1.3 | 280 | 718 | 32.5 | 30.8 | 56.0 | 6.8 | 25.7 | 48.4 | 31 | 5 | 1.0 | 0.0 | 0.3 |
| regulatory_legal | 418 | 1.6 | 3.6 | 7.2 | 47.6 | 52.4 | 0.2 | 273 | 658 | 74.4 | 8.9 | 33.5 | 2.2 | 72.2 | 18.5 | 27 | 2 | 2.5 | 0.0 | 0.3 |
| exchange_query | 391 | 1.5 | 9.7 | 18.7 | 4.1 | 95.9 | 9.2 | 232 | 445 | 72.4 | 1.2 | 1.8 | 10.3 | 62.1 | 26.7 | 15 | 1 |  | 0.0 | 0.2 |
| esop | 360 | 1.4 | 1.1 | 3.9 | 4.7 | 47.2 | 2.8 | 225 | 395 | 94.7 | 9.4 | 3.8 | 42.2 | 52.4 | 66.7 | 12 | 4 | 1.5 | 0.0 | 0.2 |
| shareholding_insider | 252 | 0.9 | 11.1 | 10.0 | 38.5 | 57.9 | 0.4 | 86 | 198 | 80.2 | 15.9 | 11.6 | 0.0 | 80.2 | 37.5 | 8 | 0 |  |  | 0.1 |
| correction | 68 | 0.3 | 4.4 | 5.9 | 7.4 | 72.1 | 0.0 | 39 | 83 | 79.5 | 58.1 | 3.2 | 0.0 | 79.5 | 66.7 | 3 | 1 | 7.0 | 0.0 | 0.0 |

**Reading the table**
- **Specific headline %** is the headline arm's ceiling. If the text is shared with ≥5 companies,
  the model knows nothing about *this* company except its name.
- **Signals on label-only %** is the share of YES/NO verdicts that can only be a category prior.
- **Flip %** covers the 1,334 announcements the re-scoring bug scored about 15 times each. It is
  the model's run-to-run instability on the *same* input at default temperature.
- **PDF columns** come from the 145-document random *sample*. n < 10 is insufficient evidence.

## 2. Evidence tiers (Steps 9–10)

The tiers are defined by measured properties, not by category names. They are **candidates** for
reprioritisation; nothing was excluded.

| Tier | Definition | Records | % records | Actual AI calls | % calls | Calls if scored once | Signal % | No-info signal % | Label-only % |
|---|---|---|---|---|---|---|---|---|---|
| T1 | Already excluded by the study filter (newspaper copies, trading window, SAST) | 4,434 | 16.7 | 0 | 0.0 | 0 | — | — | 68.1 |
| T2 | Procedural types: analyst-meet notices and records, shareholder meetings, routine compliance | 9,417 | 35.4 | 14,836 | 42.6 | 6,450 | 53.9 | **75.8** | 57.7 |
| T2a | …of which the headline is also label-only | 5,433 | 20.4 | 8,659 | 24.8 | 3,681 | 42.8 | **80.9** | 100 |
| T3 | Priority types: results, press releases, orders/operations, M&A, fund raising, legal, corporate actions, ratings | 6,221 | 23.4 | 10,751 | 30.8 | 4,754 | 82.5 | 32.1 | 38.0 |
| T4 | Everything else: management changes, ESOP, exchange queries, generic updates, … | 6,513 | 24.5 | 9,279 | 26.6 | 4,452 | 66.4 | 23.6 | 45.9 |

**Why T2 is low-value.** The evidence:
- 58% of its headlines are label-only, and 76% of its YES/NO verdicts come with a rationale
  saying there is no information.
- In the PDF *sample*, analyst-meet notices are 1-page covering letters (19 of 19, median ~440
  tokens), and AGM notices and proceedings are procedural.

**Exceptions inside T2 that are not empty:**
- **Earnings-call transcripts** (1,199 records typed `analyst_meet_record`) contain management
  commentary, median ~11,500 tokens. But they are stale: filed days after a public call. And the
  first 3 pages are preamble, beyond what a 3-page window would read.
- **AGM notices with special business** (e.g. fund raising or related-party approvals) exist, and
  the headline can't identify them.

**Why T3 is the priority, and the key nuance.**
- T3 headlines are the most specific (press releases 94%, M&A 87%, fund raising 74%), and the
  no-info share is low (3–9% for operations, M&A, legal and fund raising).
- **Financial results and credit ratings are the exception.** Their headlines are 64% and 83%
  label-only, and 66% / 31% of their verdicts admit no information. **Their substance is entirely
  in the PDF.**
  - They are the *worst* inputs for headline scoring and among the best for PDF scoring.
  - A rule that drops label-only headlines is therefore right for the headline arm and **wrong**
    for the PDF arm.

## 3. Redundancy — one event, many filings (Step 11)

- **Multi-filing days:** of 22,151 scorable records (after T1), 56.2% sit on a symbol-day with at
  least one other filing (4,450 of 14,158 symbol-days).
- **Same type, same symbol-day:** 2,909 records (13.1%) are an *additional* filing of the same
  analytical type on the same symbol-day.
- **Results chains:** on 1,550 results symbol-days, **807 records** are echoes of the same event
  (press release 303, investor presentation 259, other board-meeting outcomes 181, analyst-meet
  notices 64).
- **Most common same-day pairs:**
  - results + management change: 353 (board meetings approve both)
  - routine compliance + shareholder meeting: 332
  - corporate action + shareholder meeting: 307 (AGM + dividend record date)
  - results + press release: 290
  - results + investor presentation: 251
- **The AGM chain across days:** notice → newspaper copy → book closure / record date → voting
  results → scrutiniser's report → proceedings. That is 5–7 filings for one routine event.

**Estimated redundant information sent downstream** (hypothesis-grade, because it depends on the
typing): at least the 807 results echoes plus the 2,909 same-type extras, **about 3,700 records
(~17% of scorable)**. The two sets overlap, so the true figure is lower than the sum. On top of
that, **55% of all actual AI calls were pure repeats** of already-scored announcements (the
re-scoring bug).

## 4. Scorecard (Step 12)

Scorecard **method**:
- Categories with ≥150 non-excluded records are scored on six raw metrics.
- Each metric gets a band from thresholds fixed in `SCORECARD_BANDS` before the table was looked
  at. There is **no composite score**, deliberately, because the metrics trade off differently for
  the headline and PDF arms.

The six metrics:
- **Completeness:** an attachment is present, or absent by design (exchange queries). High ≥98,
  Low <90.
- **Uniqueness:** 100 − strict duplicate %. High ≥97, Low <90.
- **Specific headline:** text not shared (after masking dates and numbers) by ≥5 companies. High
  ≥70, Low <30.
- **Informative signal:** processed items with a YES/NO whose rationale doesn't admit "no
  information". High ≥60, Low <30.
- **Stability:** re-scored items (n ≥ 10) whose verdict never changed. High ≥75, Low <50.
- **Validity:** all categories are ≥99%, so it isn't shown.

**Two warnings on reading it:**
- **Informative signal** is inflated where headlines are label-only. The model rarely *admits*
  ignorance about resignations or director changes; it applies a confident prior instead. Always
  read it next to Specific headline.
- **Stability** can be high for the wrong reason: label-only resignations are 100% stable because
  they are always NO.

| Category | Records | Info type | Completeness % | Band | Uniqueness % | Band  | Specific headline % | Band   | Informative signal % | Band    | Stability % | Band     | Re-scored n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Analysts/Institutional Investor Meet/Con. Call Updates | 3,918 | analyst_meet_notice | 98.9 | High | 91.9 | Med | 27.4 | Low | 8.7 | Low | 56.9 | Med | 295 |
| Shareholders meeting | 3,442 | shareholder_meeting | 99.2 | High | 99.6 | High | 20.3 | Low | 11.7 | Low | 49.4 | Low | 162 |
| General Updates | 3,034 | generic_unclassified | 98.6 | High | 97.6 | High | 72.4 | High | 34.1 | Med | 54.0 | Med | 189 |
| Outcome of Board Meeting | 1,973 | financial_results | 96.8 | Med | 97.6 | High | 17.4 | Low | 14.8 | Low | 41.1 | Low | 124 |
| Updates | 1,893 | generic_unclassified | 98.4 | High | 95.0 | Med | 96.1 | High | 37.6 | Med | 55.6 | Med | 108 |
| Press Release | 1,029 | press_release | 97.9 | Med | 99.7 | High | 94.6 | High | 67.3 | High | 76.1 | High | 71 |
| Appointment | 592 | management_change | 97.8 | Med | 93.8 | Med | 100.0 | High | 66.8 | High | 42.4 | Low | 33 |
| Investor Presentation | 547 | investor_presentation | 96.2 | Med | 97.3 | High | 0.0 | Low | 31.3 | Med | 16.2 | Low | 37 |
| Record Date | 540 | corporate_action | 97.8 | Med | 98.7 | High | 18.7 | Low | 68.8 | High | 66.7 | Med | 33 |
| Change in Management | 363 | management_change | 98.1 | High | 98.9 | High | 44.4 | Med | 87.4 | High | 70.6 | Med | 17 |
| Resignation of Director/KMP/SMP | 337 | management_change | 98.2 | High | 95.5 | Med | 0.0 | Low | 99.4 | High | 100.0 | High | 10 |
| Credit Rating | 291 | credit_rating | 98.6 | High | 99.3 | High | 0.0 | Low | 8.1 | Low | 44.4 | Low | 18 |
| ESOP/ESOS/ESPS | 287 | esop | 99.7 | High | 98.6 | High | 39.7 | Med | 87.3 | High | 30.0 | Low | 10 |
| Monitoring Agency Report | 218 | routine_compliance | 95.0 | Med | 100.0 | High | 59.6 | Med | 14.9 | Low | 79.3 | High | 29 |
| Resignation | 209 | management_change | 99.0 | High | 97.6 | High | 99.5 | High | 99.3 | High |  |  | 9 |
| Bagging/Receiving of orders/contracts | 199 | business_operations | 98.0 | High | 95.5 | Med | 36.2 | Med | 100.0 | High | 100.0 | High | 14 |
| Change in Director(s) | 197 | management_change | 97.0 | Med | 98.0 | High | 0.0 | Low | 100.0 | High |  |  | 8 |
| Allotment of Securities | 196 | fund_raising | 99.5 | High | 98.0 | High | 55.6 | Med | 90.8 | High |  |  | 6 |
| Change in Auditors | 185 | management_change | 98.4 | High | 95.1 | Med | 0.0 | Low | 99.2 | High |  |  | 3 |
| Spurt in Volume | 183 | exchange_query | 100.0 | High | 100.0 | High | 100.0 | High | 90.0 | High |  |  | 5 |
| Acquisition | 160 | m_and_a | 98.1 | High | 99.4 | High | 64.4 | Med | 83.3 | High |  |  | 8 |

## 5. AI cost impact (Step 13)

- **Call counts are actual:** real archived calls.
- **Dollar figures are estimates:** tokens estimated from the actual text lengths (≈4 characters
  per token) at claude-haiku-4-5 list prices ($1 / $5 per million input/output tokens). No
  per-call usage was recorded.
- About $0.00042 per headline call. The actual spend is not in the data, so this is not a claim
  about the bill.

| Scenario | Calls | Reduction vs actual | Est. cost | Note |
|---|---|---|---|---|
| A. Actual (archived real calls) | 34,866 | — | $14.49 | includes the re-scoring loop |
| B. Each announcement scored once | 15,656 | 55.1% | $6.50 | fixing the rebuild bug alone |
| C. B minus duplicates | 15,323 | 56.1% | $6.36 | strict duplicates |
| C′. B minus T2 procedural types | 9,206 | 73.6% | $3.87 | candidate; see T2 exceptions |
| D. C minus label-only headlines | 7,711 | 77.9% | $3.34 | **headline arm only**, never the PDF arm |
| E. D restricted to PIT NIFTY500 | 2,240 | 93.6% | $0.97 | a **study-scope** decision, not data quality |

Two more calls-weighted facts:
- **74% of actual calls (25,665) were on companies outside the NIFTY500 on the day.** The study
  never uses those in its primary test.
- **63% of records are for symbols with no price data at all**, so they can never be matched to a
  return.
