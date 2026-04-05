# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Wiki Discipline

The project maintains a research wiki at `wiki/`. **Every implementation must also update the wiki.**

- **Before implementing**: Run `grep -r "topic" wiki/` to find relevant pages. Read them.
- **After implementing**: Update `## Project Usage` section in the relevant paper/concept page.
- **New algorithm from a paper**: Create or update `wiki/papers/<paper>.md`.
- **New concept/technique**: Create or update `wiki/concepts/<concept>.md`.
- **Wiki index**: `wiki/README.md` — add any new pages here.
- **Raw PDFs**: `~/Desktop/Proyectos/Scilian-Books/` — ingest by reading and writing a wiki page.

Wiki operations:
- **Ingest** a paper: Read source PDF → write/update `wiki/papers/<slug>.md` covering: problem, method, key numbers, and `## Project Usage` section.
- **Query** the wiki before implementing anything new: search concept pages first.
- **Update** after any code change: add what was implemented, where, and what gaps remain.

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

# Backfill India VIX into market_regime table (required for regime features):
python3 -m quant_engine.data.backfill_regime --from-csv ~/Downloads/india_vix.csv
# Download CSV from: nseindia.com → Market Data → Volatility → Historical VIX
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
- `strategies/` — `sicilian_strategy.py` (linear composite, trend-following), `regime_adaptive_strategy.py` (extends Sicilian — switches between trend-following in BULL and mean-reversion in BEAR based on macro regime score), `markov_regime.py`, `mean_reversion_index.py`, `base.py`
- `ml/trainer.py` + `ml/predictor.py` — Buy/Hold/Sell classifier
- `data/loader.py` — Reads price history from SQLite; `data/market_regime_loader.py` — VIX/Nifty trend features
- `data/nse_fetcher.py` — Direct NSE India VIX fetcher (session-based, auto-chunks >1yr ranges); `data/backfill_regime.py` — CLI to populate `market_regime` table from NSE CSV or RapidAPI

### Multi-factor scoring
8 factors scored independently, weighted into a composite score (range −100 to +100):
- Momentum 25%, Mean Reversion 15%, RSI 15%, MACD 15%, Volatility 10%, Volume 10%, Relative Strength 10%, Bollinger 0% (computed but currently unweighted)
- Signal thresholds: ≥40 = LONG, −40 to 40 = HOLD, ≤−40 = SHORT
- `scores.py` exposes both `sicilian` and `regime_adaptive` strategies; regime_adaptive uses a macro regime score (VIX 35%, Nifty trend 25%, Markov 25%, FII flow 15%) to switch modes

### Frontend (`public/`)
- Vanilla JS SPA — no framework
- `js/app.js` (~1500 lines) — all dashboard logic; `js/backtest.js` — backtest UI
- Charts via Chart.js

### Caching strategy
Historical OHLCV data is cached in SQLite to minimize paid RapidAPI calls. Current quotes are derived from cached data; force-refresh triggers a new API call.

## ML Model Notes

- **TimeSeriesSplit CV must sort by date first** — sorting by stock before splitting causes data leakage (see `feedback_ml_cv_split.md`)
- Market regime features (VIX, Nifty trend) are now live; retrain whenever regime features change
- Model lives in `quant_engine/ml/`; training script is `trainer.py`
- `RegimeAdaptiveStrategy` inherits all factor calculators from `SicilianStrategy` and only overrides `generate_signals()` — do not duplicate factor logic

## Key environment variables

```
RAPIDAPI_KEY        # Required — Indian Stock Exchange API via RapidAPI
ALPHAVANTAGE_KEYS   # Optional fallback, comma-separated for rotation
PORT                # Node server port (default 3000)
```
