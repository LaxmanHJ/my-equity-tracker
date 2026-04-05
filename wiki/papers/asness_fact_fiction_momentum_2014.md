# Fact, Fiction and Momentum Investing

**Authors**: Clifford Asness, Andrea Frazzini, Ronen Israel & Tobias Moskowitz  
**Published**: Journal of Portfolio Management, 2014 (JPM Special Issue)  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/JPM Fact Fiction and Momentum Investing.pdf`  
**Status**: Fully ingested

---

## Purpose

Directly addresses 10 common myths/criticisms of momentum investing. Written by AQR practitioners who run live momentum strategies at scale.

## The 10 Myths Debunked

| # | Myth | Reality |
|---|------|---------|
| 1 | Momentum returns are too small and insignificant | 1.0–1.5%/month (large caps), significant t-stats |
| 2 | Momentum doesn't survive trading costs | After realistic costs, still positive — large caps especially |
| 3 | Momentum works only in small-cap | Works in large caps too; smaller in large caps but significant |
| 4 | Momentum works only in the US | Works in 40+ countries, all major equity markets |
| 5 | Momentum is just short-term reversal | 12-1 momentum is distinct from 1-month reversal |
| 6 | Momentum is a January effect story | Strips out January: still works every other month |
| 7 | Momentum works best on the short side | **Long side ≈ short side** — similar contribution |
| 8 | Momentum is too volatile and crash-prone | Drawdowns exist but long-run Sharpe ~0.6–0.8 |
| 9 | Momentum is subsumed by other factors | Survives controlling for value, size, beta |
| 10 | Momentum is "too crowded" to still work | No evidence of crowding decay in out-of-sample data |

## Key Numbers

| Metric | Value |
|--------|-------|
| Long-side contribution | ~50% of total momentum return |
| Short-side contribution | ~50% of total momentum return |
| Sharpe ratio (long-only) | ~0.5–0.6 |
| Sharpe ratio (long-short) | ~0.6–0.8 |
| Value-momentum correlation | **−0.4 to −0.6** |

## The −0.4 Correlation Insight

Even if momentum had ZERO expected return, combining it with value at −0.4 correlation improves portfolio Sharpe. This makes momentum **strategically valuable** beyond its standalone return.

Combined value + momentum portfolio SR ≈ 1.4 vs ~0.7 for each alone.

## Trading Cost Analysis

The paper estimates **actual** transaction costs (bid-ask spread, market impact) for a large institutional fund. Key findings:
- Small-cap momentum: costs nearly erase alpha
- **Large-cap momentum: survives costs comfortably**
- Monthly rebalancing is too frequent; quarterly is more cost-efficient
- Netting turnover across factors (value + momentum trade in opposite directions) reduces costs further

## January Effect

Momentum loses significantly in January (consistent with Jegadeesh & Titman 1993). The paper strips January and shows:
- 11 out of 12 months, momentum wins
- January anomaly is real but doesn't kill the strategy
- Tax-loss selling reversal in January explains much of it

## Crash Risk

Momentum crashes are real (2009 rebound was brutal). But:
- Crashes are concentrated in specific conditions: post-bear-market, high-volatility rebounds
- Dynamic scaling (reduce exposure when volatility spikes) substantially reduces crash risk
- Still positive SR over any 10-year window in the data

## Project Usage

- **`quant_engine/strategies/sicilian_strategy.py`**: Momentum at 25% weight — long-side only (we're long-only). Long side ≈ short side means we capture ~50% of momentum alpha, which is consistent.
- **Key validation**: This paper confirms momentum works in **large caps** and **non-January months**. Our NSE universe is large/mid-cap → applicable.
- **Gap — no value factor**: The combined value+momentum portfolio (SR 1.4) is far superior. A value factor (P/B, P/E, or EV/EBITDA) is a high-priority addition.
- **Gap — trading costs**: Not modeled in backtests. Large-cap NSE stocks should be fine, but we're rebalancing weekly which may be too frequent.
- **January effect**: Not handled. Consider reducing momentum weight in January.
