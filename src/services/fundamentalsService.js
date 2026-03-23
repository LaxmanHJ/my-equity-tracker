/**
 * Fundamentals Service — RapidAPI Indian Stock Exchange
 * Fetches company fundamentals, financials, peers, and key metrics.
 * Data is cached in SQLite to minimize expensive API calls.
 */

import {
  saveFundamentals,
  saveFundamentalsSync,
  savePeers,
  saveFinancials,
  saveNews,
  saveAnalystRatings,
  saveShareholding,
  getFundamentals,
  getAllFundamentals,
  getFundamentalsSyncDate,
  getSectorMomentum
} from '../database/db.js';
import { portfolio } from '../config/portfolio.js';
import dotenv from 'dotenv';
import { readFileSync } from 'fs';

dotenv.config();

const RAPIDAPI_KEYS = [
  process.env.RAPIDAPI_KEY,
  process.env.RAPIDAPI_KEY_2,
  process.env.RAPIDAPI_KEY_3,
  process.env.RAPIDAPI_KEY_4
].filter(Boolean);

let currentKeyIndex = 0;
const RAPIDAPI_HOST = 'indian-stock-exchange-api2.p.rapidapi.com';
const BASE_URL = `https://${RAPIDAPI_HOST}`;

const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// ═══════════════════════════════════════════════════════
// Helper: safely parse a numeric value, returning null for sentinel values
// ═══════════════════════════════════════════════════════
function safeNum(val) {
  if (val === null || val === undefined || val === '') return null;
  const n = parseFloat(val);
  if (isNaN(n)) return null;
  // RapidAPI uses -99999.99 as a sentinel for missing data
  if (n <= -99999) return null;
  return n;
}

// ═══════════════════════════════════════════════════════
// Helper: find a key metric value from an array of {key, value} objects
// ═══════════════════════════════════════════════════════
function findMetric(metricsArr, key) {
  if (!Array.isArray(metricsArr)) return null;
  const item = metricsArr.find(m => m.key === key);
  return item ? safeNum(item.value) : null;
}

// ═══════════════════════════════════════════════════════
// Helper: extract a financial line item value from a statement
// ═══════════════════════════════════════════════════════
function findFinancial(statementArr, key) {
  if (!Array.isArray(statementArr)) return null;
  const item = statementArr.find(f => f.key === key);
  return item ? safeNum(item.value) : null;
}

// ═══════════════════════════════════════════════════════
// Core: Fetch one stock from RapidAPI
// ═══════════════════════════════════════════════════════
export async function fetchStockFromAPI(stockName) {
  if (RAPIDAPI_KEYS.length === 0) {
    throw new Error('No RAPIDAPI_KEYS are set in .env');
  }

  const url = `${BASE_URL}/stock?name=${encodeURIComponent(stockName)}`;

  for (let attempt = 0; attempt < RAPIDAPI_KEYS.length; attempt++) {
    const currentKey = RAPIDAPI_KEYS[currentKeyIndex];
    
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        'x-rapidapi-host': RAPIDAPI_HOST,
        'x-rapidapi-key': currentKey
      }
    });

    if (response.ok) {
      return await response.json();
    }

    if (response.status === 429 || response.status === 401 || response.status === 403) {
      console.log(`[Fundamentals] Key at index ${currentKeyIndex} failed (${response.status}). Rotating to next key...`);
      currentKeyIndex = (currentKeyIndex + 1) % RAPIDAPI_KEYS.length;
      
      if (attempt === RAPIDAPI_KEYS.length - 1) {
        throw new Error('All RapidAPI keys have been exhausted or failed.');
      }
      
      await delay(1000); // Short delay before trying next key
      continue;
    }

    throw new Error(`RapidAPI error ${response.status}: ${response.statusText}`);
  }
}

