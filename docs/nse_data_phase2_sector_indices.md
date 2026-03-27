# Phase 2 — NSE Sector Indices

**Status:** Planned
**Priority:** High — fixes broken sector rotation feature for single-stock sectors

---

## Why This Matters

The ML model has a `sector_rotation` feature that measures how a stock's sector is performing relative to the broader market. The problem: this feature is computed by averaging the returns of portfolio stocks grouped by industry. With only 15 stocks across 10+ industries, most "sectors" have just 1 stock.

**Current (broken):**
- TANLA's sector rotation = TANLA's own return − NIFTY return (circular — it's measuring itself)
- BAJAJHIND's sector rotation = BAJAJHIND's own return − NIFTY return (same problem)

**After Phase 2 (fixed):**
- TANLA's sector rotation = NIFTY IT return − NIFTY 50 return (actual sector momentum)
- BAJAJHIND's sector rotation = NIFTY FMCG return − NIFTY 50 return (real peer comparison)

Additionally, the sector indices file includes **P/E, P/B, and Dividend Yield per index** — bonus fundamental data at no extra cost.

---

## Data Source

**File:** NSE Index End-of-Day Data
**URL pattern:** `https://nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv`
**Example:** `https://nsearchives.nseindia.com/content/indices/ind_close_all_26032025.csv`
**Frequency:** Daily (every trading day)
**Authentication:** Browser `User-Agent` header only
**Cost:** Free
**File size:** ~5 KB per day, ~90 indices

### File Format
```csv
Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield
Nifty 50,26-03-2025,23700.95,23736.5,23451.7,23486.85,-181.8,-.77,278590831,26877.28,20.88,3.5,1.37
Nifty Next 50,26-03-2025,62721.45,63186.85,62388.6,62460.8,-242.95,-.39,389383445,15284.98,22.3,3.45,1.32
Nifty IT,26-03-2025,42300.10,42500.00,42100.00,42200.50,-99.6,-.24,...
Nifty Bank,26-03-2025,...
Nifty Auto,26-03-2025,...
```

**~90 indices per file** including all sectoral, thematic, and strategy indices.

---

## Industry → NSE Index Mapping

Defined in `quant_engine/config.py`:

```python
INDUSTRY_TO_NSE_INDEX = {
    "Information Technology":   "Nifty IT",
    "Power":                    "Nifty Energy",
    "Steel":                    "Nifty Metal",
    "Banking":                  "Nifty Bank",
    "Financial Services":       "Nifty Financial Services",
    "Non-Banking Finance":      "Nifty Financial Services",
    "Chemicals":                "Nifty Chemicals",
    "Sugar":                    "Nifty FMCG",
    "Telecom":                  "Nifty IT",          # closest proxy for CPaaS/tech
    "Automobiles":              "Nifty Auto",
    "Pharmaceuticals":          "Nifty Pharma",
    "Real Estate":              "Nifty Realty",
    "Consumer Goods":           "Nifty FMCG",
    "Infrastructure":           "Nifty Infrastructure",
}
```

If a stock's industry has no exact match, fall back to `Nifty 500` (broad market) rather than using the stock's own return.

---

## Database Table

```sql
CREATE TABLE sector_indices (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  index_name  TEXT NOT NULL,
  open        REAL,
  high        REAL,
  low         REAL,
  close       REAL,
  pct_change  REAL,
  pe_ratio    REAL,
  pb_ratio    REAL,
  div_yield   REAL,
  UNIQUE(date, index_name)
);
CREATE INDEX idx_sector_date       ON sector_indices(date);
CREATE INDEX idx_sector_index_name ON sector_indices(index_name);
```

---

## Backfill Script

**File:** `quant_engine/data/backfill_sector_indices.py`

```bash
# Backfill 2 years of sector index data
python3 -m quant_engine.data.backfill_sector_indices --from 2023-01-01

# Single date
python3 -m quant_engine.data.backfill_sector_indices --date 2025-03-26
```

**What it does:**
1. Downloads `ind_close_all_{DDMMYYYY}.csv` for each trading day
2. Parses all ~90 indices from the CSV
3. Upserts all rows into `sector_indices` (not just the ones we currently need — store everything, query selectively)
4. ~0.5s delay between requests

---

## Loader

**File:** `quant_engine/data/sector_indices_loader.py`

```python
from quant_engine.data.sector_indices_loader import load_sector_series, load_sector_pe

# Load closing prices for a sector index
series = load_sector_series("Nifty IT", limit=365)
# Returns: pd.Series indexed by date, values = closing price

# Load P/E ratio history for valuation analysis
pe_series = load_sector_pe("Nifty Bank", limit=365)
# Returns: pd.Series indexed by date, values = P/E ratio
```

---

## ML Feature Update: `sector_rotation`

**Updated in:** `quant_engine/ml/trainer.py`

**Before (broken for single-stock sectors):**
```python
# Average return of all stocks in same industry - benchmark return
industry_stocks = [s for s in all_symbols if industry_map.get(s) == stock_industry]
sector_return = mean([20d_return(s) for s in industry_stocks])
sector_rotation = (sector_return - nifty_20d_return) / scale
```

**After (uses real NSE sector index):**
```python
# Map stock's industry to official NSE sector index
nse_index = INDUSTRY_TO_NSE_INDEX.get(stock_industry, "Nifty 500")
sector_close = load_sector_series(nse_index, limit=100)
sector_20d_return = sector_close.pct_change(20)

nifty_close = load_sector_series("Nifty 50", limit=100)
nifty_20d_return = nifty_close.pct_change(20)

sector_rotation = ((sector_20d_return - nifty_20d_return) / 0.20).clip(-1, 1)
```

This now gives every stock a meaningful sector signal — even TANLA and BAJAJHIND.

---

## Bonus: Sector P/E as a Valuation Feature

The index file includes P/E, P/B, and dividend yield per sector. These can later be used to:
- Flag if a stock's P/E is at a premium/discount to its sector P/E
- Identify sectors trading at historical P/E extremes (mean reversion opportunity)
- Add a `sector_valuation` feature to the ML model in a future phase

---

## Files to Change

| File | Change |
|------|--------|
| `src/database/db.js` | Add `sector_indices` CREATE TABLE + indexes in `initDatabase()` |
| `quant_engine/config.py` | Add `INDUSTRY_TO_NSE_INDEX` mapping dict |
| `quant_engine/data/backfill_sector_indices.py` | New backfill script |
| `quant_engine/data/sector_indices_loader.py` | New loader |
| `quant_engine/ml/trainer.py` | Update `sector_rotation` feature computation |
