# Project Vision: Personal Quant Fund

> A small-scale quantitative trading system for 15–20 Indian NSE stocks.
> The goal is to generate systematic **Long / Short / Hold** signals using multi-factor models, backtest strategies against historical data, and manage real portfolio risk — the same playbook hedge funds use, adapted for a personal scale.

---

## What We Already Have (Phase 0 — Complete ✅)

| Module | File | Capabilities |
|---|---|---|
| Data Pipeline | `rapidApiService.js`, `stockData.js`, `db.js` | RapidAPI historical data, SQLite cache, Force Sync |
| Technical Analysis | `technicals.js` | RSI, MACD, SMA/EMA, Bollinger Bands, basic signals |
| Risk Metrics | `risk.js` | Beta vs NIFTY 50, Sharpe Ratio, VaR (95%), Max Drawdown, Volatility |
| Correlation | `correlation.js` | Pearson matrix, diversification scoring |
| Portfolio Tracker | `app.js`, `index.html` | Live P&L, Invested vs Current Value, per-stock breakdown |
| Alerts | `db.js`, `api.js` | Price threshold alerts |

---

## The Roadmap

### Phase 1 — Multi-Factor Scoring Engine 🧠
**Goal:** Assign every stock a composite **Factor Score** from -100 (strong short) to +100 (strong long).

#### Factors to implement:
| Factor | What it measures | Signal |
|---|---|---|
| **Momentum** | 1-month, 3-month, 6-month price returns | Stocks trending up → Long; trending down → Short |
| **Mean Reversion** | Z-score of price vs 50-day SMA | Overextended above → Short; oversold below → Long |
| **Volatility Regime** | Current vol vs historical avg vol | Low vol → breakout imminent; High vol → caution |
| **RSI Extreme** | RSI < 30 or RSI > 70 | Oversold → Long; Overbought → Short |
| **MACD Trend** | MACD histogram direction + crossovers | Bullish crossover → Long; Bearish → Short |
| **Volume Spike** | Today's volume vs 20-day avg volume | Unusual volume confirms trend direction |
| **Relative Strength** | Stock return vs NIFTY 50 return | Outperforming market → Long; Underperforming → Short |

#### Deliverables:
- [ ] `src/quant/factors.js` — Individual factor calculators
- [ ] `src/quant/scorer.js` — Weighted composite score engine
- [ ] `/api/quant/scores` — API endpoint returning ranked scores
- [ ] UI: "Quant Signals" tab with ranked stock cards (Long/Short/Hold)

---

### Phase 2 — Backtesting Engine 📊
**Goal:** Test any strategy against historical data to validate before risking real money.

#### Capabilities:
- Define entry/exit rules (e.g., "Buy when Factor Score > 60, Sell when < -20")
- Simulate trades over configurable date ranges
- Calculate key metrics: Total Return, CAGR, Sharpe, Max Drawdown, Win Rate
- Compare strategy vs Buy & Hold and vs NIFTY 50

#### Deliverables:
- [ ] `src/quant/backtester.js` — Core simulation engine
- [ ] `src/quant/strategies.js` — Pre-built strategy templates (Momentum, Mean Reversion, Multi-Factor)
- [ ] `/api/quant/backtest` — API endpoint accepting strategy config
- [ ] UI: Backtest results page with equity curve chart and trade log

---

### Phase 3 — Portfolio Optimizer ⚖️
**Goal:** Given a set of stocks and their scores, determine optimal position sizes.

#### Capabilities:
- Equal-weight baseline
- Score-weighted allocation (higher factor score → larger position)
- Risk parity (allocate inversely to volatility)
- Maximum Sharpe portfolio (mean-variance optimization)
- Respect constraints: max 15% per stock, no more than 30% in one sector

#### Deliverables:
- [ ] `src/quant/optimizer.js` — Position sizing engine
- [ ] UI: "Suggested Allocation" view showing recommended positions

---

### Phase 4 — Risk Management Dashboard 🛡️
**Goal:** Real-time monitoring of portfolio-level risk exposure.

#### Capabilities:
- Portfolio-level VaR (not just per-stock)
- Sector concentration heatmap
- Beta-weighted portfolio delta
- Drawdown monitor with circuit-breaker alerts
- Correlation drift detection (warn when diversification degrades)

#### Deliverables:
- [ ] `src/quant/portfolioRisk.js` — Aggregated risk calculations
- [ ] UI: "Risk Dashboard" tab with gauges, heatmaps, and alerts

---

### Phase 5 — Automated Signal Reports 📬
**Goal:** Daily pre-market and post-market actionable reports.

#### Capabilities:
- Pre-market brief: overnight factor score changes, stocks to watch
- Post-market summary: what moved, why, factor attribution
- Weekly portfolio rebalance suggestions
- Export to Telegram / Email

#### Deliverables:
- [ ] `src/quant/reporter.js` — Report generation engine
- [ ] Scheduled jobs via node-cron
- [ ] Telegram bot integration for push notifications

---

## Guiding Principles

1. **Data over intuition.** Every trade idea must be validated by at least two independent quantitative factors.
2. **Backtest before you bet.** No strategy goes live without historical validation showing positive expectancy.
3. **Risk first, returns second.** Position sizing and drawdown limits are non-negotiable.
4. **Incremental complexity.** Each phase builds on the previous. Don't skip ahead.
5. **Stay within edge.** We are NOT competing with institutional HFT. Our edge is patience, selectivity, and systematic discipline across a small universe of well-understood stocks.

---

## Technical Stack

- **Runtime:** Node.js (ES Modules)
- **Database:** SQLite (local, zero-config, fast)
- **Data Source:** RapidAPI (Indian Stock Exchange API)
- **Frontend:** Vanilla JS + Chart.js (lightweight, no framework overhead)
- **Math:** All quant calculations implemented from scratch in JavaScript (no external quant libraries — we understand every formula)

---

## Current Status

**Phase 0 is complete.** We are beginning **Phase 1: Multi-Factor Scoring Engine** today.
