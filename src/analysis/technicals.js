/**
 * Technical Analysis Module
 * Calculates RSI, MACD, Moving Averages, and Bollinger Bands
 */

import { settings } from '../config/settings.js';

/**
 * Calculate Simple Moving Average
 */
export function calculateSMA(prices, period) {
  if (prices.length < period) return [];

  const sma = [];
  for (let i = period - 1; i < prices.length; i++) {
    const sum = prices.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
    sma.push({
      index: i,
      value: sum / period
    });
  }
  return sma;
}

/**
 * Calculate Exponential Moving Average
 */
export function calculateEMA(prices, period) {
  if (prices.length < period) return [];

  const multiplier = 2 / (period + 1);
  const ema = [];

  // First EMA is the SMA
  let sum = 0;
  for (let i = 0; i < period; i++) {
    sum += prices[i];
  }
  let prevEMA = sum / period;
  ema.push({ index: period - 1, value: prevEMA });

  // Calculate EMA for remaining prices
  for (let i = period; i < prices.length; i++) {
    const currentEMA = (prices[i] - prevEMA) * multiplier + prevEMA;
    ema.push({ index: i, value: currentEMA });
    prevEMA = currentEMA;
  }

  return ema;
}

/**
 * Calculate Relative Strength Index (RSI)
 */
export function calculateRSI(prices, period = settings.analysis.rsiPeriod) {
  if (prices.length < period + 1) return [];

  const rsi = [];
  const gains = [];
  const losses = [];

  // Calculate price changes
  for (let i = 1; i < prices.length; i++) {
    const change = prices[i] - prices[i - 1];
    gains.push(change > 0 ? change : 0);
    losses.push(change < 0 ? Math.abs(change) : 0);
  }

  // Calculate first average gain/loss
  let avgGain = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
  let avgLoss = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;

  // First RSI
  if (avgLoss === 0) {
    rsi.push({ index: period, value: 100 });
  } else {
    const rs = avgGain / avgLoss;
    rsi.push({ index: period, value: 100 - (100 / (1 + rs)) });
  }

  // Calculate remaining RSI values using smoothed averages
  for (let i = period; i < gains.length; i++) {
    avgGain = (avgGain * (period - 1) + gains[i]) / period;
    avgLoss = (avgLoss * (period - 1) + losses[i]) / period;

    if (avgLoss === 0) {
      rsi.push({ index: i + 1, value: 100 });
    } else {
      const rs = avgGain / avgLoss;
      rsi.push({ index: i + 1, value: 100 - (100 / (1 + rs)) });
    }
  }

  return rsi;
}

/**
 * Calculate MACD (Moving Average Convergence Divergence)
 */
export function calculateMACD(
  prices,
  fastPeriod = settings.analysis.macdFast,
  slowPeriod = settings.analysis.macdSlow,
  signalPeriod = settings.analysis.macdSignal
) {
  const fastEMA = calculateEMA(prices, fastPeriod);
  const slowEMA = calculateEMA(prices, slowPeriod);

  if (fastEMA.length === 0 || slowEMA.length === 0) return { macd: [], signal: [], histogram: [] };

  // Calculate MACD line (Fast EMA - Slow EMA)
  const macdLine = [];
  const macdValues = [];

  for (const slow of slowEMA) {
    const fast = fastEMA.find(f => f.index === slow.index);
    if (fast) {
      const macdValue = fast.value - slow.value;
      macdLine.push({ index: slow.index, value: macdValue });
      macdValues.push(macdValue);
    }
  }

  // Calculate Signal line (EMA of MACD)
  const signalEMA = calculateEMA(macdValues, signalPeriod);

  // Calculate Histogram
  const histogram = [];
  for (let i = 0; i < signalEMA.length; i++) {
    const macdIdx = macdLine.length - signalEMA.length + i;
    if (macdIdx >= 0) {
      histogram.push({
        index: macdLine[macdIdx].index,
        value: macdLine[macdIdx].value - signalEMA[i].value
      });
    }
  }

  return {
    macd: macdLine,
    signal: signalEMA.map((s, i) => ({
      index: macdLine[macdLine.length - signalEMA.length + i]?.index,
      value: s.value
    })),
    histogram
  };
}

/**
 * Calculate Bollinger Bands
 */
