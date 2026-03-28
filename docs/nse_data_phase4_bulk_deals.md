# Phase 4 — Bulk Deals & Block Deals

**Status:** Implemented
**Priority:** Medium — event-based institutional signal; ML feature pending data accumulation

---

## Why This Matters

Phases 1–3 improved the model's continuous signals: delivery percentage, sector rotation, and FII/DII flows. Phase 4 adds **event-based institutional signals** — discrete, high-information events that happen irregularly but carry outsized informational value.

When a single entity buys or sells more than 0.5% of a company's shares in one day, NSE classifies it as a **bulk deal**. These are typically institutional funds, promoters, or large investors making significant position changes.

For a 15-stock portfolio, a bulk deal by a known institution is more informative than 10 days of price action. A PE firm selling ₹500 crore worth of INFY shares is a data point that the existing model would have no awareness of — it would see only the resulting price movement, not the cause.

**Block deals** are pre-negotiated large trades executed in the first 35 minutes of trading. They indicate planned institutional accumulation or distribution and often precede multi-day directional moves.

**The current model has zero awareness of these events.** This phase gives it a path to awareness — first via dashboard visibility and alerts, then via an ML feature once sufficient history accumulates.

Note: Short selling data was investigated but no accessible archive was found. It is skipped for now and may be revisited in a future phase.

---

## Data Sources

### Bulk Deals CSV

```
https://nsearchives.nseindia.com/content/equities/bulk.csv
```

### Block Deals CSV

```
https://nsearchives.nseindia.com/content/equities/block.csv
```

**Authentication:** None required. A standard `User-Agent` header is sufficient. No NSE session cookie needed.

**Update frequency:** Both files update daily with the current trading day's deals.

**Historical data:** No archive files exist for either source. Data accumulates from the implementation date onwards. There is no way to backfill historical bulk or block deals from these endpoints.

**CSV format** (identical for both files):

```
Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price
27-MAR-2026,CUBEINVIT,Cube Highways Trust,SPARK FINANCIAL HOLDINGS,BUY,1800000,146.00
```

Both files are filtered to portfolio symbols only before being saved to the database.

---

## Database Schema

A single table stores both bulk and block deals, distinguished by the `deal_type` column:

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
```

The unique constraint on `(date, symbol, client_name, deal_type)` makes upserts safe — re-running the fetch on the same day will not create duplicate rows.

---

## Files Changed

| File | Change |
|------|--------|
| `src/database/db.js` | Added `bulk_block_deals` table + indexes in schema; added `saveBulkDeals(deals)` and `getBulkDeals(symbol, limit)` functions |
| `quant_engine/data/backfill_bulk_deals.py` | New script — downloads both CSVs, filters for portfolio symbols, upserts to DB |
| `src/services/stockData.js` | Added `fetchBulkDealsToday()` — fetches both CSVs via HTTP, saves to DB |
| `src/routes/api.js` | Added `GET /api/bulk-deals/:symbol` endpoint; `fetchBulkDealsToday()` is also called during force sync |
| `public/js/app.js` | Added bulk deals panel to individual stock analysis page |

---

## API Endpoints

### `GET /api/bulk-deals/:symbol`

Returns the most recent bulk and block deals for a given stock symbol.

**Example request:**
```
GET /api/bulk-deals/INFY
```

**Example response:**
```json
{
  "symbol": "INFY",
  "deals": [
    {
      "date": "2026-03-27",
      "clientName": "SOME FUND LTD",
      "tradeType": "SELL",
      "quantity": 2500000,
      "price": 1285.50,
      "dealType": "BULK"
    },
    {
      "date": "2026-03-27",
      "clientName": "ANOTHER FUND",
      "tradeType": "BUY",
      "quantity": 1200000,
      "price": 1270.00,
      "dealType": "BLOCK"
    }
  ]
}
```

Returns up to 20 most recent deals by default. Returns an empty `deals` array if no deals are recorded for the symbol.

---

## Frontend Integration

The bulk deals panel appears on the individual stock analysis page when viewing a specific stock.

**Display:** A table showing the last 20 bulk and block deals for the currently viewed stock.

**Columns:** Date, Client Name, Type (BULK / BLOCK), Buy/Sell, Quantity, Price.

**Color coding:** BUY rows are highlighted green; SELL rows are highlighted red.

**Empty state:** Displays "No bulk or block deals recorded yet" when no data is available (expected for the first days after implementation, and for stocks that rarely appear in deals).

---

## Backfill & Daily Automation

### Python backfill script

`quant_engine/data/backfill_bulk_deals.py` downloads both CSVs and upserts records for portfolio symbols:

```bash
python3 -m quant_engine.data.backfill_bulk_deals
```

This script can be run at any time. Re-running it on the same day is safe due to the upsert logic and the unique constraint on the database table.

### Daily automation (Node.js)

`fetchBulkDealsToday()` in `src/services/stockData.js` fetches both CSV files and saves the results to the database. This function is called automatically as part of the force sync flow (`GET /api/sync/force`), so bulk/block deal data refreshes whenever the user triggers a full data sync.

There is no separate cron job required — the data is always current after the next force sync.

---

## ML Feature (Future — After 6 Months of Data)

Bulk and block deal data is used for dashboard visibility and future alerting in this phase, not as an active ML feature. The data volume from a 15-stock portfolio is too low for a reliable ML signal until meaningful history accumulates.

Once approximately 6 months of deal history is available, the following feature becomes viable:

```python
# bulk_deal_score: net buy/sell direction over last 20 trading days
# +1.0 = all bulk deals in the past 20 days were BUY
# -1.0 = all bulk deals in the past 20 days were SELL
#  0.0 = mixed or no deals
net_deals = bulk_buys_20d - bulk_sells_20d
bulk_deal_score = (net_deals / max(total_deals_20d, 1)).clip(-1, 1)
```

This score would be added as a new factor in `quant_engine/strategies/sicilian_strategy.py` alongside the existing `delivery_score` and `sector_rotation` features. Suggested initial weight: 5–10%, replacing or supplementing part of the volume factor.

The ML model will need retraining once this feature is added. See `quant_engine/ml/trainer.py`.

---

## Implementation Notes

### Why a single combined table

Bulk and block deals share an identical CSV schema and carry the same semantic meaning (large institutional trade). Splitting them into two tables would add query complexity without any practical benefit. The `deal_type` column provides full discrimination when needed.

### No session cookie required

The NSE archives endpoint (`nsearchives.nseindia.com`) serves CSV files without requiring an NSE session. This is simpler and more reliable than the session-based approach used in some other NSE integrations. A plain HTTP GET with a browser-like `User-Agent` header is sufficient.

### No historical backfill possible

Unlike price data or delivery data (which NSE archives by date), there are no historical archive files for bulk or block deals at these endpoints. The dataset starts accumulating from the day the feature is first deployed. This is the primary reason the ML feature is deferred — a useful signal requires months of history.

### Data volume expectations

A 15-stock NSE portfolio will not appear in bulk deals every day. For large-cap stocks like INFY or TCS, deals may appear a few times per quarter. For mid-caps like TANLA or REPCOHOME, deals may be rarer but more informative when they do occur. The database table and the empty state in the frontend are both designed to handle sparse data gracefully.
