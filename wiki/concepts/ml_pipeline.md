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

**Per-date IC series (2026-04-18)**: both pipelines now retain the chronological per-date Spearman IC, not just aggregates.
- Historical: `data/ml_diagnostic.json` → `aggregate_pooled.{ml,linear}.{Nd}.{per_date_dates, per_date_ics}`. Fold-level entries stay compact (aggregate is the single source for time-series analysis).
- Live: `GET /api/quant/signal-quality/series?horizon={1,5,10,20}&track={ml,linear}` (Node proxy `/api/signal-quality/series`).

Why: enables regime-conditional IC analysis, proper two-sample drift tests against the live series, autocorrelation-corrected ICIR, and Deflated Sharpe / PBO work that requires higher-moment or split-series inputs (see `wiki/concepts/factor_scoring.md` → "Per-date IC series" for the full rationale).

**Gap**: No per-feature IC tracking (we only measure composite model output), no DSR calculation — DSR is now unblocked by the new series.
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

### 2026-04-16 Daily Cadence Restored (Angel One)

After Angel One integration (`src/services/angelOneService.js` became primary OHLC source), `price_history` was rebuilt at **daily** cadence again — 247-252 bars/year from 2011 onward (verified via Turso). The 2026-04-09 weekly regime was therefore transient. All downstream row-position semantics flip back to days: `shift(-20)` = 20-day forward return; momentum 21/63/126 = 1m/3m/6m.

Practical effects:
- `BUY_RETURN_THRESHOLD = +3%` is now a 1-month target again (much stricter than it was under weekly)
- Fold-5 post-fix diagnostic of +0.187 IC was measured on weekly data — it does not directly carry to the current daily regime. Re-run `python -m quant_engine.ml.diagnostic` after any cadence change.
- Wiki historical entries above remain accurate to what happened; the live state is now **daily**.

### 2026-04-17 Daily-Cadence Diagnostic — Linear Beats ML

Fresh walk-forward purged CV on the now-daily dataset (24,578 rows, 14 stocks, 2018-03 → 2026-03), with the linear track scored on the same folds:

**Pooled aggregate (all folds, n=20,480 OOS rows per horizon):**

| Horizon | ML cs_IC | ML ICIR | ML hit | Linear cs_IC | Linear ICIR | Linear hit |
|---------|---------:|--------:|-------:|-------------:|------------:|-----------:|
| 1d  | +0.005 | +0.01 | 49.1% | +0.016 | +0.05 | 50.4% |
| 5d  | +0.011 | +0.04 | 49.4% | +0.025 | +0.08 | 51.2% |
| 10d | +0.003 | +0.01 | 49.6% | +0.043 | +0.14 | 52.0% |
| 20d | −0.000 | −0.00 | 49.0% | **+0.040** | **+0.13** | **53.1%** |

**Takeaway**: on current daily data the RF model (at production `RF_PARAMS`, 15 features) shows **no out-of-sample edge** — IC indistinguishable from zero, hit rate at coin flip. The 7-factor linear composite has a small but real edge at 10-20d (IC ~0.04, hit ~52-53%) — modest by Grinold-Kahn standards but non-zero.

This is a reversal of the 2026-04-09 weekly diagnostic (where fold-5 ML IC was +0.187). The model learned on weekly bars generalised to weekly test folds. Retrained on daily, the same RF architecture does not find edge.

**Decision implications**:
- For live trading buy decisions: prefer the **linear composite** signal until the ML model is rebuilt with a daily-aware label/feature redesign.
- The ML pipeline needs work: label horizon (shift(-20) = 20 days now, ±3% threshold too tight?), feature set review, and possibly meta-labeling (López de Prado) to get a daily edge.

### 2026-04-17 Signal Quality Pollution Fix

`routers/signal_quality.py` was reporting a live "ML IC = −0.243" that was misleading. Root cause: `signals_log` was 98% polluted with linear-only rows bulk-written by an earlier one-shot `backfill_signals.py` (ML predictor never ran; `ml_confidence` was NULL for 9,263/9,428 rows). The endpoint then used `ml_confidence.fillna(50.0) * ml_dir` — constant-50 substitution turned the "ML IC" into a degraded 3-valued ordinal of the LINEAR direction. It was not measuring the ML model.

