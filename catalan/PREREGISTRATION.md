# CATALAN — Pre-registration

> **STATUS: THRESHOLDS SET 2026-08-20.**
>
> **Sequencing, stated plainly because it is the whole point.** Scoring began
> before this file was finalised: 7,884 headlines carried a verdict when these
> numbers were fixed. That is a deviation from the original plan and it is
> recorded rather than hidden.
>
> **It does not compromise the pre-registration, for a specific and checkable
> reason: not one gate statistic had been computable.** Turso `price_history`
> ends 2026-08-04 and the forward log begins 2026-08-07 — the two do not
> overlap, so no scored headline had ever been joined to a return. No hit rate,
> no drift, no Sharpe existed to be seen. The thresholds below were therefore
> set blind to every quantity they judge, which is the only property that
> matters.
>
> Anyone auditing this can verify it from git history: the panel builder
> (`catalan/data/panel.py`) landed 2026-08-19 and the price-data gap is recorded
> in `catalan/README.md` under "Known data limits" from the same commit.

This is the single most important sequencing decision in the project. R2
produced a gate PASS gross of costs and then died on costs. Declaring the bar
before seeing the number is what prevents that repeating — and the only way
that works is if the declaration is dated earlier than the result.

---

## What is being tested

Do Claude's readings of NSE corporate-announcement headlines predict the
announcing stock's **same-session drift** (`open(t) → close(t)`), and does that
prediction survive Indian round-trip trading costs?

Replicates Lopez-Lira & Tang, JFE 184 (2026) 104335. US benchmarks: 93.3% daily
hit rate on the initial reaction, 34 bps/day gross on drift, 2.97 annualized
Sharpe, **unprofitable at 20 bps round trip**.

## The two arms, and which one counts

| Arm | Corpus | Status |
|---|---|---|
| **Forward log** | announcements collected after the study started | **The only ship-gate evidence.** |
| Full history | 2019 → now | Exploratory. Used to *measure* contamination, never to claim an edge. |

Claude Opus 5's training cutoff is May 2026 and today is past it. A 2019→2026
backtest scored by Opus 5 is a model recalling what happened, not predicting it.
The forward arm is immune by construction, which is why it alone clears C2.

---

## Gates

### C0 — Comprehension. **Not a trading claim.**

Long-short **initial-reaction** (`close(t-1) → open(t)`) portfolio hit rate on
the forward log, over overnight announcements in the PIT NIFTY500.

This leg is **not tradable** — you cannot trade before the news prints. C0 is a
pipeline smoke test: it verifies that entity linkage, session classification and
the returns join all work. Failing C0 means something upstream is broken, **not**
that the market is efficient.

- Paper's US figure: 93.3%
- **Threshold: daily long-short hit rate ≥ 70%**
- **Minimum observations: 40 trading days**

40 days at 70% is 28/40, one-sided binomial *p* = 0.0083 against a fair coin —
comfortably inside a 1% bar. Deliberately far below the paper's 93.3%: C0 asks
"is the pipeline wired correctly", not "does India match the US". A pass means
entity linkage, session classification and the returns join work. **A fail means
something upstream is broken and C1/C2 must not be evaluated at all** — it is
not evidence about market efficiency.

C0 is evaluable long before C1/C2 and should be run as soon as 40 sessions of
overlapping price data exist, precisely so a broken pipeline is caught early
rather than after a year of collection.

Supplementary diagnostic, reported alongside but carrying no gate authority:
the **per-announcement** initial-reaction hit rate. At ~107 directional
in-universe overnight names/day it reaches tight bounds far faster than the
daily portfolio statistic, which makes it the better early warning — but the
portfolio form is what the paper reports, so the portfolio form is the gate.

### C1 — Drift, gross of costs

Daily long-short **drift** (`open(t) → close(t)`) return on the forward log.

- Reported: mean daily return (bps), t-statistic with **date-clustered** standard errors, hit rate, annualized Sharpe
- **Threshold: mean daily long-short drift > 0 with t ≥ 2.0** (date-clustered SE)
- **Minimum observations: 120 trading days** — see *Horizon and stopping rule*

C1 is a **necessary, not sufficient** condition. It gates nothing on its own and
is never reported without C2 beside it.

### C2 — Drift, NET of costs. **THIS IS THE SHIP GATE.**

The same statistic after the cost model below, reported **per liquidity bucket**.

**Threshold — all four must hold simultaneously:**

| # | Condition | Why it is here |
|---|---|---|
| 1 | mean daily net LS drift **> 0 with t ≥ 2.0**, date-clustered SE | statistical significance |
| 2 | mean daily net LS drift **≥ 5 bps** | economic materiality |
| 3 | **net annualized Sharpe ≥ 0.5** | materiality, scale-free |
| 4 | **Deflated Sharpe > 0.95** over the declared cells | multiple testing |

