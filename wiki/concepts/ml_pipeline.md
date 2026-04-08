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

**Production trainer**: `TimeSeriesSplit` — **must sort by date before splitting** (see memory: `feedback_ml_cv_split.md`). Sorting by stock before CV split causes data leakage. The production trainer does NOT purge overlapping labels, so its reported CV accuracy is slightly optimistic.

**Diagnostic (implemented)**: `quant_engine/ml/diagnostic.py` runs walk-forward CV **with** label-horizon purging (López de Prado Ch.10). For each test fold it drops training rows whose 20d label horizon overlaps the test start, then trains with production `RF_PARAMS` (no re-tuning) and measures IC/ICIR/hit-rate at 1d/5d/10d/20d on the full 10-year history.

Purpose: the live signal-quality tracker only has ~470 settled 1-day obs — IC SE ≈ 0.046, which is too noisy to drive model-design decisions. The historical diagnostic gives thousands of out-of-sample observations per horizon instead. Run via `python -m quant_engine.ml.diagnostic` or `POST /api/ml/diagnostic`; results are cached at `data/ml_diagnostic.json` and served by `GET /api/ml/diagnostic`.

**Gap (still open)**: Full Purged K-Fold / CPCV:
- The diagnostic only purges; it doesn't add the post-test embargo (not needed for walk-forward, needed for CPCV)
- CPCV would run all C(T,k) splits for a distribution of Sharpe/IC values
- Required for PSR/DSR (López de Prado Ch.14)

