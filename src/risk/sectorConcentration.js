/**
 * Sector Concentration Check
 *
 * Flags any sector whose total market value exceeds
 * `maxSectorConcentrationPct` of total portfolio value.
 *
 * Sector metadata comes from src/config/portfolio.js.
 */

import { riskLimits } from '../config/riskLimits.js';

/**
 * @param {Array<{symbol, quantity, currentPrice, sector}>} positions
 * @returns {{
 *   breaches: Array<{sector: string, valuePct: number, thresholdPct: number, symbols: string[]}>,
 *   exposures: Array<{sector: string, valuePct: number, value: number}>,
 *   totalValue: number,
 * }}
 */
export function checkSectorConcentration(positions) {
  const cfg = riskLimits.portfolio;
  const sectorValue = new Map();
  const sectorSymbols = new Map();
  let totalValue = 0;

  for (const p of positions) {
    const qty = p.quantity || 0;
    const price = Number(p.currentPrice) || 0;
    const value = qty * price;
    const sector = p.sector || 'Unknown';

    totalValue += value;
    sectorValue.set(sector, (sectorValue.get(sector) || 0) + value);
    if (!sectorSymbols.has(sector)) sectorSymbols.set(sector, []);
    sectorSymbols.get(sector).push(p.symbol);
  }

  const exposures = [];
  const breaches = [];

  for (const [sector, value] of sectorValue.entries()) {
    const pct = totalValue > 0 ? (value / totalValue) * 100 : 0;
    exposures.push({
      sector,
      value: Number(value.toFixed(2)),
      valuePct: Number(pct.toFixed(2)),
    });
    if (pct > cfg.maxSectorConcentrationPct) {
      breaches.push({
        sector,
        valuePct: Number(pct.toFixed(2)),
        thresholdPct: cfg.maxSectorConcentrationPct,
        symbols: sectorSymbols.get(sector),
      });
    }
  }

  exposures.sort((a, b) => b.valuePct - a.valuePct);

  return {
    breaches,
    exposures,
    totalValue: Number(totalValue.toFixed(2)),
  };
}
