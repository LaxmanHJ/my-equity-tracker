import express from 'express';
import { portfolio, getStocksBySector } from '../config/portfolio.js';
import {
  getAllQuotes,
  getHistoricalData,
  getPortfolioSummary,
  getQuote
} from '../services/stockData.js';
import { getFullAnalysis, generateSignals } from '../analysis/technicals.js';
import {
  buildCorrelationMatrix,
  findHighCorrelations,
  analyzeDiversification
} from '../analysis/correlation.js';
import { getFullRiskAnalysis } from '../analysis/risk.js';
import {
  createAlert,
  getActiveAlerts,
  triggerAlert,
  saveDailyReport,
  getDailyReport
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
    const signals = [];

    for (const stock of portfolio) {
      const historical = await getHistoricalData(stock.symbol, '3m');
      if (historical.length > 0) {
        const prices = historical.map(d => d.close);
        const dates = historical.map(d => d.date);
        const stockSignals = generateSignals(prices, dates);

        signals.push({
          symbol: stock.displaySymbol,
          name: stock.name,
          sector: stock.sector,
          ...stockSignals
        });
      }
    }

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
router.get('/alerts', (req, res) => {
  try {
    const alerts = getActiveAlerts();
    res.json({ alerts });
  } catch (error) {
    res.status(500).json({ error: 'Failed to fetch alerts' });
  }
});

/**
 * POST /api/alerts
 * Create a new price alert
 */
router.post('/alerts', (req, res) => {
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

    const alertId = createAlert(stock.symbol, type || 'price', threshold, direction);

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
router.delete('/alerts/:id', (req, res) => {
  try {
    triggerAlert(parseInt(req.params.id));
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
    let report = getDailyReport(today);

    if (!report) {
      // Generate new report
      const summary = await getPortfolioSummary();
      const diversification = await analyzeDiversification(90);

      // Collect signals for all stocks
      const stockAnalysis = [];
      for (const stock of portfolio) {
        const historical = await getHistoricalData(stock.symbol, '3m');
        if (historical.length > 0) {
          const prices = historical.map(d => d.close);
          const signals = generateSignals(prices, historical.map(d => d.date));
          stockAnalysis.push({
            symbol: stock.displaySymbol,
            name: stock.name,
            signals: signals.signals
          });
        }
      }

      const reportData = {
        date: today,
        portfolio: summary,
        diversification,
        stockAnalysis,
        marketStatus: isMarketOpen() ? 'OPEN' : 'CLOSED',
        generatedAt: new Date().toISOString()
      };

      saveDailyReport(today, reportData);
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

export default router;
