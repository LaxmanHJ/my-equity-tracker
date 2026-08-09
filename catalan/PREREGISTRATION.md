# CATALAN — Pre-registration

> **STATUS: DRAFT.** Thresholds marked `TBD` are not yet set.
> **This file must be finalized and committed before the first scoring run.**
> Nothing in `catalan/scoring/` may be executed against the corpus until the
> `TBD`s are numbers and this banner is replaced with a commit hash and date.

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
- Threshold: `TBD` — set well below 93.3%; the point is "far from chance", not parity
- Minimum observations: `TBD` trading days

### C1 — Drift, gross of costs

Daily long-short **drift** (`open(t) → close(t)`) return on the forward log.

- Reported: mean daily return (bps), t-statistic with **date-clustered** standard errors, hit rate, annualized Sharpe
- Threshold: `TBD`
- Minimum observations: `TBD` trading days

### C2 — Drift, NET of costs. **THIS IS THE SHIP GATE.**

The same statistic after the cost model below, reported **per liquidity bucket**.

- Threshold: `TBD`
- **C1 without C2 is not a result.** No gross number is reported anywhere —
  API, UI, or write-up — without its net companion beside it.
- If the edge survives **only** in the most illiquid bucket, that is recorded as
  a **negative result**. Stated here, before any number is seen, so it cannot be
  re-argued later.

### Multiple-testing control

Deflated Sharpe over the number of declared cells. **The cell count is fixed in
advance**, here, and is not the number of things that turn out to have been
tried:

| Dimension | Values | Count |
|---|---|---|
| Model arm | Haiku 4.5, Sonnet 5, Opus 5 | 3 |
| Baseline arm | Loughran–McDonald, textblob_v1, finbert_v1 | 3 |
| Liquidity bucket | quintiles | 5 |
| Rebalance fraction | 100%, 25% | 2 |

Declared cell count for the deflation: `TBD` (fill in before scoring).

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
| Score mapping | YES → +1, UNKNOWN → 0, NO → −1 |
| Prompt | the paper's, verbatim — **transcribed 2026-08-10** from Section 5, p.5. Hash `1cc1a88738863a44`. |

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
5. Set every `TBD` threshold above
6. Declare the cell count for the deflated Sharpe
