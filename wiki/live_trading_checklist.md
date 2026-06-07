# Live Trading Readiness Checklist

**Assessment Date**: 2026-04-06  
**Verdict**: NOT READY — estimated 6–8 weeks of work before safe real-money deployment  
**Full analysis**: This file is the living checklist. Check off items as implemented.

---

## How to Use This File

- `[ ]` = Not started
- `[~]` = In progress
- `[x]` = Completed

When completing an item, also update the relevant `wiki/concepts/` or `wiki/papers/` page's `## Project Usage` section.

---

## CRITICAL — Would cause immediate loss or failure

### C0. PIT CPCV/DSR Ship Gate (2026-06-04 — audit terminal finding)
- [ ] **No live capital deploys behind any ML or factor track until
      `python -m quant_engine.ml.cpcv_diagnostic --pit` reports
      `DSR > 0.95` AND `PBO < 0.15` for at least one track (LO or LS).**
- [ ] The 2026-06-04 run produced `LO DSR = 0.000` and `LS DSR = 0.000`
      for all three tracks (`ml`, `linear`, `ml_regression`) and
      `PBO = 0.585`. Until a new signal or wider universe clears this
      gate, the long-only signal queue must not size new positions
      on the legacy linear or RF paths.
- [ ] Every re-run of this gate must record `pit_universe_active: true`,
      the exact `n_trials` used for DSR, and `n_combinations` in
      `data/cpcv_diagnostic.json` — they're the audit trail.

