/**
 * Conviction Gates — pure, dependency-free.
 *
 * Extracted from signalQueueService.js so the gate logic can be unit-tested
 * without booting the Turso/Express/Claude transitive imports. Behaviour is
 * unchanged: same gate names, same order, same per-gate shape (`{name, pass,
 * value, required}`). See signalQueueService.js for orchestration.
 *
 * Gates (in order):
 *   1. composite        — composite_score >= minCompositeScore
 *   2. linear_agreement — linear_signal == 'LONG' (when requireLinearAgreement)
 *   3. ml_confidence    — ml_confidence >= minMlConfidencePct (only when ml_path)
 *   4. meta_labeler     — meta_prob >= minMetaProb (SIC-42, when requireMetaPass)
 *   5. liquidity_adv    — avg_volume_20d >= minAvgDailyVolume
 *   6. data_points      — bar count >= minDataPoints
 *
 * Meta gate semantics (SIC-42): a missing meta_prob FAILS the gate. The
 * scoring engine omits meta_prob only when the secondary abstained
 * (NaN feature, model not loaded, etc.); silently bypassing in that case
 * would let unscored signals through — exactly the failure mode the gate
 * exists to prevent.
 */
import { riskLimits } from '../config/riskLimits.js';

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

  if (c.requireMetaPass) {
    const mp = s.meta_prob;
    gates.push({
      name: 'meta_labeler',
      pass: mp != null && mp >= c.minMetaProb,
      value: mp,
      required: `>= ${c.minMetaProb}`,
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
