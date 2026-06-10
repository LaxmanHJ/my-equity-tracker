# Delivery-Spike Events — 2026-06-10 Study

Strategy-review item "delivery-spike": does an abnormal spike in delivery
percentage (shares taken to demat vs intraday churn — institutional
accumulation proxy) predict drift? Previously delivery_pct was only an ML
z-score feature; never a standalone event signal.

## Data

`delivery_data` was expanded 2026-06-10: **523,558 rows, 2019-01-01 →
2026-06-09, 335 symbols** (was 15 symbols before 2026-03). Source: NSE MTO
archive files. The backfill was rewritten for this: parallel CDN fetch +
serial **multi-row VALUES** upserts — Turso's pipeline has ~40ms
per-statement overhead, so one-statement-per-row `executemany` cost ~12s
per trading day vs ~1s for a single multi-row INSERT (11×; full 7-year
backfill ran in 17 min instead of ~11 h). libSQL is single-writer: 8
parallel batch-writers stall to zero throughput — never parallelize Turso
writes, parallelize fetches and write serially.

`python3 -m quant_engine.data.backfill_delivery --from 2019-01-01 --workers 8`

## Declared rule

Spike = delivery_pct z > 2 vs trailing 60 sessions (min 30 obs, own
history). Cohorts: SPIKE_UP (day return > +1%, accumulation-confirmed) and
SPIKE_DOWN (day return < −1%). Entry close(t+1); market-adjusted drift at
1/5/10/20d; monthly-clustered t. Ship bar |t| > 3 AND > 40bp.

## Results — the survivorship lesson, reproduced live

| Universe | Cohort | n | 5d AR / t | 10d AR / t | Verdict |
|---|---|---:|---|---|---|
| no-PIT | SPIKE_UP | 2,921 | **+85bp / t=3.3** | +122bp / 2.0 | "passes" the gate |
| **PIT** | SPIKE_UP | 1,807 | **+12bp / t=0.9** | +38bp / 1.1 | **FAIL** |
| PIT | SPIKE_DOWN | 3,178 | −28bp / −0.7 | −45bp / −0.9 | nothing |

Restricting events to dates of actual NIFTY-200 membership removes ~38% of
UP events and ~86% of the drift. The inflated cohort is events on stocks
**before** they entered the index — names that subsequently got promoted
have mechanically great trailing drift. This is the same +0.040→+0.003 IC
collapse the 2026-06-04 audit measured on the linear composite, reproduced
on a fresh signal in one afternoon. **The no-PIT version would have
shipped and lost money.**

**Verdict: FAIL on the declared gate.** Thread closed at this design. The
honest residue (+38bp at 10d, t=1.1, right-skewed) is not nothing — if it
ever gets revisited, it must be as a pre-registered interaction with
another conditioning variable, not a z/confirm threshold sweep.

## Project Usage

- Study: `quant_engine/research/delivery_spike.py` (`--pit` flag; PIT is
  the canonical run, results in `data/delivery_spike.json`)
- Data: `quant_engine/data/backfill_delivery.py` (parallel-fetch +
  multi-row-VALUES rewrite, 2026-06-10)
- **Rule for all future event studies: PIT filter is mandatory, not a
  robustness check.** `bulk_deal_drift.py` / `block_deal_counterparty.py`
  ran without it — their (negative) verdicts can only be biased *toward*
  finding edge, so the FAILs stand a fortiori.
