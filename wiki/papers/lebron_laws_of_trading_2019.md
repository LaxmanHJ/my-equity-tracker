# The Laws of Trading — Agustin Lebron (2019)

**Full title:** The Laws of Trading: A Trader's Guide to Better Decision-Making for Everyone  
**Author:** Agustin Lebron (Jane Street Capital quant trader/researcher)  
**Publisher:** Wiley, 2019  
**Structure:** 11 chapters, each built around one law

---

## Problem

Most trading books focus on *what* to trade. This book focuses on *how to think* about trading — the mental framework that separates professional quant traders from discretionary retail traders. Lebron argues that edge, risk, costs, and adaptation are the four load-bearing pillars; all 11 laws derive from these.

---

## The 11 Laws

### Law 1 — Motivation: Know why you are actually trading

Most traders say "to make money" but real motivations are often boredom, status, intellectual stimulation, or risk-seeking thrill. Misaligned motivation corrupts every downstream decision: holding losers to avoid acknowledging mistakes, over-trading for excitement, under-sizing when it matters.

**Model implication:** Build objective functions that reflect actual goals (risk-adjusted returns, drawdown limits) — not vanity metrics like hit-rate or gross PnL.

---

### Law 2 — Adverse Selection: You are never happy with the amount you traded

*"If you did a good trade, in retrospect you should have done it bigger. If you did a bad trade, you shouldn't have done it at all."*

Every fill is tainted by adverse selection — the counterparty acts when they have an informational advantage. In market-making, you get filled most aggressively just before the price moves against you. In systematic trading, when a signal fires, the market has already partially priced in what you think you know.

**Key insight:** The quality of a signal degrades between generation and execution. The gap between paper-trade Sharpe and live Sharpe is largely adverse selection plus market impact.

**Model implication:**
- Measure signal decay: how much of the alpha is captured by the time you execute?
- Prefer signals with slow decay (hours/days) over fast-decay signals if execution latency is high.
- Build execution latency benchmarks into backtest; never assume instantaneous fills.

---

### Law 3 — Risk: Take only the risks you're being paid to take; hedge the others

Not all risk is rewarded. Distinguish:
- **Core risk** = the risk that is the source of your edge (e.g., momentum exposure). Take this.
- **Incidental risk** = risk accumulated as a byproduct of expressing the core risk (e.g., sector concentration, currency risk). Hedge this.

Uncompensated risk lowers Sharpe without adding return. Every unit of unhedged incidental risk is a tax on your edge.

**Model implication:**
- After generating signals, decompose portfolio risk into factor exposures (Barra-style).
- Neutralize exposures that are not part of the thesis (sector, market cap, beta).
- Our `SicilianStrategy` currently takes full market beta — introducing a beta-hedge on the Nifty 50 would remove uncompensated index risk.
- Target: market-neutral or low-beta portfolio; let alpha factors drive returns.

---

### Law 4 — Liquidity: Use the most liquid instrument for each risk

Liquidity has three dimensions:
1. **Bid-ask spread** — immediate cost of entry/exit.
2. **Market depth** — how much can you trade before moving the price?
3. **Resilience** — how fast does the market recover after your trade?

Illiquid instruments offer a liquidity risk premium, but you pay for it in: inability to exit at crisis time (when liquidity vanishes), higher market impact, and wider spreads that eat edge.

*Key law: "Liquidity crises correlate everything to 1."* During drawdowns, the assets that looked uncorrelated become correlated precisely because everyone exits illiquid positions simultaneously.

**Model implication:**
- Filter the stock universe for minimum daily traded value (e.g., ₹50 Cr/day) before running factor scores.
- Use liquidity-adjusted position sizing: `position_size ∝ min(signal_size, daily_volume_fraction)`.
- In the backtest, apply realistic market impact — do not assume you trade at VWAP with zero slippage.
- Monitor bid-ask spread as a transaction cost input, not just commissions.

---

### Law 5 — Edge: If you can't explain it in five minutes, you don't have a very good one

Edge has two components:
1. **Informational** — you know something the marginal participant does not.
2. **Capability** — you can act on it faster, cheaper, or more precisely than others.

True edge is durable because it is hard to replicate: superior data pipeline, faster compute, proprietary research process, better risk management. False "edge" comes from: overfitting, look-ahead bias, survivorship bias, data mining, regime luck.

The test: *Can you articulate the economic mechanism that produces the edge?* Momentum works because of under-reaction + herding. Mean reversion works because of over-reaction + liquidity provision. If the only explanation is "the backtest said so," it isn't edge.

**Model implication:**
- Every factor in `quant_engine/factors/` must have a documented economic hypothesis (see factor wiki pages).
- Periodically stress-test: does the factor still work in recent data, in bear markets, after costs?
- Separate in-sample discovery from out-of-sample validation — never tune parameters on the full dataset.

---

### Law 6 — Models: The model expresses the edge

A model is a formal, testable encoding of your edge hypothesis. Key principles:

