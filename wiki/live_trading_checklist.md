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
- [ ] Implement per-stock stop-loss: exit if position loses >10% from entry
- [ ] Implement daily portfolio circuit breaker: halt all trading if portfolio loses >2% in one day
- [ ] Implement max sector concentration: no single sector >25% of portfolio
- [ ] Add `max_position_size = min(5% of portfolio, 3% of daily volume)` cap
- [ ] Add `config/risk_limits.js` (or `settings.js`) with all limits in one place

**Why it matters**: Portfolio can lose 40-50% with no protective mechanism during correlated crash.  
**Files**: New file `src/risk/risk_manager.js` (to be created), `config/settings.js`

---

### C4. No Broker Integration (Signals Never Executed)
- [ ] Implement Zerodha Kite API connection (best documented for NSE India)
- [ ] Implement order placement: market orders on signal
- [ ] Implement position reconciliation: compare current holdings to signal state
- [ ] Implement trade logging: timestamp, price, quantity, cost to DB
- [ ] Paper trade for minimum 2 weeks before live capital
- [ ] Add account cash balance check before placing order

**Why it matters**: System is a research tool — signals are generated but never executed.  
**Files**: New `src/brokers/zerodha.js` (to be created)

---

### C5. ML Model Has Data Leakage
- [ ] Remove analyst consensus from ML feature set (`quant_engine/ml/trainer.py`)
- [ ] Fix sector rotation feature: use NSE sector indices (not portfolio peer returns) in all code paths including training
- [ ] Add holdout set: reserve last 20% of time-ordered data, never used in hyperparameter search
- [ ] Report per-class precision/recall (not just accuracy); halt deploy if BUY recall < 40%
- [ ] Document true OOS accuracy separately from CV accuracy

**Why it matters**: CV accuracy ~75% is overstated; true out-of-sample likely 55-60%.  
**Files**: `quant_engine/ml/trainer.py` (lines 75-449), `quant_engine/ml/predictor.py`

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

**Critical items completed**: 1 / 5 (C2 done — Alpha Vantage primary, RapidAPI fallback with flat-bar warning)  
**Data bottleneck items completed**: 0 / 4  
**Moderate items completed**: 0 / 4  
**Validation items completed**: 0 / 3  

_Update this section as items are checked off._
