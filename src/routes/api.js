import express from 'express';
import { portfolio, getStocksBySector } from '../config/portfolio.js';
import {
  getAllQuotes,
  getHistoricalData,
  getPortfolioSummary,
  getQuote,
  getBenchmarkData,
  fetchIndexData,
  fetchFiiDiiToday
} from '../services/stockData.js';
import { getFullAnalysis, generateSignals } from '../analysis/technicals.js';
import {
  buildCorrelationMatrix,
  findHighCorrelations,
  analyzeDiversification
} from '../analysis/correlation.js';
import { getFullRiskAnalysis } from '../analysis/risk.js';
import {
  syncAllFundamentals,
  getFundamentals,
  getAllFundamentals,
  getFundamentalsSyncDate
} from '../services/fundamentalsService.js';
import {
  createAlert,
  getActiveAlerts,
  triggerAlert,
  saveDailyReport,
  getDailyReport,
  getNews,
  getAnalystRatings,
  getShareholding,
  getSectorMomentum
} from '../database/db.js';

const router = express.Router();

// ============================================
// Portfolio Endpoints
// ============================================

/**
 * GET /api/portfolio
 * Get portfolio summary with current prices
 */
router.get('/portfolio', async (req, res) => {
  try {
    const forceRefresh = req.query.force === 'true';
    const summary = await getPortfolioSummary(forceRefresh);
    res.json(summary);
  } catch (error) {
    console.error('Portfolio error:', error);
    res.status(500).json({ error: 'Failed to fetch portfolio data' });
  }
});

/**
 * GET /api/portfolio/sectors
 * Get stocks grouped by sector
 */
router.get('/portfolio/sectors', (req, res) => {
  try {
    const sectors = getStocksBySector();
    res.json(sectors);
  } catch (error) {
    res.status(500).json({ error: 'Failed to get sector data' });
  }
});

// ============================================
// Stock Endpoints
// ============================================

/**
 * GET /api/stock/:symbol
 * Get detailed info for a single stock
 */
router.get('/stock/:symbol', async (req, res) => {
  try {
    const { symbol } = req.params;
    const stock = portfolio.find(
      s => s.displaySymbol.toLowerCase() === symbol.toLowerCase() ||
        s.symbol.toLowerCase() === symbol.toLowerCase()
    );

    if (!stock) {
      return res.status(404).json({ error: 'Stock not found in portfolio' });
    }

    const quote = await getQuote(stock.symbol);
    const historical = await getHistoricalData(stock.symbol, '1y');

    res.json({
      ...stock,
      quote,
      historical: historical.slice(-30) // Last 30 days
    });
  } catch (error) {
    console.error('Stock error:', error);
    res.status(500).json({ error: 'Failed to fetch stock data' });
  }
});

/**
 * GET /api/stock/:symbol/history
 * Get historical data for a stock
 */
router.get('/stock/:symbol/history', async (req, res) => {
  try {
    const { symbol } = req.params;
    const { period = '1y' } = req.query;

    const stock = portfolio.find(
      s => s.displaySymbol.toLowerCase() === symbol.toLowerCase() ||
        s.symbol.toLowerCase() === symbol.toLowerCase()
    );

    if (!stock) {
      return res.status(404).json({ error: 'Stock not found in portfolio' });
    }

    const historical = await getHistoricalData(stock.symbol, period);
    res.json({ symbol: stock.displaySymbol, period, data: historical });
  } catch (error) {
    res.status(500).json({ error: 'Failed to fetch historical data' });
  }
});

// ============================================
// Analysis Endpoints
// ============================================

/**
 * GET /api/analysis/technicals/:symbol
 * Get technical analysis for a stock
 */
