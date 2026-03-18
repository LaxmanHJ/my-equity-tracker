/**
 * TODO (2026-01-12): Replace Yahoo Finance API with paid APIs
 * - Yahoo Finance API is unreliable and has rate limiting issues
 * - Research and integrate paid stock data APIs (e.g., Alpha Vantage, Polygon.io, Twelve Data)
 * - Update all functions that use yahooFinance to use the new paid API
 */

import yahooFinance from 'yahoo-finance2';
import { getRapidApiChartData } from './rapidApiService.js';
import { getAlphaVantageChartData } from './alphaVantageService.js';
import { getPriceHistory, getLatestPriceDate, savePriceHistory } from '../database/db.js';
import { portfolio, getSymbols, benchmark, indexes } from '../config/portfolio.js';
import { settings } from '../config/settings.js';

/**
 * Delay helper for rate limiting
 */
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Get quote for a single symbol by analyzing the cached historical data
 */
async function getQuote(symbol, forceRefresh = false) {
  try {
    // This will hit the intelligent local SQLite cache, making it extremely fast
    // and avoiding 15x real-time API calls to rate-limited services.
    const history = await getHistoricalData(symbol, '1y', forceRefresh);

    if (!history || history.length < 2) {
      console.warn(`[getQuote] Not enough historical data to generate quote for ${symbol}`);
      return null;
    }

    // history from DB is ordered ASC. Last element is the most recent day.
    const current = history[history.length - 1];
    const previous = history[history.length - 2];

    const change = current.close - previous.close;
    const changePercent = (change / previous.close) * 100;

    let fiftyTwoWeekHigh = current.high;
    let fiftyTwoWeekLow = current.low;

    // Calculate 52-week high/low from the history array
    for (const day of history) {
      if (day.high > fiftyTwoWeekHigh) fiftyTwoWeekHigh = day.high;
      if (day.low < fiftyTwoWeekLow) fiftyTwoWeekLow = day.low;
    }

    return {
      symbol: symbol,
      price: current.close,
      previousClose: previous.close,
      change: change,
      changePercent: changePercent,
      dayHigh: current.high,
      dayLow: current.low,
      volume: current.volume,
      marketCap: 0,
      fiftyTwoWeekHigh: fiftyTwoWeekHigh,
      fiftyTwoWeekLow: fiftyTwoWeekLow,
      timestamp: current.date
    };

  } catch (error) {
    console.error(`[getQuote] Error calculating quote from history for ${symbol}:`, error.message);
    return null;
  }
}

/**
 * Get quotes for all portfolio stocks with batching to avoid rate limits
 */
export async function getAllQuotes(forceRefresh = false) {
  const symbols = getSymbols();
  const quotes = [];

  // When reading from local cache (normal load), no delays are needed.
  // When force-refreshing (hitting the network API), use batching with delays.
  const batchSize = forceRefresh ? 3 : symbols.length;
  const batchDelayMs = forceRefresh ? 3000 : 0;
  const perStockDelayMs = forceRefresh ? settings.api.requestDelayMs : 0;

  for (let i = 0; i < symbols.length; i += batchSize) {
    const batch = symbols.slice(i, i + batchSize);

    for (const symbol of batch) {
      const quote = await getQuote(symbol, forceRefresh);
      if (quote) {
        const matchingItems = portfolio.filter(s => s.symbol === symbol);

        for (const stockInfo of matchingItems) {
          quotes.push({
            ...quote,
            displaySymbol: stockInfo.displaySymbol,
            name: stockInfo.name,
            sector: stockInfo.sector,
            quantity: stockInfo.quantity || 0,
            avgPrice: stockInfo.avgPrice || 0
          });
        }
      }
      if (perStockDelayMs > 0) await delay(perStockDelayMs);
    }

    // Pause between batches only during force refresh
    if (batchDelayMs > 0 && i + batchSize < symbols.length) {
      console.log(`Batch ${Math.floor(i / batchSize) + 1} complete. Waiting ${batchDelayMs / 1000}s before next batch...`);
      await delay(batchDelayMs);
    }
  }

  // Also sync NIFTY 50 and SENSEX benchmark data
  try {
    console.log('[Benchmark Sync] Ensuring index data (NIFTY 50, SENSEX) is synced...');
    await fetchIndexData('1y', forceRefresh);
  } catch (err) {
    console.warn('[Benchmark Sync] Failed to sync index data:', err.message);
  }

  return quotes;
}

/**
 * Get historical data for a symbol intelligently using local cache and RapidAPI
 */
// Map index/benchmark symbols to their RapidAPI-compatible names.
// RapidAPI uses human-readable names for indices (e.g., 'NIFTY 50'), not ticker symbols.
const INDEX_SYMBOL_MAP = {
  '^NSEI': 'NIFTY',
  'NSEI': 'NIFTY',
  '^NSEBANK': 'NIFTY BANK',
  '^BSESN': 'BSE SENSEX',
  'BSESN': 'BSE SENSEX',
};

