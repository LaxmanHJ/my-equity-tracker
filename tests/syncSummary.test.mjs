/**
 * Tests for the pure sync helpers (src/services/syncSummary.js).
 *
 * These cover the two pieces of the EOD sync that are easy to get subtly
 * wrong and impossible to notice in production: the gap-fill date window
 * (an off-by-one re-fetches or silently skips a trading day) and the
 * stale-table exit code (which is the GitHub Actions alert — if it always
 * returns 0, a frozen table looks identical to a healthy run, which is
 * exactly the 2026-06-10 staleness incident).
 *
 * Run: `npm test`
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { computeBackfillWindow, summarizeSync } from '../src/services/syncSummary.js';

// ── computeBackfillWindow ───────────────────────────────────

test('backfill resumes the day after the last cached bar', () => {
  const { from, skip } = computeBackfillWindow('2026-07-20', '2026-07-24');
  assert.equal(from, '2026-07-21');
  assert.equal(skip, false);
});

test('backfill skips when the cache is already at today', () => {
  const { from, skip } = computeBackfillWindow('2026-07-24', '2026-07-24');
  assert.equal(from, '2026-07-25');
  assert.equal(skip, true, 'from > today must skip, not re-request a future window');
});

test('backfill skips when the cache is ahead of today', () => {
  // Can happen if a run straddles midnight or the exchange posts early.
  const { skip } = computeBackfillWindow('2026-07-25', '2026-07-24');
  assert.equal(skip, true);
});

test('backfill crosses a month boundary correctly', () => {
  const { from } = computeBackfillWindow('2026-07-31', '2026-08-05');
  assert.equal(from, '2026-08-01');
});

test('backfill crosses a year boundary correctly', () => {
  const { from } = computeBackfillWindow('2025-12-31', '2026-01-05');
  assert.equal(from, '2026-01-01');
});

test('backfill handles a leap day', () => {
  const { from } = computeBackfillWindow('2028-02-28', '2028-03-01');
  assert.equal(from, '2028-02-29');
});

test('cold start uses the lookback window, not epoch', () => {
  const { from, skip } = computeBackfillWindow(null, '2026-07-24', 400);
  assert.equal(skip, false);
  assert.equal(from, '2025-06-19');
  // Must be strictly before today and a plausible date, not "1970-01-01"
  assert.ok(from < '2026-07-24');
});

// ── summarizeSync ───────────────────────────────────────────

const freshTables = {
  price_history: { max_date: '2026-07-24', stale: false },
  iv_daily: { max_date: '2026-07-24', stale: false },
};

test('all-fresh run exits 0 with no stale tables', () => {
  const s = summarizeSync({
    subSyncs: { holdings: { ok: true }, ivChain: { ok: true } },
    freshness: { tables: freshTables },
  });
  assert.deepEqual(s.failedSubSyncs, []);
  assert.deepEqual(s.staleTables, []);
  assert.equal(s.anyStale, false);
  assert.equal(s.exitCode, 0);
});

test('a stale table exits 1 — silence must not look like success', () => {
  const s = summarizeSync({
    subSyncs: { holdings: { ok: true } },
    freshness: {
      tables: {
        ...freshTables,
        iv_daily: { max_date: '2026-07-02', stale: true },
      },
    },
  });
  assert.equal(s.anyStale, true);
  assert.equal(s.exitCode, 1);
  assert.deepEqual(s.staleTables, [{ name: 'iv_daily', maxDate: '2026-07-02' }]);
});

test('failed sub-syncs are reported but do not by themselves fail the run', () => {
  // A sub-sync can fail transiently (Angel outside market hours) while every
  // table is still fresh — that is a warning, not a red run.
  const s = summarizeSync({
    subSyncs: { holdings: { ok: true }, ivChain: { ok: false }, pcrOi: { ok: false } },
    freshness: { tables: freshTables },
  });
  assert.deepEqual(s.failedSubSyncs, ['ivChain', 'pcrOi']);
  assert.equal(s.exitCode, 0);
});

test('an empty table with no max_date still counts as stale', () => {
  const s = summarizeSync({
    subSyncs: {},
    freshness: { tables: { option_chain_daily: { max_date: null, stale: true } } },
  });
  assert.equal(s.exitCode, 1);
  assert.deepEqual(s.staleTables, [{ name: 'option_chain_daily', maxDate: null }]);
});

test('a missing freshness report does not crash or falsely alert', () => {
  // The freshness fetch is itself in a try/catch; absent report => no stale info.
  const s = summarizeSync({ subSyncs: { holdings: { ok: true } }, freshness: null });
  assert.equal(s.anyStale, false);
  assert.equal(s.exitCode, 0);
  assert.deepEqual(s.failedSubSyncs, []);
});

test('a completely empty result does not throw', () => {
  const s = summarizeSync({});
  assert.equal(s.exitCode, 0);
  assert.deepEqual(s.failedSubSyncs, []);
});