// ═══════════════════════════════════════════════════════
// Extract: Parse API response into our normalized structure
// ═══════════════════════════════════════════════════════
export function extractFundamentals(apiData, symbol) {
  const km = apiData.keyMetrics || {};

  const fundamentals = {
    company_name: apiData.companyName || null,
    industry: apiData.industry || apiData.companyProfile?.mgIndustry || null,

    // Valuation — P/E and P/B come from keyMetrics.valuation, NOT from the peer list
    pe_ratio: findMetric(km.valuation, 'pPerEBasicExcludingExtraordinaryItemsTTM') ??
              findMetric(km.valuation, 'pPerEExcludingExtraordinaryItemsMostRecentFiscalYear'),
    pb_ratio: findMetric(km.valuation, 'priceToBookMostRecentFiscalYear') ??
              findMetric(km.valuation, 'priceToBookMostRecentQuarter'),
    eps_diluted: findMetric(km.persharedata, 'DilutedEPSExcludingExtraOrdItems') ??
                 findMetric(km.persharedata, 'ePSIncludingExtraordinaryItemsMostRecentFiscalYear'),
    dividend_yield: findMetric(km.valuation, 'currentDividendYieldCommonStockPrimaryIssueLTM') ??
                    findMetric(km.valuation, 'dividendYieldIndicatedAnnualDividendDividedByClosingprice'),
    price_to_sales: findMetric(km.valuation, 'priceToSalesMostRecentFiscalYear') ??
                    findMetric(km.valuation, 'priceToSalesTrailing12Month'),
    price_to_cash_flow: findMetric(km.valuation, 'priceToCashFlowPerShareTrailing12Month'),

    // Profitability / Margins
    roe_5y_avg: findMetric(km.mgmtEffectiveness, 'returnOnAverageEquity5YearAverage'),
    roe_ttm: findMetric(km.mgmtEffectiveness, 'returnOnAverageEquityTrailing12Month'),
    net_profit_margin_ttm: findMetric(km.margins, 'netProfitMarginPercentTrailing12Month'),
    net_profit_margin_5y_avg: findMetric(km.margins, 'netProfitMargin5YearAverage'),
    gross_margin_ttm: findMetric(km.margins, 'grossMarginTrailing12Month'),
    operating_margin_ttm: findMetric(km.margins, 'operatingMarginTrailing12Month'),

    // Growth
    revenue_growth_5y: findMetric(km.growth, 'revenueGrowthRate5Year'),
    eps_growth_5y: findMetric(km.growth, 'ePSGrowthRate5Year'),
    eps_growth_3y: findMetric(km.growth, 'growthRatePercentEPS3year'),
    revenue_growth_3y: findMetric(km.growth, 'growthRatePercentRevenue3Year'),

    // Financial Strength
    debt_to_equity: findMetric(km.financialstrength, 'totalDebtPerTotalEquityMostRecentFiscalYear'),
    current_ratio: findMetric(km.financialstrength, 'currentRatioMostRecentFiscalYear'),
    quick_ratio: findMetric(km.financialstrength, 'quickRatioMostRecentFiscalYear'),
    interest_coverage: findMetric(km.financialstrength, 'netInterestCoverageMostRecentFiscalYear'),
    free_cash_flow: findMetric(km.financialstrength, 'freeCashFlowMostRecentFiscalYear'),
    payout_ratio: findMetric(km.financialstrength, 'payoutRatioMostRecentFiscalYear'),

    // Price & Market — try multiple key variants
    market_cap: findMetric(km.priceandVolume, 'marketCapitalization') ??
                findMetric(km.priceandVolume, 'marketCap') ??
                findMetric(km.priceandVolume, 'MarketCapitalization'),
    year_high: safeNum(apiData.yearHigh),
    year_low: safeNum(apiData.yearLow),
    beta: findMetric(km.priceandVolume, 'beta'),
    book_value_per_share: findMetric(km.persharedata, 'bookValuePerShareMostRecentFiscalYear'),
    revenue_ttm: findMetric(km.incomeStatement, 'revenueTrailing12Month'),
    total_debt: null,
    total_equity: null
  };

  // Try to get total_debt and total_equity from the latest annual financials
  const annualFinancials = (apiData.financials || []).filter(f => f.Type === 'Annual');
  if (annualFinancials.length > 0) {
    const latestBAL = annualFinancials[0].stockFinancialMap?.BAL;
    if (latestBAL) {
      fundamentals.total_debt = findFinancial(latestBAL, 'TotalDebt');
      fundamentals.total_equity = findFinancial(latestBAL, 'TotalEquity');
    }
  }

  // --- Peers ---
  const peers = (apiData.companyProfile?.peerCompanyList || [])
    .filter(p => p.companyName !== apiData.companyName)
    .map(p => ({
      peer_name: p.companyName,
      peer_pe: safeNum(p.priceToEarningsValueRatio),
      peer_pb: safeNum(p.priceToBookValueRatio),
      peer_market_cap: safeNum(p.marketCap),
      peer_roe_ttm: safeNum(p.returnOnAverageEquityTrailing12Month),
      peer_npm_ttm: safeNum(p.netProfitMarginPercentTrailing12Month),
      peer_debt_to_equity: safeNum(p.ltDebtPerEquityMostRecentFiscalYear),
      peer_dividend_yield: safeNum(p.dividendYieldIndicatedAnnualDividend),
      peer_price: safeNum(p.price),
      peer_change_pct: safeNum(p.percentChange)
    }));

  // --- Financial Statements (annual + interim) ---
  const financials = (apiData.financials || []).map(f => {
    const INC = f.stockFinancialMap?.INC || [];
    const BAL = f.stockFinancialMap?.BAL || [];
    const CAS = f.stockFinancialMap?.CAS || [];

    const capex = findFinancial(CAS, 'CapitalExpenditures');
    const cashOps = findFinancial(CAS, 'CashfromOperatingActivities');
    const fcf = (capex !== null && cashOps !== null) ? cashOps + capex : null; // capex is already negative

    return {
      fiscal_year: f.FiscalYear,
      end_date: f.EndDate,
      statement_type: f.Type,
      revenue: findFinancial(INC, 'TotalRevenue') || findFinancial(INC, 'Revenue'),
      gross_profit: findFinancial(INC, 'GrossProfit'),
      operating_income: findFinancial(INC, 'OperatingIncome'),
      net_income: findFinancial(INC, 'NetIncome'),
      eps_diluted: findFinancial(INC, 'DilutedEPSExcludingExtraOrdItems'),
      total_assets: findFinancial(BAL, 'TotalAssets'),
      total_liabilities: findFinancial(BAL, 'TotalLiabilities'),
      total_equity: findFinancial(BAL, 'TotalEquity'),
      total_debt: findFinancial(BAL, 'TotalDebt'),
      cash_from_operations: cashOps,
      capex: capex,
      free_cash_flow: fcf
    };
  });

  return { fundamentals, peers, financials };
}

