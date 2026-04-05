# Algorithmic and High-Frequency Trading

**Authors**: Álvaro Cartea, Sebastian Jaimungal & José Penalva  
**Published**: Cambridge University Press, 2015  
**Source PDF**: `~/Desktop/Proyectos/Scilian-Books/Algorithmic and High-Frequency Trading by Álvaro Cartea .pdf`  
**Status**: Ingested (ToC + Preface fully read; chapter content via structure)

---

## Structure Overview

| Part | Chapters | Topic |
|------|----------|-------|
| I | 1–4 | Microstructure and Empirical Facts |
| II | 5 | Mathematical Tools (Stochastic Optimal Control) |
| III | 6–12 | Algorithmic and High-Frequency Trading Models |
| App | A | Stochastic Calculus for Finance |

---

## Part I: Microstructure and Empirical Facts

### Chapter 1: Electronic Markets and the Limit Order Book

Key concepts:
- **Limit Order Book (LOB)**: Queue of resting buy/sell limit orders. Best bid/ask = inside spread.
- **Order types**: Market orders (immediate execution), limit orders (passive, earn spread), IOC, FOK, iceberg orders.
- **Market participants**: Informed traders (informational edge), market makers (liquidity provision), institutional (execution algorithms), HFT (latency arbitrage).
- **Colocation**: Physical proximity to exchange servers → microsecond latency advantage.
- **Exchange fees**: Maker-taker fee model — liquidity makers receive rebates; takers pay fees. Impacts optimal order routing.

### Chapter 2: Microstructure Theory

**Grossman–Miller Market Making Model**:
- Market makers provide immediacy — earn the bid-ask spread as compensation for inventory risk.
- Competitive equilibrium: spread = 2 × cost of capital × inventory risk.

**Adverse Selection**:
- Informed traders take from market makers → market maker loses when trading against informed order flow.
- **Kyle (1985) model**: Order flow reveals information; market maker sets price as function of net order flow.
- **Price impact**: λ = dp/dQ — price moves linearly with order size (linear price impact).

**Measuring Liquidity**:
- Bid-ask spread (effective and quoted)
- Market depth (volume at best bid/ask)
- Amihud illiquidity ratio = |return| / volume

### Chapter 3: Empirical Evidence — Prices and Returns

Key empirical facts used in algorithm design:
- **Daily returns**: Fat tails (excess kurtosis), volatility clustering (GARCH-like)
- **Intraday patterns**: U-shaped volume and volatility (high at open/close, low midday)
- **Interarrival times**: Trades do NOT arrive uniformly; clustered in time (self-exciting process)
- **Non-Markovian prices**: Short-term autocorrelation in trade direction (herding)
- **Pairs trading empirics**: Co-integrated price pairs exhibit mean-reverting spread; spread statistics (ADF test, half-life)

### Chapter 4: Activity and Market Quality

**Price Impact** (critical for execution):
- **Temporary impact**: Instantaneous — reverts after trade. Modeled as: Δp_temp = g(v) where v = trade rate
- **Permanent impact**: Persistent — price discovery. Modeled as: Δp_perm = κ·v
- **Walking the LOB**: Large orders exhaust best levels and fill at worse prices — nonlinear at size

**Spreads**: Widen with volatility; narrow with volume. NSE large-caps typically 0.05–0.15%.

**Hidden Orders**: Iceberg orders — only partial quantity visible; common in large institutional flow.

---

## Part II: Mathematical Tools

### Chapter 5: Stochastic Optimal Control

The core mathematical tool for execution and market-making problems.

**Hamilton–Jacobi–Bellman (HJB) equation**:
- V(t, x) = value function at time t, state x
- HJB: ∂V/∂t + max_a [drift(x,a)·∇V + ½σ²·∇²V] = 0
- Terminal condition: V(T, x) = g(x)

Applied to:
- **Optimal liquidation**: Agent maximizes cash minus inventory penalty
- **Optimal limit order placement**: Agent chooses bid/ask offset to maximize P&L

**Stochastic optimal stopping**: When to exercise (enter/exit) a trade position.

---

## Part III: Algorithmic Trading Models

### Chapters 6–7: Optimal Execution (Almgren–Chriss Framework)

**Problem**: Liquidate Q shares over [0, T] to minimize market impact + timing risk.

**Almgren–Chriss model** (Ch.6):
- Temporary impact: h(v) = η·v (linear)
- Permanent impact: g(v) = γ·v
- Optimal schedule: Exponential decay of trading rate
- Trade-off: Slow → less impact but more timing risk; Fast → more impact but less risk