export async function getHistoricalData(symbol, period = '1y', forceRefresh = false) {
  try {
    const cleanSymbol = symbol.replace('.NS', '').replace('.BO', '');

    // For DB storage/lookup, use the clean symbol as-is (e.g., '^NSEI')
    const dbSymbol = cleanSymbol;
    // For RapidAPI calls, map index symbols to their API-friendly names
    const apiSymbol = INDEX_SYMBOL_MAP[cleanSymbol] || cleanSymbol;

    // 1. Check database for existing data
    const localData = forceRefresh ? [] : getPriceHistory(dbSymbol);
    const latestDateStr = forceRefresh ? null : getLatestPriceDate(dbSymbol);

    // Determine what we need to fetch
    let rapidApiPeriod = period;

    if (latestDateStr && !forceRefresh) {
      const latestDate = new Date(latestDateStr + 'T00:00:00Z');
      const today = new Date();
      const todayNormalized = new Date(today.toISOString().split('T')[0] + 'T00:00:00Z');

      const diffTime = Math.abs(todayNormalized - latestDate);
      const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

      if (diffDays <= 3 && localData.length > 0) {
        console.log(`[Cache Hit] Returning local historical data for ${dbSymbol}`);
        return localData;
      } else {
        console.log(`[Cache Update] Fetching recent data (1m) to fill gap for ${dbSymbol}`);
        rapidApiPeriod = '1m';
      }
    } else {
      console.log(`[Cache Miss] Fetching fresh historical data (${period}) for ${dbSymbol}`);
      if (period === '1m') rapidApiPeriod = '1m';
      if (period === '3m') rapidApiPeriod = '1m';
      if (period === '6m') rapidApiPeriod = '6m';
      if (period === '1y') rapidApiPeriod = '1yr';
      if (period === '2y') rapidApiPeriod = '3yr';
      if (period === '5y') rapidApiPeriod = '5yr';
      if (period === '10y') rapidApiPeriod = '10yr';
    }

    // Fetch new data: try RapidAPI first, fall back to Alpha Vantage
    let newData = [];
    try {
      newData = await getRapidApiChartData(apiSymbol, rapidApiPeriod);
      console.log(`[RapidAPI] ✅ ${dbSymbol}: ${newData.length} records`);
    } catch (rapidErr) {
      console.warn(`[RapidAPI] ❌ ${dbSymbol}: ${rapidErr.message}`);
      try {
        newData = await getAlphaVantageChartData(cleanSymbol);
        console.log(`[AlphaVantage] ✅ ${dbSymbol}: ${newData.length} records`);
      } catch (avErr) {
        console.error(`[AlphaVantage] ❌ ${dbSymbol}: ${avErr.message}`);
        newData = [];
      }
    }

    // Save to Database (use the original symbol so Python can find it)
    if (newData && newData.length > 0) {
      savePriceHistory(dbSymbol, newData);
    }

    // Return all local data (combined old + fresh inserted)
    return getPriceHistory(dbSymbol);

  } catch (error) {
    console.error(`Error fetching historical data for ${symbol}:`, error.message);
    return [];
  }
}

/**
 * Get NIFTY 50 benchmark data for comparison
 */
export async function getBenchmarkData(period = '1y', forceRefresh = false) {
  return getHistoricalData(benchmark.symbol, period, forceRefresh);
}

/**
 * Fetch historical data for all market indexes (NIFTY + SENSEX)
 * Used by the Markov Chain and Mean Reversion strategies
 */
export async function fetchIndexData(period = '1y', forceRefresh = false) {
  const results = {};
  for (const idx of indexes) {
    try {
      console.log(`[IndexSync] Fetching ${idx.name} (${idx.symbol})...`);
      const data = await getHistoricalData(idx.symbol, period, forceRefresh);
      results[idx.symbol] = { name: idx.name, dataPoints: data.length };
      console.log(`[IndexSync] ✅ ${idx.name}: ${data.length} records`);
    } catch (err) {
      console.error(`[IndexSync] ❌ ${idx.name}: ${err.message}`);
      results[idx.symbol] = { name: idx.name, dataPoints: 0, error: err.message };
    }
  }
  return results;
}

/**
 * Get portfolio summary with current values
 */
export async function getPortfolioSummary(forceRefresh = false) {
  const quotes = await getAllQuotes(forceRefresh);

  let totalInvested = 0;
  let currentValue = 0;

  const holdings = quotes.map(quote => {
    const invested = quote.quantity * quote.avgPrice;
    const current = quote.quantity * quote.price;
    const profitLoss = current - invested;
    const profitLossPercent = invested > 0 ? (profitLoss / invested) * 100 : 0;

    totalInvested += invested;
    currentValue += current;

    return {
      ...quote,
      invested,
      currentValue: current,
      profitLoss,
      profitLossPercent
    };
  });

  return {
    holdings,
    summary: {
      totalInvested,
      currentValue,
      totalProfitLoss: currentValue - totalInvested,
      totalProfitLossPercent: totalInvested > 0
        ? ((currentValue - totalInvested) / totalInvested) * 100
        : 0,
      stockCount: holdings.length,
      lastUpdated: new Date().toISOString()
    }
  };
}

// Export for testing
export { getQuote };
