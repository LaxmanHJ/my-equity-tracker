/**
 * TODO (2026-01-12): Replace Yahoo Finance API with paid APIs
 * - Yahoo Finance API is unreliable and has rate limiting issues
 * - Research and integrate paid stock data APIs (e.g., Alpha Vantage, Polygon.io, Twelve Data)
 * - Update all functions that use yahooFinance to use the new paid API
 */

import yahooFinance from 'yahoo-finance2';
import { getRapidApiChartData } from './rapidApiService.js';
import { getAlphaVantageChartData } from './alphaVantageService.js';
import { fetchDailyOHLC as getAngelOneDailyOHLC } from './angelOneHistorical.js';
import { getPriceHistory, getLatestPriceDate, savePriceHistory, upsertFiiDii, saveBulkDeals } from '../database/db.js';
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
    const history = await getHistoricalData(symbol, '1m', forceRefresh);

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

// Convert our period codes to a lookback window in days for Angel One.
const PERIOD_TO_DAYS = {
  '1m': 35, '3m': 100, '6m': 190, '1y': 380,
  '2y': 760, '5y': 1900, '10y': 3800, 'max': 7500,
};

function dateNDaysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function isoToday() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Normalize Angel One candle dates (ISO with +05:30) to YYYY-MM-DD strings
 * so downstream DB/dedup keys match what AlphaVantage/RapidAPI produce.
 */
function normalizeAngelCandles(rows) {
  return rows.map(r => ({
    date: typeof r.date === 'string' ? r.date.slice(0, 10) : r.date,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
    volume: r.volume,
  }));
}

