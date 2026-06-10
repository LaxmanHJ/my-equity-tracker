# PEAD — Post-Earnings Announcement Drift (SIC-93, 2026-06-10)

The seventh and final declared experiment of the 2026-06-10 research program,
and the only one with genuine economic structure in the result.

## Data — `earnings_events` (new table)

NSE serves timestamped quarterly-results broadcasts via
`/api/corporates-financial-results?index=equities&from_date=&to_date=&period=Quarterly`
(session-cookied, JSON). Backfilled 2019-01-01 → today by
`quant_engine/data/backfill_earnings.py`: **76,536 filings**, ~2,357 symbols,
each with `broadcast_ts` to the second (e.g. INFY Q3-FY24 =
`2024-01-11T18:19:23` — matches the known date; most results broadcast
after market close).

**Coverage (after same-day follow-ups):** 96k+ filings, 2018→2026, uniform
~10–15k/yr. Two sources, both in `backfill_earnings.py`:
- legacy `corporates-financial-results` (2018→early-2025; pre-2018 exists
  but is partial AND the membership registry only starts 2018-01-01)
- `integrated-filing-results?type=Integrated Filing- Financials&size=25000`
  (SEBI integrated format, 2025+; default page size 20 — `size` param
  returns the full window)

## Declared rule

Event = first broadcast per (symbol, period_end). Reaction day R = broadcast
≤ 15:30 on a bar day → same session, else next session. Surprise proxy =
reaction-day market-adjusted return, terciled per calendar quarter. Entry
close(R); market-adjusted drift at 5/20/60d; monthly-clustered t; **PIT
mandatory**. Ship bar: T3 (positive surprise) |t| > 3 AND > 40bp. Nine cells
reported jointly (null max |t| ≈ 2.5).

## Results (`data/pead_drift.json`, 4,738 PIT events, 309 symbols)

| Tercile | n | 5d AR / t / hit | 20d AR / t / hit | 60d AR / t / hit |
|---|---:|---|---|---|
| T1 (neg surprise) | 1,587 | **−59bp / −2.9 / 43%** | −18 / −1.1 / 46% | +69 / 0.7 / 47% |
| T2 (mid) | 1,572 | −0 / 0.5 / 48% | +73 / 0.5 / 51% | +189 / 1.3 / 50% |
| T3 (pos surprise) | 1,579 | +31 / 0.2 / 50% | **+132bp / 2.6 / 53%** | +190 / 0.8 / 52% |

## Verdict: FAIL on the declared gate — narrowly, and with real structure

T3@20d reaches t = 2.6 against the declared bar of 3.0. By pre-registration
discipline this does **not** ship. But unlike the six prior experiments,
the result has exactly the shape the PEAD literature predicts:

- **Monotone across terciles** at 5d and 20d (hit rates 43→48→50 and
  46→51→53) — noise doesn't produce ordered cohorts.
- **Sign-correct on both tails**: negative surprises drift down short-term
  (T1@5d −59bp, t=−2.9), positive surprises drift up over a month.
- **Economically large**: T3 long-only +132bp per 20d hold (vs 40bp cost
  bar); T3−T1 spread ≈ +149bp@20d.

The t falling just short is plausibly a sample-size problem: the 2025+
integrated-filings gap costs ~1.5 years (~1,000 PIT events ≈ +20% n).

## Sample extensions — the marginal t resolved AGAINST the signal

Two legitimate "more data, identical rule" extensions were run same-day:

| Sample | PIT events | T3@20d | T1@5d |
|---|---:|---|---|
| 2019→early-2025 (legacy only) | 4,738 | +132bp / t=2.6 | −59bp / −2.9 |
| + integrated filings 2025–26 | 5,486 | +125bp / **t=2.9** | −55bp / −2.9 |
| + legacy 2018 (membership epoch) | 6,298 | +83bp / **t=2.2** | −58bp / **−3.5** |

The first extension strengthened the long side (2.6→2.9, as a real effect
would scale); the second **weakened it** (2.9→2.2) — adding 2018 (mid/small-
cap bear year) showed the positive-surprise drift is regime-dependent, not
structural. A stable effect grows with √n; this one didn't.

**FINAL VERDICT (2026-06-10): CLOSED — FAIL on the declared gate** (T3
|t| > 3; best honest reading is t=2.2 at 20d). The statistically strongest
cell on the full sample is the SHORT side — T1 negative-surprise 5d drift
−58bp, t=−3.5 — but it was not the declared hypothesis, is not tradeable
long-only, and 5-day single-stock shorting costs (SLB/futures roll) consume
~the entire 58bp. Recorded, not promoted.

Do NOT re-run with different terciles, horizons, or bars. If PEAD is ever
revisited, the pre-registered design must be regime-conditioned (e.g.,
positive-surprise drift in non-bear regimes only) on data not used here —
and membership history pre-2018 would need backfilling first.

## Project Usage

- Backfill: `quant_engine/data/backfill_earnings.py`
  (`python3 -m quant_engine.data.backfill_earnings --from 2019-01-01`)
- Study: `quant_engine/research/pead_drift.py` (PIT built-in, not optional)
- Linear: SIC-93. Results JSON: `data/pead_drift.json`.
