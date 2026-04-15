/**
 * Risk Manager — on-demand orchestrator
 *
 * Runs all risk checks against the current portfolio and returns a
 * consolidated alert list. Accepts a `priceProvider` callback so the
 * same code path can be driven by EOD cache today and by Angel One
 * LTP later (Chunk 3).
 *
 * Architecture note (2026-04-11):
 * This system is deployed on localhost — no guaranteed background
 * process during market hours. ALL risk checks are triggered
 * on-demand (user opens the app, hits /api/risk/check, or a new
 * order is being placed). We do NOT run a timer.
 *
 * Checks performed
 * ----------------
 *   1. Portfolio circuit breaker (aggregate daily drawdown)
 *   2. Sector concentration breaches
 *   3. Per-position stop-loss (hybrid vol + chandelier)
 *
 * Inputs
 * ------
 *   portfolio        — array of {symbol, quantity, avgPrice, sector}
 *   priceProvider    — async fn(symbol) → {currentPrice, prevClose, bars}
 *                      bars: [{date, open, high, low, close, volume}, ...]
 */

import { checkCircuitBreaker } from './circuitBreaker.js';
import { checkSectorConcentration } from './sectorConcentration.js';
import { checkStopLoss } from './stopLoss.js';

export async function runRiskChecks(portfolio, priceProvider) {
  const enriched = [];
  const stopLossAlerts = [];
  const errors = [];

  for (const pos of portfolio) {
    try {
      // Price history in Turso is keyed by displaySymbol (e.g. "INFY"),
      // not the Yahoo-style "INFY.NS" used elsewhere in config.
      const priceKey = pos.displaySymbol || pos.symbol;
      const { currentPrice, prevClose, bars } = await priceProvider(priceKey);
      if (currentPrice == null || !Array.isArray(bars)) {
        errors.push({ symbol: pos.symbol, reason: 'no_price_data' });
        continue;
      }

      enriched.push({
        symbol: pos.symbol,
        displaySymbol: pos.displaySymbol || pos.symbol,
        sector: pos.sector || 'Unknown',
        quantity: pos.quantity,
        avgPrice: pos.avgPrice,
        currentPrice,
        prevClose: prevClose ?? currentPrice,
        bars,
      });

      const stopResult = checkStopLoss(pos.avgPrice, currentPrice, bars);
      if (stopResult.triggered) {
        stopLossAlerts.push({
          type: 'stop_loss',
          severity: 'critical',
          symbol: pos.symbol,
          displaySymbol: pos.displaySymbol || pos.symbol,
          entryPrice: pos.avgPrice,
          currentPrice,
          stop: stopResult.stop,
          method: stopResult.method,
          volStop: stopResult.volStop,
          trailStop: stopResult.trailStop,
          distancePct: stopResult.distancePct,
          quantity: pos.quantity,
          message: `${pos.displaySymbol || pos.symbol} breached ${stopResult.method} stop at ₹${stopResult.stop} (current ₹${currentPrice})`,
        });
      }
    } catch (err) {
      errors.push({ symbol: pos.symbol, reason: err.message });
    }
  }

  const circuitBreaker = checkCircuitBreaker(enriched);
  const sector = checkSectorConcentration(enriched);

  const alerts = [...stopLossAlerts];

  if (circuitBreaker.triggered) {
    alerts.push({
      type: 'circuit_breaker',
      severity: 'critical',
      drawdownPct: circuitBreaker.drawdownPct,
      thresholdPct: circuitBreaker.thresholdPct,
      currentValue: circuitBreaker.currentValue,
      previousValue: circuitBreaker.previousValue,
      message: `Portfolio down ${circuitBreaker.drawdownPct}% — circuit breaker triggered (threshold ${circuitBreaker.thresholdPct}%)`,
    });
  }

  for (const breach of sector.breaches) {
    alerts.push({
      type: 'sector_concentration',
      severity: 'warning',
      sector: breach.sector,
      valuePct: breach.valuePct,
      thresholdPct: breach.thresholdPct,
      symbols: breach.symbols,
      message: `Sector ${breach.sector} at ${breach.valuePct}% of portfolio (cap ${breach.thresholdPct}%)`,
    });
  }

  const tradingHalted = circuitBreaker.triggered;

  return {
    checkedAt: new Date().toISOString(),
    tradingHalted,
    alertCount: alerts.length,
    alerts,
    circuitBreaker,
    sector: {
      breaches: sector.breaches,
      exposures: sector.exposures,
    },
    positionsChecked: enriched.length,
    errors,
  };
}
