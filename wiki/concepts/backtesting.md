# Backtesting

## Current Implementation

**Router**: `quant_engine/routers/backtest.py`  
**Strategy**: `SicilianStrategy` and `RegimeAdaptiveStrategy`  
**Data**: SQLite price history via `data/loader.py`

### What's Implemented
- Walk-forward backtest on historical OHLCV data
- Daily position updates based on composite signal
- Equal-weight portfolio across stocks with LONG signal
- Performance metrics: total return, Sharpe ratio, max drawdown
- Benchmark comparison (Nifty 50 / equal-weight buy-and-hold)
- Trade-level statistics (win rate, avg hold period)
- Drawdown chart via Chart.js in `public/js/backtest.js`

## Pitfalls to Avoid

### Look-Ahead Bias
- All signals must use data available at time t to predict for time t+1
- Prices used: close[t] signals → enter at open[t+1] (or close[t+1])
- **Risk**: Using adjusted prices with dividends applied retroactively

### Survivorship Bias
- Current universe = 15 hardcoded NSE stocks from `config/portfolio.js`
- All are large-cap survivors → positive survivorship bias
- Impact: backtested returns are artificially high

### Data Snooping / Multiple Testing
- We have tested multiple weight combinations and thresholds
- Each iteration is an implicit hypothesis test
- **Fix**: Use DSR (Deflated Sharpe Ratio) from López de Prado Ch.14

### Transaction Costs
- Not modeled in current backtest
- NSE typical spread: 0.05–0.15% for large caps
- Brokerage: ~0.01–0.03% (discount brokers)
- Impact on weekly-rebalanced strategy: significant over many trades

### Overfitting to In-Sample Period
- No walk-forward or out-of-sample test
- All parameters (weights, thresholds) fit on the full history

## CPCV (Combinatorial Purged Cross-Validation)

From López de Prado Ch.10:
- Generate all C(T, k) train/test splits
- For each split, run a full backtest
- Result: **distribution** of Sharpe ratios (mean, variance)
- Compute PBO: probability that best strategy in-sample was due to luck

**Roadmap**: Implement CPCV in `quant_engine/routers/backtest.py` as an optional mode.

## Minimum Backtest Length

For SR = 0.5, at 95% confidence with 10 trials tested:
- Minimum observations: ~252 × 3 = ~756 days (3 years)
- Current data: depends on backfill depth (likely 1–3 years)

## Reporting Metrics

### Currently Reported
- Total return
- Annualized Sharpe ratio
- Maximum drawdown
- Win rate
- Average trade return

### Should Add (from literature)
| Metric | Source |
|--------|--------|
| Calmar ratio (return/max drawdown) | Standard |
| PSR / DSR (deflated SR) | López de Prado Ch.14 |
| Turnover (annual round trips) | Asness (2014) |
| IC / ICIR per factor | Grinold & Kahn |
| Hit rate by regime | Regime-Adaptive logic |

## Related Concepts
- [ml_pipeline.md](ml_pipeline.md) — ML cross-validation mirrors backtest methodology
- [factor_scoring.md](factor_scoring.md) — what's being backtested
- [regime_detection.md](regime_detection.md) — regime-split performance analysis
- [covariance_estimation.md](covariance_estimation.md) — portfolio construction in backtest
