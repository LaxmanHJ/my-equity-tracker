# Turso DB Migration Plan

**Date:** 2026-03-28
**Current DB:** SQLite (`data/portfolio.db`, 1.9 MB)
**Target:** Turso (libSQL cloud, free tier — 9 GB storage)

---

## Why Turso

| Option | Free Storage | Notes |
|--------|-------------|-------|
| Turso | 9 GB | SQLite-compatible, minimal code changes |
| Supabase | 500 MB | Postgres, requires schema migration |
| Neon | 512 MB | Postgres, requires schema migration |

Turso is the best fit because it uses libSQL (a fork of SQLite) — SQL syntax is 100% compatible, no query rewrites needed.

---

## Current DB Schema

11 tables, 1.9 MB total.

| Table | Rows | Purpose |
|-------|-----:|---------|
| `price_history` | 9,730 | OHLCV data per symbol per day (2016–present) |
| `market_regime` | 992 | VIX / Nifty trend features for ML |
| `stock_shareholding` | 468 | Promoter/FII/DII holding over time |
| `stock_news` | 225 | Headlines, URLs, thumbnails |
| `stock_financials` | 180 | Annual/quarterly P&L, balance sheet, cash flow |
| `stock_peer_comparison` | 75 | Peer metrics per stock |
| `stock_fundamentals` | 15 | PE, PB, margins, growth ratios |
| `stock_fundamentals_sync` | 15 | Last fetch timestamp & status |
| `stock_analyst_ratings` | 15 | Buy/Hold/Sell analyst consensus |
| `alerts` | 0 | Price alert rules |
| `daily_reports` | 0 | Cached daily portfolio reports |

---

## Files That Touch the DB

### Node.js (writes + reads)
| File | Operations |
|------|-----------|
| `src/database/db.js` | Schema init, all CRUD functions |
| `src/services/stockData.js` | READ/WRITE `price_history` |
| `src/routes/api.js` | READ/WRITE alerts, reports, fundamentals |
| `src/services/fundamentalsService.js` | WRITE fundamentals, peers, news, analyst ratings; READ |

### Python (reads only, except backfill scripts)
| File | Mode | Operations |
|------|------|-----------|
| `quant_engine/data/loader.py` | Read | `price_history`, `stock_fundamentals`, `stock_analyst_ratings` |
| `quant_engine/data/market_regime_loader.py` | Read | `market_regime` table |
| `quant_engine/data/fundamentals_loader.py` | Read | `stock_fundamentals` |
| `quant_engine/routers/backtest.py` | Read | `price_history` for date range |
| `quant_engine/data/backfill_daily.py` | Read/Write | WRITE to `price_history` from RapidAPI |
| `quant_engine/data/backfill_regime.py` | Read/Write | CREATE + WRITE `market_regime` from CSV |

---

## Migration Steps

### Phase 1 — Turso Setup (~10 min)

```bash
# Install Turso CLI
brew install tursodatabase/tap/turso
turso auth login

# Create database
turso db create personal-stock-analyser

# Get credentials
turso db show personal-stock-analyser --url
turso db tokens create personal-stock-analyser
```

Add to `.env`:
```
TURSO_DATABASE_URL=libsql://personal-stock-analyser-<your-org>.turso.io
TURSO_AUTH_TOKEN=<token>
```

---

### Phase 2 — Data Migration (~5 min)

```bash
# Dump current SQLite
sqlite3 data/portfolio.db .dump > portfolio_dump.sql

# Push to Turso
turso db shell personal-stock-analyser < portfolio_dump.sql
```

Verify in Turso shell:
```bash
turso db shell personal-stock-analyser "SELECT COUNT(*) FROM price_history;"
```

---

### Phase 3 — Python Changes (low effort, ~30 min)

**Install driver:**
```bash
pip install libsql-experimental
```

**Update `quant_engine/config.py`:**
```python
# Before
DB_PATH = PROJECT_ROOT / "data" / "portfolio.db"

# After
import os
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
```

**Update each loader's connection (same change in all 6 files):**
```python
# Before
import sqlite3
conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

# After
import libsql_experimental as libsql
conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
```

`pd.read_sql_query()` works unchanged with the libsql connection.

---

### Phase 4 — Node.js Changes (medium effort, ~2–3 hrs)

This is the largest change. `better-sqlite3` is **synchronous**; `@libsql/client` is **async**. Every db function and its callers need `await`.

**Package swap:**
```bash
npm uninstall better-sqlite3
npm install @libsql/client
```

**`src/database/db.js` — client init:**
```js
// Before
import Database from 'better-sqlite3';
const db = new Database(dbPath);

// After
import { createClient } from '@libsql/client';
const db = createClient({
  url: process.env.TURSO_DATABASE_URL,
  authToken: process.env.TURSO_AUTH_TOKEN,
});
```

**All exported functions become async:**
```js
// Before
export function getPriceHistory(symbol) {
  return db.prepare(`SELECT * FROM price_history WHERE symbol = ?`).all(symbol);
}

// After
export async function getPriceHistory(symbol) {
  const result = await db.execute({
    sql: `SELECT * FROM price_history WHERE symbol = ?`,
    args: [symbol],
  });
  return result.rows;
}
```

**All callers in `api.js`, `stockData.js`, `fundamentalsService.js` need `await`:**
```js
// Before
const history = getPriceHistory(symbol);

// After
const history = await getPriceHistory(symbol);
```

WAL pragma is not needed — Turso handles this server-side.

---

### Phase 5 — Local Dev Replica (optional but recommended)

Turso supports an **embedded replica** — keeps a local SQLite file synced with the cloud DB. Zero-latency reads, cloud writes. App works offline too.

```python
# Python
conn = libsql.connect("local_replica.db", sync_url=TURSO_URL, auth_token=TURSO_TOKEN)
conn.sync()  # pull latest
```

```js
// Node.js
const db = createClient({
  url: 'file:local_replica.db',
  syncUrl: process.env.TURSO_DATABASE_URL,
  authToken: process.env.TURSO_AUTH_TOKEN,
});
await db.sync();
```

---

## Effort Summary

| Phase | Effort | Risk |
|-------|--------|------|
| Phase 1 — Turso setup | ~10 min | None |
| Phase 2 — Data migration | ~5 min | None |
| Phase 3 — Python loaders | ~30 min | Low |
| Phase 4 — Node.js async rewrite | ~2–3 hrs | Medium |
| Phase 5 — Local replica (optional) | ~30 min | Low |

---

## Rollback Plan

Keep `data/portfolio.db` in place and don't delete it until the migration is verified end-to-end. To rollback, revert the driver packages and restore the original connection strings. No data is lost since the SQLite file remains untouched during migration.