- **Minimum observations: 120 trading days.**
- Conditions 2 and 3 exist because with enough days, t ≥ 2 can certify an edge
  far too small to trade. R2 is the cautionary case in the other direction — it
  passed gross and died on costs — and a bar that certifies 1 bp/day would be
  the same mistake wearing different clothes. At N = 120 these floors are
  **insurance, not the active bar**: they correspond to daily Sharpe 0.028 and
  0.032 against the 0.183 that condition 1 demands.
- **C1 without C2 is not a result.** No gross number is reported anywhere —
  API, UI, or write-up — without its net companion beside it.
- If the edge survives **only** in the most illiquid bucket, that is recorded as
  a **negative result**. Stated here, before any number is seen, so it cannot be
  re-argued later.

### Multiple-testing control

Deflated Sharpe over the number of declared cells. **The cell count is fixed in
advance**, here, and is not the number of things that turn out to have been
tried.

**Primary endpoint — the ship gate.** One configuration, declared now:

> the **primary model arm** (`config.PRIMARY_MODEL`, Haiku 4.5), at **100%
> rebalancing**, reported across the **5 liquidity buckets**.

**Declared cell count for the deflation: K = 5.**

**Secondary endpoints — reported, but they gate nothing.** The other two model
arms, the three non-LLM baselines, and the 25% rebalancing variant. 55 further
configurations, all reported for completeness and none of them able to clear C2.
A secondary cell that looks better than the primary is **not** promotable: that
is the multiple-testing failure this section exists to prevent, and swapping the
primary after seeing results would void the pre-registration.

**Why 5 and not 60.** An earlier draft crossed model arms with baseline arms
(3 × 3 × 5 × 2 = 90), which is arithmetically wrong — a headline is scored by
*one* scorer, so the six scorers are alternatives, not a product. The honest
full-grid count is 6 × 5 × 2 = 60. Deflating over all 60 while only one
configuration can actually clear the gate is a self-inflicted penalty: it raises
the required effect from 58% of the paper's to 158% of it, turning a ~2-month
study into a 7.5-month one at best and a 2.5-year one if the Indian effect is
half the US figure. Primary/secondary endpoint separation is the standard
answer and is what is declared here.

### Horizon and stopping rule

**C1 and C2 are evaluated exactly once, at 120 trading days of forward log with
usable price data. There is no second look and no extension.**

This is the rule the rest of the document depends on. Without a fixed horizon
the natural behaviour — keep collecting, re-check, stop when the t-statistic
crosses 2 — is optional stopping, and it inflates the false-positive rate
without leaving a trace in the reported numbers. A pre-registration that omits
it is decoration.

Three consequences, all accepted in advance:

1. **"Inconclusive" is a declared outcome, not a failure to be worked around.**
   If |t| < 2.0 at 120 days the study reports *underpowered — no result* and
   stops. It does not keep collecting until significance appears.
2. **A near-miss is a miss.** t = 1.9 at day 120 is inconclusive. Extending to
   day 140 to see whether it crosses is exactly the prohibited move.
3. **The 120-day clock starts when price data and the forward log overlap**, not
   when collection started. As of 2026-08-20 the overlap is **zero days** — see
   the blocker note below.

**Declared statistical power, so "inconclusive" is interpretable.**

Taking the paper's own ratio (34 bps/day at 2.97 annualized ⇒ daily Sharpe
0.187, implying ~182 bps/day long-short volatility):

| Constraint at N = 120 | Minimum detectable daily Sharpe | As % of the paper's effect |
|---|---|---|
| t ≥ 2.0 | 0.183 | **98%** |
| Deflated Sharpe, K = 5 | 0.109 | 58% |

**t ≥ 2.0 is the binding constraint, and 120 days can only detect an effect
essentially as strong as the US result** — about 33 bps/day. An Indian effect at
70% of the paper's would need ~233 days and will read as inconclusive here; at
half, ~457 days. This horizon was chosen with that limitation understood. It
buys a fast, clean answer to *"is there a US-sized effect in India?"* and
explicitly does not answer *"is there a smaller one?"*

A future study may ask the smaller question. It must do so under a **new
pre-registration with its own horizon** — not by extending this one.

### Blocker on the clock

`price_history` ends **2026-08-04**; the forward log begins **2026-08-07**. They
do not overlap, so **zero of the 120 days have been accrued** despite 8 days of
headlines being collected and scored. The clock cannot start until the price
side is repaired (Angel One → Turso). Recorded here because a horizon that
silently never starts is the same failure as no horizon at all.

