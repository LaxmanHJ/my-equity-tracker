# CATALAN data by category — 113 NSE categories, 26,585 records

All 113 categories are in `data_category_analysis.csv`. The 40 largest are shown below (96.3% of
records).

**Column definitions**
- **Excluded now %:** removed by the study's current category/headline filter before scoring.
- **Dup %:** strict duplicates (see `data_quality_audit.md` §4).
- **Proj. filter drop %:** dropped by the project's own dedup rule, which over-matches (§4 of the
  audit).
- **No attachment %:** null or `"-"`.
- **Label-only %:** headline text, minus the company name, identical across ≥5 companies.
- **AI processed:** announcements with a real verdict.
- **AI calls:** real calls, including the re-scoring loop.
- **Signal %:** YES or NO, out of processed.
- **No-info signal %:** YES/NO verdicts whose own rationale says "routine / neutral / no material
  information". A lower bound: all 25 flagged in a 60-row hand check were correct, and at least 2
  of the 35 unflagged should have been flagged.
- **Signals on label-only %:** share of YES/NO verdicts given on a label-only headline.
- **Flip % (re-scored):** among announcements accidentally scored more than once, the share whose
  verdict changed between runs.

AI verdicts are **not** ground truth, and a signal rate is **not** evidence of predictive value.

