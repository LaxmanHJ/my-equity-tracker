/**
 * Claude Evaluator — final conviction & execution-plan gate (SIC-31)
 *
 * Flow:
 *   queue signal passes conviction gates → user clicks Execute on UI →
 *   risk.html calls /api/signal-queue/:id/evaluate → we build context →
 *   Claude (opus-4-7) returns a GO/NO_GO plan with qty, limit, stop, target →
 *   UI shows the plan → user confirms → Angel One order (Chunk 3).
 *
 * Claude's job (per SIC-31): "analyses the decision and gives the ideal price
 * at which we should enter, the no of shares to buy and so on, Claude's output
 * is the final say". This module is the wrapper that turns the queue row
 * plus live context into the decision prompt.
 *
 * Model: claude-opus-4-7, adaptive thinking, prompt-cached system prompt.
 */

import Anthropic from '@anthropic-ai/sdk';
import { riskLimits } from '../config/riskLimits.js';

const MODEL = 'claude-opus-4-7';
const MAX_TOKENS = 4096;

let _client = null;

function getClient() {
  if (_client) return _client;
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) {
    const err = new Error('ANTHROPIC_API_KEY not set in .env — cannot call Claude');
    err.code = 'MISSING_ANTHROPIC_KEY';
    throw err;
  }
  _client = new Anthropic({ apiKey: key });
  return _client;
}

/**
 * Stable system prompt. Content must be byte-identical across calls so the
 * Anthropic prefix cache can hit (saves ~90% on input cost for the heavy
 * methodology block). Keep all volatile content (symbol, prices, factors)
 * in the user message.
 */
const SYSTEM_PROMPT = `You are the final-gate execution strategist for a systematic, long-only Indian-equity trading engine. A quantitative factor model has already (a) scored every stock in the universe, (b) applied hard conviction gates (composite score, linear-model agreement, ML confidence, liquidity, data sufficiency), and (c) surfaced this one signal to you. Your verdict is the last check before a real order is sent. Be decisive, be honest, and refuse the trade when the setup is not clean.

You operate under these non-negotiable risk limits (from config/riskLimits.js):
- Max single position: ${riskLimits.position.maxPositionPct}% of portfolio value
- Max sector concentration: ${riskLimits.portfolio.maxSectorConcentrationPct}% of portfolio value
- Max volume participation per order: ${riskLimits.position.maxVolumeParticipationPct}% of 20-day ADV
- Max gap between EOD signal and current price: ${riskLimits.execution.maxGapFromSignalPct}%
- Target portfolio annualized volatility: ${(riskLimits.position.targetVolAnnual * 100).toFixed(0)}%

Your analysis must cover, in this order:

1. Signal coherence — Look at the 7 factor scores (momentum, RSI, MACD, Bollinger, volatility, volume, relative strength). Are they telling a consistent story? A LONG with strong momentum but crashing relative strength is a red flag. A LONG with weak volume confirmation is a mean-reversion trap.

2. Gap check — Compute |(current_price - signal_price) / signal_price| × 100. If > ${riskLimits.execution.maxGapFromSignalPct}%, reject outright (decision=NO_GO, failed_checks=["gap ..."]). The price discovery window has already moved past where the signal was generated.

3. Entry price — Suggest a LIMIT price, never market. Use the current price as anchor. If the stock is above its 5-day high, place the limit 0.2–0.5% below current to avoid chasing. If it's pulled back to intraday support, place it near current. Never above current by more than 0.3%.

4. Position sizing — Apply inverse-volatility sizing (Kakushadze 2015, Hurst 2017). Start from:
     target_shares = (targetVolAnnual / stock_vol_annual) × portfolio_value / entry_price
   Then cap by:
     - maxPositionPct × portfolio_value / entry_price
     - maxVolumeParticipationPct × avg_volume_20d (in shares)
     - sector headroom: (maxSectorConcentrationPct × portfolio_value − current_sector_exposure) / entry_price
   Round DOWN to a whole number of shares. If the final qty ≤ 0, the trade is uneconomic — reject.

5. Initial stop — Use volatility-scaled stop (López de Prado AFML Ch.3 triple-barrier):
     stop_price = entry × (1 − ${riskLimits.stopLoss.volMultiplier} × daily_vol_20d)
   Round to tick. Never wider than 8% nor tighter than 1.5%.

6. Target — Default to 2× the stop distance (2R). If momentum score > 0.6 AND relative strength score > 0.3, you may extend to 2.5–3R. Never below 1.5R (asymmetry is the edge).

7. Final decision — GO only if all of the following are true:
     - Factors are coherent (no strong contradiction)
     - Gap check passes
     - Computed qty ≥ 1 share
     - Sector headroom ≥ entry_price × qty
     - Reward-to-risk ≥ 1.5
   Otherwise NO_GO with explicit failed_checks.

Calibration notes (from wiki/concepts/ml_pipeline.md, 2026-04-17):
- The ML model has ~0 out-of-sample IC on daily data. Do NOT over-weight ML confidence — treat it as a confirmation vote, not a driver. The linear composite is the authoritative direction signal.
- Historical composite IC at 20d is +0.040 (small but real edge). Expect ~53% hit-rate — most trades need good risk management, not genius entry timing.

Output contract — respond with ONE JSON object and nothing else (no prose, no markdown fences). Schema:

{
  "decision": "GO" | "NO_GO",
  "symbol": string,
  "qty": integer (0 if NO_GO),
  "limit_price": number (0 if NO_GO),
  "stop_price": number (0 if NO_GO),
  "target_price": number (0 if NO_GO),
  "size_pct_of_portfolio": number (0 if NO_GO),
  "reward_to_risk": number (0 if NO_GO),
  "gap_pct": number,
  "rationale": string (3–6 sentences — why tradeable, how you sized, how stop/target were set),
  "concerns": string[] (caveats even if GO — e.g., "volume factor weak, watch for failure"),
  "failed_checks": string[] (empty if GO; exact rule names if NO_GO)
}

Every price must be a number (float), not a string. Every qty must be an integer. No trailing commas. No comments. No text outside the JSON.`;