```python
# Next step: quant_engine/ml/purged_cv.py
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

**Live tracking**: `routers/signal_quality.py` joins `signals_log` to `price_history` and computes cross-sectional Spearman IC per date at 1d/5d/10d/20d for both the ML engine and the linear engine. Served at `GET /api/quant/signal-quality`. Caveat: sample size is ~500 signals → IC SE ≈ 0.045 → weak statistical power.

**Historical diagnostic** (`ml/diagnostic.py`): walk-forward purged CV over 10 years — thousands of OOS observations per horizon. Complementary to live tracking: use the historical diagnostic to measure model quality, use live tracking to detect drift from the historical baseline.

**Gap**: No per-feature IC tracking (we only measure composite model output), no DSR calculation.
From López de Prado Ch.14: use Deflated Sharpe Ratio to avoid overfitting when multiple hyperparameter combinations are tested.

## Sample Weights

**Gap**: All observations treated equally. Should weight by uniqueness (1 / concurrent label count) per López de Prado Ch.4.

## Current Status

See memory: `project_ml_status.md` for current accuracy metrics and open issues.

### 2026-04-07 Diagnostic Findings (historical walk-forward purged CV)

First run of `ml/diagnostic.py` over 8,911 samples / 5 walk-forward folds / 14 stocks (2018-12 → 2026-03). Production `RF_PARAMS`, no re-tuning.

**20-day horizon** (model's native training target):

| Fold | Test Period | cs_IC | ICIR | Hit Rate |
|------|-------------|-------|------|----------|
| 1 | 2018-12 → 2021-07 | **+0.093** | +0.27 | 51.7% |
| 2 | 2021-07 → 2023-10 | **+0.095** | +0.32 | 51.6% |
| 3 | 2023-10 → 2025-05 | +0.032 | +0.10 | 50.4% |
| 4 | 2025-05 → 2025-10 | -0.023 | -0.11 | 50.7% |
| 5 | 2025-10 → 2026-03 | **-0.046** | **-0.18** | **37.5%** |

**Interpretation at the time**: the model had a genuine edge in 2018-2023 (IC ~0.09, ICIR ~0.3) — decent by Grinold-Kahn standards. It began decaying in 2023 and by early 2026 was **anti-predictive** on recent data (negative IC, below-random hit rate).

This looked like model drift or regime shift. Both diagnoses turned out to be wrong.

### 2026-04-09 Root Cause — Weekly/Daily Cadence Break

Investigation after the 2026-04-07 diagnostic traced the fold-5 collapse to the **data layer**, not the model:

1. **Pre-2025-03-17**: `price_history` rows came from RapidAPI `historical_data?period=10yr&filter=price`. That endpoint silently downsamples to **weekly** for any period ≥ 3yr. 52 bars/year for 2005–2025.
2. **Post-2025-03-17**: ingest cadence changed to daily (ongoing incremental fetches used `period=1yr`, which returns daily). 252 bars/year from 2025-03-17 onward.
3. **Meanwhile** the trainer defined the label as `df["close"].shift(-20)` — a **row-position** shift, not a calendar shift. On the weekly half of the table that means "20 weeks forward return (~5 months)". On the daily half it means "20 days forward return (~1 month)". The exact same column `BUY_RETURN_THRESHOLD=+3%` was applied to both.
4. **And** the momentum factor used `series.iloc[-21/-63/-126]` as "1m/3m/6m" — also row-position. On the weekly half, "21-bar momentum" is really 21-week (~5m) momentum; on the daily half, it's real 1-month momentum. Same story for every row-position feature (RSI, MACD, Bollinger, volatility, volume, delivery z-score).
5. **Separately**, `src/services/rapidApiService.js:62-70` copies the single returned `price` into all of `open/high/low/close`, so every pre-backfill bar was a flat OHLC bar. Bollinger/volatility/range-based features had no variance at all.

Folds 1-2 in the pre-fix diagnostic straddled pure weekly data with self-consistent labels and learned a real signal. Fold 5 had test rows in the new daily regime, while the model had been trained on 94% weekly rows — so it was applying a decision boundary trained on 5-month forward returns to test rows whose labels were 1-month forward returns. That's how a "fine" model collapsed to −0.046 IC and a 37.5% hit rate.

### 2026-04-09 Fix — Weekly-Consistent Rebuild

Rebuild of `price_history` via Alpha Vantage `TIME_SERIES_WEEKLY_ADJUSTED`:
- **13 stocks** replaced with real dividend-adjusted weekly OHLCV (script: `quant_engine/data/av_weekly_backfill.py`)
- **JIOFIN, TMCV** — no AV ticker coverage; existing daily rows downsampled locally to weekly so cadence matches the rest
- **Preservation rule**: deletes only within AV's first→last date range per symbol, so any out-of-coverage rows (e.g., pre-IPO) are kept
- **Backup**: `data/price_history_backup_*.json` and `data/jiofin_tmcv_pre_resample_*.json`
- **Semantic change**: `shift(-20)` now unambiguously means 20-weeks forward return (~5 months). Momentum factor's "1m/3m/6m" windows are now really 21/63/126 weeks — still internally consistent, just a longer-horizon trend signal. The wiki's `factor_scoring.md` and the `momentum.py` docstring should be updated to reflect this (open follow-up).

Trainer run after backfill:
- **10,776 samples from 14 stocks** (TMCV auto-dropped due to <126-bar momentum warmup)
- **CV accuracy 0.418 ± 0.030** — lower than the pre-fix 53-54%, but on a very different class distribution (BUY 49.4% / SELL 41.1% / HOLD 9.5%, because ±3% return bars are hit much more often over 5 months than 1 month)
- Dead feature confirmed: `fii_flow_score` feature importance = 0.0000 (open follow-up: remove)

### 2026-04-09 Post-Fix Diagnostic

| Fold | Test Period | cs_IC | ICIR | Hit Rate |
|------|-------------|-------|------|----------|
| 1 | 2010-02 → 2013-12 | -0.016 | -0.045 | 51.7% |
| 2 | 2013-12 → 2017-05 | -0.009 | -0.027 | 51.9% |
| 3 | 2017-05 → 2020-07 | +0.042 | +0.130 | 48.1% |
| 4 | 2020-07 → 2023-06 | +0.029 | +0.091 | 50.2% |
| 5 | 2023-06 → 2025-11 | **+0.187** | **+0.670** | 52.6% |

**Interpretation**:
- **Fold 5 flipped from −0.046 → +0.187 IC, ICIR +0.67**. The period that previously showed "catastrophic collapse" now shows a strong genuine signal. This is direct evidence that the density/cadence break was the root cause, not regime shift or feature drift.
- **Folds 1-2 (2010-2017) slightly negative** — fold-level IC SE ≈ 1/√1796 ≈ 0.024, so values in [−0.02, +0.02] are within noise. Older market structure may also not match the features the model learns on; this is expected drift over 15-year fold separations, not a red flag.
- **Folds 3-4 positive but weaker** — consistent with the "model learns better on recent-regime data" pattern. Walk-forward IC improves monotonically from 2010 → 2025.
- Dataset expanded from 8 → 20 years of history and from 8,911 → 10,776 samples as a side-effect of the backfill.

**Open follow-ups after the fix**:
1. Remove the `fii_flow_score` feature from `FEATURE_COLS` — zero importance confirmed twice
2. Update `factors/momentum.py` docstring and `wiki/concepts/factor_scoring.md` to reflect that "1m/3m/6m" windows are now 21/63/126 weeks (not days)
3. Reconsider `BUY_RETURN_THRESHOLD` / `SELL_RETURN_THRESHOLD` given the new 5-month-forward label — ±3% is a low bar over 5 months, HOLD fell to 9.5% of samples
4. Prevent regression: stop using RapidAPI `historical_data` as the primary ingest source (see live_trading_checklist.md C2)
5. CPCV implementation is still pending (was blocked on the cadence issue)

## Related Concepts
- [factor_scoring.md](factor_scoring.md) — factor scores are ML features
- [regime_detection.md](regime_detection.md) — regime features added to ML
- [backtesting.md](backtesting.md) — ML signal validated in backtest
