# Quant Engine — Knowledge Bank

> A plain-language guide to every data point on the Quant Signals screen.
> Designed so that even if you're seeing a stock card for the first time, you know exactly what each number means and what action it suggests.

---

## 1. The Summary Cards (Top Row)

| Card | What It Shows |
|---|---|
| **Stocks Scored** | Total number of stocks the engine analyzed. Should match your portfolio count. |
| **Long Signals** | Stocks with composite score **≥ +40**. The engine is saying: "This stock has strong bullish factors — consider buying or holding." |
| **Hold** | Stocks between **-40 and +40**. No strong conviction in either direction. Wait for clearer signals. |
| **Short Signals** | Stocks with composite score **≤ -40**. The engine is saying: "This stock has strong bearish factors — consider selling, reducing position, or avoiding." |

---

## 2. The Stock Cards

Each card represents one stock, ranked from highest composite score (most bullish) to lowest (most bearish).

### Card Header

| Data Point | Meaning | Example |
|---|---|---|
| **#Rank** | Position in the ranking. #1 = the engine's top pick right now. | #1 |
| **Symbol** | The NSE ticker (e.g., TATAELXSI, INFY). | TATAELXSI |
| **Price (₹)** | Last closing price from your cached data. | ₹4,449.30 |
| **Composite Score** | The master number. Ranges from **-100 to +100**. Positive = bullish, negative = bearish. Higher absolute value = stronger conviction. | +10.44 |
| **Signal Badge** | 🟢 LONG / ⚪ HOLD / 🔴 SHORT — the actionable recommendation. | ⚪ HOLD |

### What the Composite Score Tells You

```
-100 ◄━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━► +100
 🔴 SHORT    |    ⚪ HOLD    |         🟢 LONG
          -40              +40
```

- **+60 to +100**: Very strong long — multiple factors agree this stock is poised to rise.
- **+40 to +60**: Long signal — favorable conditions, worth considering.
- **+10 to +40**: Mild bullish lean — some factors are positive but not enough for a trade.
- **-10 to +10**: Neutral — conflicting signals, no edge.
- **-40 to -10**: Mild bearish lean — some warning signs.
- **-40 to -60**: Short signal — unfavorable conditions.
- **-60 to -100**: Very strong short — multiple factors agree this stock is likely to fall.

---

## 3. The 7 Factor Bars (Inside Each Card)

Each bar represents one quantitative factor. The bar fills from left (bearish, -1.0) to right (bullish, +1.0), with the center being neutral (0.0).

### Understanding the Bar Colors

- 🟩 **Green bar** (score > +0.2): This factor is **bullish** for this stock.
- 🟥 **Red bar** (score < -0.2): This factor is **bearish** for this stock.
- ⬜ **Gray bar** (-0.2 to +0.2): This factor is **neutral** — no strong signal.

---

### Factor 1: MOMENTUM (Weight: 25%)

**What it measures:** How much has the stock price moved up or down over the past 1, 3, and 6 months?

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `return_1m` | Price change over the last ~21 trading days | -18.21% |
| `return_3m` | Price change over the last ~63 trading days | -13.22% |
| `return_6m` | Price change over the last ~126 trading days | -16.94% |
| `raw_momentum` | Weighted average: 20% × 1m + 40% × 3m + 40% × 6m | -15.71% |
| `score` | Normalized to [-1, +1]. A momentum of +50% or more → +1.0, -50% or worse → -1.0 | -0.31 |

**How to read it:**
- **Positive momentum** = stock is trending up. Trend-following says: keep riding it.
- **Negative momentum** = stock is trending down. Something is pushing it lower.

**Why it matters:** Momentum is the strongest alpha factor in Indian mid/small-cap stocks historically. Stocks that have been going up tend to continue going up (and vice versa) for weeks or months.

---

### Factor 2: MEAN REVERSION (Weight: 15%)

**What it measures:** How far is the current price from its "normal" level (50-day average)?

| Field | Meaning | Example |
|---|---|---|
| `sma_50` | The 50-day Simple Moving Average — the stock's "average price" over 50 trading days | ₹5,245.27 |
| `z_score` | How many standard deviations the current price is from the SMA. Z=0 means at the average, Z=+2 means very far above, Z=-2 means very far below. | -2.24 |
| `score` | **Inverted**: A very high Z (overbought) → negative score. A very low Z (oversold) → positive score. | +1.0 |

**How to read it:**
- **Score near +1.0** (Z-score deeply negative) = Stock has fallen far below its average. It's "stretched" downward like a rubber band. Mean reversion says it's likely to bounce back up.
- **Score near -1.0** (Z-score highly positive) = Stock has risen far above its average. It's "overextended" and may pull back.
- **Score near 0.0** = Price is near its average. Nothing unusual.

