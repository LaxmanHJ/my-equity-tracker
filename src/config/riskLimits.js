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

  // Paper trading mode — log orders but don't send to broker
  // Must be explicitly set to 'false' in .env to enable real orders
  paperTrading: process.env.PAPER_TRADING !== 'false',

  // Risk check cadence (auto-run every N minutes during market hours)
  checkIntervalMinutes: parseInt(process.env.RISK_CHECK_INTERVAL_MINUTES, 10) || 15,
};

export default riskLimits;
