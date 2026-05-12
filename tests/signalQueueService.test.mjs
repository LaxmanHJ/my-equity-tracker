/**
 * Conviction-gate tests for signalQueueService.evaluateConviction.
 *
 * Covers the SIC-42 meta-labeler integration: the gate must pass only when
 * meta_prob is present AND >= minMetaProb, and must fail noisily (not
 * silently bypass) when the secondary model abstained.
 *
 * Run: `npm test`
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { evaluateConviction } from '../src/services/convictionGates.js';
import { riskLimits } from '../src/config/riskLimits.js';

/** Build a baseline score record that clears every non-meta gate. */
function baseScore(overrides = {}) {
  return {
    symbol: 'INFY',
    composite_score: 55,
    linear_signal: 'LONG',
    ml_path: false,
    ml_confidence: null,
    data_points: 250,
    factors: { volume: { avg_volume_20d: 5_000_000 } },
    // meta_prob is set per-test
    ...overrides,
  };
}

test('meta gate: passes when meta_prob exceeds minMetaProb', () => {
  const v = evaluateConviction(baseScore({ meta_prob: 0.80 }));
  assert.equal(v.passed, true);
  const metaGate = v.gates.find(g => g.name === 'meta_labeler');
  assert.ok(metaGate, 'meta_labeler gate present');
  assert.equal(metaGate.pass, true);
});

test('meta gate: fails when meta_prob is below minMetaProb', () => {
  const v = evaluateConviction(baseScore({ meta_prob: 0.60 }));
  assert.equal(v.passed, false);
  const metaGate = v.failedGates.find(g => g.name === 'meta_labeler');
  assert.ok(metaGate, 'meta_labeler reported as failed');
  assert.equal(metaGate.value, 0.60);
});

test('meta gate: fails (does not silently bypass) when meta_prob is missing', () => {
  // When the secondary abstains (e.g., feature NaN), composite.py omits
  // meta_prob entirely. The gate must FAIL in this case — silently bypassing
  // would let unscored signals through.
  assert.equal(riskLimits.conviction.requireMetaPass, true,
    'precondition: requireMetaPass should default to true');
  const v = evaluateConviction(baseScore({ meta_prob: undefined }));
  assert.equal(v.passed, false);
  const metaGate = v.failedGates.find(g => g.name === 'meta_labeler');
  assert.ok(metaGate, 'missing meta_prob must surface as a failed gate');
});

test('meta gate: fails when meta_prob is exactly null (model not loaded)', () => {
  const v = evaluateConviction(baseScore({ meta_prob: null }));
  const metaGate = v.failedGates.find(g => g.name === 'meta_labeler');
  assert.ok(metaGate);
  assert.equal(metaGate.value, null);
});

test('meta gate boundary: meta_prob == minMetaProb passes', () => {
  const v = evaluateConviction(baseScore({ meta_prob: riskLimits.conviction.minMetaProb }));
  const metaGate = v.gates.find(g => g.name === 'meta_labeler');
  assert.equal(metaGate.pass, true);
});

test('meta gate runs alongside linear/composite gates without conflict', () => {
  // Pass everything cleanly
  const v = evaluateConviction(baseScore({ meta_prob: 0.90 }));
  const names = v.gates.map(g => g.name);
  assert.ok(names.includes('composite'));
  assert.ok(names.includes('linear_agreement'));
  assert.ok(names.includes('meta_labeler'));
  assert.ok(names.includes('liquidity_adv'));
  assert.ok(names.includes('data_points'));
  assert.equal(v.passed, true);
});

test('low composite still fails even with strong meta_prob', () => {
  // Sanity: meta_prob is a bet-sizer ON TOP of the primary, not a replacement.
  const v = evaluateConviction(baseScore({ composite_score: 30, meta_prob: 0.95 }));
  assert.equal(v.passed, false);
  const failed = v.failedGates.map(g => g.name);
  assert.ok(failed.includes('composite'));
});
