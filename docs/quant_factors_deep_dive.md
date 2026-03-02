# Quant Factors: Deep Dive & Calculations

This document provides an in-depth explanation of the 7 quantitative factors used in the Quant Engine, including what they are, how they are calculated, and how they help in analyzing a stock.

---

## 1. Momentum (Weighted 1m/3m/6m returns)
- **Concept:** Momentum is built on the trend-following philosophy that assets performing well recently tend to continue performing well, while losers keep losing.
- **Calculation:** 
  It takes the percentage change in price over three distinct timeframes to smooth out short-term noise while catching medium-term trends.
  - 1-month return (~21 trading days) × 20% weight
  - 3-month return (~63 trading days) × 40% weight
  - 6-month return (~126 trading days) × 40% weight
  - **Formula:** `(R_1m * 0.20) + (R_3m * 0.40) + (R_6m * 0.40)`
- **How it helps:** By giving 80% of the weight to 3-month and 6-month timeframes, it prevents fakeouts from volatile 1-month swings, ensuring you are aligning with the stock's dominant, entrenched trend.

## 2. Mean Reversion (Z-score vs 50-day SMA)
- **Concept:** Reversion to the mean posits that asset prices will eventually return to their long-term historical average. Extreme deviations from normality are temporary.
- **Calculation:**
  - Calculate the 50-day Simple Moving Average (SMA) of the closing price.
  - Calculate the Standard Deviation (SD) of the closing prices over those 50 days (to measure normal volatility).
  - **Z-Score Formula:** `(Current Price - 50-day SMA) / SD`
- **How it helps:** Identifies stretched rubber bands. A Z-score > +2 indicates the stock is statistically overextended upwards (overbought) and due for a pullback. A Z-score < -2 means it has fallen abnormally far and fast (oversold) and is due for a bounce. 

## 3. RSI (Overbought/Oversold extremes)
- **Concept:** The Relative Strength Index is a momentum oscillator that measures the speed and change of price movements scaled from 0 to 100.
- **Calculation:**
  - Evaluated over a 14-day lookback period.
  - Calculate the Average Gain and Average Loss over those 14 days.
  - Relative Strength (RS) = Average Gain / Average Loss.
  - **Formula:** `100 - (100 / (1 + RS))`
- **How it helps:** It captures short-term buying/selling exhaustion. An RSI < 30 indicates deep oversold conditions—sellers are exhausted. An RSI > 70 indicates overbought conditions—buying power is drying up. It is best used alongside Mean Reversion.

## 4. MACD (Histogram + Crossover detection)
- **Concept:** Moving Average Convergence Divergence tracks the relationship between two moving averages of a price, offering a visual representation of trend changes and momentum acceleration.
- **Calculation:**
  - **MACD Line:** 12-day Exponential Moving Average (EMA) - 26-day EMA.
  - **Signal Line:** 9-day EMA of the MACD Line.
  - **MACD Histogram = MACD Line - Signal Line.**
- **How it helps:** 
  - **Crossovers:** When the MACD line crosses *above* the signal line, the short-term trend is turning bullish. Crossing *below* is bearish.
  - **Histogram:** The size of the histogram shows the *acceleration* of momentum. A growing positive histogram means the bullish trend is gaining strength.

## 5. Volatility Regime (Short vs long window vol)
- **Concept:** Measures how erratic or stable the stock's price movements are right now compared to its recent past.
- **Calculation:**
  - Short Volatility (e.g., 20 days) = Annualized standard deviation of daily returns.
  - Long Volatility (e.g., 60 days) = Annualized standard deviation of daily returns.
  - **Volatility Ratio = Short Volatility / Long Volatility**
- **How it helps:** A ratio less than 1.0 means volatility is *contracting*—the stock is calming down and stabilizing, often setting up a secure base for a breakout. A ratio greater than 1.0 means volatility is *expanding*—the stock is becoming chaotic and riskier to trade.