**Why it matters**: The historical "+0.040 IC" and "+9.73 pp meta-label
uplift" headlines deflated to **no shippable edge** under
survivorship-corrected, multiple-testing-corrected evaluation. Live
capital behind any of those configurations is trading a signal that
wins ~34 % of the time on honest data. See
[ml_audit_2026_05_21.md §2026-06-04 Terminal Findings](concepts/ml_audit_2026_05_21.md#2026-06-04-terminal-findings).
**Files**: `quant_engine/ml/cpcv_diagnostic.py`,
`quant_engine/scoring/composite.py`,
`src/services/signalQueueService.js`.

---

### C1. Backtest vs Live Strategy Mismatch
- [ ] Unify factor set: backtest and live must use the **same 7 technical factors** (no analyst consensus, PE/PB, growth in either path)
- [ ] Remove analyst consensus from `sicilian/engine.py` live scoring (look-ahead biased — 2026 ratings applied to 2023 backtest bars)
- [ ] Verify backtest and live use identical `BUY_THRESHOLD` / `SELL_THRESHOLD` values (currently 0.40 vs 0.35)

**Why it matters**: Backtested returns are not reproducible in live trading because live uses 15 factors, backtest uses 7.  
**Files**: `quant_engine/strategies/sicilian_strategy.py`, `quant_engine/strategies/sicilian/engine.py`, `quant_engine/routers/backtest.py`

---

### C2. RapidAPI Returns Only Close Price (Not OHLCV) + Cadence Drift
- [x] **2026-04-09**: Backfilled 13/15 portfolio stocks with Alpha Vantage `TIME_SERIES_WEEKLY_ADJUSTED` (real OHLCV, dividend-adjusted, 20+ years). JIOFIN/TMCV kept (no AV coverage) but resampled daily→weekly for cadence consistency.
- [x] **2026-04-11**: Switched primary data source to Alpha Vantage (real OHLCV), RapidAPI demoted to fallback (`src/services/stockData.js:179-207`)
- [x] **2026-04-11**: Added flat-bar detection — logs warning when RapidAPI fallback produces synthetic OHLC
- [ ] Revalidate: Bollinger Bands and volatility factor scores on stocks still relying on RapidAPI fallback (JIOFIN, TMCV, index symbols)

**Why it matters**: (1) All range-based factors (Bollinger, ATR, MACD signal quality, volatility) are computed on flat bars → artificially low signal variance. (2) Worse, `src/services/rapidApiService.js:62-70` fills open/high/low = close, and the endpoint silently returned **weekly** data for `period=10yr` back to 2005 but **daily** data for `period=1yr` starting 2025-03-17. The historical `price_history` table thus contained a weekly→daily cadence break that silently invalidated every row-position feature and label in the ML trainer (see **ml_pipeline.md "2026-04-09 density fix"** for the diagnostic evidence).  
**Files**: `src/services/rapidApiService.js` (fills open/high/low = close), `quant_engine/factors/bollinger.py`, `quant_engine/factors/volatility.py`, `quant_engine/data/av_weekly_backfill.py` (remediation tool)

---

### C3. No Position-Level Risk Management
- [x] **2026-04-11**: Centralized risk limits in `src/config/riskLimits.js` (stop-loss, circuit breaker, sector cap, position cap, ADV cap)
- [x] **2026-04-11**: Hybrid stop-loss (volatility + chandelier trailing) in `src/risk/stopLoss.js` — López de Prado AFML Ch.3 vol-scaled initial + trend-following chandelier
- [x] **2026-04-11**: Daily portfolio circuit breaker in `src/risk/circuitBreaker.js` — halts new orders if portfolio down > 2% intraday
- [x] **2026-04-11**: Max sector concentration (25%) check in `src/risk/sectorConcentration.js`
- [x] **2026-04-11**: Inverse-vol position sizing in `src/risk/positionSizing.js` — Kakushadze (R ~ V^0.76), 5% hard cap, 3% ADV cap, 15% target portfolio vol
- [x] **2026-04-21**: Claude final gate (SIC-31) — every signal must pass Claude's GO/NO_GO review before an order is placed; Claude re-derives qty/stop/target using the same risk limits (see `wiki/concepts/claude_final_gate.md`)

**Why it matters**: Portfolio can lose 40-50% with no protective mechanism during correlated crash.  
**Files**: `src/config/riskLimits.js`, `src/risk/*.js`, `src/services/claudeEvaluator.js`, `src/services/signalQueueService.js`

---

### C4. No Broker Integration (Signals Never Executed)
- [x] **2026-04-21**: Trade-trigger queue + user-driven execution flow in place (`src/services/signalQueueService.js`, `public/risk.html`) — paper mode works, broker call is the one remaining stub
- [x] **2026-04-21**: Claude (`opus-4-7`) evaluates every signal before execution — returns limit price, qty, stop, target, rationale (SIC-31). See [claude_final_gate.md](concepts/claude_final_gate.md)
- [x] **2026-04-21**: Trade logging: `signal_queue` table records signal_date, exec_price, order_id, reject_reason, executed_at
- [ ] Switch broker from Zerodha plan to **Angel One** (already integrated for market data — `src/services/angelOne*.js`). Implement `placeOrder(symbol, qty, side, type, limit)` using SmartAPI
- [ ] Implement position reconciliation: compare current holdings to broker holdings
- [ ] Paper trade for minimum 2 weeks before flipping `PAPER_TRADING=false`
- [ ] Add account cash balance check before placing order (Angel One `/funds` endpoint)

**Why it matters**: System is a research tool — signals are generated but never executed.  
**Files**: `src/services/signalQueueService.js`, `src/services/claudeEvaluator.js`, `src/services/angelOneAuth.js`, future `src/services/angelOneOrders.js`

---

### C5. ML Model Has Data Leakage — CLOSED 2026-06-04 by audit
- [x] **2026-06-04**: Item largely superseded by the P0-P2 audit
      (see [C0](#c0-pit-cpcvdsr-ship-gate-2026-06-04--audit-terminal-finding)
      and [ml_audit_2026_05_21.md §2026-06-04 Terminal Findings](concepts/ml_audit_2026_05_21.md#2026-06-04-terminal-findings)).
      Per-line resolution below.
- [x] *Remove analyst consensus from ML feature set* — done. Audit also
      removed `pcr_score`, `fii_flow_score`; trainer never used analyst features.
- [x] *Fix sector rotation feature: use NSE sector indices* — done via P1-e
      (`quant_engine/ml/features.py :: build_feature_frame` is the single
      source of truth shared by trainer + strategy).
- [x] *Add holdout set* — **exceeded.** P2-a (CPCV, `quant_engine/ml/cpcv.py`)
      + P2-b (DSR, `quant_engine/ml/validation.py`) is the stricter replacement.
- [x] *Report per-class precision/recall* — `class_distribution` recorded
      in `metadata.json` on every retrain.
- [x] *Document true OOS accuracy separately from CV accuracy* — every
      `ml_diagnostic.json` records `pit_universe_active`, `rf_params_source`,
      per-fold IC distributions.
- [x] *Halt deploy if BUY recall < 40%* — **moot.** C0 ship gate
      (DSR > 0.95 AND PBO < 0.15) is strictly stronger and blocks deploy
      regardless of any per-class threshold.

**Why it matters (historical)**: CV accuracy was overstated. **Resolution**:
the audit found that the deeper problem was survivorship + multiple-testing
in the universe, not the model. C5's items are obsolete because the audit's
infrastructure (`cpcv_diagnostic.py --pit`) measures the right thing
directly. See the closing record in the audit doc.
**Files**: `quant_engine/ml/trainer.py`, `quant_engine/ml/predictor.py`,
`quant_engine/ml/diagnostic.py`, `quant_engine/ml/cpcv_diagnostic.py`.

---

## DATA BOTTLENECKS — Specific to this project's data setup

### D1. FII/DII Data Is Unreliable (NSE Scraping)
- [ ] Replace NSE HTML scraping with a reliable paid source OR remove FII from regime score
- [ ] Backfill FII/DII history from NSE CSV archives (cover at least 2 years)
- [ ] Add data freshness check: warn if FII data > 3 days old before scoring
- [ ] If FII removed: rebalance regime weights (VIX 45%, Nifty trend 30%, Markov 25%)

**Why it matters**: FII is 15% of regime score and breaks every 3-5 days via scraping. When VIX is also missing, regime signal is completely blind.  
**Files**: `src/services/stockData.js` (lines 283-311), `quant_engine/strategies/regime_adaptive_strategy.py`

---

### D2. RapidAPI Rate Limits Insufficient for Daily Refresh
- [ ] Upgrade to RapidAPI paid tier OR switch primary data source to Polygon.io / Zerodha Historical API
- [ ] Implement per-key quota tracking: log calls/day per key, rotate before hitting limit
- [ ] Add alert: notify when fallback to Alpha Vantage triggered
- [ ] Document data freshness SLA: maximum acceptable staleness per data type

**Why it matters**: After 2 full refreshes, Alpha Vantage free quota exhausted; system falls back to stale cache silently.  
**Files**: `src/services/rapidApiService.js`, `src/services/alphaVantageService.js`, `src/services/stockData.js`

---

### D3. No Data Gap Detection in Loader
- [ ] Add gap check in `quant_engine/data/loader.py`: warn if < 200 bars, error if < 50 bars
- [ ] Add holiday calendar check: expected NSE trading days vs actual bars stored
- [ ] Add price spike detection: flag if day-over-day change > 15% (possible bad tick)
- [ ] Add zero-volume day detection: log and skip if > 3 consecutive zero-volume days

**Why it matters**: If a stock has 50 bars instead of 252, RSI/MACD/momentum compute on wrong windows — silent garbage signals.  
**Files**: `quant_engine/data/loader.py`

---

### D4. VIX Backfill Not Automated
- [ ] Automate VIX backfill via NSE fetcher on startup if > 30 days of data missing
- [ ] Add VIX data freshness check before regime score computation
- [ ] Document required minimum VIX history (252 trading days) in CLAUDE.md

**Why it matters**: VIX is 35% of regime score. If table is empty, regime score runs on 0.0 and system has no macro awareness.  
**Files**: `quant_engine/data/nse_fetcher.py`, `quant_engine/data/backfill_regime.py`, `quant_engine/data/market_regime_loader.py`

---

## MODERATE — Would hurt returns but not blow up account

### M1. Transaction Costs Underestimated by 2-5x
- [ ] Scale slippage by liquidity: large-caps 0.10%, mid-caps 0.30%, micro-caps 1.0%+
- [ ] Add bid-ask spread model: flat 0.10% on top of commission for all stocks
- [ ] Add market impact for large positions: if order > 1% of daily volume, add 0.50% extra
- [ ] Re-run all backtests after cost correction (expect 30-50% reduction in reported returns)

**Why it matters**: Current backtest assumes 0.15% round-trip for all stocks. BAJAJHIND/TMCV/TMPV reality is 1-3%.  
**Files**: `quant_engine/backtest/engine.py`

---

### M2. Remove Micro-Cap Stocks From Trading Universe
- [ ] Add minimum liquidity filter: skip stocks with < 500k average daily volume
- [ ] Apply filter to: BAJAJHIND, TMCV, TMPV (confirm current daily volumes)
- [ ] Update `config/portfolio.js` to flag/exclude illiquid positions
- [ ] Document universe eligibility criteria in `wiki/concepts/factor_scoring.md`

**Why it matters**: Backtested prices are not achievable in live trading for illiquid names.  
**Files**: `config/portfolio.js`, `quant_engine/factors/volume.py`

---

### M3. Align Signal Thresholds (Backtest vs Live)
- [ ] Standardize: pick one threshold pair and use it everywhere (recommend: BUY ≥ 0.40, SELL ≤ -0.40)
- [ ] Grid search thresholds on holdout set; document the optimal values
- [ ] Add threshold values to `config/settings.js` as single source of truth

**Files**: `quant_engine/strategies/sicilian_strategy.py`, `quant_engine/strategies/sicilian/engine.py`

---

### M4. Secure Credentials
- [ ] Rotate all API keys (RapidAPI, Alpha Vantage, Turso, Angel One, News API)
- [ ] Remove `.env` from git history: `git rm --cached .env && echo ".env" >> .gitignore`
- [ ] Move secrets to environment-level config (not file-based)
- [ ] Verify `.gitignore` includes `.env` before next commit

**Files**: `.env`, `.gitignore`

---

## RESEARCH PRIORITIES — post-2026-06-04 audit

The audit closed with no track passing the [C0](#c0-pit-cpcvdsr-ship-gate-2026-06-04--audit-terminal-finding)
ship gate on the honest PIT universe. The path to live capital now runs
through *finding a signal that clears the gate*, not through productionizing
the current one. Every R-item below must be evaluated via
`python -m quant_engine.ml.cpcv_diagnostic --pit` and must meet
**PIT LS Sharpe > ~0.6, DSR > 0.95, PBO < 0.15** before promotion to
production routing in `composite.py`. The bar to beat is the audit's
post-PIT ml_regression LS Sharpe of 0.595 with DSR 0.000.

Ordered by expected lift / cost. See
[ml_audit_2026_05_21.md §H](concepts/ml_audit_2026_05_21.md#h-open-research-items-post-audit)
for the full reasoning behind the ranking.

### R1. Alternative horizons (1-3d)
- [ ] Extend `quant_engine/ml/diagnostic.py` and `cpcv_diagnostic.py`
      to evaluate 1d, 3d horizons alongside 20d
- [ ] Cross-sectional momentum/reversal at short horizons has different
      signal/noise properties from 20d trend-following — the wrong window
      for our current factor set is itself a hypothesis worth ruling out
- [ ] ~50 LOC; cheapest possible test of "is the current feature set
      shippable at any horizon?"

### R2. Universe expansion to Nifty 500 PIT
- [ ] Source historical Nifty 500 membership (CMIE Prowess preferred,
      NSE corporate-actions archive + Nifty Indices PDFs fallback)
- [ ] Backfill OHLCV for the additional ~300 symbols (Bhavcopy fetcher
      `backfill_bhavcopy.py` already does this — proven on Nifty 200)
- [ ] Re-run `cpcv_diagnostic --pit --pit-index NIFTY500`
- [ ] **Expected lift**: ~3× SE shrinkage at same signal strength → a
      0.6 LS Sharpe at the current N could clear DSR at the larger N

### R3. Fracdiff features (AFML Ch.5)
- [ ] Add `fracdiff(close, d≈0.35)` to `quant_engine/ml/features.py`
- [ ] Stationarized prices preserve memory that raw returns destroy
- [ ] ~40 LOC; expected lift in published benchmarks 2-5 bp IC

### R4. Order-flow / delivery-pct signals
- [ ] Build delivery-pct rolling z-score features in
      `quant_engine/factors/delivery.py` (data already in `delivery_data` table)
- [ ] Add intraday absorption / OBV-style features from existing 15-min bars
- [ ] Different theory of edge than factor scores — likely complementary
      diversification even if marginal individually

### R5. Event-driven (earnings, analyst revisions)
- [ ] Backfill event tables: earnings dates + surprise magnitude, sell-side
      revision counts
- [ ] Structural alpha source in EM equities — highest-confidence direction
      academically; highest data-sourcing cost
- [ ] Gate the same way as R1-R4 via `cpcv_diagnostic --pit`

### R6. Dollar bars (P3-c — speculative)
- [ ] Build dollar bars from 15-min intraday data
- [ ] AFML-recommended bar construction; ~300 LOC
- [ ] Defer until at least one of R1-R5 produces a passing signal —
      proves the feature/horizon search isn't the bottleneck before
      attempting a more expensive bar redesign

---

## BEFORE GOING LIVE — Validation steps

### V1. Paper Trading Period
- [ ] Run 2 weeks of paper trading: live signals, manual execution in broker app
- [ ] Document signal → actual fill price comparison for each trade
- [ ] Compute actual vs expected slippage

### V2. Crisis Simulation
- [ ] Run strategy through 2020 COVID crash data
- [ ] Run strategy through 2023 Adani crisis data
- [ ] Verify circuit breakers trigger correctly
- [ ] Verify stop-losses fire before max drawdown threshold

### V3. Monitoring Setup
- [ ] Alert: API key quota < 20% remaining
- [ ] Alert: Price data > 2 days stale for any stock
- [ ] Alert: Portfolio drawdown > 5% from high
- [ ] Alert: VIX data missing > 1 day
- [ ] Daily P&L report vs backtest P&L expectation

---

## Minimum Viable Safe Start (if you must go live sooner)

If you want to test with real money before all items above are complete:

1. **Universe**: 5 most liquid stocks only — INFY, RELIANCE, HDFCBANK, TCS, ICICIBANK
2. **Capital**: ₹50k-100k maximum (not full portfolio)
3. **Execution**: Manual — read signal from dashboard, place order yourself in broker app
4. **Hard stop**: Exit ALL positions if portfolio loses 5% from entry
5. **Duration**: 4 weeks observation before scaling
6. **Max loss**: ₹2,500-5,000 (acceptable learning cost)

---

## What Is Already Working (do not rebuild)

| Component | Status | Notes |
|-----------|--------|-------|
| Price history DB | GOOD | 9+ years cached, solid foundation |
| Technical factor logic | GOOD | Handles NaN/edge cases correctly |
| Regime detection architecture | GOOD | Design is sound; data is the weak link |
| TimeSeriesSplit CV | GOOD | Bug fixed (date-sorted, non-leaky) |
| Backtest engine (vectorized) | GOOD | Solid; needs cost correction only |
| Dual API fallback structure | GOOD | Right pattern; needs hardening |
| Markov regime model | GOOD | Well-implemented; needs more VIX data |

---

## Progress Summary

**Critical items completed**: 3 / 5 (C2 done, C3 done 2026-04-11, C4 mostly done 2026-04-21 — only Angel One order placement + reconciliation + cash check remain)
**Data bottleneck items completed**: 0 / 4
**Moderate items completed**: 0 / 4
**Validation items completed**: 0 / 3

_Update this section as items are checked off._
