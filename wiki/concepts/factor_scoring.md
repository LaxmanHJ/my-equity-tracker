# Multi-Factor Scoring Engine

## Architecture

Each factor returns a raw score in **[−1.0, +1.0]**. Scores are combined as a weighted composite scaled to **[−100, +100]**.

## Current Factors and Weights

Calibrated against ML feature importances (per `quant_engine/config.py`):

| Factor | Module | Weight | Type | IC-adaptive |
|--------|--------|--------|------|-------------|
| Volatility | `factors/volatility.py` | 20% | Risk | yes |
| Momentum | `factors/momentum.py` | 15% | Trend | yes |
| RSI (trend-confirming) | `factors/rsi.py` | 15% | Oscillator | yes |
| Bollinger Bands | `factors/bollinger.py` | 15% | Oscillator (replaces mean_reversion) | yes |
| MACD | `factors/macd.py` | 12% | Trend | yes |
| **Sentiment** | `factors/sentiment.py` | **10%** (fallback) | News (soft) | **yes** |
| Relative Strength | `factors/relative_strength.py` | 8% | Cross-sectional | yes |
| Volume | `factors/volume.py` | 5% | Volume | yes |

**Sum: 100%**. `mean_reversion` replaced by `bollinger` (same contrarian intent, volatility-adaptive). RSI direction flipped to trend-confirmation — high RSI = bullish, not overbought. Sentiment activated 2026-05-13 at 10% (user-directed, ahead of wiki Phase 3 IC gate) — 5pp from momentum, 5pp from RSI; see `wiki/concepts/sentiment.md`. Static values above are the fallback used when the IC engine returns `static_fallback`.

**IC-adaptive.** All 8 factors are listed in `IC_ADAPTIVE_FACTORS` and have their weights daily-rebalanced by `quant_engine/scoring/ic_weights.py` over the full **100% budget**. The IC panel builder (`_panel_row`) left-joins per-day `sentiment_daily.sent_score` onto each symbol's price history, so sentiment competes for weight on exactly the same Spearman-rank-IC criterion as the price factors. Until enough historical sentiment rows accumulate (≥ `MIN_IC_OBS = 20` valid date observations with ≥ `MIN_CROSS_N = 5` stocks each), sentiment's IC falls below the floor and it receives **0% live weight** — same gating Phase 3 envisaged, now expressed through the IC engine itself. The `reserved` mechanism in `IC_ADAPTIVE_FACTORS` is kept for any future hard-static factor we deliberately want the IC engine to leave alone (empty set today).

## Signal Thresholds

```
composite ≥ +40  → LONG
−40 < composite < +40  → HOLD
composite ≤ −40  → SHORT
```

## Composite Score Formula

```python
composite = (
    0.20 * volatility_score +
    0.15 * momentum_score +
    0.15 * rsi_score +
    0.15 * bollinger_score +
    0.12 * macd_score +
    0.10 * sentiment_score +       # sent_24h ∈ [-1, +1]; 0.0 when no sentiment row
    0.08 * relative_strength_score +
    0.05 * volume_score
) * 100
```

The numerical weights above are the static `FACTOR_WEIGHTS` defined in `quant_engine/config.py` — the live composite uses `get_active_weights()`, which IC-rebalances all 8 factors (sentiment included) once per day. Call `GET /api/ic-weights` to see the current live values vs. the static fallback.

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

### Per-date IC series (2026-04-18) — why we persist it

Aggregate `mean_ic` / `icir` summarise a distribution. The distribution itself is load-bearing for four things aggregates can't answer:

1. **Regime-conditional IC.** `RegimeAdaptiveStrategy` switches trend↔mean-reversion on a macro regime score (VIX 35% + Nifty trend 25% + Markov 25% + FII 15%). Validating that switch requires IC conditioned on the regime score *at each signal date* — impossible without the date-indexed series.
2. **Structural-break / drift detection.** The live "drift detector" card can move from a single-point Δ-vs-baseline to a two-sample test (KS, or z on the paired distribution) once the full baseline series is available.
3. **Honest ICIR.** Grinold-Kahn ICIR assumes IID per-date IC. Common risk factors induce autocorrelation, so effective N is smaller and ICIR is inflated. Only the series lets us check `acf(per_date_ics)` and apply a Newey-West correction.
4. **Deflated Sharpe / PBO.** Lopez de Prado AFML Ch.14. DSR needs skew and kurtosis of the IC series; PBO/CSCV need the full series to split. Can't deflate from `{mean, std}` alone.

**Where the data lives now:**
- Historical: `data/ml_diagnostic.json` → `aggregate_pooled.{ml,linear}.{1d,5d,10d,20d}.{per_date_dates, per_date_ics}` (aggregate only; fold entries stay compact).
- Live: `GET /api/quant/signal-quality/series?horizon={1,5,10,20}&track={ml,linear}` — Node proxy at `/api/signal-quality/series`.

**Still open**: per-factor IC tracking (we currently measure composite-level IC, not factor-by-factor); regime-conditioned IC analysis using the new series; DSR computation across the shipped strategies.

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

## Downstream: Claude Final Gate (2026-04-21)

Once a composite passes ≥ 40 and the other hard conviction gates (linear agreement, ML confidence ≥ 40%, ADV ≥ 500k, data_points ≥ 200), the signal is enqueued for **Claude review** before any order is placed. Claude receives the full factor vector and checks for **coherence** — a LONG with strong momentum but collapsing relative strength gets flagged even if the composite is > 60. See [claude_final_gate.md](claude_final_gate.md) for the full flow, prompt structure, and output schema.

## Related Concepts
- [momentum.md](momentum.md)
- [mean_reversion.md](mean_reversion.md)
- [regime_detection.md](regime_detection.md)
- [ml_pipeline.md](ml_pipeline.md)
- [claude_final_gate.md](claude_final_gate.md)
