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

## Study A — monthly 12-1 momentum on NIFTY200 PIT (2026-07-25) — FAIL

The one cost-survivable frequency band never previously tested. Pre-registered
declared rule in `quant_engine/research/monthly_momentum.py` (docstring is the
registration); results in `data/monthly_momentum_study.json`.

Rule: mom_12_1 = close[t−21]/close[t−252] − 1, NIFTY200 PIT universe (2018→,
333 symbols, avg 194 eligible/month), month-end rebalance, 99 months, 30bp
one-way costs on replaced names.

| Portfolio | Gross | Net | Sharpe (net) | Turnover/mo |
|---|---|---|---|---|
| Long-only top-15 EW | 24.8%/yr | 22.6%/yr | 0.91 | 30.1% |
| LS decile EW | 15.2%/yr | 10.9%/yr | 0.42 | 29.7% |

Long-only net excess vs ^NSEI: +11.65%/yr, IR 0.74, monthly hit 54.5%.

**Gates (declared before run): G1 LS net Sharpe > 0.6 → FAIL (0.42).
G2 PSR > 0.95 → FAIL (0.878). G3 LO net excess > 0 → pass. Verdict: FAIL.**

Interpretation: the momentum premium exists on NSE at monthly cadence (gross
LS Sharpe 0.59, strong long-only cell) but the alpha does not clear the
C0-consistent bar net of costs; the long-only excess is substantially
beta/small-sample and was not the declared gate. Per the pre-registered
decision rule, the fund's FACTOR_EQ sleeve is implemented via the **Nifty200
Momentum 30 index fund** (captures the same premium with institutional
execution), not a self-managed book. Thread closed — no variants, no
threshold shopping.

## Project Usage
- `quant_engine/factors/momentum.py` — 1/3/6-month weighted factor score (legacy composite)
- `quant_engine/research/monthly_momentum.py` — Study A (12-1 monthly, PIT, pre-registered)
- `src/services/fundService.js` — reads the study verdict as the sleeve-2 gate on the Fund page

## Related Concepts
- [factor_scoring.md](factor_scoring.md) — how momentum score is combined with others
- [mean_reversion.md](mean_reversion.md) — mean reversion is the "opposite" strategy; regime determines which to use
- [regime_detection.md](regime_detection.md) — BULL → momentum, BEAR → mean reversion
- [fund_design.md](fund_design.md) — the multi-sleeve book this study gated
