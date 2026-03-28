# Phase 3 — FII/DII Flows & F&O Participant OI

**Status:** Implemented (backfill pending)
**Priority:** Medium — adds two new regime features orthogonal to VIX

---

## Why This Matters

The ML model has three existing market regime features:
- `vix_regime` — fear/calm gauge (volatility)
- `nifty_trend` — price trend above/below moving averages
- `markov_regime` — transition probability (momentum)

All three look at the **same thing from different angles** — market price and volatility. None capture **who is buying and selling**.

FII (Foreign Institutional Investors) flows are the single biggest driver of Indian equity markets. When FIIs are consistent net sellers for 10+ days, even technically strong setups tend to fail.

**Two complementary new signals:**

| Feature | What it measures | Edge over VIX |
|---------|-----------------|---------------|
| `fii_flow_score` | FIIs net buying/selling in cash equities (INR crore/day) | Captures capital flows, not fear |
| `fii_fo_score` | FIIs net long/short in NIFTY index futures (contracts) | Forward-looking positioning |

**Example (VIX misses, FII data catches):**
- VIX = 12 → model sees BULLISH (calm)
- FII cash outflow = -₹8,000 crore/day for 10 days → massive selling
- FII net short futures = -50,000 contracts → hedged/bearish positioning
- **Reality:** Correction likely despite low VIX

---

## Data Sources

### Source 1 — FII/DII Cash Flows (historical CSV + live API)

**Historical (one-time manual download):**
1. Go to: `https://www.nseindia.com/reports-and-statistics/securities-statistics/foreign-institutional-investors`
2. Scroll to **"FII/FPI and DII Trading Activity"**
3. Set range: 01-Jan-2023 → today → **Download CSV**
4. Run: `python3 -m quant_engine.data.backfill_fii_dii --from-csv ~/Downloads/fiidiiTradeReact.csv`

**Daily live update (no manual step):**
```bash
python3 -m quant_engine.data.backfill_fii_dii --today
```
Uses: `https://www.nseindia.com/api/fiidiiTradeReact` (User-Agent header only, no auth)

> **Why the historical CSV is manual:** `www.nseindia.com` is behind Cloudflare — headless requests are blocked for the date-range API. The archives subdomain (`nsearchives`) doesn't host FII/DII daily files. The one-time CSV download is the only way to get history; daily updates are fully automated after that.

### Source 2 — F&O Participant OI (fully automated)

**URL pattern:**
```
https://archives.nseindia.com/content/nsccl/fao_participant_oi_{DDMMYYYY}.csv
```
No auth, no session — plain HTTP GET with User-Agent header.

**Backfill:**
```bash
python3 -m quant_engine.data.backfill_fo_oi --from 2023-01-01
```

**Key metric:** `FII Future Index Long - FII Future Index Short`
- Positive = FIIs net long NIFTY futures → bullish institutional positioning
- Negative = FIIs net short NIFTY futures → hedged or expecting decline

---

## Database Schema

Three new columns added to the existing `market_regime` table:

```sql
ALTER TABLE market_regime ADD COLUMN fii_net_cash    REAL;  -- INR crore/day (FII cash)
ALTER TABLE market_regime ADD COLUMN dii_net_cash    REAL;  -- INR crore/day (DII cash)
ALTER TABLE market_regime ADD COLUMN fii_fo_net_long REAL;  -- contracts (FII index futures)
```

Stored alongside `india_vix` — all regime signals are date-aligned in one table.
The `initDatabase()` in `db.js` handles the `ALTER TABLE` idempotently (try/catch).

---

## ML Feature Construction

Both features use the same normalisation pattern as `vix_regime` — rolling percentile rank mapped to [-1, +1]:

### `fii_flow_score`
```python
fii_10d = fii_net_cash.rolling(10).sum()   # 10-day cumulative flow
percentile = fii_10d.rolling(252, min_periods=30).apply(lambda s: s.rank(pct=True).iloc[-1])
fii_flow_score = (2 * percentile - 1).clip(-1, 1)
```
- `+1.0` = FII inflow at multi-year high → strongly bullish backdrop
- ` 0.0` = FII flow at historical median → neutral
- `-1.0` = FII outflow at multi-year high → strongly bearish backdrop

### `fii_fo_score`
```python
percentile = fii_fo_net_long.rolling(252, min_periods=30).apply(lambda s: s.rank(pct=True).iloc[-1])
fii_fo_score = (2 * percentile - 1).clip(-1, 1)
```
- `+1.0` = FIIs maximally net long futures → highest bullish positioning
- `-1.0` = FIIs maximally net short futures → highest bearish/hedged positioning

The 10-day window for FII cash (vs single-day for F&O) smooths the noise in daily cash flows while preserving the trend signal.

---

## Files Changed

| File | Change |
|------|--------|
| `src/database/db.js` | Add 3 columns to `market_regime` via ALTER TABLE in `initDatabase()` |
| `quant_engine/data/backfill_fo_oi.py` | **New** — downloads F&O OI CSVs, extracts FII net index futures |
| `quant_engine/data/backfill_fii_dii.py` | **New** — CSV import mode + live API daily update mode |
| `quant_engine/data/market_regime_loader.py` | Add `load_fii_flow_series()`, `load_fii_fo_series()`, `_flow_to_score()`, `load_fii_flow_score_today()`, `load_fii_fo_score_today()` |
| `quant_engine/ml/trainer.py` | Add `fii_flow_score` and `fii_fo_score` to FEATURE_COLS (now 15 features); load and compute both series in `build_training_dataset()` |
| `quant_engine/ml/predictor.py` | Add both features to FEATURE_COLS (must match trainer) |
| `quant_engine/sicilian/engine.py` | Compute both scores in `run_sicilian()` sub_scores dict for live inference |

---

## Backfill & Retrain Sequence

```bash
# Step 1: F&O OI — fully automated (~800 days, ~7 min)
python3 -m quant_engine.data.backfill_fo_oi --from 2023-01-01

# Step 2: FII/DII cash — after you download the CSV from NSE website
python3 -m quant_engine.data.backfill_fii_dii --from-csv ~/Downloads/fiidiiTradeReact.csv

# Step 3: Retrain
python3 -m quant_engine.ml.trainer
```

**Daily automation (add to cron at 18:30 IST):**
```bash
python3 -m quant_engine.data.backfill_fii_dii --today
python3 -m quant_engine.data.backfill_fo_oi --date $(date +%Y-%m-%d)
```

---

## Expected Impact

`fii_fo_score` is expected to be a meaningful feature immediately — 3 years of F&O OI history gives the percentile window enough data to be well-calibrated.

`fii_flow_score` will also be meaningful after the historical CSV import. The 10-day rolling sum + 252-day percentile rank needs at least ~260 trading days to stabilise (~1 year of data).

Both features capture institutional sentiment that is **uncorrelated with VIX, NIFTY trend, and Markov regime** — they should add genuine new information to the model.
