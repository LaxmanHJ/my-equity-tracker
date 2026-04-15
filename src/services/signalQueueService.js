/**
 * Signal Queue Service
 *
 * Orchestrates the user-driven trade flow:
 *   1. generateQueue()  — runs the quant engine, enqueues LONG signals
 *   2. executeSignal()  — user clicks "Execute" → gap check → paper/live order
 *   3. rejectSignal()   — user clicks "Skip"
 *
 * The user controls every trade. Nothing fires automatically.
 */

import { riskLimits } from '../config/riskLimits.js';
import { portfolio } from '../config/portfolio.js';
import {
  enqueueSignal,
  getSignalById,
  markSignalExecuted,
  markSignalRejected,
} from '../database/db.js';
import { createEodPriceProvider } from '../risk/priceProvider.js';
import { runRiskChecks } from '../risk/riskManager.js';
import { nextTradingDay, todayStr } from '../utils/tradingCalendar.js';

const QUANT_ENGINE_URL = process.env.QUANT_ENGINE_URL || 'http://localhost:5001';

/**
 * Run the scoring engine and enqueue every LONG signal for user review.
 * Returns the list of enqueued rows.
 */
export async function generateQueue() {
  const res = await fetch(`${QUANT_ENGINE_URL}/api/scores`);
  if (!res.ok) throw new Error(`Scoring engine returned ${res.status}`);
  const data = await res.json();

  const stocks = data.stocks || [];
  const today = todayStr();
  const executeOn = nextTradingDay(today);

  const enqueued = [];
  for (const s of stocks) {
    if (s.signal !== 'LONG') continue;

    const source = s.ml_path ? 'ML' : 'Sicilian';
    const mlConf = s.ml_confidence ?? null;

    await enqueueSignal({
      signalDate: today,
      executeOn,
      symbol: s.symbol,
      signal: 'BUY',
      signalPrice: s.price,
      targetShares: null,
      compositeScore: s.composite_score,
      signalSource: source,
      mlConfidence: mlConf,
    });

    enqueued.push({
      symbol: s.symbol,
      signal: 'BUY',
      signalPrice: s.price,
      compositeScore: s.composite_score,
      signalSource: source,
      mlConfidence: mlConf,
      executeOn,
    });
  }

  return {
    signalDate: today,
    executeOn,
    totalScored: stocks.length,
    longSignals: enqueued.length,
    enqueued,
    summary: data.summary,
  };
}

/**
 * Execute a single queued signal. Called when the user clicks "Execute".
 *
 * Flow:
 *   1. Fetch current price (EOD cache — or Angel One LTP in Chunk 3)
 *   2. Gap check: reject if LTP moved > maxGapFromSignalPct from signal price
 *   3. Run risk checks: reject if trading is halted (circuit breaker)
 *   4. Paper mode → log as PAPER order; Live mode → broker call (Chunk 3)
 */
export async function executeSignal(signalId) {
  const signal = await getSignalById(signalId);
  if (!signal) return { error: 'signal_not_found' };
  if (signal.status !== 'pending') return { error: 'signal_not_pending', currentStatus: signal.status };

  const priceProvider = createEodPriceProvider(60);

  // Look up portfolio entry for sector/displaySymbol
  const portfolioEntry = portfolio.find(
    p => p.displaySymbol === signal.symbol || p.symbol === signal.symbol
  );
  const priceKey = portfolioEntry?.displaySymbol || signal.symbol;
  const { currentPrice } = await priceProvider(priceKey);

  if (!currentPrice) {
    await markSignalRejected(signal.id, 'no_current_price', 'rejected_risk');
    return { status: 'rejected', reason: 'no_current_price' };
  }

  // Gap check
  const gapPct = Math.abs((currentPrice - signal.signal_price) / signal.signal_price) * 100;
  if (gapPct > riskLimits.execution.maxGapFromSignalPct) {
    await markSignalRejected(
      signal.id,
      `gap ${gapPct.toFixed(2)}% exceeds ${riskLimits.execution.maxGapFromSignalPct}% limit`,
      'rejected_gap',
    );
    return {
      status: 'rejected_gap',
      gapPct: Number(gapPct.toFixed(2)),
      signalPrice: signal.signal_price,
      currentPrice,
    };
  }

  // Portfolio-level risk gate
  const riskResult = await runRiskChecks(portfolio, priceProvider);
  if (riskResult.tradingHalted) {
    await markSignalRejected(signal.id, 'circuit_breaker_active', 'rejected_risk');
    return { status: 'rejected_risk', reason: 'circuit_breaker_active', drawdownPct: riskResult.circuitBreaker.drawdownPct };
  }

  // Paper trading mode (default)
  if (riskLimits.paperTrading) {
    const orderId = `PAPER-${Date.now()}`;
    await markSignalExecuted(signal.id, { execPrice: currentPrice, orderId });
    return {
      status: 'executed_paper',
      orderId,
      symbol: signal.symbol,
      execPrice: currentPrice,
      signalPrice: signal.signal_price,
      gapPct: Number(gapPct.toFixed(2)),
    };
  }

  // Live mode — will be wired in Chunk 3 (Angel One broker)
  return { status: 'error', reason: 'live_broker_not_implemented' };
}

/**
 * User-initiated rejection (skip).
 */
export async function rejectSignal(signalId, reason = 'user_skip') {
  const signal = await getSignalById(signalId);
  if (!signal) return { error: 'signal_not_found' };
  if (signal.status !== 'pending') return { error: 'signal_not_pending', currentStatus: signal.status };

  await markSignalRejected(signal.id, reason, 'rejected_user');
  return { status: 'rejected_user', symbol: signal.symbol };
}
