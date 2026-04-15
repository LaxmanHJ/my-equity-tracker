/**
 * Price Provider — EOD mode (Turso cache)
 *
 * Returns `{currentPrice, prevClose, bars}` for a symbol using the
 * price_history table as the source of truth.
 *
 * This is the mode used on localhost where no intraday polling runs.
 * Chunk 3 will add an Angel One LTP-backed provider with the same
 * signature so the risk manager can swap between them without code
 * changes.
 */

import { getPriceHistory } from '../database/db.js';

/**
 * Build an EOD price provider bound to a lookback window.
 *
 * @param {number} lookbackBars — number of recent bars to return (default 60)
 * @returns {(symbol: string) => Promise<{currentPrice, prevClose, bars}>}
 */
export function createEodPriceProvider(lookbackBars = 60) {
  return async function eodPriceProvider(symbol) {
    const rows = await getPriceHistory(symbol);
    if (!rows || rows.length === 0) {
      return { currentPrice: null, prevClose: null, bars: [] };
    }
    const bars = rows.slice(-lookbackBars).map(r => ({
      date: r.date,
      open: Number(r.open),
      high: Number(r.high),
      low: Number(r.low),
      close: Number(r.close),
      volume: Number(r.volume) || 0,
    }));
    const last = bars[bars.length - 1];
    const prev = bars.length > 1 ? bars[bars.length - 2] : last;
    return {
      currentPrice: last.close,
      prevClose: prev.close,
      bars,
    };
  };
}
