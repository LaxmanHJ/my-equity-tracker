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

**Calculation Criteria:** It uses historical closing prices to calculate percentage returns over short (1-month/21 days), medium (3-month/63 days), and long (6-month/126 days) periods. These returns are blended using a weighted average (20% for 1m, 40% for 3m, 40% for 6m) to emphasize more sustainable medium-term trends while still capturing recent movement. The raw momentum percentage is then clamped between -50% and +50% and scaled proportionally to a final factor score from -1.0 to +1.0.

**How it helps decide stock performance:** Momentum relies on the principle that "an object in motion stays in motion." A high positive score indicates strong, sustained buying pressure, suggesting the stock is likely to continue rising (a strong "Long" contributor). Conversely, a strong negative score indicates entrenched selling pressure, suggesting the stock is a falling knife and should be avoided or shorted.

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `return_1m` | Price change over the last ~21 trading days | -18.21% |
| `return_3m` | Price change over the last ~63 trading days | -13.22% |
| `return_6m` | Price change over the last ~126 trading days | -16.94% |
| `raw_momentum` | Weighted average: 20% × 1m + 40% × 3m + 40% × 6m | -15.71% |
| `score` | Normalized to [-1, +1]. A momentum of +50% or more → +1.0 | -0.31 |

---

### Factor 2: MEAN REVERSION (Weight: 15%)

**What it measures:** How far is the current price from its "normal" baseline (50-day average)?

**Calculation Criteria:** Calculates the 50-day Simple Moving Average (SMA) and the standard deviation of prices over those 50 days. It computes the "Z-score," representing how many standard deviations the current price is away from the SMA. The score is then inverted: a highly positive Z-score (price abnormally high) maps to a negative score (bearish), while a deeply negative Z-score (price abnormally low) maps to a positive score (bullish), capping at Z-scores of ±2.5.

**How it helps decide stock performance:** Markets tend to overreact to news in the short term. Mean reversion assumes that extreme, rapid price moves are statistically unsustainable and prices will eventually "revert to the mean" (snap back to the SMA). A positive score here (Z-score < -2) signals that the stock has been unfairly punished and is due for a bounce, offering a contrarian "Long" entry point for value hunters buying the dip.

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `sma_50` | The 50-day Simple Moving Average | ₹5,245.27 |
| `z_score` | Standard deviations from the SMA. Z=0 is average. | -2.24 |
| `score` | **Inverted**: High Z (overbought) → negative. Low Z (oversold) → positive. | +1.0 |

> 💡 **Momentum vs Mean Reversion — why both?**
> Momentum catches strong ongoing trends, while Mean Reversion catches overdone, extreme moves. When they **agree** (both positive), you have a stock that is trending up but just had an extreme, temporary pullback — a perfect buy. When they **disagree**, it keeps the overall composite score balanced.

---

### Factor 3: RSI — Relative Strength Index (Weight: 15%)

**What it measures:** The speed and magnitude of recent price changes to determine if a stock has been bought or sold too aggressively.

**Calculation Criteria:** Uses J. Welles Wilder's formula over a 14-day lookback period. It measures the average gain of up-days vs the average loss of down-days to calculate an index from 0 to 100. Our engine maps this to a -1.0 to +1.0 score by identifying extremes: RSI values below 30 map proportionally up to +1.0 (bullish), and RSI values above 70 map down to -1.0 (bearish). RSI between 30 and 70 represents neutral territory (score around 0.0).

**How it helps decide stock performance:** RSI acts as a momentum oscillator to identify exhausted market participants. A high positive score (RSI < 30) means the asset has been dumped so rapidly that sellers are likely exhausted, favoring a "Long" reversal. A negative score (RSI > 70) warns that the buying frenzy has peaked, buyers are exhausted, and a pullback is imminent (a "Short" or "Take Profit" signal). 

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `rsi` | The RSI value, ranging from 0 to 100 | 27.79 |
| `score` | RSI 20 → +1.0, RSI 50 → 0.0, RSI 80 → -1.0 | +0.74 |

---

### Factor 4: MACD — Moving Average Convergence Divergence (Weight: 15%)

**What it measures:** Is the trend accelerating or decelerating? Is there a confirmed trend reversal underway?

**Calculation Criteria:** Computes the 12-day and 26-day Exponential Moving Averages (EMA). Their difference is the "MACD Line". A 9-day EMA of the MACD Line is the "Signal Line". The "Histogram" is the MACD Line minus the Signal Line. To allow fair comparison across stocks of any price, the histogram is normalized as a percentage of the current stock price. A growing positive histogram yields a positive score. Additionally, we scan for 'crossovers' (MACD crossing the Signal line), adding a heavy ±0.3 bonus/penalty to the final factor score.