export function calculateBollingerBands(prices, period = 20, stdDevMultiplier = 2) {
  if (prices.length < period) return [];

  const bands = [];

  for (let i = period - 1; i < prices.length; i++) {
    const slice = prices.slice(i - period + 1, i + 1);
    const sma = slice.reduce((a, b) => a + b, 0) / period;

    // Calculate standard deviation
    const squaredDiffs = slice.map(p => Math.pow(p - sma, 2));
    const variance = squaredDiffs.reduce((a, b) => a + b, 0) / period;
    const stdDev = Math.sqrt(variance);

    bands.push({
      index: i,
      middle: sma,
      upper: sma + (stdDevMultiplier * stdDev),
      lower: sma - (stdDevMultiplier * stdDev),
      bandwidth: ((sma + (stdDevMultiplier * stdDev)) - (sma - (stdDevMultiplier * stdDev))) / sma * 100
    });
  }

  return bands;
}

/**
 * Generate trading signals based on indicators
 */
export function generateSignals(prices, dates) {
  const rsi = calculateRSI(prices);
  const macd = calculateMACD(prices);
  const sma20 = calculateSMA(prices, 20);
  const sma50 = calculateSMA(prices, 50);

  const signals = [];
  const latestPrice = prices[prices.length - 1];
  const latestRSI = rsi[rsi.length - 1]?.value;
  const latestMACD = macd.histogram[macd.histogram.length - 1]?.value;
  const latestSMA20 = sma20[sma20.length - 1]?.value;
  const latestSMA50 = sma50[sma50.length - 1]?.value;

  // RSI signals
  if (latestRSI !== undefined) {
    if (latestRSI < 30) {
      signals.push({ type: 'RSI', signal: 'OVERSOLD', value: latestRSI.toFixed(2), description: 'RSI below 30 - potential buying opportunity' });
    } else if (latestRSI > 70) {
      signals.push({ type: 'RSI', signal: 'OVERBOUGHT', value: latestRSI.toFixed(2), description: 'RSI above 70 - potential selling opportunity' });
    } else {
      signals.push({ type: 'RSI', signal: 'NEUTRAL', value: latestRSI.toFixed(2), description: 'RSI in normal range' });
    }
  }

  // MACD signals
  if (latestMACD !== undefined) {
    if (latestMACD > 0) {
      signals.push({ type: 'MACD', signal: 'BULLISH', value: latestMACD.toFixed(4), description: 'MACD histogram positive - bullish momentum' });
    } else {
      signals.push({ type: 'MACD', signal: 'BEARISH', value: latestMACD.toFixed(4), description: 'MACD histogram negative - bearish momentum' });
    }
  }

  // Moving Average signals
  if (latestSMA20 !== undefined && latestSMA50 !== undefined) {
    if (latestPrice > latestSMA20 && latestPrice > latestSMA50) {
      signals.push({ type: 'MA', signal: 'BULLISH', description: 'Price above both 20 and 50 SMA - uptrend' });
    } else if (latestPrice < latestSMA20 && latestPrice < latestSMA50) {
      signals.push({ type: 'MA', signal: 'BEARISH', description: 'Price below both 20 and 50 SMA - downtrend' });
    } else {
      signals.push({ type: 'MA', signal: 'MIXED', description: 'Price between moving averages - consolidation' });
    }

    // Golden/Death cross
    if (sma20.length >= 2 && sma50.length >= 2) {
      const prevSMA20 = sma20[sma20.length - 2]?.value;
      const prevSMA50 = sma50[sma50.length - 2]?.value;

      if (prevSMA20 < prevSMA50 && latestSMA20 > latestSMA50) {
        signals.push({ type: 'CROSS', signal: 'GOLDEN_CROSS', description: '🔥 20 SMA crossed above 50 SMA - strong bullish signal' });
      } else if (prevSMA20 > prevSMA50 && latestSMA20 < latestSMA50) {
        signals.push({ type: 'CROSS', signal: 'DEATH_CROSS', description: '⚠️ 20 SMA crossed below 50 SMA - strong bearish signal' });
      }
    }
  }

  return {
    signals,
    indicators: {
      rsi: latestRSI,
      macd: {
        line: macd.macd[macd.macd.length - 1]?.value,
        signal: macd.signal[macd.signal.length - 1]?.value,
        histogram: latestMACD
      },
      sma20: latestSMA20,
      sma50: latestSMA50
    }
  };
}

/**
 * Get full technical analysis for a stock
 */
export function getFullAnalysis(historicalData) {
  const prices = historicalData.map(d => d.close);
  const dates = historicalData.map(d => d.date);

  return {
    rsi: calculateRSI(prices),
    macd: calculateMACD(prices),
    sma20: calculateSMA(prices, 20),
    sma50: calculateSMA(prices, 50),
    ema12: calculateEMA(prices, 12),
    ema26: calculateEMA(prices, 26),
    bollingerBands: calculateBollingerBands(prices),
    signals: generateSignals(prices, dates),
    cmp: prices[prices.length - 1]
  };
}