1. **Every parameter is a potential overfit.** Prefer fewer parameters; use regularization.
2. **Wrong but useful.** All models are wrong; the question is whether they are useful after costs.
3. **Model ≠ reality.** When the model and your intuition disagree, investigate — one is wrong. Never blindly override the model, but never blindly follow it either.
4. **Stress-test across regimes.** A model calibrated only on bull-market data will fail in bear markets.
5. **Forecast uncertainty, not just forecasts.** A model that tells you "LONG with confidence 0.55" is more useful than one that says "LONG" and nothing else.

**Model implication:**
- Our 8-factor linear composite is a model expressing the momentum/mean-reversion/trend edge. The weights (25% momentum, 15% mean-reversion, etc.) should be validated out-of-sample, not just tuned in-sample.
- The ML classifier (`ml/trainer.py`) must use `TimeSeriesSplit` (already enforced per `feedback_ml_cv_split.md`).
- Add prediction confidence/probability output to ML signals; use it for position sizing.
- Document the economic hypothesis for each factor weight in the factor wiki pages.
- Regime-conditional models (like `RegimeAdaptiveStrategy`) are explicitly endorsed by Lebron: a model calibrated for all regimes is inferior to one that knows which regime it is in.

---

### Law 7 — Costs and Capacity: If costs seem negligible vs. edge, you're wrong about at least one

Total transaction cost = commissions + bid-ask spread + market impact + opportunity cost (missing fills).

**Capacity** is the AUM ceiling beyond which market impact destroys edge. The relationship:

```
Capacity ∝ (edge / market_impact_per_unit)²
```

High-Sharpe, high-turnover strategies have the lowest capacity. Low-turnover, lower-Sharpe strategies scale to much larger AUM.

*"If you think your costs are negligible relative to your edge, you're wrong about at least one of them."* — this is the most violated rule in systematic trading. Backtests assume zero or minimal impact; live trading reveals the true cost.

**Model implication:**
- Add a transaction cost model to `quant_engine/routers/backtest.py`:
  - Commission: ₹20/order flat (Zerodha) + 0.03% STT for delivery
  - Spread cost: 0.05–0.15% depending on stock liquidity tier
  - Market impact: Kyle's lambda estimate = `σ * sqrt(trade_size / ADV)`
- Track net-of-cost Sharpe, not gross Sharpe, as the primary backtest metric.
- Set a minimum signal threshold below which the edge does not cover the round-trip cost.
- For NSE mid-cap stocks: assume 0.2–0.4% round-trip all-in cost; factor this into the ±40 signal threshold.

---

### Law 8 — Possibility: Just because it hasn't happened doesn't mean it can't

Financial returns are fat-tailed (leptokurtic) — 5-sigma events occur far more frequently than Gaussian models predict. Risk management calibrated only to historical data underestimates tail risk.

*"The events that kill you are precisely those with no historical precedent."*

Kelly criterion application: size positions such that no single event can cause catastrophic loss. A Kelly fraction of 0.25–0.5 (quarter-Kelly to half-Kelly) is standard in professional practice for this reason.

**Model implication:**
- Replace normal-distribution VaR with historical VaR or CVaR (Expected Shortfall) in risk metrics.
- Stress-test the portfolio against: 2008 GFC, 2020 COVID crash, 2013 taper tantrum (NSE-equivalent).
- Never size a position based on "this 30% drop has never happened to this stock." Use sector-level or index-level worst-case as a floor.
- Implement maximum position size hard limits independent of the signal strength.
- Consider adding a "max drawdown circuit breaker": if rolling 20-day PnL < -X%, halt new signals and reduce all positions.

---

### Law 9 — Alignment: Aligning everyone's interests is time well spent

Misaligned incentives produce bad outcomes even with smart participants. The principal-agent problem: if your model is optimized for a metric that isn't what you actually care about, it will exploit that misalignment.

Common misalignments in systematic trading:
- Optimizing backtest Sharpe → real-world returns (overfitting)
- Maximizing trade count → real P&L (churn without edge)
- Maximizing alpha signal → ignoring execution cost (academic alpha vs. net alpha)

**Model implication:**
- The objective function in `ml/trainer.py` should be aligned with net-of-cost P&L, not just signal accuracy.
- Live trading metrics (real P&L, real Sharpe) must be tracked alongside model predictions to detect drift.
- Consider adding a "realized alpha" tracker: compare forecasted direction to actual next-day return, as a model health metric.

---

### Law 10 — Technology: Master technology and data, or lose to someone who does

Data quality > data quantity. Garbage-in-garbage-out: a factor built on survivorship-biased or adjusted-price data will appear to have edge it doesn't have in practice.

Key infrastructure principles:
- **Reproducibility**: backtests must be deterministic and versioned.
- **Data integrity**: validate OHLCV data for splits, dividends, missing bars, erroneous outliers.
- **Latency awareness**: know the lag between signal generation and execution; model it explicitly.
- **Monitoring**: production systems must have alerts, health checks, and automatic fallback paths.

