/**
 * Correlation Analysis Module
 * Calculate correlations between portfolio stocks
 */

import { getHistoricalData } from '../services/stockData.js';
import { portfolio } from '../config/portfolio.js';
import { settings } from '../config/settings.js';

/**
 * Calculate Pearson correlation coefficient between two arrays
 */
export function pearsonCorrelation(x, y) {
  if (x.length !== y.length || x.length === 0) return null;

  const n = x.length;
  const sumX = x.reduce((a, b) => a + b, 0);
  const sumY = y.reduce((a, b) => a + b, 0);
  const sumXY = x.reduce((acc, xi, i) => acc + xi * y[i], 0);
  const sumX2 = x.reduce((acc, xi) => acc + xi * xi, 0);
  const sumY2 = y.reduce((acc, yi) => acc + yi * yi, 0);

  const numerator = n * sumXY - sumX * sumY;
  const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));

  if (denominator === 0) return 0;

  return numerator / denominator;
}

/**
 * Calculate daily returns from prices
 */
export function calculateReturns(prices) {
  const returns = [];
  for (let i = 1; i < prices.length; i++) {
    returns.push((prices[i] - prices[i - 1]) / prices[i - 1]);
  }
  return returns;
}

/**
 * Build correlation matrix for all portfolio stocks
 */
export async function buildCorrelationMatrix(days = settings.analysis.correlationDays) {
  const stockData = {};
  const symbols = [...new Set(portfolio.map(s => s.symbol))];

  // Fetch historical data for all stocks in parallel (all reads from local cache)
  const results = await Promise.all(
    symbols.map(async (symbol) => {
      const historical = await getHistoricalData(symbol, '1y');
      return { symbol, historical };
    })
  );

  for (const { symbol, historical } of results) {
    if (historical.length > 0) {
      const prices = historical.slice(-days).map(d => d.close);
      stockData[symbol] = {
        prices,
        returns: calculateReturns(prices),
        info: portfolio.find(s => s.symbol === symbol)
      };
    }
  }

  // Build correlation matrix
  const matrix = [];
  const symbolList = Object.keys(stockData);

  for (const symbolA of symbolList) {
    const row = {
      symbol: symbolA,
      displaySymbol: stockData[symbolA].info?.displaySymbol,
      correlations: {}
    };

    for (const symbolB of symbolList) {
      // Align returns by length (use shorter length)
      const returnsA = stockData[symbolA].returns;
      const returnsB = stockData[symbolB].returns;
      const minLength = Math.min(returnsA.length, returnsB.length);

      const correlation = pearsonCorrelation(
        returnsA.slice(-minLength),
        returnsB.slice(-minLength)
      );

      row.correlations[symbolB] = correlation !== null ? parseFloat(correlation.toFixed(4)) : null;
    }

    matrix.push(row);
  }

  return {
    matrix,
    symbols: symbolList.map(s => ({
      symbol: s,
      displaySymbol: stockData[s].info?.displaySymbol
    })),
    period: `${days} days`,
    generatedAt: new Date().toISOString()
  };
}

/**
 * Find highly correlated stock pairs
 */
export async function findHighCorrelations(threshold = 0.7, days = settings.analysis.correlationDays) {
  const { matrix } = await buildCorrelationMatrix(days);
  const highCorrelations = [];

  // Find pairs with correlation above threshold
  for (let i = 0; i < matrix.length; i++) {
    for (let j = i + 1; j < matrix.length; j++) {
      const symbolA = matrix[i].symbol;
      const symbolB = matrix[j].symbol;
      const correlation = matrix[i].correlations[symbolB];

      if (correlation !== null && Math.abs(correlation) >= threshold) {
        highCorrelations.push({
          stockA: {
            symbol: symbolA,
            displaySymbol: matrix[i].displaySymbol
          },
          stockB: {
            symbol: symbolB,
            displaySymbol: matrix[j].displaySymbol
          },
          correlation,
          type: correlation > 0 ? 'positive' : 'negative',
          risk: correlation > 0.8
            ? 'High diversification risk - stocks move together'
            : correlation < -0.8
              ? 'Good hedge - stocks move opposite'
              : 'Moderate correlation'
        });
      }
    }
  }

  // Sort by absolute correlation
  highCorrelations.sort((a, b) => Math.abs(b.correlation) - Math.abs(a.correlation));

  return {
    pairs: highCorrelations,
    threshold,
    period: `${days} days`,
    analysis: highCorrelations.length > 0
      ? `Found ${highCorrelations.length} highly correlated pairs (|r| >= ${threshold})`
      : 'Portfolio is well diversified - no highly correlated pairs found'
  };
}

/**
 * Analyze portfolio diversification
 */
export async function analyzeDiversification(days = settings.analysis.correlationDays) {
  const { matrix, symbols } = await buildCorrelationMatrix(days);

  // Calculate average correlation (excluding self-correlations)
  let totalCorrelation = 0;
  let count = 0;

  for (const row of matrix) {
    for (const [symbol, correlation] of Object.entries(row.correlations)) {
      if (symbol !== row.symbol && correlation !== null) {
        totalCorrelation += Math.abs(correlation);
        count++;
      }
    }
  }

  const avgCorrelation = count > 0 ? totalCorrelation / count : 0;

  // Determine diversification level
  let diversificationLevel;
  let recommendation;

  if (avgCorrelation < 0.3) {
    diversificationLevel = 'Excellent';
    recommendation = 'Your portfolio is well diversified. Stocks show low correlation with each other.';
  } else if (avgCorrelation < 0.5) {
    diversificationLevel = 'Good';
    recommendation = 'Portfolio has reasonable diversification. Consider adding uncorrelated assets for improvement.';
  } else if (avgCorrelation < 0.7) {
    diversificationLevel = 'Moderate';
    recommendation = 'Many stocks move together. Consider adding stocks from different sectors or asset classes.';
  } else {
    diversificationLevel = 'Poor';
    recommendation = 'High correlation among holdings. Portfolio may have concentrated risk. Review sector allocation.';
  }

  return {
    averageCorrelation: parseFloat(avgCorrelation.toFixed(4)),
    diversificationLevel,
    recommendation,
    stockCount: symbols.length,
    period: `${days} days`
  };
}
