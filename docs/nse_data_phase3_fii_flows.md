# Phase 3 — FII/DII Flows & F&O Participant OI

**Status:** Planned
**Priority:** Medium — adds two new regime features orthogonal to VIX

---

## Why This Matters

The ML model currently has three market regime features:
- `vix_regime` — how fearful/calm the market is (volatility)
- `nifty_trend` — whether NIFTY is above/below its moving averages (price trend)
- `markov_regime` — probability of bull vs bear from recent transitions (momentum)

All three look at the **same thing from different angles** — market price and volatility. None of them capture **who is buying and selling**.

FII (Foreign Institutional Investors) flows are the single most important driver of Indian equity markets. When FIIs are consistent net sellers over 10+ days, even technically strong setups tend to fail. This is a genuine new signal the model currently has zero visibility into.

**Two complementary flow signals:**

| Feature | What it measures | Why it's different from VIX |
|---------|-----------------|----------------------------|
| `fii_flow_score` | FIIs net buying/selling in cash equities (INR crore/day) | Flow of actual capital, not just fear |
| `fii_fo_score` | FIIs net long/short in NIFTY futures (contracts) | Forward-looking positioning, not just current action |

**Example scenario this captures (that VIX misses):**
- VIX = 12 (calm market, low fear) ← current model sees BULLISH
- FII cash outflow = -₹8,000 crore/day for 10 days ← massive institutional selling
- FII net short in futures = -50,000 contracts ← hedging/betting on decline
- **Reality:** Market likely to correct despite low VIX

---

## Data Source 1 — FII/DII Cash Market Flows

**Live API:**
```
https://www.nseindia.com/api/fiidiiTradeReact
```
**Authentication:** Browser `User-Agent` header
**Response format:**
```json
[
  {"category": "FII/FPI", "date": "27-Mar-2026", "buyValue": "20486.39", "sellValue": "24853.69", "netValue": "-4367.30"},
  {"category": "DII",     "date": "27-Mar-2026", "buyValue": "37579.14", "sellValue": "34012.99", "netValue":  "3566.15"}
]
```
`netValue` = `buyValue - sellValue` in INR crore. Positive = net buying, negative = net selling.

**Historical backfill:**
Download the CSV directly from NSE:
1. Go to `nseindia.com/products/content/equities/equities/eq_fiidii_archives.htm`
2. Select date range → download CSV
3. Run: `python3 -m quant_engine.data.backfill_fii_dii --from-csv ~/Downloads/FII_DII_Data.csv`

This is the same pattern used for the VIX backfill — manual one-time CSV import for history, then live API for daily updates.

---

## Data Source 2 — F&O Participant OI

**URL pattern:**
```
https://archives.nseindia.com/content/nsccl/fao_participant_oi_{DDMMYYYY}.csv
```
**Example:**
```
https://archives.nseindia.com/content/nsccl/fao_participant_oi_26032025.csv
```
**Authentication:** None (works without User-Agent — fully public)

**File format:**
```csv
Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,...
FII,         85611,            169344,            3795529,          2023719,...
DII,         104363,           40255,             273056,           4049334,...
Client,      184778,           143235,             2380818,          553956,...
```

**Key metric:** `FII Future Index Long - FII Future Index Short`
- Positive = FIIs net long NIFTY futures → bullish positioning
- Negative = FIIs net short NIFTY futures → bearish/hedged positioning

This works with direct URL downloads, no scraping needed.

---

## Database Schema

Extend the existing `market_regime` table (keeps all regime signals aligned by date):

```sql
ALTER TABLE market_regime ADD COLUMN fii_net_cash    REAL;  -- INR crore, daily net (FII cash)
ALTER TABLE market_regime ADD COLUMN dii_net_cash    REAL;  -- INR crore, daily net (DII cash)
ALTER TABLE market_regime ADD COLUMN fii_fo_net_long REAL;  -- contracts, FII index futures net long
```

All three are date-indexed, same as `india_vix`. Storing them in `market_regime` keeps the existing loader pattern intact — `market_regime_loader.py` already reads this table and returns date-aligned series.

---

## Backfill Scripts

### Script 1: `quant_engine/data/backfill_fii_dii.py`

```bash
# One-time historical import from NSE CSV
python3 -m quant_engine.data.backfill_fii_dii --from-csv ~/Downloads/FII_DII_Data.csv

# Daily live update (call from cron/startup)
python3 -m quant_engine.data.backfill_fii_dii --today
```

**What it does:**
- CSV mode: Parses the NSE archives CSV (columns: Date, FII Buy, FII Sell, FII Net, DII Buy, DII Sell, DII Net)
- Live mode: Calls the JSON API, extracts FII and DII net values, upserts today's row
- Updates `fii_net_cash` and `dii_net_cash` columns in `market_regime`

### Script 2: `quant_engine/data/backfill_fo_oi.py`

```bash
# Backfill from 2023
python3 -m quant_engine.data.backfill_fo_oi --from 2023-01-01

# Single date
python3 -m quant_engine.data.backfill_fo_oi --date 2025-03-26
```

**What it does:**
- Downloads `fao_participant_oi_{DDMMYYYY}.csv` for each trading day
- Extracts FII row → computes `Future Index Long - Future Index Short`
- Upserts `fii_fo_net_long` column in `market_regime`

---

## ML Features

Both features follow the same normalisation pattern as `vix_regime` — rolling percentile rank → [-1, +1]:

### `fii_flow_score`
```python
# 10-day rolling sum of FII net cash, normalised by 252-day percentile
fii_10d = fii_net_cash.rolling(10).sum()
percentile = fii_10d.rolling(252, min_periods=60).apply(lambda s: s.rank(pct=True).iloc[-1])
fii_flow_score = (2 * percentile - 1).clip(-1, 1)  # maps 0-1 percentile to -1 to +1
```
- `+1.0` = FII buying is at multi-year high (strong inflow)
- `0.0` = FII flow is at historical median
- `-1.0` = FII selling is at multi-year high (strong outflow)

### `fii_fo_score`
```python
# FII net futures position, normalised by 252-day percentile
percentile = fii_fo_net_long.rolling(252, min_periods=60).apply(lambda s: s.rank(pct=True).iloc[-1])
fii_fo_score = (2 * percentile - 1).clip(-1, 1)
```
- `+1.0` = FIIs are maximally net long futures (very bullish positioning)
- `-1.0` = FIIs are maximally net short futures (very bearish/hedged)

Both features are updated in `market_regime_loader.py` alongside the existing `load_vix_series()` function.

---

## Files to Change

| File | Change |
|------|--------|
| `src/database/db.js` | Add 3 new columns to `market_regime` table schema |
| `quant_engine/data/backfill_fii_dii.py` | New backfill script |
| `quant_engine/data/backfill_fo_oi.py` | New backfill script |
| `quant_engine/data/market_regime_loader.py` | Add `load_fii_flow_series()` and `load_fii_fo_series()` |
| `quant_engine/ml/trainer.py` | Add `fii_flow_score` and `fii_fo_score` features |

---

## Historical Data Note

For `fii_flow_score`, you need at least 252 trading days (~1 year) of FII data for the rolling percentile window to be meaningful. Download 3+ years from NSE archives for a robust signal.

For `fii_fo_score`, the daily F&O OI files go back to 2010 on NSE archives — but 2 years is sufficient for training.