| Category | Count | % total | Info type | Excluded now % | Dup % | Proj. filter drop % | No attachment % | Label-only % | AI processed | AI calls | Signal % | No-info signal % | Signals on label-only % | Flip % (re-scored) | Re-scored n |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Analysts/Institutional Investor Meet/Con. Call Updates | 3,918 | 14.7 | analyst_meet_notice | 0.0 | 8.1 | 20.1 | 1.1 | 71.8 | 2,697 | 6,897 | 23.0 | 62.3 | 23.8 | 43.1 | 295 |
| Shareholders meeting | 3,442 | 12.9 | shareholder_meeting | 0.0 | 0.4 | 6.8 | 0.8 | 65.7 | 2,189 | 4,508 | 82.1 | 85.8 | 70.0 | 50.6 | 162 |
| Copy of Newspaper Publication | 3,171 | 11.9 | routine_compliance | 100.0 | 1.0 | 5.5 | 4.0 | 56.3 | 0 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| General Updates | 3,037 | 11.4 | generic_unclassified | 0.1 | 2.4 | 14.3 | 1.4 | 27.6 | 2,152 | 4,862 | 64.5 | 47.1 | 11.9 | 46.0 | 189 |
| Outcome of Board Meeting | 1,973 | 7.4 | financial_results | 0.0 | 2.4 | 5.7 | 3.2 | 81.3 | 1,769 | 3,609 | 62.1 | 76.1 | 82.0 | 58.9 | 124 |
| Updates | 1,894 | 7.1 | generic_unclassified | 0.1 | 5.0 | 17.2 | 1.6 | 3.9 | 1,309 | 2,857 | 76.9 | 51.1 | 3.5 | 44.4 | 108 |
| Trading Window | 1,147 | 4.3 | routine_compliance | 100.0 | 0.0 | 0.6 | 0.2 | 100.0 | 0 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| Press Release | 1,029 | 3.9 | press_release | 0.0 | 0.3 | 4.6 | 2.1 | 1.9 | 791 | 1,820 | 87.9 | 23.5 | 2.6 | 23.9 | 71 |
| Appointment | 592 | 2.2 | management_change | 0.0 | 6.2 | 34.5 | 2.2 | 0.0 | 389 | 870 | 85.6 | 21.9 | 0.0 | 57.6 | 33 |
| Investor Presentation | 547 | 2.1 | investor_presentation | 0.0 | 2.7 | 22.1 | 3.8 | 100.0 | 483 | 1,019 | 53.0 | 41.0 | 100.0 | 83.8 | 37 |
| Record Date | 540 | 2.0 | corporate_action | 0.0 | 1.3 | 21.5 | 2.2 | 53.9 | 446 | 922 | 97.1 | 29.1 | 57.5 | 33.3 | 33 |
| Change in Management | 363 | 1.4 | management_change | 0.0 | 1.1 | 22.0 | 1.9 | 55.6 | 254 | 505 | 93.7 | 6.7 | 58.4 | 29.4 | 17 |
| Resignation of Director/KMP/SMP | 337 | 1.3 | management_change | 0.0 | 4.5 | 47.8 | 1.8 | 100.0 | 178 | 320 | 100.0 | 0.6 | 100.0 | 0.0 | 10 |
| Credit Rating | 291 | 1.1 | credit_rating | 0.0 | 0.7 | 9.6 | 1.4 | 100.0 | 172 | 425 | 16.9 | 51.7 | 100.0 | 55.6 | 18 |
| ESOP/ESOS/ESPS | 287 | 1.1 | esop | 0.0 | 1.4 | 13.9 | 0.3 | 0.0 | 173 | 315 | 96.5 | 9.6 | 0.0 | 70.0 | 10 |
| Monitoring Agency Report | 218 | 0.8 | routine_compliance | 0.0 | 0.0 | 6.0 | 5.0 | 38.1 | 215 | 648 | 20.0 | 25.6 | 2.3 | 20.7 | 29 |
| Resignation | 209 | 0.8 | management_change | 0.0 | 2.4 | 20.1 | 1.0 | 0.5 | 138 | 268 | 100.0 | 0.7 | 0.7 | 0.0 | 9 |
| Bagging/Receiving of orders/contracts | 199 | 0.7 | business_operations | 0.0 | 4.5 | 9.0 | 2.0 | 63.8 | 143 | 342 | 100.0 | 0.0 | 63.6 | 0.0 | 14 |
| Change in Director(s) | 197 | 0.7 | management_change | 0.0 | 2.0 | 39.6 | 3.0 | 100.0 | 120 | 238 | 100.0 | 0.0 | 100.0 | 0.0 | 8 |
| Allotment of Securities | 196 | 0.7 | fund_raising | 0.0 | 2.0 | 6.6 | 0.5 | 0.0 | 120 | 205 | 98.3 | 7.6 | 0.0 | 100.0 | 6 |
| Change in Auditors | 185 | 0.7 | management_change | 0.0 | 4.9 | 41.6 | 1.6 | 100.0 | 120 | 165 | 100.0 | 0.8 | 100.0 | 0.0 | 3 |
| Spurt in Volume | 183 | 0.7 | exchange_query | 0.0 | 0.0 | 15.8 | 51.4 | 0.0 | 110 | 180 | 90.0 | 0.0 | 0.0 | 60.0 | 5 |
| Acquisition | 160 | 0.6 | m_and_a | 0.0 | 0.6 | 14.4 | 1.9 | 35.6 | 114 | 227 | 86.8 | 4.0 | 40.4 | 37.5 | 8 |
| Clarification - Financial Results | 129 | 0.5 | financial_results | 0.0 | 0.0 | 0.0 | 100.0 | 0.0 | 129 | 171 | 98.4 | 0.0 | 0.0 | 0.0 | 3 |
| Price movement | 121 | 0.5 | exchange_query | 0.0 | 4.1 | 16.5 | 55.4 | 0.0 | 71 | 144 | 31.0 | 0.0 | 0.0 | 20.0 | 5 |
| Disclosure under SEBI Takeover Regulations | 112 | 0.4 | shareholding_insider | 100.0 | 3.6 | 8.0 | 0.0 | 78.6 | 0 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| Cessation | 107 | 0.4 | management_change | 0.0 | 0.9 | 29.9 | 3.7 | 0.0 | 68 | 110 | 100.0 | 1.5 | 0.0 | 0.0 | 3 |
| Action(s) taken or orders passed | 104 | 0.4 | regulatory_legal | 0.0 | 1.0 | 10.6 | 1.0 | 69.2 | 64 | 107 | 40.6 | 15.4 | 19.2 | 66.7 | 3 |
| Statement of deviation(s) or variation(s) under Reg. 32 | 104 | 0.4 | routine_compliance | 0.0 | 1.9 | 29.8 | 2.9 | 100.0 | 99 | 278 | 100.0 | 1.0 | 100.0 | 0.0 | 12 |
| Pendency of Litigation(s)/dispute(s) or the outcome impacting the Company | 103 | 0.4 | regulatory_legal | 0.0 | 5.8 | 8.7 | 3.9 | 49.5 | 75 | 146 | 89.3 | 7.5 | 56.7 | 0.0 | 5 |
| Corporate Insolvency Resolution Process | 96 | 0.4 | regulatory_legal | 0.0 | 5.2 | 7.3 | 4.2 | 31.2 | 60 | 203 | 98.3 | 1.7 | 28.8 | 0.0 | 10 |
| Reply to Clarification- Financial results | 90 | 0.3 | financial_results | 0.0 | 6.7 | 1.1 | 0.0 | 0.0 | 25 | 25 | 84.0 | 0.0 | 0.0 | 0.0 | 0 |
| Amendment to AOA/MOA | 75 | 0.3 | routine_compliance | 0.0 | 0.0 | 40.0 | 0.0 | 100.0 | 38 | 68 | 81.6 | 54.8 | 100.0 | 50.0 | 2 |
| Disclosure of material issue | 74 | 0.3 | generic_unclassified | 0.0 | 2.7 | 17.6 | 5.4 | 66.2 | 48 | 119 | 64.6 | 0.0 | 51.6 | 40.0 | 5 |
| Credit Rating- Revision | 73 | 0.3 | credit_rating | 0.0 | 0.0 | 13.7 | 0.0 | 64.4 | 45 | 144 | 64.4 | 3.4 | 58.6 | 42.9 | 7 |
| Agreements | 70 | 0.3 | business_operations | 0.0 | 5.7 | 17.1 | 2.9 | 35.7 | 46 | 104 | 60.9 | 0.0 | 0.0 | 75.0 | 4 |
| News Verification | 67 | 0.3 | exchange_query | 0.0 | 49.3 | 25.4 | 52.2 | 0.0 | 43 | 113 | 97.7 | 2.4 | 0.0 | 0.0 | 5 |
| Action(s) initiated or orders passed | 59 | 0.2 | regulatory_legal | 0.0 | 0.0 | 6.8 | 1.7 | 78.0 | 38 | 67 | 42.1 | 25.0 | 50.0 | 50.0 | 2 |
| Credit Rating- New | 56 | 0.2 | credit_rating | 0.0 | 0.0 | 17.9 | 0.0 | 64.3 | 40 | 83 | 35.0 | 35.7 | 21.4 | 33.3 | 3 |
| Corrigendum | 53 | 0.2 | correction | 0.0 | 1.9 | 13.2 | 1.9 | 9.4 | 34 | 78 | 82.4 | 64.3 | 3.6 | 66.7 | 3 |

