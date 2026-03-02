# Python Quant Engine — Implementation Plan

## Overview

Add a **Python FastAPI microservice** (`quant_engine/`) running on port `5001` alongside the existing Node.js app on port `3000`. Python handles all quantitative calculations; Node.js handles the web UI and data pipeline.

## Architecture

```mermaid
graph LR
    Browser -->|HTTP| Node["Node.js :3000<br/>Web UI + API"]
    Node -->|HTTP :5001| Python["Python FastAPI :5001<br/>Quant Engine"]
    Node -->|Read/Write| DB[(SQLite DB)]
    Python -->|Read Only| DB
```

The Python engine **reads directly from the same SQLite database** that Node.js writes to. No data duplication, no syncing needed.

---

## Proposed Changes

### Python Quant Engine (all new files)

#### [NEW] `quant_engine/` — Project root

```
quant_engine/
├── requirements.txt        # FastAPI, uvicorn, pandas, numpy, scipy
├── main.py                 # FastAPI app entry point + health check
├── config.py               # DB path, settings
├── data/
│   └── loader.py           # Reads price history from SQLite
├── factors/
│   ├── momentum.py         # 1m, 3m, 6m price returns
│   ├── mean_reversion.py   # Z-score vs 50-day SMA
│   ├── volatility.py       # Current vol vs historical avg
│   ├── rsi.py              # RSI extremes (< 30, > 70)
│   ├── macd.py             # MACD histogram direction + crossovers
│   ├── volume.py           # Volume spike detection
│   └── relative_strength.py # Stock return vs NIFTY return
├── scoring/
│   └── composite.py        # Weighted multi-factor composite score
└── routers/
    └── scores.py           # /api/scores endpoint
```

#### Factor Score Design

Each factor returns a score from **-1.0 (strong short)** to **+1.0 (strong long)**:

| Factor | Weight | Score = -1 | Score = +1 |
|---|---|---|---|
| Momentum (3m) | 25% | Worst 3-month return | Best 3-month return |
| Mean Reversion | 15% | Z-score > +2 (overbought) | Z-score < -2 (oversold) |
| RSI | 15% | RSI > 80 | RSI < 20 |
| MACD | 15% | Bearish crossover | Bullish crossover |
| Volatility | 10% | Vol expanding (risky) | Vol contracting (stable) |
| Volume | 10% | No unusual activity | Volume spike confirming trend |
| Relative Strength | 10% | Underperforming NIFTY | Outperforming NIFTY |

**Composite Score** = Σ (factor_score × weight) × 100 → range **-100 to +100**

> **≥ 40** → LONG signal | **-40 to 40** → HOLD | **≤ -40** → SHORT signal

---

### Node.js Changes

#### [MODIFY] [api.js](file:///Users/elj/Desktop/Proyectos/PersonalStockAnalyser/src/routes/api.js)
- Add proxy endpoint `/api/quant/scores` that fetches from Python `http://localhost:5001/api/scores`

#### [MODIFY] [index.html](file:///Users/elj/Desktop/Proyectos/PersonalStockAnalyser/public/index.html)
- Add "Quant Signals" nav tab

#### [MODIFY] [app.js](file:///Users/elj/Desktop/Proyectos/PersonalStockAnalyser/public/js/app.js)
- Add `loadQuantScores()` function and render ranked stock cards

---

## Verification Plan

### Automated Tests
- `curl http://localhost:5001/health` → `{"status": "ok"}`
- `curl http://localhost:5001/api/scores` → JSON array of 15 stocks with composite scores
- Browser test: navigate to Quant Signals tab, verify ranked cards display

### Manual Verification
- Cross-check factor scores against known stock behavior
- Verify composite score matches manual weighted calculation