**Key formula**: Optimal execution rate v*(t) = Q·κ/sinh(κT) · cosh(κ(T−t))  
Where κ = √(γ/η) × risk_aversion

**Extensions (Ch.7)**:
- Price limiter: Stop trading if price falls below threshold
- Order flow incorporation: Adjust rate based on buy/sell imbalance
- Dark pool execution: Split between lit venue (guaranteed fill, impact) and dark pool (no impact, uncertain fill)

### Chapter 8: Execution with Limit + Market Orders

Extends execution to use both order types:
- **Limit orders**: Post inside spread, earn spread, but uncertain fill
- **Market orders**: Certain fill, pay spread
- **Optimal mix**: Dynamic switching based on urgency, inventory, and time remaining

Targeting execution schedules (VWAP, TWAP) as constraints.

### Chapter 9: Targeting Volume (VWAP)

**VWAP** = Volume Weighted Average Price = Σ(price_i × volume_i) / Σ(volume_i)

**Percentage of Volume (PoV)**: Trade at a fixed fraction of market volume each period.  
**Stochastic volume**: Model market volume as mean-reverting process (OU process).

HJB equation gives optimal trading rate that minimizes deviation from VWAP benchmark.

### Chapter 10: Market Making

**Avellaneda–Stoikov model** (referenced):
- Market maker posts bid δ_b below mid, ask δ_a above mid
- Optimal spread = f(inventory, time, volatility, adverse selection)
- With no inventory risk: symmetric spread = σ²·γ·(T−t) + 2/γ·ln(1+γ/k)

**Adverse selection in market making**:
- Informed order flow → midprice moves against market maker
- "Short-term alpha" of market orders: E[ΔP | buy order] > 0
- Market maker widens spread when informed flow is high

**Inventory risk**: Market maker accumulates directional exposure → adjusts quotes asymmetrically to rebalance.

### Chapter 11: Pairs Trading and Statistical Arbitrage

**Co-integration**: Two price series S₁, S₂ with spread X = S₁ − β·S₂ ~ mean-reverting OU process:  
dX = κ(μ − X)dt + σ·dW

**Optimal entry/exit bands**:
- Enter long spread at −b, exit at 0
- Enter short spread at +b, exit at 0
- Optimal b = f(κ, σ, r, transaction costs) — solved via optimal stopping HJB

**Short-term alpha**: Co-integrated pairs may also have short-lived trend components → model as sum of OU + drift.

### Chapter 12: Order Imbalance

**Order imbalance** = (buy volume − sell volume) / total volume

Predictive of short-term price direction:
- High positive imbalance → price likely to rise
- Used as execution signal: accelerate buying when imbalance is against you

**Markov chain model**: LOB state transitions (price up/down, volume change) as Markov chain.  
**Optimal liquidation with imbalance**: Adjust execution rate based on current imbalance signal.

---

## Project Usage

### Relevance to Current Project

This book is primarily focused on **execution** and **microstructure** — a different level from our current daily-bar factor investing. However, several concepts directly apply:

| Concept | Chapter | Applicability |
|---------|---------|---------------|
| Price impact of trades | 6–7 | Backtest transaction cost modeling |
| VWAP benchmark | 9 | Execution quality measurement |
| Pairs trading / co-integration | 11 | Future strategy: pairs within NSE sectors |
| Order imbalance signal | 12 | Could adapt to daily buy/sell volume ratio |
| Adverse selection | 2, 10 | Understanding why our signals may decay |

### Currently Applicable

- **Transaction costs in backtest** (`routers/backtest.py`): The temporary/permanent impact model from Ch.6 gives a principled way to model costs beyond flat percentage. For our 15-stock universe and weekly rebalancing, flat 0.1% per side is a reasonable approximation.
- **VWAP**: Not used, but relevant if execution timing is added.

### Gaps / Roadmap

- **Pairs trading** (Ch.11): High potential for NSE — co-integrated pairs within same sector (e.g., HDFCBANK/ICICIBANK, Reliance/ONGC). The OU spread model + optimal band selection from this book gives the mathematical framework.
- **Order imbalance feature** (Ch.12): Daily buy/sell volume ratio as an additional factor signal. Would require intraday data or directional volume estimates.
- **Execution improvement**: Current backtest uses end-of-day prices with no slippage model. Ch.6 model could inform realistic slippage.

### Related Concepts
- [backtesting.md](../concepts/backtesting.md) — transaction costs section
- [mean_reversion.md](../concepts/mean_reversion.md) — pairs trading is mean-reversion on spread
- [factor_scoring.md](../concepts/factor_scoring.md) — order imbalance as potential new factor
