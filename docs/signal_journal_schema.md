# Signal Journal — Technical Reference

## `signals_log` table

```sql
CREATE TABLE IF NOT EXISTS signals_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_date     TEXT NOT NULL,   -- ISO date the signal was generated (YYYY-MM-DD)
  symbol          TEXT NOT NULL,   -- NSE ticker, e.g. "RELIANCE"
  signal          TEXT NOT NULL,   -- "LONG" | "HOLD" | "SHORT"
  composite_score REAL,            -- raw composite score in [-100, +100]
  recorded_at     TEXT NOT NULL,   -- full ISO timestamp of the write
  UNIQUE(signal_date, symbol)      -- one signal per stock per day; upsert on conflict
);

CREATE INDEX idx_signals_log_date   ON signals_log(signal_date);
CREATE INDEX idx_signals_log_symbol ON signals_log(symbol);
```

### Column notes

| Column | Type | Notes |
|---|---|---|
| `signal_date` | TEXT (ISO date) | Set to `new Date().toISOString().slice(0,10)` at write time, so it reflects the *calendar* date of the `/api/quant/scores` call, not the latest market date in the DB. |
| `signal` | TEXT | One of `LONG`, `HOLD`, `SHORT`. Thresholds: score ≥ 40 → LONG, score ≤ −40 → SHORT, otherwise HOLD. |
| `composite_score` | REAL | Weighted sum of the 7 factor scores, range −100 to +100. Nullable in edge cases where scoring fails for a stock. |
| `recorded_at` | TEXT (ISO datetime) | Full timestamp; useful for debugging if the same date is written multiple times (the UNIQUE constraint upserts to the latest values). |

---

## How signals are written

On every `GET /api/quant/scores` call in `src/routes/api.js`, after the response is sent to the client, a fire-and-forget write persists the signals:

```js
// api.js — /api/quant/scores handler (simplified)
const data = await response.json();
res.json(data);

if (data.stocks?.length) {
  saveSignalsLog(data.stocks.map(s => ({
    symbol:          s.symbol,
    signal:          s.signal,
    composite_score: s.composite_score
  }))).catch(err => console.error('signals_log write failed:', err));
}
```

The write is non-blocking — a failure does not affect the response to the client. The upsert semantics mean calling `/api/quant/scores` multiple times in the same day overwrites the signal for that day with the latest score.

`saveSignalsLog` lives in `src/database/db.js` and executes one `INSERT OR REPLACE` per stock using the Turso/libsql client.

---

## Point-in-time forward return join

`getSignalsHistory` (also in `src/database/db.js`) computes forward returns by joining `signals_log` to `price_history` twice — once for the entry price, once for the exit price — using a subquery that counts exactly N trading-day rows forward:

```sql
-- pattern shown for 20d; same approach for 1d (OFFSET 0), 5d (OFFSET 4), 10d (OFFSET 9)
LEFT JOIN price_history exit20
  ON exit20.symbol = sl.symbol
  AND exit20.date = (
    SELECT date FROM price_history
    WHERE symbol = sl.symbol AND date > sl.signal_date
    ORDER BY date ASC
    LIMIT 1 OFFSET 19          -- 20th trading day after signal_date
  )
```

The `OFFSET N−1` pattern resolves to the Nth available trading day in the DB. If fewer than N days of prices exist after the signal date, the subquery returns NULL and `forward_return_Nd` is NULL — the row is treated as PENDING in the UI.

Forward return formula:

```
forward_return_Nd = (exit_close - entry_close) / entry_close * 100
```

Rounded to 2 decimal places. Entry price is the closing price on `signal_date` itself (joined on exact date equality).

---

## IC formula

Computed in the Python `/api/signal-quality` endpoint. Pseudocode:

```python
from scipy.stats import spearmanr
import numpy as np

# rows: all settled signal rows with (signal_date, composite_score, forward_return_Nd)
# group cross-sectionally by date

ic_per_date = []
for date, group in rows.groupby("signal_date"):
    if len(group) < 3:          # too few stocks to rank meaningfully
        continue
    rho, _ = spearmanr(group["composite_score"], group["forward_return_Nd"])
    ic_per_date.append(rho)

mean_IC = np.mean(ic_per_date)
std_IC  = np.std(ic_per_date, ddof=1)
ICIR    = mean_IC / std_IC if std_IC > 0 else None
```

This is computed independently for each horizon (1d, 5d, 10d, 20d). Only rows where the forward return is not NULL (i.e., settled) are included.

---

## `/api/signal-quality` endpoint

**Route:** `GET /api/signal-quality` (Python FastAPI, port 5001, proxied through Node at the same path)

**Response shape:**

```json
{
  "horizons": {
    "1d":  { "hit_rate": 0.58, "IC": 0.07, "ICIR": 0.61, "n_obs": 120 },
    "5d":  { "hit_rate": 0.61, "IC": 0.09, "ICIR": 0.74, "n_obs": 118 },
    "10d": { "hit_rate": 0.60, "IC": 0.08, "ICIR": 0.70, "n_obs": 112 },
    "20d": { "hit_rate": 0.57, "IC": 0.06, "ICIR": 0.55, "n_obs": 95  }
  },
  "signals_logged": 310,
  "settled_20d":    95
}
```

- `hit_rate` — float in [0, 1]. Excludes HOLD signals from numerator and denominator.
- `IC` — Spearman rank IC averaged across cross-sectional dates (float, can be negative).
- `ICIR` — `mean_IC / std_IC`; null if fewer than 2 dates are available.
- `n_obs` — count of rows with a non-null forward return at that horizon.
- `signals_logged` — total rows in `signals_log` (all time).
- `settled_20d` — rows where `forward_return_20d` is not null.

---

## Frontend signal journal row schema

Each row returned by `getSignalsHistory` (used by the Signal Journal table):

| Field | Source |
|---|---|
| `signal_date` | `signals_log.signal_date` |
| `symbol` | `signals_log.symbol` |
| `signal` | `signals_log.signal` |
| `composite_score` | `signals_log.composite_score` |
| `recorded_at` | `signals_log.recorded_at` |
| `entry_price` | `price_history.close` on `signal_date` |
| `exit_price_20d` | `price_history.close` 20 trading days later |
| `forward_return_20d` | computed: `(exit - entry) / entry * 100`, null if pending |

UI status derivation:

```
if forward_return_20d IS NULL                       → "PENDING"
else if signal == "LONG"  AND return > 0            → "WIN"
else if signal == "SHORT" AND return < 0            → "WIN"
else if signal == "HOLD"                            → "HOLD"
else                                                → "LOSS"
```
