# Phase 1 — NSE Delivery Data & Circuit Breaker Detection

**Status:** Implemented
**Priority:** High — fixes the weakest ML feature (volume importance: 0.021)

---

## Why This Matters

The ML model currently uses raw trading volume as a feature. The problem: volume alone is noisy — a stock can have huge volume on purely speculative intraday trades that reverse by end of day, with no real money changing hands.

**Delivery percentage** is far more meaningful. It tells you what fraction of the day's traded volume was actually *delivered* (i.e., settled T+2 — real buyers and sellers committing capital). A breakout on 80% delivery is fundamentally different from the same move on 15% delivery.

| Signal | Low Delivery % | High Delivery % |
|--------|---------------|-----------------|
| Price up | Speculative spike, likely to fade | Real accumulation by buyers |
| Price down | Intraday panic, may reverse | Genuine distribution, confirms weakness |

---

## Data Source

**File:** NSE Security Wise Delivery Position (MTO file)
**URL pattern:** `https://nsearchives.nseindia.com/archives/equities/mto/MTO_{DDMMYYYY}.DAT`
**Example:** `https://nsearchives.nseindia.com/archives/equities/mto/MTO_26032025.DAT`
**Frequency:** Daily (every trading day)
**Authentication:** None — just a browser `User-Agent` header
**Cost:** Free

### File Format
```
Security Wise Delivery Position - Compulsory Rolling Settlement
10,MTO,26032025,1689021724,0002786
Trade Date <26-MAR-2025>,Settlement Type <N>
Record Type,Sr No,Name of Security,Series,Quantity Traded,Deliverable Quantity,% of Deliverable Quantity to Traded Quantity
20,1,INFY,EQ,5200000,4160000,80.00
20,2,TATASTEEL,EQ,8900000,3115000,35.00
...
```

**Parsing rules:**
- Keep only rows where `Record Type == "20"` (equity delivery records)
- Keep only rows where `Series == "EQ"` (skip GC, BE, SM series)
- Column mapping: `[0]=RecordType, [1]=SrNo, [2]=Symbol, [3]=Series, [4]=TradedQty, [5]=DeliverableQty, [6]=DeliveryPct`

---

## Database Table

```sql
CREATE TABLE delivery_data (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol        TEXT NOT NULL,
  date          TEXT NOT NULL,
  traded_qty    INTEGER,
  delivery_qty  INTEGER,
  delivery_pct  REAL,
  circuit_hit   INTEGER DEFAULT 0,  -- +1 upper circuit, -1 lower circuit, 0 none
  UNIQUE(symbol, date)
);
CREATE INDEX idx_delivery_symbol ON delivery_data(symbol);
CREATE INDEX idx_delivery_date   ON delivery_data(date);
```

---

## Backfill Script

**File:** `quant_engine/data/backfill_delivery.py`

```bash
# Backfill from 2023 to today
python3 -m quant_engine.data.backfill_delivery --from 2023-01-01

# Backfill a specific date
python3 -m quant_engine.data.backfill_delivery --date 2025-03-26

# Backfill a custom range
python3 -m quant_engine.data.backfill_delivery --from 2024-01-01 --to 2024-12-31
```

**What it does:**
1. Iterates trading days in the date range (skips weekends automatically)
2. Downloads the MTO `.DAT` file for each day
3. Parses and filters for portfolio symbols only
4. Upserts into `delivery_data` table (safe to re-run — `INSERT OR REPLACE`)
5. Returns 0 rows for holidays/non-trading days (404 from NSE → skipped gracefully)
6. ~0.5s delay between requests to avoid rate limiting

**Expected output:**
```
2025-03-26 INFY — 8 rows upserted
2025-03-27 INFY — 8 rows upserted
...
Done — 2340 total rows upserted, 52 non-trading days skipped
```

---

## Loader

**File:** `quant_engine/data/delivery_loader.py`

```python
from quant_engine.data.delivery_loader import load_delivery_series, load_circuit_status

# Get delivery data for a stock (last 365 days)
df = load_delivery_series("INFY", limit=365)
# Returns DataFrame with columns: delivery_pct, delivery_qty, circuit_hit
# Index: date (datetime)

# Check if stock is in circuit right now
status = load_circuit_status("TATASTEEL")
# Returns: +1 (upper circuit), -1 (lower circuit), 0 (normal)
```

---

## ML Feature: `delivery_score`

**Added to:** `quant_engine/ml/trainer.py`
**Replaces:** raw `volume` feature (importance was 0.021 — weakest of all 12 features)

**Logic:**
```python
# Rolling z-score of delivery_pct vs 60-day moving average, normalised to [-1, +1]
delivery_pct  = load_delivery_series(symbol)["delivery_pct"]
roll_mean     = delivery_pct.rolling(60, min_periods=10).mean()
roll_std      = delivery_pct.rolling(60, min_periods=10).std().replace(0, 1)
delivery_score = ((delivery_pct - roll_mean) / roll_std).clip(-3, 3) / 3
```

**Interpretation:**
- `+1.0` — delivery % is 3 standard deviations above its 60-day average → strong conviction move
- `0.0` — delivery % is at its historical average → neutral
- `-1.0` — delivery % is far below average → speculative, low-conviction move

---

## Circuit Breaker Filter

**Added to:** `quant_engine/scoring/composite.py`
**Logic:** After the composite score is computed, if the stock's most recent `circuit_hit == -1` (lower circuit), any LONG signal is overridden to HOLD.

**Why:** A stock locked at lower circuit cannot be bought. The ML model has no visibility into circuit status from price data alone, so this explicit filter prevents useless LONG signals on illiquid/halted stocks.

**Note:** Upper circuit (+1) does NOT suppress SHORT signals — you can still flag a stock as overbought even if it hit the upper circuit.

---

## Retraining

After backfilling data, retrain the ML model:
```bash
cd quant_engine
python3 -m ml.trainer
```

The model will now include `delivery_score` as a feature. Check the feature importances output — expect `delivery_score` to rank higher than the old `volume` feature (0.021 was the baseline to beat).

---

## Files Changed

| File | Change |
|------|--------|
| `src/database/db.js` | Added `delivery_data` CREATE TABLE + 2 indexes in `initDatabase()` |
| `quant_engine/data/backfill_delivery.py` | New backfill script |
| `quant_engine/data/delivery_loader.py` | New loader |
| `quant_engine/ml/trainer.py` | Added `delivery_score` feature |
| `quant_engine/scoring/composite.py` | Added circuit breaker filter |