// ═══════════════════════════════════════════════════════
// Extractors for New Data Points
// ═══════════════════════════════════════════════════════

export function extractNews(apiData) {
  return (apiData.recentNews || []).map(n => ({
    headline: n.headline,
    news_date: n.date,
    url: n.url,
    source: n.source || null,
    thumbnail_url: n.thumbnailImage || n.listimage || null
  }));
}

export function extractAnalystRatings(apiData) {
  const av = apiData.analystView || [];
  const rb = apiData.recosBar || {};
  const rm = apiData.riskMeter || {};

  // Find counts from analystView (1=Strong Buy, 2=Buy, 3=Hold, 4=Sell, 5=Strong Sell)
  const strongBuy = safeNum(av.find(a => a.ratingValue === 1)?.numberOfAnalystsLatest) || 0;
  const buy = safeNum(av.find(a => a.ratingValue === 2)?.numberOfAnalystsLatest) || 0;
  const hold = safeNum(av.find(a => a.ratingValue === 3)?.numberOfAnalystsLatest) || 0;
  const sell = safeNum(av.find(a => a.ratingValue === 4)?.numberOfAnalystsLatest) || 0;
  const strongSell = safeNum(av.find(a => a.ratingValue === 5)?.numberOfAnalystsLatest) || 0;

  // Use the total from the API or compute it
  const total = safeNum(av.find(a => a.ratingValue === 6)?.numberOfAnalystsLatest) || 
               (strongBuy + buy + hold + sell + strongSell);

  return {
    strong_buy: strongBuy,
    buy: buy,
    hold: hold,
    sell: sell,
    strong_sell: strongSell,
    total_analysts: total,
    mean_rating: safeNum(rb.meanValue),
    risk_category: rm.categoryName || null,
    risk_std_dev: safeNum(rm.stdDev)
  };
}

