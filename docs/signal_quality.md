# Signal Quality

## Problem this solves

The composite scoring engine generates LONG/HOLD/SHORT signals daily, but nothing previously verified whether those signals were actually predictive. A model can look sophisticated while producing noise. Signal Quality closes that loop: every signal is logged at the time it fires, then its actual forward return is measured at 1d, 5d, 10d, and 20d horizons. The result is an empirical track record you can inspect in real time.

---

## Metrics

### Hit Rate

Fraction of directional signals (LONG + SHORT only; HOLD is excluded) where the engine was correct:
- LONG signal was correct if the stock's forward return is positive.
- SHORT signal was correct if the forward return is negative.

**Target:** > 55% across any settled horizon. Below 50% means the engine is a contrarian indicator. Exactly 50% means it has no edge.

### Information Coefficient (IC)

Spearman rank correlation between `composite_score` and forward return, computed *cross-sectionally* (across all stocks on the same signal date), then averaged across all dates in the window.

Spearman rank is used instead of Pearson because it is robust to outlier returns and treats the signal as an ordinal ranking tool, which is what it is.

**Target:** IC > 0.05 is a real edge. IC > 0.10 is good. IC below 0 means the score is inversely predictive.

### ICIR (Information Coefficient Information Ratio)

`mean_IC / std_IC`

Measures *consistency* of the IC, not just its level. An engine with IC = 0.08 every month is far more valuable than one with IC = 0.30 one month and IC = −0.20 the next.

**Target:** ICIR > 0.5 is acceptable. > 1.0 is strong. Below 0.5 means the edge is noisy even if the mean IC looks positive.

### IC Decay Curve

IC plotted as a function of horizon (1d → 5d → 10d → 20d). Healthy signals decay smoothly — IC is highest at short horizons and falls toward zero at longer ones. This tells you at what horizon the factor information has been fully priced in.

**Warning signs:**
- IC rising with horizon — suggests lagging factor, not predictive signal.
- IC oscillating around zero at all horizons — the factor is not informative at any time scale.
- IC negative at short horizons but positive at long — the signal triggers mean-reversion effects you did not intend.

### n_obs

Number of settled observations for each horizon. Observations are pending until enough trading days have elapsed for the exit price to exist in `price_history`. Do not draw conclusions from fewer than ~30 settled observations.

---

## How to interpret the dashboard

**Daily check (< 5 minutes)**

Open the Signal Quality page and look at the Signal Journal table. Scan for fresh LONG/SHORT entries. Check whether yesterday's directional signals have a WIN or LOSS status at 1d. One day is too short to judge — note it but do not act.

**Weekly check**

1. Look at the 5d and 10d hit rates. If either drops below 50% for two consecutive weeks, the factor weights probably need revisiting.
2. Check the IC Decay chart. Confirm IC is still highest at 1d or 5d and falling. Inversion is a red flag.
3. Check ICIR at the 20d horizon. If it falls below 0.3, the signal has become erratic.

---

## Methodology notes

**Point-in-time joins** — forward returns are computed by joining `signals_log` to `price_history` using only prices that existed *after* the signal date. There is no look-ahead bias because the entry price is the closing price on `signal_date` and the exit price is the closing price exactly N trading days later (by row offset, not calendar days).

**Spearman rank IC** — ranks are computed cross-sectionally per date, then Spearman correlation is calculated between score ranks and return ranks. This is the standard academic definition of IC for equity factor research.

**Why not backtest returns?** Backtest returns reflect strategy execution (entries, exits, position sizing, transaction costs). IC and hit rate measure the *raw signal quality* independent of any execution decisions. They answer "is the score predictive?" before asking "how would a strategy trade it?"

**Cross-sectional scope** — with 15 portfolio stocks the cross-sectional IC has high variance on any single date. The averaged IC over many dates is the meaningful number. Require at least 20 dates before trusting the IC level.