**Why it matters:** Stocks don't go in one direction forever. When they deviate too far from their average, there's a statistical tendency to snap back. This is the opposing force to Momentum — and having both creates a balanced system.

> 💡 **Momentum vs Mean Reversion — why both?**
> Momentum catches strong trends. Mean Reversion catches overdone moves. When they **agree** (both positive or both negative), that's a high-conviction signal. When they **disagree**, it's a conflicting signal — and the composite score will be closer to zero.

---

### Factor 3: RSI — Relative Strength Index (Weight: 15%)

**What it measures:** The speed and magnitude of recent price changes. Is the stock being bought aggressively (overbought) or sold aggressively (oversold)?

| Field | Meaning | Example |
|---|---|---|
| `rsi` | The RSI value, ranging from 0 to 100 | 27.79 |
| `score` | RSI 20 → +1.0, RSI 50 → 0.0, RSI 80 → -1.0 | +0.74 |

**How to read the RSI number:**

```
  0 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100
  OVERSOLD   |        NEUTRAL        |      OVERBOUGHT
           30                       70
```

- **RSI < 30** = Oversold. Sellers are exhausted. Often a buying opportunity.
- **RSI 30-70** = Normal range. No extreme.
- **RSI > 70** = Overbought. Buyers are exhausted. Price may pull back.

**Why it matters:** RSI is one of the most widely used indicators in finance. When RSI hits extremes (below 20 or above 80), the probability of a reversal increases significantly. Our engine uses RSI as a complementary confirmator for mean reversion signals.

---

### Factor 4: MACD — Moving Average Convergence Divergence (Weight: 15%)

**What it measures:** Is the trend accelerating or decelerating? Are we seeing a trend change?

| Field | Meaning | Example |
|---|---|---|
| `macd_line` | The difference between the 12-day EMA and 26-day EMA. Positive = short-term trend is above long-term. | -228.44 |
| `signal_line` | 9-day EMA of the MACD line (a smoothed version). | -181.71 |
| `histogram` | MACD line minus Signal line. This is what drives the score. | -46.73 |
| `histogram_pct` | Histogram as a percentage of the stock price (so we can compare across stocks). | -1.05% |
| `crossover` | `BULLISH` if histogram just flipped from negative to positive. `BEARISH` if flipped positive to negative. `NONE` otherwise. | NONE |
| `score` | Based on histogram percentage + crossover bonus (±0.3). | -0.70 |

**How to read it:**
- **Positive histogram growing** = Bullish momentum is accelerating. Strong buy signal.
- **Positive histogram shrinking** = Bullish momentum is fading. Trend may be ending.
- **Negative histogram growing (more negative)** = Bearish momentum is accelerating.
- **BULLISH crossover** = The trend may be reversing from bearish to bullish. This adds +0.3 bonus to the score.
- **BEARISH crossover** = The trend may be reversing from bullish to bearish. This adds -0.3 penalty.

**Why it matters:** MACD captures trend direction AND acceleration. A crossover is one of the most popular "buy/sell" triggers used by traders worldwide. We weight crossovers heavily because they often mark the exact point where momentum shifts.

---

### Factor 5: VOLATILITY (Weight: 10%)

**What it measures:** Is the stock becoming more or less volatile compared to its recent history?

| Field | Meaning | Example |
|---|---|---|
| `vol_short` | Annualized volatility over the last 20 trading days (in %). | 35.45% |
| `vol_long` | Annualized volatility over the last 60 trading days (in %). | 39.72% |
| `vol_ratio` | Short vol / Long vol. Below 1.0 = calming down. Above 1.0 = heating up. | 0.89 |
| `regime` | `CONTRACTING` (< 0.85), `STABLE` (0.85-1.15), or `EXPANDING` (> 1.15). | STABLE |
| `score` | Contracting → positive (calm = good). Expanding → negative (risky). | +0.27 |

**How to read it:**
- **CONTRACTING** (green) = Volatility is decreasing. The stock is stabilizing. This often precedes a breakout, and holding positions is safer.
- **STABLE** = No significant change in volatility. Neutral.
- **EXPANDING** (red) = Volatility is increasing. The stock is becoming more unpredictable. This means higher risk — position sizes should be smaller.

**Why it matters:** Volatility is risk. A stock going up 20% but with 50% annualized volatility is much riskier than one going up 15% with 20% volatility. This factor penalizes stocks that are becoming erratic and rewards those that are calm and predictable.

---

### Factor 6: VOLUME (Weight: 10%)

**What it measures:** Is trading volume unusually high or normal? And does the volume confirm or contradict the price trend?

| Field | Meaning | Example |
|---|---|---|
| `current_volume` | Today's traded volume (number of shares). | 320,311 |
| `avg_volume_20d` | Average daily volume over the last 20 trading days. | 338,717 |
| `volume_ratio` | Current / Average. Above 1.5 = spike. | 0.95 |
| `price_trend_5d` | Price change over the last 5 trading days (%). Used to determine trend direction. | -1.08% |
| `spike` | `true` if volume is more than 1.5× the 20-day average. | false |
| `score` | 0 if no spike. If spike: positive when confirming uptrend, negative when confirming downtrend. | 0.00 |

