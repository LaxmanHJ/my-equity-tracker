# Market Regime Detection

## What Is a Regime

A regime is a persistent state of the market (BULL, BEAR, SIDEWAYS) that changes which strategies work. Momentum dominates BULL; mean-reversion dominates BEAR.

## Current Implementation

**Files**: `strategies/regime_adaptive_strategy.py`, `strategies/markov_regime.py`, `data/market_regime_loader.py`

### Macro Regime Score

Composite of 4 signals (range: −100 to +100):

| Signal | Weight | Source |
|--------|--------|--------|
| India VIX | 35% | NSE India via `nse_fetcher.py` |
| Nifty 50 trend | 25% | Price series from SQLite |
| Markov Hidden Regime | 25% | `markov_regime.py` |
| FII Flow | 15% | (Placeholder/estimated) |

### Regime Classification

```
macro_score ≥ +20  → BULL
−20 < macro_score < +20  → NEUTRAL
macro_score ≤ −20  → BEAR
```

### Strategy Switching

- **BULL**: Standard Sicilian weights (momentum-heavy: 25%)
- **BEAR**: Shift to mean-reversion (increase RSI/MR weights, reduce momentum)
- **NEUTRAL**: Default weights

## VIX as Regime Signal

India VIX is the primary regime indicator (35% weight). VIX measures 30-day implied volatility of Nifty 50 options.

| VIX Level | Regime Interpretation |
|-----------|----------------------|
| < 15 | Low fear — BULL |
| 15–25 | Normal — NEUTRAL |
| > 25 | High fear — BEAR |
| > 35 | Panic — Extreme BEAR |

**Data backfill**: `python3 -m quant_engine.data.backfill_regime --from-csv ~/Downloads/india_vix.csv`  
Source: nseindia.com → Market Data → Volatility → Historical VIX

## Markov Hidden Regime Model

**File**: `strategies/markov_regime.py`  
Uses returns distribution properties to infer hidden regime:
- 2-state HMM: low-volatility (BULL) / high-volatility (BEAR)
- Parameters estimated on rolling window of returns
- Outputs: P(regime=BULL), P(regime=BEAR)

## Trend-Following vs. Mean-Reversion Rotation

From Hurst et al. (2017): trend-following exhibits "smile" pattern — works best at extremes (strong bull or strong bear). Our regime switch:
- BULL → emphasize trend/momentum (right side of smile)
- BEAR → emphasize mean-reversion (bottom of smile — early bear, overshooting)

## FII Flow

Foreign Institutional Investor net buying/selling in NSE. Positive FII → bullish signal. Currently partially implemented.

## Data Storage

**Table**: `market_regime` in Turso (cloud libSQL). Access via `quant_engine/data/turso_client.connect()` on the Python side or `@libsql/client` on the Node side.

| Column | Description |
|--------|-------------|
| date | Trading date |
| vix | India VIX value |
| nifty_close | Nifty 50 closing price |
| regime_label | BULL/BEAR/NEUTRAL |

## Gaps

| Gap | Priority |
|-----|---------|
| FII flow data not reliably fetched | Medium |
| Markov model not retrained on regime data | Medium |
| No look-ahead validation of regime calls | Low |
| VIX thresholds hardcoded, not adaptive | Low |

## Related Concepts
- [momentum.md](momentum.md) — BULL regime strategy
- [mean_reversion.md](mean_reversion.md) — BEAR regime strategy
- [factor_scoring.md](factor_scoring.md) — regime adjusts weights
- [ml_pipeline.md](ml_pipeline.md) — VIX/Nifty trend as ML features