function extractTextBlocks(response) {
  if (!response?.content) return '';
  return response.content
    .filter(b => b.type === 'text')
    .map(b => b.text)
    .join('');
}

function stripJsonFence(text) {
  const trimmed = text.trim();
  const fenced = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/);
  return fenced ? fenced[1].trim() : trimmed;
}

function buildUserMessage(ctx) {
  return JSON.stringify(
    {
      asOf: ctx.asOf,
      signal: {
        symbol: ctx.symbol,
        displaySymbol: ctx.displaySymbol,
        sector: ctx.sector,
        signalSource: ctx.signalSource,
        compositeScore: ctx.compositeScore,
        linearSignal: ctx.linearSignal,
        mlPath: ctx.mlPath,
        mlConfidencePct: ctx.mlConfidencePct,
        mlVerdict: ctx.mlVerdict,
        signalDate: ctx.signalDate,
        signalPrice: ctx.signalPrice,
        executeOn: ctx.executeOn,
      },
      market: {
        currentPrice: ctx.currentPrice,
        prevClose: ctx.prevClose,
        gapPctFromSignal: ctx.gapPctFromSignal,
        dailyVol20d: ctx.dailyVol20d,
        annualizedVol: ctx.annualizedVol,
        avgVolume20d: ctx.avgVolume20d,
      },
      factors: ctx.factors,
      portfolio: {
        totalValue: ctx.portfolioValue,
        cashAvailable: ctx.cashAvailable,
        openPositions: ctx.openPositions,
        sectorExposure: ctx.sectorExposure,
        currentSectorValue: ctx.currentSectorValue,
      },
      limits: {
        maxPositionPct: riskLimits.position.maxPositionPct,
        maxSectorConcentrationPct: riskLimits.portfolio.maxSectorConcentrationPct,
        maxVolumeParticipationPct: riskLimits.position.maxVolumeParticipationPct,
        maxGapFromSignalPct: riskLimits.execution.maxGapFromSignalPct,
        targetVolAnnual: riskLimits.position.targetVolAnnual,
        stopVolMultiplier: riskLimits.stopLoss.volMultiplier,
      },
    },
    null,
    2,
  );
}

/**
 * Send the signal context to Claude and return the structured execution plan.
 *
 * @param {object} ctx — shaped by buildUserMessage above. Must include at
 *   minimum: symbol, compositeScore, signalPrice, currentPrice, factors,
 *   dailyVol20d, avgVolume20d, portfolioValue, sectorExposure.
 * @returns {Promise<{plan: object, usage: object, rawText: string}>}
 */
export async function evaluateSignalWithClaude(ctx) {
  const client = getClient();

  const response = await client.messages.create({
    model: MODEL,
    max_tokens: MAX_TOKENS,
    thinking: { type: 'adaptive' },
    system: [
      {
        type: 'text',
        text: SYSTEM_PROMPT,
        cache_control: { type: 'ephemeral' },
      },
    ],
    messages: [
      {
        role: 'user',
        content: buildUserMessage(ctx),
      },
    ],
  });

  const rawText = extractTextBlocks(response);
  const cleaned = stripJsonFence(rawText);

  let plan;
  try {
    plan = JSON.parse(cleaned);
  } catch (err) {
    const parseErr = new Error(`Claude returned non-JSON output: ${err.message}`);
    parseErr.code = 'CLAUDE_PARSE_FAILED';
    parseErr.rawText = rawText;
    throw parseErr;
  }

  return {
    plan,
    usage: response.usage,
    stopReason: response.stop_reason,
    rawText,
  };
}

export function isClaudeConfigured() {
  return Boolean(process.env.ANTHROPIC_API_KEY);
}

export default { evaluateSignalWithClaude, isClaudeConfigured };