## What stands out

**Dominant categories.** Four categories are 51% of all records:
- Con. Call Updates: 15%
- Shareholders meeting: 13%
- Newspaper copies: 12%
- General Updates: 11%

**Rare categories.** 54 categories have fewer than 20 records each, 354 records or 1.3% of the corpus. They
include Fraud/Default/Arrest, Delisting, Buyback, Bonus, Stock split, Open offer, Strikes and
Closure of operations. They're too few to judge, and several are highly material by nature. See
the report's "leave untouched" section.

**Unusually high duplication.** Duplicates are low almost everywhere. The exceptions are:
- Analyst/Investor Meet: 8%
- The generic Updates: 5%
- Exchange queries (Spurt in Volume / Price movement): up to 10% as info type `exchange_query`
- Shareholding/insider disclosures: 11%

The **project filter**, not duplication, is what removes most records in:
- Resignation of Director/KMP/SMP: 48%
- Change in Auditors: 42%
- Change in Director(s): 40%
- Amendment to AOA: 40%
- Appointment: 34%

These are mostly distinct events, removed because their headlines share a template with another
filing that day.

**Poor content (headline arm).** These categories have label-only headlines 100% of the time:
- Credit Rating, Investor Presentation
- Resignation of Director/KMP/SMP, Change in Director(s), Change in Auditors
- Statement of deviation, Trading Window

Outcome of Board Meeting is 81% label-only, and Con. Call Updates 72%. For these the headline
tells the model nothing about *this* company.

**The model gives directions anyway.**
- **Shareholders meeting:** 82% get YES/NO, and 86% of those verdicts' own rationales say "no
  material information".
- **Outcome of Board Meeting:** 76% of its YES/NO verdicts say the same.
- **Resignation / Change in Director(s) / Change in Auditors:** 100% get a verdict, all on
  label-only headlines, so these are category priors ("director changes typically create
  uncertainty"), not news.
- **Investor Presentation:** only 16% of re-scored items kept the same verdict. It's the least
  stable category.
