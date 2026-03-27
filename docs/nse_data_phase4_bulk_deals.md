# Phase 4 — Bulk Deals, Block Deals & Short Selling

**Status:** Planned
**Priority:** Medium — alert triggers and contrarian signals, lower ML value

---

## Why This Matters

Phases 1–3 improve the model's continuous signals. Phase 4 adds **event-based signals** — discrete, high-information events that happen irregularly but carry outsized informational value.

### Bulk & Block Deals
When a single entity buys or sells more than 0.5% of a company's shares in one day, NSE classifies it as a **bulk deal**. These are often institutional funds, promoters, or large investors making significant position changes.

For a 15-stock portfolio, if INFY appears in a bulk deal with a PE firm selling ₹500 crore worth of shares, that is more informative than 10 days of price action. The current system has no awareness of these events.

**Block deals** are pre-negotiated large trades executed in the first 35 minutes of trading. They indicate planned institutional accumulation or distribution.

### Short Selling
NSE publishes weekly short selling data showing which institutional participants sold short and how much. High short interest in a mid-cap like REPCOHOME or TANLA can mean:
- **Bearish signal:** Smart money has a thesis that the stock is overvalued
- **Contrarian signal:** Very high short interest = potential short squeeze setup if news turns positive

---

## Data Sources

### Bulk & Block Deals — NSE API

**Live endpoints:**
```
https://www.nseindia.com/api/bulk-deal
https://www.nseindia.com/api/block-deal
```
**Authentication:** NSE session cookie (same mechanism as `nse_fetcher.py` in the existing codebase)
**Response format:**
```json
[
  {
    "symbol": "INFY",
    "date": "26-Mar-2025",
    "clientName": "SOME FUND LTD",
    "dealType": "BUY",
    "quantity": 2500000,
    "price": 1285.50
  },
  ...
]
```

**Historical data:** Download from NSE website → `nseindia.com/report-detail/display-bulk-and-block-deals` → select date range → Export CSV.

### Short Selling

**URL:** Short selling archive — exact URL to be confirmed (NSE moved this page).
Fallback: Manual CSV download from `nseindia.com/products/content/equities/equities/bulk.htm` → Short Selling tab.

**File format:**
```csv
Symbol, Short Selling Qty, % of Total Short Selling, ISIN
INFY, 125000, 0.12, INE009A01021
TATASTEEL, 85000, 0.08, INE081A01020
```

---

## Database Tables

```sql
CREATE TABLE bulk_block_deals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  symbol      TEXT NOT NULL,
  client_name TEXT,
  trade_type  TEXT,        -- 'BUY' or 'SELL'
  quantity    INTEGER,
  price       REAL,
  deal_type   TEXT,        -- 'BULK' or 'BLOCK'
  UNIQUE(date, symbol, client_name, deal_type)
);
CREATE INDEX idx_bulk_symbol ON bulk_block_deals(symbol);
CREATE INDEX idx_bulk_date   ON bulk_block_deals(date);

CREATE TABLE short_selling (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol    TEXT NOT NULL,
  date      TEXT NOT NULL,
  short_qty REAL,
  short_pct REAL,
  UNIQUE(symbol, date)
);
CREATE INDEX idx_short_symbol ON short_selling(symbol);
```

---

## Backfill Scripts

### `quant_engine/data/backfill_bulk_deals.py`

```bash
# Historical import from manually downloaded CSV
python3 -m quant_engine.data.backfill_bulk_deals --from-csv ~/Downloads/bulk_deals.csv

# Live fetch for today (requires NSE session)
python3 -m quant_engine.data.backfill_bulk_deals --today
```

Uses the existing `nse_fetcher.py` session management pattern for the live API call.

### `quant_engine/data/backfill_short_selling.py`

```bash
# Weekly CSV import
python3 -m quant_engine.data.backfill_short_selling --from-csv ~/Downloads/shortselling.csv
```

Short selling data is published weekly, so this script is run once per week after downloading the CSV.

---

## API Endpoints (Node.js)

Add to `src/routes/api.js`:

### `GET /api/bulk-deals/:symbol`
Returns recent bulk/block deals for a stock.
```json
{
  "symbol": "INFY",
  "deals": [
    {"date": "2025-03-26", "clientName": "SOME FUND LTD", "tradeType": "SELL", "quantity": 2500000, "price": 1285.50, "dealType": "BULK"},
    {"date": "2025-03-20", "clientName": "ANOTHER FUND", "tradeType": "BUY",  "quantity": 1200000, "price": 1270.00, "dealType": "BLOCK"}
  ]
}
```

### `GET /api/short-selling/:symbol`
Returns short interest trend for a stock.
```json
{
  "symbol": "TANLA",
  "shortSelling": [
    {"date": "2025-03-22", "shortQty": 85000, "shortPct": 0.42},
    {"date": "2025-03-15", "shortQty": 62000, "shortPct": 0.31}
  ]
}
```

---

## Frontend Integration

Surface on the individual stock analysis page:

**Bulk Deals panel:** Show last 10 bulk/block deals for the currently viewed stock. Color-code: green for BUY, red for SELL. Include client name, quantity, price.

**Short Interest panel:** Mini chart showing short selling % over the last 8 weeks. A rising trend is bearish; a sudden spike followed by price strength is a squeeze signal.

**Alert integration:** If a new SELL bulk deal is detected for a portfolio stock during today's session, trigger an alert via the existing alerts system with `type = "BULK_SELL"`.

---

## ML Feature (Optional — Future Enhancement)

Phase 4 data is primarily used for alerts and dashboard display, not ML features. However, once 6+ months of data is accumulated, consider:

```python
# bulk_deal_score: rolling count of net bulk deal direction over last 20 days
# +1 = all bulk deals were BUY in past 20 days
# -1 = all bulk deals were SELL in past 20 days
# 0  = mixed or no deals
net_deals = bulk_buys_20d - bulk_sells_20d
bulk_deal_score = (net_deals / max(total_deals_20d, 1)).clip(-1, 1)
```

This requires meaningful deal history before it becomes reliable as an ML feature.

---

## Files to Change

| File | Change |
|------|--------|
| `src/database/db.js` | Add `bulk_block_deals` and `short_selling` CREATE TABLE + indexes |
| `quant_engine/data/backfill_bulk_deals.py` | New backfill script |
| `quant_engine/data/backfill_short_selling.py` | New backfill script |
| `src/routes/api.js` | Add `GET /api/bulk-deals/:symbol` and `GET /api/short-selling/:symbol` |
| `src/database/db.js` | Add `getBulkDeals()` and `getShortSelling()` db functions |
| `public/js/app.js` | Add bulk deals and short interest panels to stock analysis page |

---

## Implementation Note on NSE Session

The bulk/block deals live API requires an NSE session cookie. The existing `nse_fetcher.py` already handles NSE session management. Extend it with:

```python
def fetch_bulk_deals(self) -> list[dict]:
    """Fetch today's bulk deals from NSE API."""
    return self._get("/api/bulk-deal")

def fetch_block_deals(self) -> list[dict]:
    """Fetch today's block deals from NSE API."""
    return self._get("/api/block-deal")
```

The session is refreshed automatically by the existing mechanism — no additional auth work needed.
