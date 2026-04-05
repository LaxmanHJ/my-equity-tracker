# A Century of Evidence on Trend-Following Investing

**Authors**: Brian Hurst, Yao Hua Ooi & Lasse Heje Pedersen  
**Published**: Journal of Portfolio Management, 2017 (AQR Capital Management)  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/A Century of Evidence on Trend-Following .pdf`  
**Status**: Fully ingested

---

## The Finding

Time-series momentum (trend-following) generates positive returns across **137 years** (1880–2012) and across **67 markets** in 4 asset classes. Sharpe ratio ~0.76 net of fees.

## What Is Trend-Following

Buy assets that have gone up over the past 1, 3, or 12 months. Sell (or short) assets that have gone down. Applied as a **time-series** strategy — each asset decides long/short independently based on its own past return (vs. cross-sectional momentum which ranks assets against each other).

## The 3-Signal Combination

| Lookback | Signal Type |
|----------|-------------|
| 1 month | Short-term trend |
| 3 months | Medium-term trend |
| 12 months | Long-term trend |

All three signals combined, equal-weighted. Position size is **volatility-scaled**: position = signal × (target_vol / realized_vol). This keeps risk constant across assets and time.

## Key Numbers

| Metric | Value |
|--------|-------|
| Sharpe ratio (net) | 0.76 |
| Win rate by decade | Positive in all 14 decades |
| Worst decade | 1990s (0.27 SR) |
| Best decade | 1970s (1.35 SR — inflation trends) |
| Crisis alpha | Positive in most major crises |

## The "Smile" Pattern

Trend-following performs best at the extremes of equity returns:
- **Extreme down markets (crashes)**: Bonds rally, commodities fall → trend captures both
- **Extreme up markets (bull runs)**: Rides equity trends up
- **Middle (flat to mildly volatile)**: Smallest positive contribution

This creates a "smile" when plotting trend-following returns against equity market returns — it acts as **crisis insurance** while still positive in bull markets.

## 4 Asset Classes

1. **Equity index futures** — 29 markets
2. **Fixed income futures** — 11 markets
3. **Commodity futures** — 24 markets
4. **Currency forwards** — 9 pairs

NSE-only equity investors get only 1 of 4 classes → diversification benefit is much lower.

## Factor Attribution

Paper decomposes returns into:
- **Pure trend signal**: ~60% of SR
- **Carry** (positive roll yield in futures): ~20% of SR
- **Residual** (unexplained): ~20%

## Why It Works

Three behavioral explanations cited:
1. **Initial under-reaction**: Investors anchored to past prices, slow to update → trend persists
2. **Herding**: As trend becomes obvious, more investors pile in → trend extends
3. **Risk management**: Stop-loss orders + forced deleveraging create feedback loops in extreme moves

## Difference from Cross-Sectional Momentum

| Feature | Cross-Sectional (J&T) | Time-Series (Trend) |
|---------|----------------------|---------------------|
| Signal | Rank vs. peers | Own past return |
| Long/Short | Dollar neutral | Not necessarily |
| Universe | Equities only | Multi-asset |
| Holding period | 3-12 months | Continuous/daily |

Correlation between the two: ~0.5 — related but distinct.

## Project Usage

- **`quant_engine/strategies/sicilian_strategy.py`**: Implements trend-following logic (the strategy name "Sicilian" emphasizes trend). The composite score with momentum at 25% is a cross-sectional approach, not pure time-series.
- **`quant_engine/factors/momentum.py`**: Uses 12-month lookback (1 of the 3 signals here). Missing: 1-month and 3-month signals.
- **Gap — volatility scaling**: Not implemented. Position sizes don't scale with realized volatility. This paper says vol-scaling is essential for consistent risk. Would improve signal quality.
- **Gap — multi-signal trend**: Only 12-month signal used. Adding 1-month and 3-month lookbacks and averaging would smooth the momentum factor.
- **"Smile" = regime protection**: Our regime_adaptive strategy aims for similar crisis protection. The VIX-based regime switch is consistent with the "smile" pattern (bear market = reduce trend, increase mean-reversion).
