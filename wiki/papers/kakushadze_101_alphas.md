# 101 Formulaic Alphas

**Authors**: Zura Kakushadze  
**Published**: Wilmott Magazine, 2016 (SSRN 2015)  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/101FormulaicAlphas.pdf`  
**Status**: Fully ingested

---

## What It Is

WorldQuant's compendium of 101 real trading alpha formulas used in live strategies. Each alpha is a mathematical expression over OHLCV + fundamental data that predicts future cross-sectional returns.

## Input Data

| Symbol | Meaning |
|--------|---------|
| `returns` | Daily returns |
| `open, close, high, low` | Price series |
| `volume` | Daily volume |
| `vwap` | Volume-weighted average price |
| `cap` | Market cap |
| `adv{d}` | Average daily dollar volume over d days |
| `IndClass` | Industry classification |

## Key Operators

| Operator | Description |
|----------|-------------|
| `rank(x)` | Cross-sectional rank [0,1] |
| `delay(x, d)` | x lagged d days |
| `delta(x, d)` | x - delay(x, d) |
| `correlation(x, y, d)` | Rolling d-day correlation |
| `decay_linear(x, d)` | Linearly weighted moving average |
| `indneutralize(x, g)` | Remove industry mean |
| `ts_rank(x, d)` | Time-series rank over d days |
| `ts_min/max(x, d)` | Rolling min/max |
| `scale(x)` | Normalize so sum of abs values = 1 |
| `stddev(x, d)` | Rolling standard deviation |
| `signedpower(x, a)` | sign(x) * abs(x)^a |

## Empirical Finding

Returns scale as **R ~ V^0.76** (volatility to the 0.76 power). Turnover has no significant independent effect on returns. This validates volatility-adjusted position sizing.

## Representative Alphas

- **Alpha#1**: `rank(Ts_ArgMax(SignedPower(returns,1), 5)) - 0.5` — recent return direction
- **Alpha#4**: `-ts_rank(rank(low), 9)` — low-price rank reversal
- **Alpha#6**: `-correlation(open, volume, 10)` — open-volume anticorrelation
- **Alpha#12**: `sign(delta(volume,1)) * (-delta(close,1))` — volume-price divergence
- **Alpha#101**: `((close - open) / ((high - low) + 0.001))` — intraday bar shape

Most alphas combine: (1) a price/volume signal, (2) cross-sectional ranking, (3) time-series lookback, and optionally (4) industry neutralization.

## Classification

Roughly split between **mean-reversion** (majority, using `rank`, reversal patterns) and **momentum** (using `delay`, `ts_rank` over longer horizons). Short lookbacks (1–5 days) tend to be mean-reverting; longer lookbacks (12–252 days) tend to be momentum.

## References in Paper

- Grinold & Kahn (2000) — IC/ICIR framework
- Jegadeesh & Titman (1993) — momentum foundation
- Avellaneda & Lee (2010) — statistical arbitrage
- Pastor & Stambaugh (2003) — liquidity risk

## Project Usage

- **`quant_engine/factors/`**: Each factor module computes a signal conceptually equivalent to one or more alphas. The `rank()`-based normalization in `scores.py` mirrors the cross-sectional ranking here.
- **Roadmap**: The `indneutralize` operator maps to sector-neutralization, not yet implemented. `adv{d}` liquidity filtering not yet applied.
- **Key gap**: Our factors don't use VWAP or intraday OHLC data — we use only daily OHLCV.
