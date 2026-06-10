# Bulk/Block-Deal Drift — 2026-06-10 Event Study

Strategy-review item #3a: do NSE bulk/block deals predict post-disclosure
drift? Deals are disclosed by NSE after market close, tradeable from the
next session.

## Data

`bulk_block_deals` (Turso) was rebuilt 2026-06-10: **123,758 deals,
2019-01-01 → 2026-06-09**, via NSE's `historicalOR/bulk-block-short-deals`
API with `csv=true` (the JSON variant caps at one day per call). Two data
bugs found and fixed during the rebuild:

1. **UTF-8 BOM** in NSE CSVs corrupted the first column name → all dates
   landed empty. Fixed with `encoding="utf-8-sig"`.
2. **UNIQUE key collapsed churner legs** — the original
   `(date, symbol, client, deal_type)` key kept only one of a client's
   same-day BUY and SELL rows, biasing any net-flow computation. Key now
   includes trade side, quantity, and price.

Backfill: `python3 -m quant_engine.data.backfill_bulk_deals --from 2019-01-01`

## Declared rule

Event = (symbol, date) where one side dominates the day's deal value:
|net/gross| > 0.5 (excludes same-day churners — Graviton-style quant desks
crossing both sides). Entry close(t+1); outcome = stock − NIFTY abnormal
return to t+1+h, h ∈ {1,5,10,20}; t-stat clustered by event month.
Ship bar: BUY side |t| > 3 and drift > 40bp.

## Results (`data/bulk_deal_drift.json`)

13,579 dominance events from 124k deals; **only 606 (4.5%) touch our
price universe** — bulk deals skew to micro-caps outside Nifty-200.

| Cohort | n | 1d AR (med) | 5d | 10d | 20d | best t |
|---|---:|---|---|---|---|---|
| BULK_BUY | 186 | +44bp (+1bp) | −64bp (−78bp) | −122bp (−92bp) | −139bp | 1.3 |
| BULK_SELL | 420 | +2bp | +26bp | +26bp | +25bp | 0.9 |

**Verdict: FAIL — no exploitable drift.** Nothing approaches the |t| > 3
bar. Directionally, bulk-BUY events on our universe are followed by
*under*performance beyond day 1 (hit rate 42–45%) — consistent with the
audit's momentum-trap finding: names that attract disclosed bulk buying
have already run. The thread is closed at this design; do not re-run with
tweaked dominance thresholds.

## Structural findings (worth more than the result)

1. **Block deals are untestable by side-dominance**: every block deal is a
   negotiated cross (buyer row + seller row, equal value) → dominance ≡ 0 →
   all 7k block rows are filtered out by construction. Testing block-deal
   information requires classifying the *counterparties* (`client_name`:
   FII/MF buyer vs promoter seller). That is a different, follow-up design —
   the client-name data is now in the table for all 7 years.
2. **Coverage is the binding constraint** (4.5%): a bulk-deal strategy on
   the investable universe has ~80 actionable BUY events/year. Any future
   event study on this table should consider whether widening price
   coverage (small-cap backfill via Angel) is worth it first.

## 2026-06-10 Follow-up: Block-Deal Counterparty Study — ALSO FAIL

The open follow-up was run the same day: `quant_engine/research/
block_deal_counterparty.py`, results in `data/block_deal_counterparty.json`.
Declared keyword taxonomy over `client_name`: ACTIVE (MF / insurer /
pension / SWF / hedge fund — the hypothesis cohort), DESK (bank/ODI swap
desks — control), PASSIVE (ETF/index plumbing — control). Event = net
class-level dominance > 0.5 per (symbol, date), same drift machinery.

| Cohort | n | 1d | 5d | 20d | Verdict |
|---|---:|---|---|---|---|
| **ACTIVE_BUY** (hypothesis) | 141 | +6bp | +60bp (t=−0.6) | +97bp (t=0.1) | **No drift** |
| DESK_BUY (control) | 97 | −27bp | +56bp | +83bp | Nothing |
| DESK_SELL (control) | 62 | +103bp (hit 81%) | +191bp (t=2.4) | +213bp (t=2.0) | Below bar; control cohort; noise-level given 24 reported cells (null max |t| ≈ 2.9) |

**FAIL on the declared gate** (ACTIVE_BUY |t| > 3). The DESK_SELL curiosity
is explicitly NOT promoted — selecting the best control cell post-hoc is the
exact multiple-testing trap the 2026-06-04 audit closed. If anyone wants to
chase it, it must be pre-registered as its own hypothesis (mechanism: ODI
desk net-sell may proxy offshore swap-client positioning) on fresh data.

## Project Usage

- Studies: `quant_engine/research/bulk_deal_drift.py`,
  `quant_engine/research/block_deal_counterparty.py`
- Data rebuild: `quant_engine/data/backfill_bulk_deals.py` (historical mode
  added 2026-06-10)
- Both threads closed. The bulk/block deal table remains valuable as a
  feature source (e.g., crowding flags), just not as a standalone signal.
