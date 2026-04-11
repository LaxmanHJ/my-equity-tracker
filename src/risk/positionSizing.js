/**
 * Position Sizing — Inverse Volatility Weighting
 *
 * Based on Kakushadze (101 Formulaic Alphas): returns scale as R ~ V^0.76,
 * which validates giving less volatile stocks larger allocations.
 *
 * Formula:
 *   weight_i = (1 / σ_i) / Σ(1 / σ_j)
 *   position_value_i = portfolio_value × min(weight_i, maxPositionPct)
 *   shares_i = floor(position_value_i / current_price_i)
 *
 * Reference: wiki/papers/kakushadze_101_alphas.md
 */

import { riskLimits } from '../config/riskLimits.js';

/**
 * Annualized volatility from log returns.
 */
function annualizedVol(prices, tradingDays = 252) {
  if (prices.length < 2) return null;
  const returns = [];
  for (let i = 1; i < prices.length; i++) {
    if (prices[i - 1] > 0 && prices[i] > 0) {
      returns.push(Math.log(prices[i] / prices[i - 1]));
    }
  }
  if (returns.length < 2) return null;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const variance = returns.reduce((s, r) => s + (r - mean) ** 2, 0) / (returns.length - 1);
  const daily = Math.sqrt(variance);
  return daily * Math.sqrt(tradingDays);
}

/**
 * Compute target position sizes for each symbol using inverse-vol weighting.
 *
 * @param {Array<{symbol: string, currentPrice: number, bars: Array}>} positions
 * @param {number} portfolioValue — total AUM in same currency as prices
 * @returns {Array<{
 *   symbol: string,
 *   annualVol: number | null,
 *   rawWeight: number,        // pre-cap inverse-vol weight
 *   cappedWeight: number,     // after maxPositionPct cap
 *   targetValue: number,
 *   targetShares: number,
 *   currentPrice: number,
 * }>}
 */
export function computePositionSizes(positions, portfolioValue) {
  const cfg = riskLimits.position;

  // 1. Calculate annualized volatility for each position
  const vols = positions.map(p => {
    const closes = (p.bars || []).map(b => b.close);
    return annualizedVol(closes);
  });

  // 2. Inverse volatility weights (drop positions with missing vol)
  const invVols = vols.map(v => (v && v > 0 ? 1 / v : 0));
  const sumInvVol = invVols.reduce((a, b) => a + b, 0);

  if (sumInvVol === 0) {
    // Fallback to equal weight
    const equalWeight = 1 / positions.length;
    return positions.map((p, i) => {
      const capped = Math.min(equalWeight, cfg.maxPositionPct / 100);
      const targetValue = portfolioValue * capped;
      return {
        symbol: p.symbol,
        annualVol: vols[i],
        rawWeight: equalWeight,
        cappedWeight: capped,
        targetValue,
        targetShares: p.currentPrice > 0 ? Math.floor(targetValue / p.currentPrice) : 0,
        currentPrice: p.currentPrice,
      };
    });
  }

  // 3. Normalize and cap
  return positions.map((p, i) => {
    const rawWeight = invVols[i] / sumInvVol;
    const cappedWeight = Math.min(rawWeight, cfg.maxPositionPct / 100);
    const targetValue = portfolioValue * cappedWeight;
    const targetShares = p.currentPrice > 0 ? Math.floor(targetValue / p.currentPrice) : 0;
    return {
      symbol: p.symbol,
      annualVol: vols[i] !== null ? Number(vols[i].toFixed(4)) : null,
      rawWeight: Number(rawWeight.toFixed(4)),
      cappedWeight: Number(cappedWeight.toFixed(4)),
      targetValue: Number(targetValue.toFixed(2)),
      targetShares,
      currentPrice: p.currentPrice,
    };
  });
}

/**
 * Maximum shares that can be ordered for a single trade.
 * Enforces both position-size cap and volume-participation cap.
 *
 * @param {number} portfolioValue
 * @param {number} currentPrice
 * @param {number} avgDailyVolume — 20-day average volume for the stock
 * @returns {{
 *   maxShares: number,
 *   limitedBy: 'position_pct' | 'volume_pct',
 * }}
 */
export function maxOrderShares(portfolioValue, currentPrice, avgDailyVolume) {
  const cfg = riskLimits.position;
  if (currentPrice <= 0) return { maxShares: 0, limitedBy: 'position_pct' };

  const maxByValue = Math.floor((portfolioValue * (cfg.maxPositionPct / 100)) / currentPrice);
  const maxByVolume = Math.floor(avgDailyVolume * (cfg.maxVolumeParticipationPct / 100));

  if (maxByValue <= maxByVolume) {
    return { maxShares: maxByValue, limitedBy: 'position_pct' };
  }
  return { maxShares: maxByVolume, limitedBy: 'volume_pct' };
}
