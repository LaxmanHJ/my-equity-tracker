# Mean Reversion

## The Concept

Assets that have deviated significantly from their mean (price, value, or factor score) tend to revert. Opposite of momentum. Short lookbacks (1–5 days) → mean-reverting; long lookbacks (12+ months) → momentum.

## Evidence

### Short-Term Reversal
- **1-week reversal**: Jegadeesh (1990) — most recent week's return reverses. Explains why J&T strategies skip the most recent month.
- **1-month reversal**: Robust across markets; driven by bid-ask bounce and liquidity provision by market makers.
- **Mechanism**: Overshooting by noise traders or liquidity providers earning a spread.

### Long-Term Reversal (DeBondt & Thaler 1985)
- Past 3–5 year losers outperform winners over next 3–5 years.
- Opposite of momentum; consistent with fundamental value pull.
- This is what J&T (1993) observed starting at month 13 (post-momentum reversal).

### Mean-Reversion of Fundamentals
- Earnings, margins, and ROE revert toward industry mean over 3–7 years.
- This is the economic foundation for value investing (P/B, P/E as value signals).

## Signals in Project

**File**: `quant_engine/factors/mean_reversion.py`  
**Signal**: Short-term price deviation from moving average (RSI-like / z-score)  
**Weight**: 15% in composite score

Additional mean-reversion signals in the project:
- **RSI** (`factors/rsi.py`, 15%): Classic overbought/oversold
- **Bollinger Bands** (`factors/bollinger.py`, 0%): Currently unweighted; price at band extremes

## Regime Dependency

Mean reversion is the **dominant strategy in BEAR markets** (high VIX, trending down):
- In crashes, momentum breaks catastrophically (momentum crash of 2009)
- Mean reversion works as prices overshoot down and snap back
- Our `regime_adaptive_strategy.py` switches to mean-reversion weighting when regime = BEAR

## Kakushadze (2015) Classification
Most of the 101 alphas are mean-reverting:
- Short lookbacks (1–5 days): reversal patterns
- `rank()` operators → measure relative value (current vs. historical)
- Examples: Alpha#4 (low-price rank reversal), Alpha#12 (volume-price divergence)

## Gaps vs. Literature

| Gap | Priority |
|-----|---------|
| No long-term reversal signal (3–5 year) | Low |
| No fundamental reversion (P/E, P/B) | High (value factor needed) |
| Bollinger bands weighted 0% — underused | Medium |

## Related Concepts
- [momentum.md](momentum.md) — the regime where momentum wins vs. mean-reversion loses
- [regime_detection.md](regime_detection.md) — VIX determines which strategy to use
- [factor_scoring.md](factor_scoring.md) — weights in composite score