router.get('/analysis/technicals/:symbol', async (req, res) => {
  try {
    const { symbol } = req.params;

    const stock = portfolio.find(
      s => s.displaySymbol.toLowerCase() === symbol.toLowerCase() ||
        s.symbol.toLowerCase() === symbol.toLowerCase()
    );

    if (!stock) {
      return res.status(404).json({ error: 'Stock not found in portfolio' });
    }

    const historical = await getHistoricalData(stock.symbol, '1y');

    if (historical.length === 0) {
      return res.status(404).json({ error: 'No historical data available' });
    }

    const analysis = getFullAnalysis(historical);

    res.json({
      symbol: stock.displaySymbol,
      name: stock.name,
      analysis,
      dataPoints: historical.length
    });
  } catch (error) {
    console.error('Analysis error:', error);
    res.status(500).json({ error: 'Failed to perform technical analysis' });
  }
});

/**
 * GET /api/analysis/signals
 * Get trading signals for all portfolio stocks
 */
router.get('/analysis/signals', async (req, res) => {
  try {
    const results = await Promise.all(
      portfolio.map(async (stock) => {
        const historical = await getHistoricalData(stock.symbol, '3m');
        if (historical.length > 0) {
          const prices = historical.map(d => d.close);
          const dates = historical.map(d => d.date);
          const stockSignals = generateSignals(prices, dates);
          return {
            symbol: stock.displaySymbol,
            name: stock.name,
            sector: stock.sector,
            ...stockSignals
          };
        }
        return null;
      })
    );
    const signals = results.filter(Boolean);

    res.json({ signals, generatedAt: new Date().toISOString() });
  } catch (error) {
    res.status(500).json({ error: 'Failed to generate signals' });
  }
});

/**
 * GET /api/analysis/correlation
 * Get correlation matrix for portfolio
 */
router.get('/analysis/correlation', async (req, res) => {
  try {
    const { days = 90 } = req.query;
    const matrix = await buildCorrelationMatrix(parseInt(days));
    res.json(matrix);
  } catch (error) {
    console.error('Correlation error:', error);
    res.status(500).json({ error: 'Failed to calculate correlations' });
  }
});

/**
 * GET /api/analysis/high-correlations
 * Get highly correlated stock pairs
 */
router.get('/analysis/high-correlations', async (req, res) => {
  try {
    const { threshold = 0.7, days = 90 } = req.query;
    const result = await findHighCorrelations(parseFloat(threshold), parseInt(days));
    res.json(result);
  } catch (error) {
    res.status(500).json({ error: 'Failed to find correlations' });
  }
});

/**
 * GET /api/analysis/diversification
 * Analyze portfolio diversification
 */
router.get('/analysis/diversification', async (req, res) => {
  try {
    const { days = 90 } = req.query;
    const analysis = await analyzeDiversification(parseInt(days));
    res.json(analysis);
  } catch (error) {
    res.status(500).json({ error: 'Failed to analyze diversification' });
  }
});

/**
 * GET /api/analysis/risk/:symbol
 * Get risk metrics for a stock
 */
router.get('/analysis/risk/:symbol', async (req, res) => {
  try {
    const { symbol } = req.params;

    const stock = portfolio.find(
      s => s.displaySymbol.toLowerCase() === symbol.toLowerCase() ||
        s.symbol.toLowerCase() === symbol.toLowerCase()
    );

    if (!stock) {
      return res.status(404).json({ error: 'Stock not found in portfolio' });
    }

    const riskAnalysis = await getFullRiskAnalysis(stock.symbol);

    res.json({
      symbol: stock.displaySymbol,
      name: stock.name,
      risk: riskAnalysis
    });
  } catch (error) {
    console.error('Risk analysis error:', error);
    res.status(500).json({ error: 'Failed to perform risk analysis' });
  }
});

// ============================================
// Alerts Endpoints
// ============================================

/**
 * GET /api/alerts
 * Get all active alerts
 */
router.get('/alerts', async (req, res) => {
  try {
    const alerts = await getActiveAlerts();
    res.json({ alerts });
  } catch (error) {
    res.status(500).json({ error: 'Failed to fetch alerts' });
  }
});

/**
 * POST /api/alerts
 * Create a new price alert
 */
