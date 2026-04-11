/**
 * Dynamic Stop-Loss Calculator
 *
 * Two methods combined (takes the more protective = higher value):
 *
 * 1. Volatility-scaled initial stop (Lopez de Prado, AFML Ch.3)
 *    stop = entry × (1 - h × σ_20)
 *    where h is a multiplier and σ_20 is rolling daily volatility.
 *
 * 2. Chandelier trailing stop (classic trend-following)
 *    stop = max(high_last_N) - k × ATR_14
 *    Ratchets up only as price rises.
 *
 * Reference: wiki/papers/lopez_de_prado_afml_2018.md
 */

import { riskLimits } from '../config/riskLimits.js';

/**
 * Compute log returns from a price series.
 */
function logReturns(prices) {
  const returns = [];
  for (let i = 1; i < prices.length; i++) {
    if (prices[i - 1] > 0 && prices[i] > 0) {
      returns.push(Math.log(prices[i] / prices[i - 1]));
    }
  }
  return returns;
}

/**
 * Rolling standard deviation of daily log returns (daily volatility).
 */
function rollingStdev(returns, window) {
  if (returns.length < window) return null;
  const slice = returns.slice(-window);
  const mean = slice.reduce((a, b) => a + b, 0) / slice.length;
  const variance = slice.reduce((sum, r) => sum + (r - mean) ** 2, 0) / (slice.length - 1);
  return Math.sqrt(variance);
}

/**
 * Wilder's Average True Range (ATR).
 * TR = max(high - low, abs(high - prevClose), abs(low - prevClose))
 * ATR = smoothed moving average of TR over `window` bars.
 */
export function calculateATR(bars, window = 14) {
  if (bars.length < window + 1) return null;

  const trueRanges = [];
  for (let i = 1; i < bars.length; i++) {
    const { high, low } = bars[i];
    const prevClose = bars[i - 1].close;
    const tr = Math.max(
      high - low,
      Math.abs(high - prevClose),
      Math.abs(low - prevClose)
    );
    trueRanges.push(tr);
  }

  // Wilder's smoothing: seed with simple average, then recursive EMA
  let atr = trueRanges.slice(0, window).reduce((a, b) => a + b, 0) / window;
  for (let i = window; i < trueRanges.length; i++) {
    atr = (atr * (window - 1) + trueRanges[i]) / window;
  }
  return atr;
}

/**
 * Volatility-scaled initial stop (Lopez de Prado method).
 *
 * @param {number} entryPrice — average purchase price
 * @param {Array<{close: number}>} bars — OHLCV bars (most recent last)
 * @param {number} volMultiplier — h in the triple-barrier formula
 * @param {number} volWindow — rolling window for σ
 * @returns {number | null} stop price, or null if insufficient data
 */
export function volatilityStop(entryPrice, bars, volMultiplier, volWindow) {
  if (!bars || bars.length < volWindow + 1) return null;
  const closes = bars.map(b => b.close);
  const returns = logReturns(closes);
  const sigma = rollingStdev(returns, volWindow);
  if (sigma === null || !isFinite(sigma)) return null;
  return entryPrice * (1 - volMultiplier * sigma);
}

/**
 * Chandelier trailing stop.
 *
 * @param {Array<{high: number, low: number, close: number}>} bars
 * @param {number} atrMultiplier — k in the formula
 * @param {number} atrWindow — bars for ATR calculation
 * @param {number} trailWindow — lookback for highest high
 * @returns {number | null} stop price
 */
export function chandelierStop(bars, atrMultiplier, atrWindow, trailWindow) {
  if (!bars || bars.length < Math.max(atrWindow + 1, trailWindow)) return null;
  const recent = bars.slice(-trailWindow);
  const highestHigh = Math.max(...recent.map(b => b.high));
  const atr = calculateATR(bars, atrWindow);
  if (atr === null || !isFinite(atr)) return null;
  return highestHigh - atrMultiplier * atr;
}

/**
 * Hybrid stop — uses the higher (more protective) of volatility and chandelier stops.
 *
 * @param {number} entryPrice
 * @param {Array<{high,low,close}>} bars
 * @returns {{
 *   stop: number | null,
 *   method: string,
 *   volStop: number | null,
 *   trailStop: number | null,
 * }}
 */
export function computeStop(entryPrice, bars) {
  const cfg = riskLimits.stopLoss;
  const volStop = volatilityStop(entryPrice, bars, cfg.volMultiplier, cfg.volWindow);
  const trailStop = chandelierStop(bars, cfg.atrMultiplier, cfg.atrWindow, cfg.trailWindow);

  let stop = null;
  let method = null;

  if (cfg.method === 'volatility') {
    stop = volStop;
    method = 'volatility';
  } else if (cfg.method === 'chandelier') {
    stop = trailStop;
    method = 'chandelier';
  } else {
    // hybrid — higher stop is more protective (closer to current price)
    if (volStop !== null && trailStop !== null) {
      stop = Math.max(volStop, trailStop);
      method = stop === volStop ? 'hybrid:volatility' : 'hybrid:chandelier';
    } else {
      stop = volStop ?? trailStop;
      method = volStop !== null ? 'hybrid:volatility' : 'hybrid:chandelier';
    }
  }

  return { stop, method, volStop, trailStop };
}

/**
 * Check if the current price has breached the stop.
 *
 * @returns {{
 *   triggered: boolean,
 *   stop: number | null,
 *   currentPrice: number,
 *   distancePct: number | null,   // negative = below stop (triggered)
 *   method: string | null,
 * }}
 */
export function checkStopLoss(entryPrice, currentPrice, bars) {
  const { stop, method, volStop, trailStop } = computeStop(entryPrice, bars);
  if (stop === null) {
    return { triggered: false, stop: null, currentPrice, distancePct: null, method: null };
  }
  const distancePct = ((currentPrice - stop) / stop) * 100;
  return {
    triggered: currentPrice <= stop,
    stop: Number(stop.toFixed(2)),
    currentPrice,
    distancePct: Number(distancePct.toFixed(2)),
    method,
    volStop: volStop !== null ? Number(volStop.toFixed(2)) : null,
    trailStop: trailStop !== null ? Number(trailStop.toFixed(2)) : null,
  };
}
