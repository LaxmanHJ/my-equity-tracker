# Advances in Financial Machine Learning

**Author**: Marcos López de Prado  
**Published**: Wiley, 2018  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/AdvancesINMachineLearning.pdf`  
**Status**: Ingested (ToC + Ch.1 fully read; remaining chapters via ToC structure)

---

## Structure Overview

| Part | Chapters | Topic |
|------|----------|-------|
| I | 1–2 | Financial Data Structures |
| II | 3–5 | Labeling |
| III | 6–8 | Features |
| IV | 9–11 | Model Selection |
| V | 12–14 | Backtest |
| VI | 15–18 | High-Performance |
| VII | 19–20 | Meta-Strategies |

---

## Chapter 1: Financial Data Structures

### Standard Bars vs. Information-Driven Bars

Standard OHLCV bars have flaws:
- **Time bars**: Oversample during low activity, undersample during high activity
- **Tick bars**: More stationary than time bars; sample ~1000 ticks/bar
- **Volume bars**: Sample when N shares traded
- **Dollar bars**: Sample when $N traded (best for across-time comparison)

All four produce more IID-like returns than fixed time bars.

### Imbalance Bars (Information-Driven)

- **Tick imbalance bars (TIB)**: Form bar when cumulative signed tick imbalance exceeds expected value E[θ]
- **Volume imbalance bars (VIB)**: Same but by volume
- **Dollar imbalance bars (DIB)**: Same but by dollar value
- **Run bars**: Track buying/selling runs independently

**Key property**: Imbalance bars form more frequently when informed trading is happening — they concentrate bars where price discovery occurs.

---

## Chapter 3: Labeling (Triple-Barrier Method)

Three simultaneous barriers:
1. **Upper barrier** (profit-taking): +h × σ_t above entry
2. **Lower barrier** (stop-loss): −h × σ_t below entry
3. **Vertical barrier** (time): T days maximum hold

Label = which barrier is touched first:
- Upper → **+1** (Buy/Long)
- Lower → **−1** (Sell/Short)  
- Vertical → **0** (Hold, or whichever side based on final return)

**Volatility-scaled barriers**: h is a multiplier of rolling daily σ, so barriers adapt to market conditions.

### Meta-Labeling

Two-stage approach:
1. **Primary model**: Generates trade direction (long/short)
2. **Secondary model (meta-label)**: Predicts whether the primary model is correct for this specific trade

Meta-label output is a **bet size** [0,1] — scales position, doesn't flip direction. Improves precision/recall trade-off.

---

## Chapter 4: Sample Weights

**Problem**: Overlapping labels (a 20-day holding period today overlaps with a 20-day holding period started yesterday) → observations are NOT iid → standard CV is invalid.

**Solution**: Uniqueness weights — weight each observation by 1/(number of concurrent labels). Rare, isolated trades get high weight; overlapping trades get low weight.

---

## Chapter 7: Fractional Differentiation (fracdiff)

**Problem**: Raw price series are I(1) (non-stationary). Taking returns (diff order=1) is stationary but loses all memory — "throws the baby out with the bathwater."

**Solution**: Fractional differencing at order d ∈ (0,1):
- d=1: returns (fully stationary, no memory)
- d=0: raw prices (full memory, non-stationary)
- d=0.35 (typical): stationary AND preserves significant memory

**Application**: Use fracdiff(close, d=0.35) as ML feature instead of raw close or simple returns.

---

## Chapter 8: Entropy Features

- **Shannon entropy**: Measures dispersion of patterns in price series
- **Plug-in entropy estimator** and **Lempel-Ziv complexity**
- High entropy → less predictable (noisy market)
- Low entropy → structured, potentially trending/mean-reverting

---

## Chapter 10: Purged K-Fold Cross-Validation

**Standard k-fold fails in finance**: Training/test sets share overlapping observations due to labeling horizon. Information leaks from future.

**Purged k-fold**:
1. Set aside test fold
2. Remove ("purge") training observations whose label **overlaps** with test period
3. Add **embargo**: also remove the N observations just after the test fold (to prevent future leakage in event-driven strategies)

**CPCV (Combinatorial Purged Cross-Validation)**:
- Generate all C(T, k) possible train/test splits
- Average performance across all combinations
- Provides a **distribution** of Sharpe ratios, not a single estimate
- Enables PSR/DSR calculation

---

## Chapter 14: Backtest Overfitting

### Probability of Backtest Overfitting (PBO)
Probability that the selected strategy was the best in-sample only by luck.

### Deflated Sharpe Ratio (DSR)
SR adjusted for:
- Number of trials (strategies tested)
- Non-normality (skewness, kurtosis)
- Length of track record
- SR distribution shape

**DSR formula**: SR* = SR × √(1 − γ₃·SR + (γ₄−1)/4·SR²)  
Where γ₃ = skewness, γ₄ = kurtosis of returns.

Only accept a strategy if DSR > threshold (e.g. 0 at 95% confidence).

### Minimum Track Record Length
Given target SR and number of trials, minimum # of observations needed for statistical significance.

---

## Chapter 16: Hierarchical Risk Parity (HRP)

Three-step portfolio construction:
1. **Tree clustering**: Hierarchical clustering of correlation matrix → quasi-diagonal structure
2. **Quasi-diagonalization**: Reorder assets so similar assets are adjacent
3. **Recursive bisection**: Allocate weights top-down, splitting by inverse variance at each branch

**Advantages over mean-variance optimization**:
- No matrix inversion → numerically stable with near-singular covariance
- Works with p ≈ T
- Produces diversified, out-of-sample stable weights
- No need for expected return estimates (error-maximizing in MVO)

---

## Chapter 17-18: Bet Sizing

Convert binary signal → position size using:
- **Sigmoid**: size = 2·σ(f) − 1 where f = forecast score
- **Discretized**: size ∈ {0, 0.25, 0.5, 0.75, 1.0}
- **Meta-label probability**: size = P(correct) from meta-label model

---

## Project Usage

### Already Implemented
- **Triple-barrier concept**: Our `LONG/HOLD/SHORT` labels with ≥40/≤−40 thresholds are a simplified version (no stop-loss barrier, time is implicit).
- **ML classifier** (`ml/trainer.py`): Trains on factor scores → Buy/Hold/Sell. The framework is correct; labels need upgrading to triple-barrier.
- **Ch.3 vol-scaled stop (2026-04-21)**: `riskLimits.stopLoss.volMultiplier = 2.5` applied at two points — (1) live stop-loss detector (`src/risk/stopLoss.js` hybrid vol + chandelier) and (2) the Claude final gate prompt (`stop = entry × (1 − 2.5 × σ_20d)`). See [claude_final_gate.md](../concepts/claude_final_gate.md).
- **Ch.17 bet sizing + Ch.3 meta-labeling (2026-04-21)**: Hard conviction gates act as the primary classifier ("should I trade"); Claude (`opus-4-7`) is the second-stage sizer that discretizes the bet into `{qty, limit_price, stop, target, size_pct}`. NO_GO verdicts are the meta-label saying "primary signal unreliable on this setup."

### Gaps / Roadmap
- **Triple-barrier labeling (training)**: The live stop uses vol-scaled barriers, but ML training labels are still score-threshold. Replace with proper volatility-scaled triple-barrier labels.
- **Purged k-fold CV**: `trainer.py` uses `TimeSeriesSplit` (fixed after bug). Upgrade to Purged K-Fold for clean validation.
- **Fracdiff features**: Current features are raw scores. Add fracdiff(close) as an ML feature.
- **Formal meta-labeling classifier**: Claude-as-meta-label is an LLM approximation. A trained binary meta-label model (per Ch.3) would be more auditable and backtestable.
- **HRP**: If/when we add portfolio optimization, use HRP instead of MVO.
- **DSR**: After adding more strategies, use DSR to deflate Sharpe ratios.

### Related: `feedback_ml_cv_split.md`
The TimeSeriesSplit sort-by-date fix (see memory) partially addresses the purged CV concern. Full purging is the next step.