router.post('/alerts', async (req, res) => {
  try {
    const { symbol, type, threshold, direction } = req.body;

    if (!symbol || !threshold || !direction) {
      return res.status(400).json({ error: 'Missing required fields' });
    }

    const stock = portfolio.find(
      s => s.displaySymbol.toLowerCase() === symbol.toLowerCase()
    );

    if (!stock) {
      return res.status(404).json({ error: 'Stock not found in portfolio' });
    }

    const alertId = await createAlert(stock.symbol, type || 'price', threshold, direction);

    res.json({
      success: true,
      alertId,
      message: `Alert created: ${symbol} ${direction} ₹${threshold}`
    });
  } catch (error) {
    res.status(500).json({ error: 'Failed to create alert' });
  }
});

/**
 * DELETE /api/alerts/:id
 * Delete an alert
 */
router.delete('/alerts/:id', async (req, res) => {
  try {
    await triggerAlert(parseInt(req.params.id));
    res.json({ success: true });
  } catch (error) {
    res.status(500).json({ error: 'Failed to delete alert' });
  }
});

// ============================================
// Reports Endpoints
// ============================================

/**
 * GET /api/reports/daily
 * Generate or retrieve daily report
 */
router.get('/reports/daily', async (req, res) => {
  try {
    const today = new Date().toISOString().split('T')[0];

    // Check if report already exists
    let report = await getDailyReport(today);

    if (!report) {
      // Generate new report
      const summary = await getPortfolioSummary();
      const diversification = await analyzeDiversification(90);

      // Collect signals for all stocks
      const analysisResults = await Promise.all(
        portfolio.map(async (stock) => {
          const historical = await getHistoricalData(stock.symbol, '3m');
          if (historical.length > 0) {
            const prices = historical.map(d => d.close);
            const signals = generateSignals(prices, historical.map(d => d.date));
            return {
              symbol: stock.displaySymbol,
              name: stock.name,
              signals: signals.signals
            };
          }
          return null;
        })
      );
      const stockAnalysis = analysisResults.filter(Boolean);

      const reportData = {
        date: today,
        portfolio: summary,
        diversification,
        stockAnalysis,
        marketStatus: isMarketOpen() ? 'OPEN' : 'CLOSED',
        generatedAt: new Date().toISOString()
      };

      await saveDailyReport(today, reportData);
      report = { report_data: reportData };
    }

    res.json(report.report_data);
  } catch (error) {
    console.error('Report error:', error);
    res.status(500).json({ error: 'Failed to generate report' });
  }
});

/**
 * Check if Indian market is currently open
 */
function isMarketOpen() {
  const now = new Date();
  const istOffset = 5.5 * 60 * 60 * 1000;
  const istTime = new Date(now.getTime() + istOffset);

  const day = istTime.getUTCDay();
  const hours = istTime.getUTCHours();
  const minutes = istTime.getUTCMinutes();
  const time = hours * 60 + minutes;

  // Market hours: 9:15 AM to 3:30 PM IST, Mon-Fri
  const marketOpen = 9 * 60 + 15;
  const marketClose = 15 * 60 + 30;

  return day >= 1 && day <= 5 && time >= marketOpen && time <= marketClose;
}

// ============================================
// Sync Endpoints
// ============================================

/**
 * POST /api/portfolio/sync
 * Force-refresh ALL portfolio + index data into SQLite (single source of truth).
 * After this completes, both the Node.js portfolio endpoints and the Python
 * quant engine will read today's prices from the same up-to-date DB.
 */
router.post('/portfolio/sync', async (req, res) => {
  try {
    console.log('[ForceSync] Starting full portfolio + index data refresh into SQLite...');
    const quotes = await getAllQuotes(true); // fetches from RapidAPI/AlphaVantage → writes to SQLite
    console.log(`[ForceSync] ✅ Synced ${quotes.length} holdings to SQLite`);

    // Fetch today's FII/DII cash flows — builds fii_flow_score history over time
    await fetchFiiDiiToday();

    res.json({
      success: true,
      synced: quotes.length,
      message: `Refreshed ${quotes.length} holdings in database`,
      timestamp: new Date().toISOString()
    });
  } catch (error) {
    console.error('[ForceSync] Error:', error);
    res.status(500).json({ success: false, error: 'Failed to sync portfolio data' });
  }
});

/**
 * POST /api/sync/benchmark
 * Manually trigger NIFTY 50 benchmark data sync
 */
