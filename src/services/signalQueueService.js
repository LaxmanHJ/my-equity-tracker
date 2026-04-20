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
import { evaluateSignalWithClaude, isClaudeConfigured } from './claudeEvaluator.js';

const QUANT_ENGINE_URL = process.env.QUANT_ENGINE_URL || 'http://localhost:5001';

/**
 * Conviction gates — applied at queue-generation time so only threshold-passing
 * stocks reach the UI for Claude-assisted execution. Returns per-gate pass/fail
 * so skipped signals carry their reasons into the response for debugging.
 *
 * Thresholds live in `config/riskLimits.js` → `conviction`. Rationale:
 *   - composite ≥ 40: project's canonical LONG threshold
 *   - linear_signal agreement: ML has ~0 OOS IC, linear is the authoritative direction
 *   - ml_confidence ≥ 55 (when ML path active): confirmation, not a driver
 *   - avg_volume_20d ≥ 500k: liquidity cap (checklist M2)
 *   - data_points ≥ 200: enough bars for factor windows to be meaningful
 */
export function evaluateConviction(s) {
  const c = riskLimits.conviction;
  const gates = [];

  gates.push({
    name: 'composite',
    pass: s.composite_score >= c.minCompositeScore,
    value: s.composite_score,
    required: `>= ${c.minCompositeScore}`,
  });

  if (c.requireLinearAgreement) {
    gates.push({
      name: 'linear_agreement',
      pass: s.linear_signal === 'LONG',
      value: s.linear_signal,
      required: 'LONG',
    });
  }

  if (s.ml_path) {
    const conf = s.ml_confidence;
    gates.push({
      name: 'ml_confidence',
      pass: conf != null && conf >= c.minMlConfidencePct,
      value: conf,
      required: `>= ${c.minMlConfidencePct}`,
    });
  }

  const adv = s.factors?.volume?.avg_volume_20d ?? null;
  gates.push({
    name: 'liquidity_adv',
    pass: adv != null && adv >= c.minAvgDailyVolume,
    value: adv,
    required: `>= ${c.minAvgDailyVolume}`,
  });

  gates.push({
    name: 'data_points',
    pass: (s.data_points ?? 0) >= c.minDataPoints,
    value: s.data_points,
    required: `>= ${c.minDataPoints}`,
  });

  const failed = gates.filter(g => !g.pass);
  return { passed: failed.length === 0, failedGates: failed, gates };
}

/**
 * Run the scoring engine and enqueue only signals that clear all conviction
 * gates. Stocks that fail any gate are returned in `skipped` with reasons.
 */