**How it helps decide stock performance:** MACD reveals changes in the strength, direction, and duration of a trend. A growing positive histogram indicates accelerating bullish momentum. A bullish crossover (MACD line crosses *above* Signal line) is a classic, powerful "Buy" signal indicating a new uptrend has just begun. MACD crossovers are essential for timing your entries and confirming that a stock's momentum has actually shifted in your favor.

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `macd_line` | Short-term EMA minus long-term EMA | -228.44 |
| `signal_line` | 9-day EMA of the MACD line | -181.71 |
| `histogram` | MACD line minus Signal line | -46.73 |
| `histogram_pct` | Histogram relative to stock price | -1.05% |
| `crossover` | `BULLISH` (just crossed up), `BEARISH` (crossed down), or `NONE` | NONE |
| `score` | Based on histogram percentage + crossover bonus (±0.3) | -0.70 |

---

### Factor 5: VOLATILITY (Weight: 10%)

**What it measures:** Risk and stability. Is the stock becoming wildly erratic or settling into a calm, predictable pattern?

**Calculation Criteria:** Calculates the annualized historical volatility (standard deviation of daily returns) over two windows: a short 20-day window and a long 60-day window. It divides short volatility by long volatility to get a ratio. Ratios < 0.85 indicate a "Contracting" regime (mapped to positive scores up to +1.0). Ratios > 1.15 indicate an "Expanding" regime (mapped to negative scores down to -1.0). Between 0.85 and 1.15 is considered "Stable" (neutral score).

**How it helps decide stock performance:** Volatility is a proxy for uncertainty and risk. Contracting volatility (green score) means the stock is consolidating quietly, which often precedes a strong, clean, and safe directional breakout — it's much easier to hold. Expanding volatility (red score) means price swings are becoming wild and unpredictable, significantly increasing the risk of getting stopped out or caught in a massive drop. This factor penalizes risky behavior.

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `vol_short` | Annualized volatility over the last 20 trading days (in %) | 35.45% |
| `vol_long` | Annualized volatility over the last 60 trading days (in %) | 39.72% |
| `vol_ratio` | Short vol / Long vol. Below 1.0 = calming. Above 1.0 = heating up. | 0.89 |
| `regime` | `CONTRACTING` (< 0.85), `STABLE` (0.85-1.15), or `EXPANDING` (> 1.15) | STABLE |
| `score` | Contracting → positive. Expanding → negative. | +0.27 |

---

### Factor 6: VOLUME (Weight: 10%)

**What it measures:** Institutional conviction. Is trading volume unusually high, and does that volume confirm the current price trend?

**Calculation Criteria:** Computes the average daily volume over the past 20 trading days. It compares today's volume to this average. A "Spike" is registered if today's volume is >1.5x the normal 20-day average. If a spike occurs, it evaluates the 5-day price trend: if the trend is up, the volume spike confirms buying pressure (positive score up to +1.0). If the trend is down, it indicates panic selling (negative score down to -1.0). If there is no spike, the score remains 0.0.

**How it helps decide stock performance:** Volume is the engine that drives true price changes. A stock price moving on low volume is untrustworthy and could be retail noise or a "bull trap." A price moving on a massive volume spike means institutional smart money is entering or exiting the stock. When you see a high positive volume score, it confirms that large funds are aggressively buying, greatly increasing the reliability of a "Long" signal.

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `current_volume` | Today's traded volume (number of shares) | 320,311 |
| `avg_volume_20d` | Average daily volume over the last 20 trading days | 338,717 |
| `volume_ratio` | Current / Average. Above 1.5 = spike. | 0.95 |
| `price_trend_5d` | Price change over the last 5 trading days (%) | -1.08% |
| `spike` | `true` if volume is more than 1.5× the 20-day avg | false |
| `score` | 0 if no spike. Positive if spike confirms an uptrend. | 0.00 |

---

### Factor 7: RELATIVE STRENGTH (Weight: 10%)

**What it measures:** Market leadership. Is this stock outperforming or underperforming the overall broader market (the NIFTY 50)?

**Calculation Criteria:** Compares the 3-month percentage price return of the individual stock against the 3-month return of the NIFTY 50 benchmark index. The difference between the two is the "Excess Return". An excess return of +20% or more maps linearly to a maximum +1.0 score, while underperforming by -20% or worse maps down to a -1.0 score.

**How it helps decide stock performance:** To generate actual "Alpha" (above-average returns), you must own stocks that are leading the market, not just floating up in a rising tide. A stock with a high positive relative strength score is a proven market leader; even on red market days, it falls less, and on green days, it rallies significantly harder. This implies strong underlying accumulation by institutions, making it an excellent fundamental "Long" candidate.

**The raw data you might see in the API:**

| Field | Meaning | Example |
|---|---|---|
| `stock_return` | The stock's 3-month return (%) | — |
| `benchmark_return` | NIFTY 50's 3-month return (%) | — |
| `excess_return` | Stock return minus benchmark return | — |
| `outperforming` | `true` if excess return is positive | — |
| `score` | Excess return of +20% → +1.0. Excess of -20% → -1.0. | 0.00 |

> ⚠️ **Note:** This factor currently shows `null` / `0.00` because NIFTY 50 benchmark data hasn't been completely cached yet.
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

---

> 🧠 **Want to go deeper?** Check out the [Detailed Quant Factors Guide](./quant_factors_deep_dive.md) for full calculation breakdowns and a deeper dive into the math behind each signal.
