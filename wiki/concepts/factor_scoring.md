# Multi-Factor Scoring Engine

## Architecture

Each factor returns a raw score in **[−1.0, +1.0]**. Scores are combined as a weighted composite scaled to **[−100, +100]**.

## Current Factors and Weights

| Factor | Module | Weight | Type |
|--------|--------|--------|------|
| Momentum | `factors/momentum.py` | 25% | Trend |
| Mean Reversion | `factors/mean_reversion.py` | 15% | Reversal |
| RSI | `factors/rsi.py` | 15% | Oscillator |
| MACD | `factors/macd.py` | 15% | Trend |
| Volatility | `factors/volatility.py` | 10% | Risk |
| Volume | `factors/volume.py` | 10% | Sentiment |
| Relative Strength | `factors/relative_strength.py` | 10% | Cross-sectional |
| Bollinger Bands | `factors/bollinger.py` | **0%** | Oscillator |

**Sum of active weights: 100%**. Bollinger computed but unused.

## Signal Thresholds

```
composite ≥ +40  → LONG
−40 < composite < +40  → HOLD
composite ≤ −40  → SHORT
```

## Composite Score Formula

```python
composite = (
    0.25 * momentum_score +
    0.15 * mean_reversion_score +
    0.15 * rsi_score +
    0.15 * macd_score +
    0.10 * volatility_score +
    0.10 * volume_score +
    0.10 * relative_strength_score
) * 100
```

## Dynamic Weights (Regime-Adaptive)

`regime_adaptive_strategy.py` adjusts weights based on macro regime:

| Regime | Change |
|--------|--------|
| BULL | Increase momentum, decrease mean reversion |
| BEAR | Increase mean reversion, RSI; decrease momentum |

Regime score = VIX 35% + Nifty trend 25% + Markov 25% + FII flow 15%.

## Normalization

Each factor outputs a **cross-sectionally ranked** score:
- Raw signal computed (e.g., 12-month return)
- Ranked across all stocks in universe [0, 1]
- Centered: (rank − 0.5) × 2 → [−1, +1]

This mirrors the `rank()` operator from Kakushadze (2015)'s 101 alphas.

## IC (Information Coefficient) — NOT YET TRACKED

Per Grinold & Kahn: signal quality = IC (correlation of score rank vs. next-period return rank). Should be measured for each factor separately.

Target IC per factor: 0.03–0.08 (any above 0.10 is exceptional).

```python
# Roadmap: quant_engine/risk/ic_tracker.py
# ic = spearman_corr(factor_score_today, return_next_week)
```

## ML Overlay

`ml/predictor.py` takes all factor scores as features and outputs Buy/Hold/Sell + confidence. Currently used as a **separate** signal, not a weight adjuster.

**Roadmap**: Use ML confidence to scale composite score (meta-labeling approach from López de Prado Ch.3).

## Gaps vs. Literature

| Gap | Source | Priority |
|-----|--------|---------|
| No value factor (P/B, P/E) | Asness (2013) | **High** |
| No IC tracking | Grinold & Kahn | Medium |
| No sector neutralization | Kakushadze (2015) | Medium |
| Bollinger at 0% weight | Internal | Low |
| No volatility-scaled positions | Hurst (2017) | Medium |

## Related Concepts
- [momentum.md](momentum.md)
- [mean_reversion.md](mean_reversion.md)
- [regime_detection.md](regime_detection.md)
- [ml_pipeline.md](ml_pipeline.md)