**How to read it:**
- **Volume spike + price going up** = Smart money is buying. Confirms the uptrend. Positive score.
- **Volume spike + price going down** = Institutional selling. Confirms the downtrend. Negative score.
- **No spike** = Normal trading. No additional signal. Score = 0.
- **Mild activity** (ratio 1.2-1.5) = Slight confirmation of trend direction. ±0.3 score.

**Why it matters:** Price action without volume is suspect. If a stock rises on low volume, it could be a "fake" move. But when price moves on significantly higher-than-normal volume, it means real money is behind the move. Volume is the "lie detector" of the market.

---

### Factor 7: RELATIVE STRENGTH (Weight: 10%)

**What it measures:** Is this stock outperforming or underperforming the overall market (NIFTY 50) over the last 3 months?

| Field | Meaning | Example |
|---|---|---|
| `stock_return` | The stock's 3-month return (%). | — |
| `benchmark_return` | NIFTY 50's 3-month return (%). | — |
| `excess_return` | Stock return minus benchmark return. Positive = beating the market. | — |
| `outperforming` | `true` if excess return is positive. | — |
| `score` | Excess return of +20% or more → +1.0. Excess of -20% or worse → -1.0. | 0.00 |

> ⚠️ **Note:** This factor currently shows `null` / `0.00` because NIFTY 50 benchmark data hasn't been cached yet. Once benchmark data is synced, this factor will activate and contribute to the composite score.

**How to read it (when active):**
- **Outperforming** (positive score) = This stock is doing better than the market. In quant finance, relative strength is a proven alpha factor — stocks that beat the market tend to continue beating it.
- **Underperforming** (negative score) = This stock is lagging behind the market. Money is flowing elsewhere.

**Why it matters:** Even if a stock is going up, if the market is going up more, you're losing relative value. Relative strength helps you identify the true winners in any market environment.

---

## 4. How the Composite Score is Calculated

```
Composite = (Momentum × 0.25)
          + (Mean Reversion × 0.15)
          + (RSI × 0.15)
          + (MACD × 0.15)
          + (Volatility × 0.10)
          + (Volume × 0.10)
          + (Relative Strength × 0.10)

Final Score = Composite × 100    (range: -100 to +100)
```

### Why These Weights?

| Factor | Weight | Rationale |
|---|---|---|
| Momentum | 25% | Strongest historical alpha factor in Indian equities |
| Mean Reversion | 15% | Counterbalances momentum; catches overdone moves |
| RSI | 15% | Widely validated oversold/overbought indicator |
| MACD | 15% | Captures trend acceleration and crossovers |
| Volatility | 10% | Risk awareness — penalizes erratic stocks |
| Volume | 10% | Confirmation factor — validates price moves |
| Relative Strength | 10% | Identifies market leaders vs laggards |

---

## 5. Quick Reference: Reading a Card in 5 Seconds

1. **Look at the composite score and color.** Green positive = bullish. Red negative = bearish.
2. **Check the signal badge.** 🟢 LONG means act. ⚪ HOLD means wait. 🔴 SHORT means caution.
3. **Scan the factor bars.** If most bars are green → strong consensus. If bars are mixed green and red → conflicting signals (hence HOLD).
4. **Pay special attention when Momentum and Mean Reversion agree.** Both green = strong upside case. Both red = strong downside case.
5. **Check for crossover events in MACD.** A `BULLISH` crossover on a stock with good momentum is a high-conviction buy signal.

---

## 6. Example: Reading TATAELXSI's Card

```
Score: +10.44 (HOLD — mild bullish lean)

Momentum:       -0.31 🔴  (stock falling 13-17% over 3-6 months)
Mean Reversion: +1.00 🟩  (Z-score -2.24 → deeply oversold, likely to bounce)
RSI:            +0.74 🟩  (RSI 27.8 → in oversold territory)
MACD:           -0.70 🔴  (histogram deeply negative, trend still down)
Volatility:     +0.27 🟩  (vol contracting, stabilizing)
Volume:          0.00 ⬜  (normal volume, no spike)
Rel. Strength:   0.00 ⬜  (benchmark data not yet available)
```

**Interpretation:** TATAELXSI is in a downtrend (Momentum and MACD are red), BUT it's deeply oversold (Mean Reversion and RSI are bright green). This is a classic "falling knife vs bounce candidate" scenario. The composite score is slightly positive because the oversold signals slightly outweigh the downtrend signals, but it's not strong enough for a LONG signal. **Wait for MACD to show a bullish crossover** — that would push the score into LONG territory and confirm the reversal.
