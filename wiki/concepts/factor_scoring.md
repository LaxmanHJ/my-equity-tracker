# Multi-Factor Scoring Engine

## Architecture

Each factor returns a raw score in **[−1.0, +1.0]**. Scores are combined as a weighted composite scaled to **[−100, +100]**.

## Current Factors and Weights

Calibrated against ML feature importances (per `quant_engine/config.py`):

| Factor | Module | Weight | Type |
|--------|--------|--------|------|
| Momentum | `factors/momentum.py` | 20% | Trend |
| Volatility | `factors/volatility.py` | 20% | Risk |
| RSI (trend-confirming) | `factors/rsi.py` | 20% | Oscillator |
| Bollinger Bands | `factors/bollinger.py` | 15% | Oscillator (replaces mean_reversion) |
| MACD | `factors/macd.py` | 12% | Trend |
| Relative Strength | `factors/relative_strength.py` | 8% | Cross-sectional |
| Volume | `factors/volume.py` | 5% | Sentiment |

**Sum: 100%**. `mean_reversion` replaced by `bollinger` (same contrarian intent, volatility-adaptive). RSI direction flipped to trend-confirmation — high RSI = bullish, not overbought.

## Signal Thresholds

```
composite ≥ +40  → LONG
−40 < composite < +40  → HOLD
composite ≤ −40  → SHORT
```

## Composite Score Formula

```python
composite = (
    0.20 * momentum_score +
    0.20 * volatility_score +
    0.20 * rsi_score +
    0.15 * bollinger_score +
    0.12 * macd_score +
    0.08 * relative_strength_score +
    0.05 * volume_score
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

## IC (Information Coefficient) — TRACKED

Signal quality is measured in two places (Grinold-Kahn framework):

1. **Historical diagnostic** (`quant_engine/ml/diagnostic.py`) — walk-forward purged CV on ~10 years of data. Reports cross-sectional Spearman IC + ICIR + hit-rate at 1d/5d/10d/20d for **both ML and linear composite** tracks. Results cached at `data/ml_diagnostic.json`. This is the headline quality metric.
2. **Live tracker** (`quant_engine/routers/signal_quality.py`) — joins `signals_log` to `price_history` and computes IC on recorded live signals. Role is **drift detection** against the historical baseline, not quality measurement (sample too small for clean IC).

Target composite IC: 0.03–0.08 at the target horizon (any above 0.10 is exceptional).

**Current daily-cadence numbers (2026-04-17, pooled 20,480 OOS rows)**:
- Linear composite at 20d: IC +0.040, ICIR +0.13, hit 53.1% — small but real edge
- RF ML at 20d: IC ≈ 0, hit 49% — no OOS edge on current daily data

See `wiki/concepts/ml_pipeline.md` for the full diagnostic table and the context around the weekly→daily cadence flip.

**Still open**: per-factor IC tracking (we currently measure composite-level IC, not factor-by-factor).

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
