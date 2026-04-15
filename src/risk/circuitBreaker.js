/**
 * Portfolio Circuit Breaker
 *
 * Halts all new orders if the portfolio has dropped more than
 * `dailyCircuitBreakerPct` from its previous session close.
 *
 * The check is on-demand (called before any order placement or
 * via the /api/risk/check endpoint). It does NOT run on a timer —
 * the system is localhost-deployed and is not guaranteed to be
 * running continuously during market hours.
 */

import { riskLimits } from '../config/riskLimits.js';

/**
 * @param {Array<{symbol, quantity, currentPrice, prevClose}>} positions
 * @returns {{
 *   triggered: boolean,
 *   currentValue: number,
 *   previousValue: number,
 *   drawdownPct: number,
 *   thresholdPct: number,
 * }}
 */
export function checkCircuitBreaker(positions) {
  const cfg = riskLimits.portfolio;

  let currentValue = 0;
  let previousValue = 0;

  for (const p of positions) {
    const qty = p.quantity || 0;
    const curr = Number(p.currentPrice) || 0;
    const prev = Number(p.prevClose) || curr;
    currentValue += qty * curr;
    previousValue += qty * prev;
  }

  if (previousValue <= 0) {
    return {
      triggered: false,
      currentValue: Number(currentValue.toFixed(2)),
      previousValue: 0,
      drawdownPct: 0,
      thresholdPct: cfg.dailyCircuitBreakerPct,
    };
  }

  const drawdownPct = ((currentValue - previousValue) / previousValue) * 100;
  const triggered = drawdownPct <= -cfg.dailyCircuitBreakerPct;

  return {
    triggered,
    currentValue: Number(currentValue.toFixed(2)),
    previousValue: Number(previousValue.toFixed(2)),
    drawdownPct: Number(drawdownPct.toFixed(2)),
    thresholdPct: cfg.dailyCircuitBreakerPct,
  };
}
