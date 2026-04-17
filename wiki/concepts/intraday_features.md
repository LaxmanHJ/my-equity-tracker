# Intraday Features

Phase 4 of the Angel One data expansion. See memory `project_angelone_data_expansion.md` for the broader plan.

## Motivation

Daily OHLCV throws away the intraday shape of each trading session. Three pieces of intraday structure are known in the empirical literature to carry short-horizon predictive signal:

- **Overnight gaps** proxy overnight news / order imbalance that accumulates outside RTH. Large gaps often mean-revert in early RTH (gap-fade) or extend (gap-and-go) depending on regime. Either way, it's information the daily close alone hides.
- **Intraday range vs volatility** separates high-volume range expansion days from low-energy drift days. Normalising by ATR14 removes the per-stock price scale so RF can split on it cross-sectionally.
- **Last-hour momentum** captures institutional rebalancing that concentrates in the final hour. A strong last-hour move is a different signal from a strong open-to-last-hour move that faded.

None of these are visible in the 15 daily-only features the ML pipeline used before Phase 4.

## Data source

- **Angel One SmartAPI `getCandleData`** with `FIFTEEN_MINUTE` interval
- Backfilled via `scripts/backfill_intraday.js` in 180-day chunks (well under Angel's 200-day cap per call)
- Coverage: ~2018-04 → present for 11 of 15 portfolio stocks + NIFTY 50. ETERNAL (2021+), AWL (2022+), JIOFIN (2023+), TMCV (2025+) have shorter history — their intraday-derived feature rows start at their listing date.
- Storage: `intraday_candles (symbol, ts, open, high, low, close, volume)` with `UNIQUE(symbol, ts)`. Total ≈ 662k bars as of 2026-04-18.

## Features

Computed per (symbol, date) in `quant_engine/data/intraday_features.py`:

```python
overnight_gap         = (today_open - prev_close) / prev_close
intraday_range_ratio  = (day_high - day_low) / ATR14_daily
last_hour_momentum    = (close_15:15 - close_14:15) / close_14:15
```

Where:
- `today_open` = first 15-min bar's open (9:15 IST)
- `prev_close` = yesterday's last 15-min bar close (≈ 15:30)
- `ATR14_daily` = Wilder ATR over 14 days from `price_history` (daily OHLC)
- `close_14:15` and `close_15:15` are the closes of the 15-min bars stamped at those times

Rows where any component is NaN (first day of listing, holiday boundaries, missing bar) are dropped.

## Integration with ML pipeline

`trainer.py` and `diagnostic.py` both call `build_intraday_features(symbol)` per stock and join into the feature frame via a helper that returns **NaN (not 0.0)** for dates where intraday data is unavailable. The existing `valid_mask = features.notna().all(axis=1)` then drops those rows, so the tree is never shown zero-filled impostor values that would encode a spurious pre-2018 / post-2018 regime split.

```python
# trainer.py
def _align_intraday(col: str) -> pd.Series:
    if intraday_feats.empty or col not in intraday_feats.columns:
        return pd.Series(np.nan, index=df.index)
    return intraday_feats[col].reindex(df.index)  # no fillna
```

## Observed impact on ML model (2026-04-18)

Measured via walk-forward purged CV (`quant_engine/ml/diagnostic.py`) on 24,030 rows, 14 stocks, 5 folds.

**Feature importances** (RF, `max_depth=12, min_samples_leaf=20`):
- `overnight_gap` 5.3%
- `last_hour_momentum` 4.9%
- `intraday_range_ratio` 4.7%
- Combined: **14.8%** — comparable to the top single feature (`vix_regime` at 10.7%).

The RF does use the intraday features in its split decisions — they aren't ignored.

**But**: adding them did **not** close the gap to the linear composite.

| Horizon | ML cs_IC before | ML cs_IC after | Linear cs_IC |
|--------:|----------------:|---------------:|-------------:|
| 1d  | +0.005 | +0.004 | +0.017 |
| 5d  | +0.011 | +0.024 | +0.027 |
| 10d | +0.003 | +0.012 | +0.044 |
| 20d | −0.000 | +0.001 | +0.041 |

Modest 5d/10d gain, no 20d gain. Linear composite still wins at every horizon.

**Interpretation**: intraday features carry short-horizon signal (weeks, not months), which is consistent with the microstructure literature — gap/range/last-hour effects decay fast. They're the wrong tool for the 20d horizon the model is trained on. A model with a shorter label horizon (or a regression target) might extract more value from them.

## Project Usage

- **Storage**: `intraday_candles` table (Turso + local SQLite)
- **Backfill script**: `scripts/backfill_intraday.js` — chunked, resumable (computes missing windows against existing DB range before fetching; rerunning fills gaps without touching existing rows)
- **Feature builder**: `quant_engine/data/intraday_features.py`
- **Used by**: `quant_engine/ml/trainer.py` (18-feature training set), `quant_engine/ml/diagnostic.py` (walk-forward IC)
- **Live predictor**: `quant_engine/ml/predictor.py` — `sub_scores.get(col, 0.0)` fallback means missing intraday at inference time degrades gracefully (not intended behaviour: live inference should always have today's intraday data since the engine runs after market close)

## Related Concepts

- [ml_pipeline.md](ml_pipeline.md) — full ML pipeline context
- [factor_scoring.md](factor_scoring.md) — how features fit into the linear composite
- [regime_detection.md](regime_detection.md) — regime features complement intraday features