router.post('/sync/benchmark', async (req, res) => {
  try {
    const forceRefresh = req.query.force === 'true';
    console.log(`[API] Syncing NIFTY 50 benchmark data (force=${forceRefresh})...`);
    const data = await getBenchmarkData('1y', forceRefresh);
    res.json({
      success: true,
      message: `NIFTY 50 benchmark data synced successfully`,
      dataPoints: data.length
    });
  } catch (error) {
    console.error('Benchmark sync error:', error);
    res.status(500).json({ error: 'Failed to sync benchmark data' });
  }
});

// ============================================
// Quant Engine Proxy (Python FastAPI on :5001)
// ============================================

const QUANT_ENGINE_URL = 'http://localhost:5001';

// ============================================
// The Sicilian — Unified Decision Engine Proxy
// ============================================

router.get('/sicilian/:symbol', async (req, res) => {
  try {
    const response = await fetch(`${QUANT_ENGINE_URL}/api/sicilian/${req.params.symbol}`);
    const data = await response.json();
    res.json(data);
  } catch (error) {
    console.error('Sicilian engine error:', error);
    res.status(502).json({ error: 'Sicilian engine unavailable. Is the Python server running on port 5001?' });
  }
});

router.get('/sicilian', async (req, res) => {
  try {
    const response = await fetch(`${QUANT_ENGINE_URL}/api/sicilian`);
    const data = await response.json();
    res.json(data);
  } catch (error) {
    console.error('Sicilian engine error:', error);
    res.status(502).json({ error: 'Sicilian engine unavailable.' });
  }
});

router.get('/quant/scores', async (req, res) => {
  try {
    const response = await fetch(`${QUANT_ENGINE_URL}/api/scores`);
    const data = await response.json();
    res.json(data);
  } catch (error) {
    console.error('Quant engine error:', error);
    res.status(502).json({ error: 'Quant engine unavailable. Is the Python server running on port 5001?' });
  }
});

router.post('/quant/backtest', async (req, res) => {
  try {
    const response = await fetch(`${QUANT_ENGINE_URL}/api/quant/backtest/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body)
    });

    if (!response.ok) {
      const errData = await response.json();
      return res.status(response.status).json(errData);
    }

    const data = await response.json();
    res.json(data);
  } catch (error) {
    console.error('Quant API Backtest error:', error);
    res.status(502).json({ error: 'Quant engine unavailable.' });
  }
});


// ============================================
// News Endpoints
// ============================================

/**
 * GET /api/news
 * Fetch latest news related to portfolio stocks
 */
// ============================================
// News Endpoints
// ============================================

router.get('/news', async (req, res) => {
  try {

    const token = process.env.NEWS_API_TOKEN;

    if (!token) {
      return res.status(500).json({
        success: false,
        message: "NEWS_API_TOKEN not configured"
      });
    }

    const url = `https://api.marketaux.com/v1/news/all?language=en&limit=10&api_token=${token}`;

    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`MarketAux API error: ${response.status}`);
    }

    const result = await response.json();

    const articles = result.data || [];

    // Sort latest first
    articles.sort(
      (a, b) => new Date(b.published_at) - new Date(a.published_at)
    );

    res.json({
      success: true,
      count: articles.length,
      news: articles
    });

  } catch (error) {

    console.error("News API error:", error.message);

    res.status(500).json({
      success: false,
      message: "Failed to fetch news",
      error: error.message
    });

  }
});

router.get('/quant/scores/:symbol', async (req, res) => {
  try {
    const response = await fetch(`${QUANT_ENGINE_URL}/api/scores/${req.params.symbol}`);
    const data = await response.json();
    res.json(data);
  } catch (error) {
    console.error('Quant engine error:', error);
    res.status(502).json({ error: 'Quant engine unavailable' });
  }
});

/**
 * GET /api/index-analysis
 * Proxy to Python quant engine for Markov Chain & Mean Reversion index analysis
 */
