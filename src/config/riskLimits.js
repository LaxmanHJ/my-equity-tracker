/**
 * Risk Management Configuration
 *
 * Based on wiki research:
 *   - Stop-loss: Lopez de Prado triple-barrier (volatility-scaled) + Chandelier trailing
 *     See `wiki/papers/lopez_de_prado_afml_2018.md` Ch.3
 *   - Position sizing: Kakushadze inverse-volatility weighting (R ~ V^0.76)
 *     See `wiki/papers/kakushadze_101_alphas.md`
 *
 * All thresholds live here so they can be tuned from one place.
 */

export const riskLimits = {
  stopLoss: {
    // Volatility-scaled initial stop (Lopez de Prado)
    // stop = entry - (volMultiplier × σ_20 × entry)
    volMultiplier: 2.5,
    volWindow: 20,          // rolling days for σ calculation

    // Chandelier trailing stop (industry standard for trend-following)
    // stop = highest_high_last_N - (atrMultiplier × ATR_14)
    atrMultiplier: 3.0,
    atrWindow: 14,
    trailWindow: 22,

    // If current price <= stop, alert is triggered.
    // Use the HIGHER of the two stops (more protective).
    method: 'hybrid',       // 'volatility' | 'chandelier' | 'hybrid'
  },

  portfolio: {
    // Halt all new orders if portfolio down this % from previous close in one day
    dailyCircuitBreakerPct: 2.0,

    // No single sector > this % of total portfolio value
    maxSectorConcentrationPct: 25.0,
  },

  position: {
    // Position sizing method: 'inverse_vol' | 'equal_weight'
    sizing: 'inverse_vol',

    // Hard cap on single position size, regardless of sizing method
    maxPositionPct: 5.0,

    // Target annualized portfolio volatility (for scaling inverse-vol weights)
    targetVolAnnual: 0.15,

    // Don't place orders exceeding this % of 20-day average volume
    maxVolumeParticipationPct: 3.0,
  },

  execution: {
    // Signals are generated EOD; orders execute next session after this delay
    // from market open (gives price discovery time to settle)
    executionDelayMinutes: 30,

    // Reject a queued signal if next-session LTP has moved more than this %
    // from the EOD signal price (overnight gap protection)
    maxGapFromSignalPct: 3.0,

    // Default product type for broker orders; UI can override to 'INTRADAY'
    defaultProductType: 'DELIVERY',
  },

  // Conviction gates — signals must clear all of these before they enter the
  // queue for Claude-assisted execution. Rationale in wiki/concepts/factor_scoring.md
  // and wiki/papers/grinold_kahn_active_portfolio.md (IC/ICIR framework).
  conviction: {
    // Composite score threshold (project default; sicilian_strategy.py BUY_THRESHOLD = 0.40)
    minCompositeScore: 40,

    // Linear composite must agree — ML has ~0 OOS IC (ml_pipeline.md 2026-04-17),
    // so the hand-tuned linear composite is the authoritative direction signal.
    requireLinearAgreement: true,

    // ML confidence floor — applied only when ml_path is active. Used as
    // confirmation, not as the driver (ML IC is near zero on daily data, so
    // the bar is "BUY probability above random for a 3-class softmax" ~33%).
    minMlConfidencePct: 40,

    // Minimum 20-day average daily volume (liquidity gate, checklist M2)
    minAvgDailyVolume: 500_000,

    // Minimum price-history bars — below this, factor windows (momentum 126,
    // volatility 20, MACD 26) don't have enough data to be reliable.
    minDataPoints: 200,
  },

  // Paper trading mode — log orders but don't send to broker
  // Must be explicitly set to 'false' in .env to enable real orders
  paperTrading: process.env.PAPER_TRADING !== 'false',
};

export default riskLimits;