### Contamination test (paper Online Appendix C)

Holding the category (`desc`) mix fixed, does a model score **better on
pre-cutoff than post-cutoff** headlines?

Holding the mix fixed matters: NSE announcement composition drifts over time, so
an unconditional pre/post comparison confounds memorisation with topic drift.

A pre-cutoff advantage quantifies memorisation and **caps what the historical
arm may claim**. Training cutoffs must be read from Anthropic's documentation
per model before the arms are assigned — assumed cutoffs invalidate this test.

---

## Cost model — declared now, not fitted later

Zerodha MIS intraday round trip, per leg-pair:

| Component | Rate |
|---|---|
| Brokerage | ₹20/order or 0.03%, whichever lower × 2 orders |
| STT | 0.025% on the sell side (intraday) |
| Exchange transaction | ~0.00297% × 2 |
| Stamp duty | 0.003% buy side |
| SEBI turnover | ₹10 per crore |
| GST | 18% on (brokerage + exchange txn) |

Explicit costs land around **4–6 bps round trip** at meaningful order size.

**The explicit part is not the risk — spread and impact are.** The paper's
strategy dies at 20 bps and its turnover was ~190%/day. Two India-specific facts
make our exit worse than the paper's:

1. **NSE's close is a VWAP of 15:00–15:30, not a call auction.** The paper
   explicitly justified flat-bps costs because both its legs cleared in auctions
   with no bid-ask spread. That justification does not hold on our exit — the
   exit pays real spread.
2. The short leg is executable intraday (MIS), which matters because the paper
   finds the short leg carries most of the alpha (26 bps vs 8 bps daily). **But**
   NSE bans intraday shorting in trade-to-trade (T2T) and surveillance (ASM/GSM)
   names. Those are flagged per-day and excluded from the short leg. A short-leg
   result computed without that filter is not reportable.

Therefore:

- Spread/impact is estimated **per liquidity bucket** from `price_history` and
  traded volume. One flat number across the cross-section is not acceptable.
- Results are reported per bucket, because the paper's alpha concentrates in
  small caps — exactly where impact is worst.
- The paper's §6.2 partial-rebalancing variant is included (25% rebalancing cut
  turnover 190%→46%/day and *improved* net Sharpe at 10 bps).

---

## Frozen study parameters

Set in `catalan/config.py`; changing any of them after scoring begins
invalidates the run.

| Parameter | Value |
|---|---|
| Universe | point-in-time NIFTY500 |
| Overnight window | after 15:30 IST on t−1, or before 09:00 IST on t |
| Initial reaction | `close(t−1) → open(t)` — not tradable |
| Drift | `open(t) → close(t)` — the tradable leg |
| Dedup | same symbol, same day, Damerau–Levenshtein similarity > 0.6 → keep earliest |
| Category exclusions | **NOT YET FROZEN** — must be decided against the full 105–122 value `desc` distribution, not a single month's top-12 |
| Headline-text exclusions | **NOT YET FROZEN** — `config.HEADLINE_EXCLUSIONS`. Matched on the text, so a row reading "… Copy of Newspaper Publication" is dropped whatever `desc` NSE assigned it. Caught 0 extra rows on 2026-08-07; a guard against vendor-category drift, not a present leak. |
| Score mapping | YES → +1, UNKNOWN → 0, NO → −1 |
| Prompt | the paper's, verbatim — **transcribed 2026-08-10** from Section 5, p.5. Hash `1cc1a88738863a44`. |
| Liquidity buckets | 5, within-date quintiles by 20-day ADV. **Orientation: 1 = LEAST liquid … 5 = MOST liquid.** |
| ADV measure | **rupee turnover** (`close × volume`), 20 sessions, `min_periods` = the full window |
| Corporate-action guard | `abs(init_ret) > 0.20` → null `init_ret` **and** `prev_close`; retain `open`, `close`, `drift_ret` |
| `prev_close` | the immediately preceding **global trading session's** close, or NULL. **No forward-fill.** |
| Phantom-session floor | a date is a session only if its breadth ≥ `max(20, 0.25 × median breadth)` over the window |
| Weekly-cadence guard | per symbol, rolling-10 median bar gap > 1.6 sessions → row dropped entirely |
| Flat-bar guard | `open == high == low == close` → row dropped entirely |

### Panel construction — declared consequences

These are stated here because each one decides which rows enter C0/C1/C2, and
because two of them are known, uncorrected biases.

