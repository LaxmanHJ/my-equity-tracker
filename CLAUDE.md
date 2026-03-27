# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Running the project
```bash
npm run dev:all        # Start both servers concurrently (recommended)
npm run dev            # Node.js only (port 3000)
npm run dev:quant      # Python Quant Engine only (port 5001)
npm run stop           # Kill both ports
```

### Setup
```bash
npm install
pip install -r quant_engine/requirements.txt
cp .env.example .env   # then fill in RAPIDAPI_KEY
```

### Data management
```bash
npm run fetch          # Backfill historical price data into SQLite
```

### Testing
```bash
npm test                                    # Node.js tests
cd quant_engine && python -m pytest tests/  # Python tests
python -m pytest tests/test_sicilian.py     # Single Python test file
```

### Health checks
```bash
curl http://localhost:3000/health
curl http://localhost:5001/health
```

## Architecture

**Dual-engine design:**
- **Node.js/Express** (port 3000) — web server, API gateway, portfolio management, SQLite writes
- **Python/FastAPI** (port 5001) — heavy quant computation (multi-factor scoring, backtesting, ML)
- **SQLite** (`data/portfolio.db`, WAL mode) — single source of truth; Node writes, Python reads

Node proxies quant/ML/backtest requests to Python over HTTP. The Python engine never makes external API calls — it only reads from SQLite.

### Node.js backend (`src/`)
- `server.js` — Express app entry point
- `routes/api.js` — All API endpoints
- `services/` — External data fetching (RapidAPI primary, Alpha Vantage fallback)
- `analysis/` — Technical indicators (RSI, MACD, Bollinger Bands), risk metrics (Beta, Sharpe, VaR), correlation matrix
- `database/db.js` — SQLite schema and all query functions
- `config/portfolio.js` — 15 NSE stocks with quantities and buy prices; `config/settings.js` — indicator parameters

### Python Quant Engine (`quant_engine/`)
- `main.py` — FastAPI app
- `routers/` — `scores.py`, `backtest.py`, `sicilian.py`, `ml.py`, `index_analysis.py`
- `factors/` — Individual scoring modules (momentum, mean_reversion, rsi, macd, volatility, volume, relative_strength) — each returns a score in [-1.0, +1.0]
- `strategies/` — `sicilian_strategy.py` (main strategy with market regime features), `markov_regime.py`
- `ml/trainer.py` + `ml/predictor.py` — Buy/Hold/Sell classifier
- `data/loader.py` — Reads price history from SQLite; `data/market_regime_loader.py` — VIX/Nifty trend features

### Multi-factor scoring
7 factors scored independently, weighted into a composite score (range −100 to +100):
- Momentum 25%, Mean Reversion 15%, RSI 15%, MACD 15%, Volatility 10%, Volume 10%, Relative Strength 10%
- Signal thresholds: ≥40 = LONG, −40 to 40 = HOLD, ≤−40 = SHORT

### Frontend (`public/`)
- Vanilla JS SPA — no framework
- `js/app.js` (~1500 lines) — all dashboard logic; `js/backtest.js` — backtest UI
- Charts via Chart.js

### Caching strategy
Historical OHLCV data is cached in SQLite to minimize paid RapidAPI calls. Current quotes are derived from cached data; force-refresh triggers a new API call.

## ML Model Notes

- **TimeSeriesSplit CV must sort by date first** — sorting by stock before splitting causes data leakage (see `feedback_ml_cv_split.md`)
- Market regime features (VIX, Nifty trend) were added in the most recent commit; model may need retraining when these features change
- Model lives in `quant_engine/ml/`; training script is `trainer.py`

## Key environment variables

```
RAPIDAPI_KEY        # Required — Indian Stock Exchange API via RapidAPI
ALPHAVANTAGE_KEYS   # Optional fallback, comma-separated for rotation
PORT                # Node server port (default 3000)
```