export function extractShareholding(apiData) {
  const result = [];
  const sh = apiData.shareholding || [];
  
  for (const category of sh) {
    const catName = category.displayName || category.categoryName || 'Unknown';
    if (category.categories && Array.isArray(category.categories)) {
      for (const quarter of category.categories) {
        result.push({
          category: catName,
          holding_date: quarter.holdingDate,
          percentage: safeNum(quarter.percentage)
        });
      }
    }
  }
  return result;
}

// ═══════════════════════════════════════════════════════
// Sync: Fetch + extract + save for one stock
// ═══════════════════════════════════════════════════════
export async function syncStockFundamentals(symbol) {
  try {
    // Use ticker symbol directly — more reliable than company display name
    console.log(`[Fundamentals] Fetching ${symbol}...`);
    let apiData;
    
    // For local testing with Mock JSON
    if (process.env.USE_MOCK_FUNDAMENTALS === 'true') {
      console.log(`[Fundamentals] Using Mock JSON for ${symbol}`);
      apiData = JSON.parse(readFileSync('FundamentalsMock.json', 'utf8'));
    } else {
      apiData = await fetchStockFromAPI(symbol);
    }

    if (!apiData.companyName) {
      throw new Error(`Stock not found in API with ticker "${symbol}"`);
    }

    const { fundamentals, peers, financials } = extractFundamentals(apiData, symbol);
    
    // New extractors
    const news = extractNews(apiData);
    const analystRatings = extractAnalystRatings(apiData);
    const shareholding = extractShareholding(apiData);

    saveFundamentals(symbol, fundamentals);
    savePeers(symbol, peers);
    saveFinancials(symbol, financials);
    
    // Save new data
    if (news.length > 0) saveNews(symbol, news);
    saveAnalystRatings(symbol, analystRatings);
    if (shareholding.length > 0) saveShareholding(symbol, shareholding);

    saveFundamentalsSync(symbol, 'success', null);

    console.log(`[Fundamentals] ✅ ${symbol}: PE=${fundamentals.pe_ratio}, MktCap=${fundamentals.market_cap}, News=${news.length}, Analysts=${analystRatings.total_analysts}`);
    return { symbol, status: 'success', data: fundamentals };
  } catch (error) {
    console.error(`[Fundamentals] ❌ ${symbol}: ${error.message}`);
    saveFundamentalsSync(symbol, 'error', error.message);
    return { symbol, status: 'error', error: error.message };
  }
}

// ═══════════════════════════════════════════════════════
// Sync All: Loop through portfolio with rate limiting
// ═══════════════════════════════════════════════════════
export async function syncAllFundamentals(progressCallback) {
  const results = [];
  // Use displaySymbol (ticker) directly for API calls
  const symbols = [...new Set(portfolio.map(s => s.displaySymbol))];

  for (let i = 0; i < symbols.length; i++) {
    const symbol = symbols[i];
    const result = await syncStockFundamentals(symbol);
    results.push(result);

    // Early bail-out: if all keys are exhausted
    if (result.status === 'error' && result.error?.includes('All RapidAPI keys')) {
      console.log(`[Fundamentals] ⛔ All keys exhausted — skipping remaining ${symbols.length - i - 1} stocks`);
      for (let j = i + 1; j < symbols.length; j++) {
        results.push({ symbol: symbols[j], status: 'skipped', error: 'Skipped because all API keys are exhausted' });
      }
      break;
    }

    if (progressCallback) {
      progressCallback({ current: i + 1, total: symbols.length, symbol, status: result.status });
    }

    // Rate limit: 2 seconds between API calls to avoid 429 errors
    if (i < symbols.length - 1) {
      await delay(2000);
    }
  }

  const successCount = results.filter(r => r.status === 'success').length;
  const errorCount = results.filter(r => r.status === 'error').length;
  console.log(`[Fundamentals] Sync complete: ${successCount} success, ${errorCount} errors`);

  return { results, summary: { total: symbols.length, success: successCount, errors: errorCount } };
}

// Re-export DB read functions for convenience
export { getFundamentals, getAllFundamentals, getFundamentalsSyncDate, getSectorMomentum };
