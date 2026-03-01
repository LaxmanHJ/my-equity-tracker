/**
 * Risk Analysis Module
 * Calculate Beta, Volatility, Sharpe Ratio, and Maximum Drawdown
 */

import { getHistoricalData, getBenchmarkData } from '../services/stockData.js';
import { calculateReturns, pearsonCorrelation } from './correlation.js';

/**
 * Calculate standard deviation
 */
function standardDeviation(values) {
  if (values.length === 0) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const squaredDiffs = values.map(v => Math.pow(v - mean, 2));
  return Math.sqrt(squaredDiffs.reduce((a, b) => a + b, 0) / values.length);
}

/**
 * Calculate annualized volatility
 */
export function calculateVolatility(prices, annualize = true) {
  const returns = calculateReturns(prices);
  const dailyVol = standardDeviation(returns);
  return annualize ? dailyVol * Math.sqrt(252) : dailyVol; // 252 trading days
}

/**
 * Calculate Beta against NIFTY 50
 */
export async function calculateBeta(symbol, period = '1y') {
  const stockData = await getHistoricalData(symbol, period);
  const benchmarkData = await getBenchmarkData(period);
  
  if (stockData.length === 0 || benchmarkData.length === 0) {
    return null;
  }
  
  // Align data by date
  const stockDates = new Set(stockData.map(d => d.date));
  const alignedBenchmark = benchmarkData.filter(d => stockDates.has(d.date));
  const alignedStock = stockData.filter(d => 
    benchmarkData.some(b => b.date === d.date)
  );
  
  if (alignedStock.length < 30) {
    return null; // Not enough data
  }
  
  const stockReturns = calculateReturns(alignedStock.map(d => d.close));
  const benchmarkReturns = calculateReturns(alignedBenchmark.map(d => d.close));
  
  // Beta = Covariance(stock, market) / Variance(market)
  const correlation = pearsonCorrelation(stockReturns, benchmarkReturns);
  const stockVol = standardDeviation(stockReturns);
  const marketVol = standardDeviation(benchmarkReturns);
  
  if (marketVol === 0) return null;
  
  const beta = (correlation * stockVol) / marketVol;
  
  return parseFloat(beta.toFixed(4));
}

/**
 * Calculate Sharpe Ratio
 * Assuming risk-free rate of 6% (approximate Indian govt bond yield)
 */
export function calculateSharpeRatio(prices, riskFreeRate = 0.06) {
  const returns = calculateReturns(prices);
  if (returns.length === 0) return null;
  
  const avgDailyReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
  const annualizedReturn = avgDailyReturn * 252;
  const annualizedVol = calculateVolatility(prices);
  
  if (annualizedVol === 0) return null;
  
  const sharpe = (annualizedReturn - riskFreeRate) / annualizedVol;
  return parseFloat(sharpe.toFixed(4));
}

/**
 * Calculate Maximum Drawdown
 */
export function calculateMaxDrawdown(prices) {
  if (prices.length === 0) return { maxDrawdown: 0, drawdownPeriod: null };
  
  let peak = prices[0];
  let maxDrawdown = 0;
  let drawdownStart = 0;
  let drawdownEnd = 0;
  let currentDrawdownStart = 0;
  
  for (let i = 1; i < prices.length; i++) {
    if (prices[i] > peak) {
      peak = prices[i];
      currentDrawdownStart = i;
    }
    
    const drawdown = (peak - prices[i]) / peak;
    
    if (drawdown > maxDrawdown) {
      maxDrawdown = drawdown;
      drawdownStart = currentDrawdownStart;
      drawdownEnd = i;
    }
  }
  
  return {
    maxDrawdown: parseFloat((maxDrawdown * 100).toFixed(2)),
    drawdownStart,
    drawdownEnd
  };
}

/**
 * Calculate Value at Risk (VaR) - Historical method
 * 95% confidence level by default
 */
export function calculateVaR(prices, confidenceLevel = 0.95, investment = 100000) {
  const returns = calculateReturns(prices);
  if (returns.length === 0) return null;
  
  // Sort returns
  const sortedReturns = [...returns].sort((a, b) => a - b);
  
  // Find the return at the given percentile
  const index = Math.floor((1 - confidenceLevel) * sortedReturns.length);
  const varReturn = sortedReturns[index];
  
  return {
    varPercent: parseFloat((varReturn * 100).toFixed(2)),
    varAmount: parseFloat((investment * Math.abs(varReturn)).toFixed(2)),
    confidenceLevel: confidenceLevel * 100,
    description: `At ${confidenceLevel * 100}% confidence, maximum daily loss is ₹${(investment * Math.abs(varReturn)).toFixed(0)}`
  };
}

/**
 * Get full risk analysis for a stock
 */
export async function getFullRiskAnalysis(symbol, historicalData = null) {
  const data = historicalData || await getHistoricalData(symbol, '1y');
  if (data.length === 0) return null;
  
  const prices = data.map(d => d.close);
  const beta = await calculateBeta(symbol, '1y');
  
  // Interpret beta
  let betaInterpretation;
  if (beta !== null) {
    if (beta > 1.5) {
      betaInterpretation = 'Very aggressive - highly volatile compared to market';
    } else if (beta > 1) {
      betaInterpretation = 'Aggressive - more volatile than market';
    } else if (beta > 0.5) {
      betaInterpretation = 'Moderate - similar volatility to market';
    } else if (beta > 0) {
      betaInterpretation = 'Defensive - less volatile than market';
    } else {
      betaInterpretation = 'Counter-cyclical - moves opposite to market';
    }
  }
  
  const volatility = calculateVolatility(prices);
  const sharpe = calculateSharpeRatio(prices);
  const { maxDrawdown, drawdownStart, drawdownEnd } = calculateMaxDrawdown(prices);
  const var95 = calculateVaR(prices, 0.95, 100000);
  
  // Interpret Sharpe Ratio
  let sharpeInterpretation;
  if (sharpe !== null) {
    if (sharpe > 2) {
      sharpeInterpretation = 'Excellent risk-adjusted returns';
    } else if (sharpe > 1) {
      sharpeInterpretation = 'Good risk-adjusted returns';
    } else if (sharpe > 0) {
      sharpeInterpretation = 'Positive but modest returns for risk taken';
    } else {
      sharpeInterpretation = 'Returns do not compensate for risk';
    }
  }
  
  return {
    beta: {
      value: beta,
      interpretation: betaInterpretation,
      benchmark: 'NIFTY 50'
    },
    volatility: {
      annualized: parseFloat((volatility * 100).toFixed(2)),
      description: `Stock moves ${(volatility * 100).toFixed(1)}% annually on average`
    },
    sharpeRatio: {
      value: sharpe,
      interpretation: sharpeInterpretation,
      riskFreeRate: '6% (approx Indian govt bond)'
    },
    maxDrawdown: {
      percent: maxDrawdown,
      startIndex: drawdownStart,
      endIndex: drawdownEnd,
      description: `Largest peak-to-trough decline was ${maxDrawdown}%`
    },
    valueAtRisk: var95,
    dataPoints: data.length,
    period: '1 year'
  };
}
