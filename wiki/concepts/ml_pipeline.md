# ML Pipeline

## Architecture

**Framework**: scikit-learn classifier (Random Forest or similar)  
**Files**: `quant_engine/ml/trainer.py`, `quant_engine/ml/predictor.py`

## Labels

Current labels: `Buy / Hold / Sell` derived from forward return + composite score threshold.

**Gap**: Labels should use Triple-Barrier Method (López de Prado Ch.3):
- Upper barrier: +h×σ (profit-taking) → Buy
- Lower barrier: −h×σ (stop-loss) → Sell
- Time barrier: T days → Hold

This would make labels volatility-adaptive and more realistic.

## Features

Current features (all 7 factor scores):
- momentum_score, mean_reversion_score, rsi_score, macd_score
- volatility_score, volume_score, relative_strength_score
- VIX, Nifty trend (regime features — added after backfill)

**Planned additions** (López de Prado):
- fracdiff(close, d=0.35) — stationary but memory-preserving
- Bar properties: bar range, volume imbalance
- Entropy features

## Cross-Validation

**Current**: `TimeSeriesSplit` — **must sort by date before splitting** (see memory: `feedback_ml_cv_split.md`). Sorting by stock before CV split causes data leakage.

**Gap**: Upgrade to Purged K-Fold CV (López de Prado Ch.10):
- Purge training observations whose label horizon overlaps with test period
- Add embargo: N days after test fold also excluded
- CPCV: run all C(T,k) splits for a distribution of Sharpe ratios

```python
# Roadmap: quant_engine/ml/purged_cv.py
# from mlfinlab.cross_validation import PurgedKFoldCV
```

## Model

Current: classifier predicts Buy/Hold/Sell from factor scores.

**Meta-labeling** (López de Prado Ch.3):
1. Primary model: factor composite → direction (Long/Short)
2. Secondary model: "is this trade worth taking?" → bet size [0,1]

The secondary model improves precision — only trades when confidence is high.

## Training Regime

```bash
# Retrain whenever:
# 1. Regime features change (VIX/Nifty trend data updated)
# 2. More price history added (> 3 months of new data)
# 3. Factor logic changes
cd quant_engine && python ml/trainer.py
```

## Model Performance Tracking

**Gap**: No IC tracking per feature, no DSR calculation.  
From López de Prado Ch.14: use Deflated Sharpe Ratio to avoid overfitting when multiple hyperparameter combinations are tested.

## Sample Weights

**Gap**: All observations treated equally. Should weight by uniqueness (1 / concurrent label count) per López de Prado Ch.4.

## Current Status

See memory: `project_ml_status.md` for current accuracy metrics and open issues.

## Related Concepts
- [factor_scoring.md](factor_scoring.md) — factor scores are ML features
- [regime_detection.md](regime_detection.md) — regime features added to ML
- [backtesting.md](backtesting.md) — ML signal validated in backtest