export async function getHistoricalData(symbol, period = '1y', forceRefresh = false) {
  try {
    const cleanSymbol = symbol.replace('.NS', '').replace('.BO', '');

    // For DB storage/lookup, use the clean symbol as-is (e.g., '^NSEI')
    const dbSymbol = cleanSymbol;
    // For RapidAPI calls, map index symbols to their API-friendly names
    const apiSymbol = INDEX_SYMBOL_MAP[cleanSymbol] || cleanSymbol;

    // 1. Check database for existing data
    const localData = forceRefresh ? [] : await getPriceHistory(dbSymbol);
    const latestDateStr = forceRefresh ? null : await getLatestPriceDate(dbSymbol);

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

    // Fetch new data: Angel One (primary) → AlphaVantage → RapidAPI
    // Index symbols (^NSEI, ^BSESN) are equity-only on Angel; skip to existing chain.
    let newData = [];
    const isIndex = cleanSymbol.startsWith('^');

    if (!isIndex) {
      try {
        const lookbackDays = PERIOD_TO_DAYS[rapidApiPeriod] ?? PERIOD_TO_DAYS[period] ?? 35;
        const from = dateNDaysAgo(lookbackDays);
        const to = isoToday();
        const angelRows = await getAngelOneDailyOHLC(cleanSymbol, from, to);
        newData = normalizeAngelCandles(angelRows);
        console.log(`[AngelOne] ✅ ${dbSymbol}: ${newData.length} records (real OHLCV)`);
      } catch (angelErr) {
        console.warn(`[AngelOne] ❌ ${dbSymbol}: ${angelErr.message}`);
      }
    }

    if (!isIndex && newData.length === 0) {
      try {
        newData = await getAlphaVantageChartData(cleanSymbol);
        console.log(`[AlphaVantage] ✅ ${dbSymbol}: ${newData.length} records (fallback)`);
      } catch (avErr) {
        console.warn(`[AlphaVantage] ❌ ${dbSymbol}: ${avErr.message}`);
      }
    }

    // Fall back to RapidAPI if Alpha Vantage failed, returned no data, or is stale
    const avLatestDate = newData.length > 0 ? newData[newData.length - 1].date : null;
    const todayStr = new Date().toISOString().split('T')[0];
    const avIsStale = avLatestDate && (() => {
      const diffMs = new Date(todayStr) - new Date(avLatestDate);
      const diffDays = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
      return diffDays > 1;
    })();

    if (newData.length === 0 || avIsStale) {
      try {
        const rapidData = await getRapidApiChartData(apiSymbol, rapidApiPeriod);
        // RapidAPI returns close-only data; open/high/low are synthetic (flat bars)
        const flatBars = rapidData.filter(d => d.open === d.high && d.high === d.low && d.low === d.close).length;
        if (flatBars === rapidData.length && rapidData.length > 0) {
          console.warn(`[RapidAPI] ⚠️  ${dbSymbol}: ${rapidData.length} records — ALL FLAT BARS (synthetic OHLC)`);
        } else {
          console.log(`[RapidAPI] ✅ ${dbSymbol}: ${rapidData.length} records${isIndex ? '' : ' (fallback)'}`);
        }

        if (avIsStale && newData.length > 0 && rapidData.length > 0) {
          // Merge: keep AV's real OHLCV for dates it covers, add RapidAPI's newer bars
          const avDates = new Set(newData.map(d => d.date));
          const freshFromRapid = rapidData.filter(d => !avDates.has(d.date));
          console.log(`[Merge] ${dbSymbol}: ${newData.length} AV bars + ${freshFromRapid.length} new RapidAPI bars`);
          newData = [...newData, ...freshFromRapid];
        } else {
          newData = rapidData;
        }
      } catch (rapidErr) {
        console.error(`[RapidAPI] ❌ ${dbSymbol}: ${rapidErr.message}`);
        if (!avIsStale) newData = [];
        // If AV was stale but had data, keep it — better than nothing
      }
    }

    // Save to Database (use the original symbol so Python can find it)
    if (newData && newData.length > 0) {
      await savePriceHistory(dbSymbol, newData);
    }

    // Return all local data (combined old + fresh inserted)
    return await getPriceHistory(dbSymbol);

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

/**
 * Fetch today's FII/DII cash market net flows from NSE and persist to market_regime.
 * Called once per force-sync so fii_flow_score builds up over time.
 * Silently no-ops on failure — non-critical data.
 */
export async function fetchFiiDiiToday() {
  try {
    const resp = await fetch('https://www.nseindia.com/api/fiidiiTradeReact', {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Referer': 'https://www.nseindia.com/',
      }
    });
    if (!resp.ok) return;
    const rows = await resp.json();

    let fiiNet = null, diiNet = null, tradeDate = null;
    for (const row of rows) {
      const cat = (row.category || '').toUpperCase();
      const d = new Date(row.date);
      if (!isNaN(d)) tradeDate = d.toISOString().slice(0, 10);
      const net = parseFloat(String(row.netValue).replace(/,/g, ''));
      if (cat.includes('FII') || cat.includes('FPI')) fiiNet = net;
      else if (cat.includes('DII')) diiNet = net;
    }

    if (tradeDate && fiiNet !== null && diiNet !== null) {
      await upsertFiiDii(tradeDate, fiiNet, diiNet);
      console.log(`[FII/DII] ${tradeDate} — FII: ${fiiNet} cr, DII: ${diiNet} cr`);
    }
  } catch (err) {
    console.warn('[FII/DII] Daily fetch failed (non-critical):', err.message);
  }
}

/**
 * Fetch today's bulk and block deals from NSE archives and persist to DB.
 * Called on every force-sync. Silently no-ops on failure.
 */
export async function fetchBulkDealsToday() {
  const URLS = {
    BULK: 'https://nsearchives.nseindia.com/content/equities/bulk.csv',
    BLOCK: 'https://nsearchives.nseindia.com/content/equities/block.csv',
  };
  const HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
  };

  const parseDate = (val) => {
    // "27-MAR-2026" → "2026-03-27"
    const d = new Date(val);
    return isNaN(d) ? val : d.toISOString().slice(0, 10);
  };

  let total = 0;
  for (const [dealType, url] of Object.entries(URLS)) {
    try {
      const resp = await fetch(url, { headers: HEADERS });
      if (!resp.ok) continue;
      const text = await resp.text();
      const lines = text.trim().split('\n');
      if (lines.length < 2) continue;

      // Header: Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price
      const deals = [];
      for (const line of lines.slice(1)) {
        const cols = line.split(',');
        if (cols.length < 7) continue;
        const [date, symbol, , clientName, tradeType, quantity, price] = cols.map(c => c.trim());
        deals.push({
          date: parseDate(date),
          symbol: symbol.trim(),
          clientName: clientName.trim(),
          tradeType: tradeType.trim().toUpperCase(),
          quantity: parseInt(quantity.replace(/,/g, '')) || 0,
          price: parseFloat(price.replace(/,/g, '')) || 0,
          dealType,
        });
      }

      if (deals.length > 0) {
        await saveBulkDeals(deals);
        console.log(`[BulkDeals] ${dealType}: ${deals.length} deals saved`);
        total += deals.length;
      }
    } catch (err) {
      console.warn(`[BulkDeals] ${dealType} fetch failed (non-critical):`, err.message);
    }
  }
  return total;
}

// Export for testing
export { getQuote };