**Model implication:**
- Our Turso database is the single source of truth — critical. Validate for: missing dates, price outliers (>20% single-day moves that are data errors, not real), correct adjusted prices post-split.
- Add data quality checks in `quant_engine/data/loader.py`: flag stocks with >5% missing bars in a rolling window.
- The Python FastAPI health check (`/health`) should validate DB connectivity and last data timestamp.
- Log all signal generations with timestamps to enable post-trade analysis vs. actual fills.

---

### Law 11 — Adaptation: If you're not getting better, you're getting worse

Markets are non-stationary — edges decay as more capital chases them, and regimes shift. A model trained on 2010–2019 data has no guarantee of working in 2020–2026 data, especially across regime changes (QE-era vs. rate-hike era).

Feedback loop discipline:
1. Track predicted direction vs. realized return on every signal.
2. Compute rolling Sharpe of each factor independently — watch for decay.
3. When a factor's rolling Sharpe drops below a threshold, reduce its weight or investigate.
4. Retrain models on a schedule (or trigger-based on out-of-sample degradation).

*"If you're not getting better, you're getting worse."* — the market is a competitive, adaptive adversary.

**Model implication:**
- Build a factor health dashboard: plot rolling 63-day Sharpe for each of the 8 factors.
- Add walk-forward validation to the backtest: train on years 1–3, test on year 4, slide forward.
- `RegimeAdaptiveStrategy` already handles regime shifts — ensure regime features (VIX, Nifty trend) are refreshed regularly.
- Schedule quarterly model retraining; more frequent if realized alpha diverges from predicted.
- Version and archive model weights so you can detect when a retrain significantly changes behavior.

---

## Key Numbers and Rules of Thumb (from Lebron's practice)

| Rule | Value / Guideline |
|------|-------------------|
| Kelly fraction for real trading | 0.25–0.5 × full Kelly |
| Minimum signal Sharpe (gross) to be worth trading | > 0.5 pre-cost |
| Minimum net-of-cost Sharpe to actually trade | > 0.3 |
| Typical all-in round-trip cost (liquid NSE large-cap) | 0.1–0.2% |
| Typical all-in round-trip cost (NSE mid-cap) | 0.2–0.4% |
| Maximum single-position size (unhedged) | 5–10% of portfolio |
| Signal decay check window | 1–5 days |
| Factor health review cadence | Rolling 63-day Sharpe |
| Model retrain cadence | Quarterly or on degradation |

---

## Relationship to Existing Wiki Concepts

| Lebron Law | Related wiki page |
|-----------|-------------------|
| Law 2 (Adverse Selection) | `concepts/backtesting.md` — execution assumptions |
| Law 3 (Risk) | `concepts/factor_scoring.md` — beta neutralization |
| Law 4 (Liquidity) | `concepts/factor_scoring.md` — universe filtering |
| Law 5 (Edge) | `concepts/momentum.md`, `concepts/mean_reversion.md` |
| Law 6 (Models) | `concepts/ml_pipeline.md`, `concepts/factor_scoring.md` |
| Law 7 (Costs) | `concepts/backtesting.md` — transaction cost model |
| Law 8 (Possibility) | `concepts/regime_detection.md` — tail risk |
| Law 11 (Adaptation) | `concepts/regime_detection.md`, `concepts/ml_pipeline.md` |

---

## Project Usage

### Implemented / Partially Addressed
- **Law 6 (Models)**: `RegimeAdaptiveStrategy` encodes regime-conditional model switching — aligned with Lebron's recommendation.
- **Law 11 (Adaptation)**: Regime features (VIX, Nifty trend, Markov) provide partial adaptation signal.
- **Law 8 (Possibility)**: `TimeSeriesSplit` CV prevents data leakage; regime features capture macro tail states.

### Gaps — Actionable Items

| Priority | Law | Gap | File to Change |
|----------|-----|-----|----------------|
| HIGH | Law 7 | No transaction cost model in backtest — gross Sharpe only | `quant_engine/routers/backtest.py` |
| HIGH | Law 4 | No liquidity filter on universe (min ADV threshold) | `quant_engine/data/loader.py` |
| HIGH | Law 8 | VaR uses normal distribution — should use historical/CVaR | `src/analysis/` |
| MEDIUM | Law 2 | Signal decay not measured — paper vs. live gap unknown | `quant_engine/factors/` |
| MEDIUM | Law 11 | No rolling per-factor Sharpe health monitoring | `quant_engine/routers/scores.py` |
| MEDIUM | Law 3 | Portfolio beta not neutralized against Nifty | `quant_engine/strategies/sicilian_strategy.py` |
| MEDIUM | Law 9 | ML objective (accuracy) not aligned with net P&L | `quant_engine/ml/trainer.py` |
| LOW | Law 10 | No data quality validation (missing bars, outliers) | `quant_engine/data/loader.py` |
| LOW | Law 8 | No drawdown circuit breaker | `quant_engine/strategies/base.py` |