## 6. Volume Spike (Trend-confirming volume detection)
- **Concept:** Volume acts as the market's polygraph test. Price movements on low volume are highly untrustworthy; price movements on high volume reflect institutional (smart money) conviction.
- **Calculation:**
  - Calculate the 20-day Average Daily Volume (ADV).
  - **Volume Ratio = Current Day Volume / 20-day ADV**.
  - A spike is detected if the ratio is > 1.5 (volume is 50% higher than normal).
- **How it helps:** Identifies strong institutional participation. A 5% price surge accompanied by a 2x volume spike yields a powerful confirmation signal that the new trend has vast capital support.

## 7. Relative Strength vs NIFTY 50
- **Concept:** Measures how well the stock is performing relative to the broader market index (NIFTY 50). Not to be confused with RSI.
- **Calculation:**
  - Stock 3-month return = `% change in stock price over 3 months`.
  - Benchmark 3-month return = `% change in NIFTY 50 over 3 months`.
  - **Excess Return = Stock Return - Benchmark Return**.
- **How it helps:** Pinpoints structural market leaders. If the NIFTY falls by 5% but your stock falls by only 1%, it demonstrates exceptional relative strength. In bull markets, stocks with high relative strength tend to massively outperform.

---

## Complete Stock Analysis Example: TATAELXSI

To see how these 7 numbers form a rich, 360-degree story, let's analyze a single stock, **TATAELXSI**, under hypothetical (but realistic distress) conditions:

### The Raw Data:
- **Momentum:** 1m = 5%, 3m = -10%, 6m = -20% `(Weighted Momentum = -11%)` 🔴 Bearish
- **Mean Reversion:** Z-score = `-2.24` (Current Price is far below the 50-day average) 🟩 Bullish
- **RSI:** `27.79` 🟩 Bullish
- **MACD:** Histogram is `-46.73`, MACD Line is below the Signal line. 🔴 Bearish
- **Volatility Regime:** Short Vol `35%` / Long Vol `40%` = Ratio `0.88` (Contracting) 🟩 Bullish
- **Volume Spike:** Ratio `0.95` (No Spike) ⬜ Neutral
- **Rel. Strength:** Underperforming NIFTY 50 by `-15%` 🔴 Bearish

### The Financial Narrative (How it helps us analyze):

**1. Acknowledging the Bloodbath (Trend & Relative Strength)**
The stock is in a severe structural downtrend. The weighted momentum of -11% proves this isn't a quick dip; it's a multi-month decline. The **MACD** confirms this bearish momentum is still active (negative histogram). Worst of all, the **Relative Strength** shows it is underperforming the NIFTY 50 by 15%. Not only is it losing value, it's doing dramatically worse than the rest of the market. Cash is fleeing this asset.

**2. Spotting the Snap-Back Potential (Mean Reversion & RSI)**
However, markets don't go down in straight lines. The **Z-score** of -2.24 acts as an alarm flashing that the selloff is statistically overdone. The rubber band is stretched to the absolute limit. Parallel to this, the **RSI** of 27.79 confirms that sellers are fundamentally exhausted. There is a very high mathematical probability of a significant upward "relief rally" or a reversal.

**3. Assessing Risk & Conviction (Volatility & Volume)**
Is it safe to try and catch this bounce? Yes and No. On the bright side, the **Volatility Regime** is contracting (0.88 ratio)—the violent daily drops are calming down, and the stock is trying to establish a floor. However, there is **no Volume Spike** (0.95 ratio). Smart money hasn't started buying in large, visible quantities yet. 

### The Engine's Verdict (The Composite Call)
The Quant Engine processes all of this and issues a **⚪ HOLD**. 

Why not Buy? Because buying a stock with terrible Momentum, negative MACD, and poor Relative Strength is "catching a falling knife." The downtrend could easily resume. 

Why not Sell? Because selling when the Z-score and RSI are this deeply oversold is asking to get caught in a massive upward rally.