router.get('/index-analysis', async (req, res) => {
  try {
    const response = await fetch(`${QUANT_ENGINE_URL}/index-analysis`);
    const data = await response.json();
    res.json(data);
  } catch (error) {
    console.error('Index analysis error:', error);
    res.status(502).json({ error: 'Quant engine unavailable for index analysis' });
  }
});

/**
 * POST /api/sync/indexes
 * Manually trigger NIFTY + SENSEX data sync
 */
router.post('/sync/indexes', async (req, res) => {
  try {
    const forceRefresh = req.query.force === 'true';
    console.log(`[API] Syncing index data (force=${forceRefresh})...`);
    const results = await fetchIndexData('1y', forceRefresh);
    res.json({ success: true, indexes: results });
  } catch (error) {
    console.error('Index sync error:', error);
    res.status(500).json({ error: 'Failed to sync index data' });
  }
});


// ============================================
// Fundamentals — RapidAPI Stock Fundamentals
// ============================================

/**
 * POST /api/fundamentals/sync
 * Triggers a full sync of fundamental data for all portfolio stocks.
 * This calls the RapidAPI, so it's expensive — only on explicit button press.
 */
router.post('/fundamentals/sync', async (req, res) => {
  try {
    console.log('[Fundamentals] Sync triggered via API...');
    const result = await syncAllFundamentals();
    res.json({ success: true, ...result });
  } catch (error) {
    console.error('Fundamentals sync error:', error);
    res.status(500).json({ error: 'Failed to sync fundamentals', message: error.message });
  }
});

/**
 * GET /api/fundamentals/:symbol
 * Returns cached fundamental data for a single stock.
 */
router.get('/fundamentals/:symbol', async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    const data = await getFundamentals(symbol);
    if (!data) {
      return res.status(404).json({ error: `No fundamental data for ${symbol}. Run sync first.` });
    }
    res.json(data);
  } catch (error) {
    console.error('Fundamentals get error:', error);
    res.status(500).json({ error: 'Failed to get fundamentals' });
  }
});

/**
 * GET /api/fundamentals
 * Returns cached fundamental data for all stocks + sync metadata.
 */
router.get('/fundamentals', async (req, res) => {
  try {
    const data = await getAllFundamentals();
    const syncInfo = await getFundamentalsSyncDate();
    res.json({ stocks: data, syncInfo });
  } catch (error) {
    console.error('Fundamentals list error:', error);
    res.status(500).json({ error: 'Failed to list fundamentals' });
  }
});

/**
 * GET /api/fundamentals/:symbol/news
 * Returns recent news articles for a stock
 */
router.get('/fundamentals/:symbol/news', async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    const news = await getNews(symbol);
    res.json(news);
  } catch (error) {
    console.error('Fundamentals news error:', error);
    res.status(500).json({ error: 'Failed to get news' });
  }
});

/**
 * GET /api/fundamentals/:symbol/analyst
 * Returns analyst ratings for a stock
 */
router.get('/fundamentals/:symbol/analyst', async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    const data = await getAnalystRatings(symbol);
    res.json(data || {});
  } catch (error) {
    console.error('Fundamentals analyst error:', error);
    res.status(500).json({ error: 'Failed to get analyst ratings' });
  }
});

/**
 * GET /api/fundamentals/:symbol/shareholding
 * Returns shareholding pattern for a stock
 */
router.get('/fundamentals/:symbol/shareholding', async (req, res) => {
  try {
    const symbol = req.params.symbol.toUpperCase();
    const data = await getShareholding(symbol);
    res.json(data);
  } catch (error) {
    console.error('Fundamentals shareholding error:', error);
    res.status(500).json({ error: 'Failed to get shareholding data' });
  }
});

// ============================================
// Sectors
// ============================================

/**
 * GET /api/sectors/momentum
 * Returns computed sector momentum scores
 */
router.get('/sectors/momentum', async (req, res) => {
  try {
    const data = await getSectorMomentum();
    res.json(data);
  } catch (error) {
    console.error('Sector momentum error:', error);
    res.status(500).json({ error: 'Failed to get sector momentum' });
  }
});

// console.log("NEWS TOKEN:", process.env.NEWS_API_TOKEN);
export default router;