Fixes applied:
1. **`routers/signal_quality.py`** — no longer fillna-s `ml_confidence`; NaN rows are dropped by `_engine_horizons`. Linear track uses `effective_linear_signal = COALESCE(linear_signal, signal)` for hit-rate direction. Response now includes `eligible_rows` per track so the UI can show sample-size honestly.
2. **`backfill_signals.py`** rewritten to produce **walk-forward OOS ML predictions** (reproducing `diagnostic.py`'s purged TimeSeriesSplit) and write both `ml_confidence` + `linear_signal` for every historical bar. No lookahead — each test fold's predictions come from a model trained strictly on earlier data.
3. **`ml/diagnostic.py`** extended to score the linear composite on the same walk-forward folds. `aggregate_pooled` now contains both `"ml"` and `"linear"` sub-dicts for apples-to-apples comparison.

**Methodology decision — quality bar vs drift detector** (per Grinold-Kahn / López de Prado):
- **Historical diagnostic** (`ml_diagnostic.json`) is the model-quality measure. It has thousands of OOS observations per horizon and purges label leakage.
- **Live signal-quality tracker** is a **drift detector**. Given limited sample (even after backfill, ~20k OOS rows), it cannot cleanly separate a −0.02 IC from zero on short windows. Its role is to flag when live IC diverges from the historical baseline.
- UI must lead with the historical diagnostic, not the live tracker. See `public/js/app.js` Signal Quality section.

### 2026-04-18 Phase 4 — Intraday Features Added

Angel One 15-min candles backfilled for 15 portfolio stocks + NIFTY 50 (~662k bars, ~2018+). Three new features engineered in `quant_engine/data/intraday_features.py`:

- `overnight_gap`        — `(today_open − prev_close) / prev_close`
- `intraday_range_ratio` — `(day_high − day_low) / ATR14`   (Wilder ATR from daily)
- `last_hour_momentum`   — `(close_15:15 − close_14:15) / close_14:15`

Feature count grew from 15 → 18. Also added `pcr_score` (Angel One PCR).

**Pre-2018 handling**: `_align_intraday` returns `NaN` (not `0.0`) for dates before a symbol's intraday history starts. The existing `valid_mask = features.notna().all(axis=1)` drops those rows — prevents zero-filled impostor values from encoding a spurious pre/post-2018 regime split.

**Training**: 24,030 samples (was 24,578 pre-filter — only ~550 rows dropped; the 2000-bar cap in `load_price_history` meant most data was already post-2018). Best hyperparams: `max_depth=12, min_samples_leaf=20`. CV accuracy 0.352 ± 0.021.

**Diagnostic (2026-04-18 post-fix)**:

| Horizon | ML cs_IC | Linear cs_IC | ML hit% | Linear hit% |
|--------:|---------:|-------------:|--------:|------------:|
| 1d  | +0.004 | +0.017 | 48.8 | 50.4 |
| 5d  | +0.024 | +0.027 | 49.5 | 51.2 |
| 10d | +0.012 | +0.044 | 50.0 | 52.1 |
| 20d | +0.001 | +0.041 | 49.9 | 53.2 |

**Intraday feature importances**: overnight_gap 5.3%, last_hour_momentum 4.9%, intraday_range_ratio 4.7% (14.8% combined — comparable to top individual features like vix_regime at 10.7%).

**Takeaway**: intraday features recovered the small ML regression observed before the NaN fix but did NOT close the gap to the linear composite. **ML still underperforms the hand-tuned linear composite at every horizon on daily data.** Phase 4 was informative — the intraday features carry real predictive weight in the RF's split decisions — but the ML pipeline's core problem (no 20d edge vs linear) is unresolved.

**Dead features confirmed**: `pcr_score` (1 bar in DB) and `fii_flow_score` (8 bars) both have importance 0.0000. They only dilute RF's `max_features="sqrt"` random selection.

**Open follow-ups after Phase 4**:
1. Remove dead features (`pcr_score`, `fii_flow_score`) from `FEATURE_COLS` until their source tables are populated
2. Investigate why ML underperforms linear at 20d — try regression target (predict fwd_ret directly) instead of 3-class
3. Consider meta-labeling (López de Prado Ch.3): use linear composite as primary direction model, train ML as secondary "should-we-take-this-trade?" model
4. CPCV still pending

## Related Concepts
- [factor_scoring.md](factor_scoring.md) — factor scores are ML features
- [regime_detection.md](regime_detection.md) — regime features added to ML
- [backtesting.md](backtesting.md) — ML signal validated in backtest
- [intraday_features.md](intraday_features.md) — Phase 4 Angel One intraday features