export async function generateQueue() {
  const res = await fetch(`${QUANT_ENGINE_URL}/api/scores`);
  if (!res.ok) throw new Error(`Scoring engine returned ${res.status}`);
  const data = await res.json();

  const stocks = data.stocks || [];
  const today = todayStr();
  const executeOn = nextTradingDay(today);

  const enqueued = [];
  const skipped = [];

  for (const s of stocks) {
    const verdict = evaluateConviction(s);

    if (!verdict.passed) {
      skipped.push({
        symbol: s.symbol,
        compositeScore: s.composite_score,
        failedGates: verdict.failedGates.map(g => ({
          name: g.name,
          value: g.value,
          required: g.required,
        })),
      });
      continue;
    }

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
    skipped,
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
 * Build the context Claude needs to generate an execution plan and call
 * claude-opus-4-7. Called when the user clicks "Evaluate with Claude"
 * (separate from execute — the user reviews Claude's plan before
 * confirming the order).
 *
 * Inputs are pulled fresh at call time:
 *   - fresh score snapshot from the quant engine (factor breakdown)
 *   - latest cached bars (for currentPrice + ADV + vol sanity)
 *   - full portfolio valuation + sector exposure (risk check output)
 *
 * Claude's output is persisted nowhere — this is a pure advice call;
 * the confirm/reject happens in a follow-up executeSignal().
 */
export async function evaluateSignal(signalId) {
  const signal = await getSignalById(signalId);
  if (!signal) return { error: 'signal_not_found' };
  if (signal.status !== 'pending') {
    return { error: 'signal_not_pending', currentStatus: signal.status };
  }

  if (!isClaudeConfigured()) {
    return { error: 'claude_not_configured', detail: 'ANTHROPIC_API_KEY is missing in .env' };
  }

  // 1) Fresh factor snapshot for this symbol from the Python scoring engine
  const scoresRes = await fetch(`${QUANT_ENGINE_URL}/api/scores`);
  if (!scoresRes.ok) {
    return { error: 'scoring_engine_unreachable', status: scoresRes.status };
  }
  const scoresData = await scoresRes.json();
  const snapshot = (scoresData.stocks || []).find(
    s => s.symbol === signal.symbol || s.displaySymbol === signal.symbol,
  );
  if (!snapshot) {
    return { error: 'symbol_not_in_scores', symbol: signal.symbol };
  }

  // 2) Live price + bars for this symbol
  const priceProvider = createEodPriceProvider(30);
  const portfolioEntry = portfolio.find(
    p => p.displaySymbol === signal.symbol || p.symbol === signal.symbol,
  );
  const priceKey = portfolioEntry?.displaySymbol || signal.symbol;
  const { currentPrice, prevClose, bars } = await priceProvider(priceKey);

  if (!currentPrice) {
    return { error: 'no_current_price', symbol: signal.symbol };
  }

  const gapPctFromSignal = Number(
    (((currentPrice - signal.signal_price) / signal.signal_price) * 100).toFixed(3),
  );

  // 3) Portfolio valuation + sector exposure (reuse risk manager)
  const risk = await runRiskChecks(portfolio, priceProvider);
  const sectorExposures = risk.sector.exposures || [];
  const portfolioValue = sectorExposures.reduce((acc, s) => acc + (s.value || 0), 0);
  const sectorExposure = Object.fromEntries(
    sectorExposures.map(s => [s.sector, Number(s.valuePct?.toFixed(2) ?? 0)]),
  );
  const thisSector = portfolioEntry?.sector || 'Unknown';
  const currentSectorValue = Number(
    (sectorExposures.find(s => s.sector === thisSector)?.value || 0).toFixed(2),
  );

  // 4) Volatility from the factor snapshot (annualized → daily)
  const annualizedVol = snapshot.factors?.volatility?.annualized_vol ?? null;
  const dailyVol20d = annualizedVol != null ? annualizedVol / Math.sqrt(252) : null;
  const avgVolume20d = snapshot.factors?.volume?.avg_volume_20d ?? null;

  const ctx = {
    asOf: new Date().toISOString(),
    symbol: signal.symbol,
    displaySymbol: portfolioEntry?.displaySymbol || signal.symbol,
    sector: thisSector,
    signalSource: signal.signal_source,
    compositeScore: snapshot.composite_score,
    linearSignal: snapshot.linear_signal,
    mlPath: snapshot.ml_path,
    mlVerdict: snapshot.ml_verdict ?? null,
    mlConfidencePct: snapshot.ml_confidence ?? null,
    signalDate: signal.signal_date,
    signalPrice: signal.signal_price,
    executeOn: signal.execute_on,
    currentPrice,
    prevClose,
    gapPctFromSignal,
    dailyVol20d,
    annualizedVol,
    avgVolume20d,
    factors: snapshot.factors,
    portfolioValue: Number(portfolioValue.toFixed(2)),
    cashAvailable: null, // not tracked yet — Claude uses portfolioValue as proxy
    openPositions: portfolio.map(p => ({
      symbol: p.displaySymbol,
      sector: p.sector,
      quantity: p.quantity,
      avgPrice: p.avgPrice,
    })),
    sectorExposure,
    currentSectorValue,
  };

  const result = await evaluateSignalWithClaude(ctx);
  return {
    status: 'evaluated',
    signalId: signal.id,
    context: ctx,
    plan: result.plan,
    usage: result.usage,
  };
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
