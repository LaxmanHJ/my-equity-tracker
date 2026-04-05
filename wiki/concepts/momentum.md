# Momentum

## Two Types

### 1. Cross-Sectional Momentum (Jegadeesh & Titman 1993)
Rank stocks by past 12-month return. Buy top decile, short bottom decile.

- **Signal**: rank(return_12_1) — 12-month return, skipping most recent month
- **Holding period**: 1–3 months
- **Return**: 1.31%/month (12/3 strategy)
- **Reversal**: After month 13, cumulative gains erode (~50% lost by month 31)
- **January**: Strongly negative — momentum reverses in January (tax-loss selling unwind)

### 2. Time-Series Momentum / Trend-Following (Hurst et al. 2017)
Each asset independently goes long if it's up over the past N months.

- **Signals**: 1-month, 3-month, 12-month (combined)
- **Volatility scaling**: position × (target_vol / realized_vol)
- **Sharpe**: 0.76 net across 137 years, 67 markets
- **"Smile"**: Best in extreme up/down markets; acts as crisis insurance

**Key difference**: Cross-sectional = "this stock beat others"; time-series = "this stock beat itself"

## Current Implementation

**File**: `quant_engine/factors/momentum.py`  
**Signal**: 12-month return skipping most recent month (cross-sectional)  
**Weight**: 25% in composite score (`sicilian_strategy.py`)

```python
# Conceptually: momentum_score = pct_change(price, 252 days, skip 21 days)
```

## Value-Momentum Combination

From Asness et al. (2013): combining value + momentum at −0.4 correlation achieves SR ~1.45 vs ~0.65 standalone. The single highest-leverage improvement available.

## Gaps vs. Literature

| Gap | Paper | Priority |
|-----|-------|---------|
| Missing 1-month and 3-month signals | Hurst (2017) | Medium |
| No volatility scaling | Hurst (2017) | Medium |
| No value factor to combine with | Asness (2013) | **High** |
| January effect not handled | J&T (1993) | Low |
| No IC tracking | Grinold & Kahn | Medium |

## Related Concepts
- [factor_scoring.md](factor_scoring.md) — how momentum score is combined with others
- [mean_reversion.md](mean_reversion.md) — mean reversion is the "opposite" strategy; regime determines which to use
- [regime_detection.md](regime_detection.md) — BULL → momentum, BEAR → mean reversion
