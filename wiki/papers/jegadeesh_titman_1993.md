# Returns to Buying Winners and Selling Losers

**Authors**: Narasimhan Jegadeesh & Sheridan Titman  
**Published**: Journal of Finance, Vol. 48, No. 1, March 1993, pp. 65–91  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/jegadeesh-titman-Returns to Buying Winners and Selling Losers.pdf`  
**Status**: Fully ingested

---

## The Finding

**Momentum works**: Stocks ranked by past 3–12 month returns continue to outperform (winners) or underperform (losers) over the next 3–12 months. This is robust, non-trivial in magnitude, and not explained by systematic risk.

## Strategy Design (J/K Strategies)

- **J** = formation period (1, 2, 3, or 4 quarters)
- **K** = holding period (1, 2, 3, or 4 quarters)
- Best: **J=12 months / K=3 months** → **1.31%/month** (1.49% with 1-week skip)
- 16 strategies tested + 16 with 1-week skip to avoid bid-ask bounce

## Key Numbers

| Metric | Value |
|--------|-------|
| Best strategy return | 1.31%/month (12/3) |
| With 1-week skip | 1.49%/month |
| Peak cumulative return | ~9.5% at month 12 |
| Reversal after month 13 | Loses ~50% of gains by month 31 |
| January effect | -6.86% average in January vs +1.66% other months |

## Profit Decomposition

Expected profit = σ²_μ + σ²_b · Cov(f_t, f_{t-1}) + Cov_i(e_{it}, e_{it-1})

- **σ²_μ**: Cross-sectional variance of expected returns (always positive, contributes to momentum)
- **σ²_b · Cov(f,f)**: Factor serial covariance (near zero for most factors)
- **Cov(e,e)**: Idiosyncratic serial covariance — main driver, consistent with delayed price reactions

**Conclusion**: Profits are NOT from systematic risk. Consistent with delayed reactions to firm-specific information.

## Subsample Evidence

| Period | Result |
|--------|--------|
| 1941–1964 | Confirms strategy |
| 1927–1940 | Breaks down (Great Depression = mean-reversion era) |
| January months | Strongly negative — momentum reverses |

## Earnings Announcement Analysis

- Past winners outperform expectations at earnings in **months 1–7** after formation
- Pattern reverses in **months 8–20** (losers beat, winners disappoint)
- Confirms delayed price reaction story, not risk-based explanation

## Project Usage

- **`quant_engine/factors/momentum.py`**: Directly implements J=12, K=1 momentum (past 12-month return, skipping recent month). The 1-week skip maps to skipping the most recent month in monthly data.
- **`quant_engine/strategies/sicilian_strategy.py`**: Momentum weight = 25% in composite score.
- **Key insight from this paper**: Momentum reverses after 12 months → our strategy uses short holding periods, which is correct. Long-term mean reversion is handled by the separate mean_reversion factor.
- **January effect**: Not handled in current implementation. Could add calendar seasonality.