**The Actionable Trading Plan:** The trader keeps TATAELXSI on their watchlist. They are waiting for *two triggers* to turn this into a high-conviction BUY: 
1. The **MACD crosses bullishly** (confirming momentum has shifted upward).
2. A **Volume Spike** occurs (confirming institutional money has finally stepped in to capitalize on the oversold conditions). 

This is the power of the Quant Engine: saving you from acting on emotion, highlighting the unseen quantitative dynamics, and telling you exactly what condition to wait for next.

---

## ADVANCED: Not in Engine, But Crucial to Know
*The following metrics are not currently integrated into the Quant Engine calculations, but they have an extremely high correlation with future price action and provide a massive edge to quantitative traders.*

### 1. Delivery Percentage (Crucial for Indian Equities)
*The lie-detector test for volume.*
- **The Concept:** Out of the total volume traded in a day, how many shares were actually bought and taken to demat accounts (Delivery) versus intraday day-trading (bought and sold the same day)?
- **The Calculation:** `(Delivery Volume / Total Volume) * 100`
- **How it helps:** Raw volume can be manipulated by high-frequency trading algorithms playing ping-pong intraday. Delivery volume cannot. A price breakout backed by a **60%+ Delivery Percentage** means real, long-term investors are buying and hoarding the stock. 

### 2. VWAP & Anchored VWAP (Volume Weighted Average Price)
*The battle lines drawn by algorithms.*
- **The Concept:** The true, absolute average price a stock was traded at, factoring in exactly how much volume was transacted at each price tier.
- **The Calculation:** `Cumulative (Typical Price * Volume) / Cumulative Volume`
- **How it helps:** Institutional trading bots are programmed to buy when the stock dips to the VWAP, and pause when it shoots far above it. If you "anchor" a VWAP to a specific major event (like an earnings date), it shows you the exact breakeven price of all recent institutional buyers.

### 3. ADX (Average Directional Index)
*The trend-strength filter.*
- **The Concept:** While MACD and Momentum tell you *which direction* the trend is going, ADX simply tells you *how intense* the trend is, regardless of whether it's up or down.
- **The Calculation:** Derived from smoothed moving averages of the differences between recent daily Highs and Lows (+DI and -DI). Scaled from 0 to 100.
- **How it helps:** If ADX is **< 20**, there is no trend; the stock is just chopping sideways. If ADX is **> 25**, a powerful trend is active. ADX tells you whether to ignore MACD crossovers or act on them.

### 4. Put-Call Ratio (PCR)
*The ultimate barometer for retail fear and greed.*
- **The Concept:** The options market often dictates the stock market. PCR looks at the ratio of Put options (bets the stock will crash) vs Call options (bets the stock will surge).
- **The Calculation:** `Total Put Volume / Total Call Volume`
- **How it helps:** PCR is a legendary **contrarian indicator**. The options crowd is usually wrong at the extremes. When PCR is heavily skewed to fear (extremely high), smart quants start building LONG positions to prepare for a short-squeeze upswing.

### 5. ATR (Average True Range)
*The mathematical secret to surviving market noise.*
- **The Concept:** Measures absolute daily volatility in pure Rupee terms. It calculates precisely how much a stock shakes up and down on an average day.
- **The Calculation:** A 14-day smoothed average of the daily True Range: `Max(High-Low, Abs(High-PrevClose), Abs(Low-PrevClose))`.
- **How it helps:** Amateurs set Stop Losses based on random percentages (e.g., 5%). Quants set Stop Losses based on ATR multiples (e.g., 2x ATR), mathematically ensuring they aren't kicked out of a trade by normal daily noise.

### 6. OBV (On-Balance Volume)
*Detecting the "Silent Accumulation".*
- **The Concept:** Volume precedes price. OBV tracks the cumulative, running total of volume flowing into and out of an asset over time.
- **The Calculation:** If today's closing price is > yesterday's close, add today's volume to the OBV total. If lower, subtract today's volume.
- **How it helps:** Highly correlated with catching major price breakouts before they happen. You look for a **Bullish Divergence**: when the price is flat, but the OBV line is aggressively climbing. This tells you "smart money is quietly accumulating."