**The corporate-action guard costs the ship gate nothing.** On an ex-date all
four of a session's prices are already quoted in the post-action basis, so only
the comparison to the *previous* close crosses the basis change. `drift_ret =
close(t)/open(t) − 1` is unaffected. The guard therefore protects only the
untradable C0 leg. It must never be "simplified" into dropping the row: that
would delete drift observations on exactly the highest-news-intensity days.

**Declared residual — ex-dividends and rights issues are not caught.** A 1–2%
dividend yield sits far inside the 20% band, so `init_ret` carries a small
negative bias on ex-dates. This is stated rather than silently corrected,
because correcting it would require a corporate-actions table this project does
not have.

**Declared consequence — `prev_close` is not forward-filled.** If a stock was
halted on t−1, `open(t)/close(t−2) − 1` is a two-day return wearing an
overnight label. Those rows get a NULL `init_ret` instead. Losing an
observation is cheap; a mislabelled horizon in the C0 leg is not.

**Declared consequence — the pre-2025-03-17 contamination is dropped, not
adjusted.** `price_history` rows from before that cutover were weekly
aggregated OHLC, and earlier RapidAPI-era rows copied one price into all four
OHLC fields (giving `drift_ret` an *exact* 0.0 — a fake zero in the tradable
leg, worse than noise because it shrinks the mean toward zero). Both are
dropped. This means the historical arm's row count is materially reduced in a
way that is **not random across time**, which is one more reason the forward
log, not the backtest, is the ship gate.

### Observed panel behaviour (2026-08-19, dry run over 2019-01-01 → 2026-08-04)

Recorded because it was measured before any gate was evaluated, and because it
revises an assumption this document was drafted under.

| Statistic | Value |
|---|---|
| Sessions / symbols / rows fetched | 1,880 / 840 / 1,283,881 |
| Rows written | 1,279,354 (99.6%) |
| Rows in PIT NIFTY500 | 921,518 (71.8% of written) |
| Dropped — flat bar | 4,227 (0.33%) |
| Dropped — phantom session | 161 (0.01%) |
| Dropped — weekly cadence | 139 (0.01%) |
| Corporate-action suppressions | 25 in 7.6 years |

**The weekly-aggregate contamination this document anticipated is essentially
absent.** 139 rows, not a large non-random fraction. The guard stays — it costs
nothing and a future re-backfill could reintroduce the problem — but the
historical arm is not disqualified on data quality. It remains **exploratory
only**, for the separate and unchanged reason that model training cutoffs fall
inside the window.

**`price_history` appears to be split-adjusted**, contrary to
`backfill_bhavcopy.py:33`. Verified against TATASTEEL 1:10 (ex 2022-07-29),
WIPRO 1:3 bonus (2019-03) and IRCTC 1:5 (ex 2021-10-29): none shows a basis
jump. This makes `init_ret` more trustworthy than assumed, and makes the
corporate-action guard near-vacuous rather than load-bearing. **The declared
ex-dividend residual above is unaffected** — dividend adjustments are a separate
question from split adjustment and remain uncorrected.

**Fewer than 5 eligible symbols on a date → that date gets NULL buckets**,
never 1..k for k < 5. Assigning 1..4 would file a mid-cap under "bucket 1 =
most illiquid", and bucket 1 is the cell this document reads its negative
verdict off.

## Observed corpus behaviour (2026-08-10, n=225, Haiku 4.5)

Recorded here because it was measured *before* thresholds were set, and it
constrains what those thresholds can reasonably be.

| Statistic | Value |
|---|---|
| Announcements, 2026-08-07 | 1,286 across 659 symbols |
| Inside PIT NIFTY500 | 411 (32.0%) |
| After category exclusions + dedup | ~900/day |
| Verdict split | 23.4% YES, 38.0% UNKNOWN, 37.6% NO |
| Parse-miss rate | ~1% (abstain, retried next run) |

61% of headlines receive a directional verdict, so there is real cross-sectional
dispersion to form portfolios on. The 32.0% in-universe share is **below the
~44% the study plan probed for June-2026** — one day is not a month and 07-Aug
was peak results season, but if it persists the corpus projections (~500k rows
2019→2026) are overstated.

## Open items blocking finalization

1. Verify documented training cutoffs for Haiku 4.5 / Sonnet 5 / Opus 5
2. ~~Transcribe the paper's prompt verbatim~~ — done 2026-08-10
3. Freeze the `desc` exclusion list
4. Source T2T / ASM / GSM daily lists for the shortability flag
5. ~~Set every `TBD` threshold above~~ — done 2026-08-20
6. ~~Declare the cell count for the deflated Sharpe~~ — done 2026-08-20 (K = 5)
7. Repair the price/forward-log overlap so the 120-day clock can start
