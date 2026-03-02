/**
 * TODO (2026-01-12): Replace Yahoo Finance API with paid APIs
 * - Yahoo Finance API is unreliable and has rate limiting issues
 * - Research and integrate paid stock data APIs (e.g., Alpha Vantage, Polygon.io, Twelve Data)
 * - Update all functions that use yahooFinance to use the new paid API
 */

import yahooFinance from 'yahoo-finance2';
import { getRapidApiChartData } from './rapidApiService.js';
import { getPriceHistory, getLatestPriceDate, savePriceHistory } from '../database/db.js';
import { portfolio, getSymbols, benchmark } from '../config/portfolio.js';
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
  const batchSize = 3;  // Process 3 stocks at a time
  const batchDelayMs = 3000;  // 3 seconds between batches

  for (let i = 0; i < symbols.length; i += batchSize) {
    const batch = symbols.slice(i, i + batchSize);

    // Process batch sequentially
    for (const symbol of batch) {
      const quote = await getQuote(symbol, forceRefresh);
      if (quote) {
        // Attach to all portfolio items that match this symbol (e.g. TMCV and TMPV both track TIINDIA.NS)
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
      await delay(settings.api.requestDelayMs);
    }

    // Pause between batches (except after the last batch)
    if (i + batchSize < symbols.length) {
      console.log(`Batch ${Math.floor(i / batchSize) + 1} complete. Waiting ${batchDelayMs / 1000}s before next batch...`);
      await delay(batchDelayMs);
    }
  }

  return quotes;
}

/**
 * Get historical data for a symbol intelligently using local cache and RapidAPI
 */
export async function getHistoricalData(symbol, period = '1y', forceRefresh = false) {
  try {
    const cleanSymbol = symbol.replace('.NS', '').replace('.BO', '');

    // 1. Check database for existing data
    const localData = forceRefresh ? [] : getPriceHistory(cleanSymbol);
    const latestDateStr = forceRefresh ? null : getLatestPriceDate(cleanSymbol);

    // Determine what we need to fetch
    let rapidApiPeriod = period;

    if (latestDateStr && !forceRefresh) {
      // We have local data. Calculate how old it is.
      // latestDateStr is like '2026-02-27'. Date parsing can be tricky with timezones, so let's normalize.
      const latestDate = new Date(latestDateStr + 'T00:00:00Z');
      const today = new Date();
      const todayNormalized = new Date(today.toISOString().split('T')[0] + 'T00:00:00Z');

      const diffTime = Math.abs(todayNormalized - latestDate);
      const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

      // If data is less than or equal to 3 days old (allowing for long weekends), just return local
      if (diffDays <= 3 && localData.length > 0) {
        console.log(`[Cache Hit] Returning local historical data for ${cleanSymbol}`);
        return localData;
      } else {
        // We need to fetch the gap. 
        // RapidAPI lowest period is 1m (1 month). So we just fetch 1 month and update the DB.
        // This is safe because `savePriceHistory` uses INSERT OR REPLACE.
        console.log(`[Cache Update] Fetching recent data (1m) to fill gap for ${cleanSymbol}`);
        rapidApiPeriod = '1m';
      }
    } else {
      // First run or force refresh: Fetch based on requested period
      console.log(`[Cache Miss] Fetching fresh historical data (${period}) for ${cleanSymbol}`);
      if (period === '1m') rapidApiPeriod = '1m';
      if (period === '3m') rapidApiPeriod = '1m';
      if (period === '6m') rapidApiPeriod = '6m';
      if (period === '1y') rapidApiPeriod = '1yr';
      if (period === '2y') rapidApiPeriod = '3yr';
      if (period === '5y') rapidApiPeriod = '5yr';
    }

    // Fetch new data
    const newData = await getRapidApiChartData(cleanSymbol, rapidApiPeriod);

    // Save to Database
    if (newData && newData.length > 0) {
      savePriceHistory(cleanSymbol, newData);
    }

    // Return all local data (combined old + fresh inserted)
    return getPriceHistory(cleanSymbol);

  } catch (error) {
    console.error(`Error fetching historical data for ${symbol}:`, error.message);
    return [];
  }
}

/**
 * Get NIFTY 50 benchmark data for comparison
 */
export async function getBenchmarkData(period = '1y') {
  return getHistoricalData(benchmark.symbol, period);
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
