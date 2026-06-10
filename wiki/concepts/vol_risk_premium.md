# NIFTY Volatility Risk Premium — SIC-92 (2026-06-10, phase 1)

The eighth experiment of the 2026-06-10 program and the first whose core
claim SURVIVED measurement. Structurally different from the seven failures:
it harvests a documented insurance premium instead of predicting
cross-sectional returns.

## Data upgrade that made it possible

India VIX history was extended **2022-03 → 2011-06** (2,649 missing dates)
from Yahoo `^INDIAVIX` via `backfill_regime.py --from-yahoo`. NSE's own VIX
API serves only ~4 trailing years (pre-2022 → HTTP 503); Yahoo
cross-validated against the NSE overlap at **0.012% median |diff|** on
1,025 days before any write (NSE stays authoritative; only gaps filled).
The sample now contains the 2020 COVID spike (VIX 72–84) — non-negotiable
for any short-vol claim.

## Declared design (phase 1 = measurement, not a strategy)

- VRP_t = VIX_t − fwd 21-trading-day realized vol of NIFTY (vol points)
- Inference: overlap-adjusted t (t_daily/√21); harvest on non-overlapping
  21d cycles: short variance at VIX², P&L/vega ≈ (IV²−RV²)/(2·IV), net of
  0.8 vol-pt round trip
- Gate: mean VRP > 0 with |t| > 3 AND net Sharpe > 0.5 AND worst-cycle
  feasibility for a capped structure

## Results (`data/vol_risk_premium.json`, 3,617 days / 173 cycles, 2011–2026)

**The premium exists, decisively:** mean +2.8 vol pts (median +3.4),
**positive on 80.3% of days, overlap-adjusted t = 5.84**, positive in 15 of
16 calendar years (2026 YTD is the only negative at −1.75).

**The naive harvest fails, instructively:**

| Harvest (non-overlap cycles) | mean/cycle | ann. Sharpe | hit | worst cycle |
|---|---|---|---|---|
| Gross | +1.72 pts | 0.59 | 80.3% | −119 pts |
| Net (0.8 pt costs) | +0.92 pts | **0.31** | 76.3% | **−119.8 pts** |

One cycle — entered 2020-03-02 at VIX 25.2, realized 81.4 — loses ~11 years
of average net harvest (11 pts/yr). Uncapped short variance is ruin in
exactly the way the literature says. By-VIX-tercile (reported jointly, not
selected): low 0.79 / mid 0.99 / high 0.19 net Sharpe — the tail lives in
high-VIX entries. Do NOT cherry-pick the mid-VIX cell (DESK_SELL discipline).

**Phase-1 verdict: premium confirmed (gate 1 passed at t=5.84); naive
harvest rejected (gate 2 failed at 0.31); capped-loss structures are the
only path — and their cap cost cannot be derived from VIX, only from real
option chains.**

## Phase 2 — forward chain collection (LIVE as of 2026-06-10)

Historical NIFTY chains aren't freely available, so collection started
immediately:

- `src/services/optionChainService.js` — Angel One `optionGreek` endpoint;
  expiry discovered dynamically (candidates in the 20–45 DTE window —
  NSE has shuffled expiry weekdays, nothing hardcoded; first live discovery
  found a TUESDAY expiry, 07JUL2026). ATM IV = 0.5Δ call/put mean;
  skew = 25Δ risk reversal. "AB9019 No Data" treated as empty candidate,
  not an error.
- Tables: `option_chain_daily` (per-strike greeks) + `iv_daily` (summary).
- Wired into Force Sync (`ivChain` sub-sync) + freshness monitoring.
- First snapshot: 2026-06-10, ATM IV 14.83, RR25 +4.32, 78 strikes.

**Phase-2 ship decision** (pre-registered now): after ≥ 60 trading days of
chain data, price a defined-risk monthly structure (short strangle + wings
at collected quotes); ship gate = net Sharpe > 0.5 on the capped P&L with
worst-cycle loss bounded by the wings, evaluated on data NOT used to pick
the strikes. Until then: collect, don't trade.

## Paper condor book (2026-06-11)

`src/services/paperCondorService.js` + dashboard card ("VRP Data
Collection" → Paper Condor Book). Simulated defined-risk monthly structure
— the declared phase-2 default: sell ~25Δ strangle, buy ~10Δ wings. Entry
legs are Black–Scholes-priced from each strike's COLLECTED IV + live Angel
NIFTY spot (model-priced, labeled as such); settlement is exact intrinsic
at the expiry-day ^NSEI close, auto-marked by Force Sync. One position per
expiry (UNIQUE), table `paper_condors`, endpoints
`POST /api/paper-condor/open`, `GET /api/paper-condor`. **No broker orders
ever originate from this path.**

Purpose: (1) teaches the mechanics with real numbers, (2) each settled
cycle is a live observation to validate the September phase-2 calibration.

First position (2026-06-10 snapshot): spot 23,215 → 21500P(buy) /
22400P(sell) / 24500C(sell) / 25250C(buy), credit ₹7,694, max loss
₹59,806, expiry 07JUL2026.

## Project Usage

- Study: `quant_engine/research/vol_risk_premium.py`
- VIX deep history: `quant_engine/data/backfill_regime.py --from-yahoo`
- Collector: `src/services/optionChainService.js` (+ db.js tables, Force
  Sync wiring, `iv_daily` freshness row)
- Paper book: `src/services/paperCondorService.js`, dashboard card,
  auto-settlement in Force Sync
- Linear: SIC-92 (In Progress — phase 2 accumulating data)
