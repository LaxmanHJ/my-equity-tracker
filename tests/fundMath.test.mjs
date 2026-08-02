/**
 * Tests for the pure fund helpers (src/services/fundMath.js).
 *
 * Two things here are easy to get subtly wrong and invisible in production:
 *
 *  - The tranche schedule. Before it was anchored on `started`, `nextDue` was a
 *    bare calendar lookahead, so a missed month silently rolled forward and the
 *    fund reported a healthy schedule while capital sat undeployed. That is the
 *    exact failure these tests exist to catch.
 *  - Average-cost basis on SELL. Getting the ordering wrong (reducing qty before
 *    computing avgCost) corrupts cost basis in a way that only shows up as a
 *    slightly wrong P&L months later.
 *
 * Run: `npm test`
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { derivePositions, trancheStatus, scheduledDate, fmtLocalDate } from '../src/services/fundMath.js';

const PLAN = { instrument: 'NIFTYBEES', tranches_total: 6, day_of_month: 1, started: null };
const at = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d, 12, 0, 0); };
const buy = (trade_date, qty = 100, price = 280) => ({ sleeve: 'BETA_CORE', side: 'BUY', trade_date, qty, price, fees: 0 });

// ── trancheStatus: not started ───────────────────────────────

test('not-started plan reports notStarted and cannot be overdue', () => {
  const t = trancheStatus(PLAN, [], at('2026-08-03'));
  assert.equal(t.notStarted, true);
  assert.equal(t.tranchesDone, 0);
  assert.equal(t.expected, 0);
  assert.equal(t.overdue, 0, 'nothing is committed to yet, so nothing can be late');
  assert.equal(t.started, null);
});

test('not-started nextDue is the upcoming day_of_month, not a past one', () => {
  // Aug 1 has passed; the next day_of_month=1 is Sep 1.
  assert.equal(trancheStatus(PLAN, [], at('2026-08-03')).nextDue, '2026-09-01');
});

test('not-started nextDue is today when today IS the tranche day', () => {
  // The old bare-calendar logic compared a midnight Date against a timestamped
  // `now`, so on the due day itself it skipped a month.
  assert.equal(trancheStatus(PLAN, [], at('2026-09-01')).nextDue, '2026-09-01');
});

// ── trancheStatus: on schedule ───────────────────────────────

test('one tranche done in the starting month is on schedule', () => {
  const plan = { ...PLAN, started: '2026-08-03' };
  const t = trancheStatus(plan, [buy('2026-08-03')], at('2026-08-03'));
  assert.equal(t.notStarted, false);
  assert.equal(t.tranchesDone, 1);
  assert.equal(t.expected, 1);
  assert.equal(t.overdue, 0);
  assert.equal(t.nextDue, '2026-09-01', 'next tranche is owed on the next day_of_month');
});

test('nextDue counts from the anchor, not from today', () => {
  const plan = { ...PLAN, started: '2026-08-03' };
  // Three done, so tranche 4 is owed at anchor month + 3 = November.
  const rows = [buy('2026-08-03'), buy('2026-09-01'), buy('2026-10-01')];
  assert.equal(trancheStatus(plan, rows, at('2026-10-15')).nextDue, '2026-11-01');
});

// ── trancheStatus: overdue ───────────────────────────────────

test('a single skipped month is reported overdue', () => {
  const plan = { ...PLAN, started: '2026-08-03' };
  // Aug and Sep are due by Sep 10; only Aug was recorded.
  const t = trancheStatus(plan, [buy('2026-08-03')], at('2026-09-10'));
  assert.equal(t.expected, 2);
  assert.equal(t.tranchesDone, 1);
  assert.equal(t.overdue, 1);
  assert.equal(t.nextDue, '2026-09-01', 'still points at the date it was owed');
});

test('multiple skipped months accumulate', () => {
  const plan = { ...PLAN, started: '2026-08-03' };
  const t = trancheStatus(plan, [buy('2026-08-03')], at('2026-11-05'));
  assert.equal(t.expected, 4, 'Aug, Sep, Oct, Nov all due by Nov 5');
  assert.equal(t.overdue, 3);
});

test('expected never exceeds tranches_total', () => {
  const plan = { ...PLAN, started: '2026-08-03' };
  // Two years on, the plan is still only 6 tranches deep.
  const t = trancheStatus(plan, [], at('2028-08-03'));
  assert.equal(t.expected, 6);
  assert.equal(t.overdue, 6);
});

test('a tranche due today is due, not overdue', () => {
  const plan = { ...PLAN, started: '2026-08-03' };
  const t = trancheStatus(plan, [buy('2026-08-03')], at('2026-09-01'));
  assert.equal(t.expected, 2);
  assert.equal(t.overdue, 1, 'Sep 1 counts as due the moment the day arrives');
});

// ── trancheStatus: completion + boundaries ───────────────────

test('completed schedule reports complete and a null nextDue', () => {
  const plan = { ...PLAN, started: '2026-08-03' };
  const rows = ['2026-08-03', '2026-09-01', '2026-10-01', '2026-11-01', '2026-12-01', '2027-01-01'].map(d => buy(d));
  const t = trancheStatus(plan, rows, at('2027-02-10'));
  assert.equal(t.complete, true);
  assert.equal(t.overdue, 0);
  assert.equal(t.nextDue, null);
});

test('schedule crosses a year boundary correctly', () => {
  const plan = { ...PLAN, started: '2026-11-01' };
  const rows = [buy('2026-11-01'), buy('2026-12-01')];
  const t = trancheStatus(plan, rows, at('2026-12-15'));
  assert.equal(t.expected, 2);
  assert.equal(t.overdue, 0);
  assert.equal(t.nextDue, '2027-01-01', 'month arithmetic must roll the year');
});

test('started mid-month with a later day_of_month does not fabricate an overdue', () => {
  // Tranche 1 executed Aug 3 but the plan day is the 15th: the first scheduled
  // date hasn't arrived, so expected(0) < done(1). Must not go negative.
  const plan = { ...PLAN, day_of_month: 15, started: '2026-08-03' };
  const t = trancheStatus(plan, [buy('2026-08-03')], at('2026-08-03'));
  assert.equal(t.expected, 0);
  assert.equal(t.overdue, 0, 'overdue is floored at zero');
});

test('day_of_month is clamped to short months rather than overflowing', () => {
  // Feb has no 31st. Overflow would silently push the date into March.
  const anchor = new Date(2027, 0, 31); // 2027-01-31
  assert.equal(fmtLocalDate(scheduledDate(anchor, 1, 31)), '2027-02-28');
});

test('avgEntry is cost-weighted across tranches and includes fees', () => {
  const plan = { ...PLAN, started: '2026-08-01' };
  const rows = [buy('2026-08-01', 100, 280), buy('2026-09-01', 100, 300)];
  rows[0].fees = 20;
  const t = trancheStatus(plan, rows, at('2026-09-02'));
  assert.equal(t.totalQty, 200);
  // (100*280 + 20 + 100*300) / 200
  assert.equal(t.avgEntry, 290.1);
});

test('SELL rows are ignored by the tranche count', () => {
  const plan = { ...PLAN, started: '2026-08-01' };
  const rows = [buy('2026-08-01'), { sleeve: 'BETA_CORE', side: 'SELL', qty: 50, price: 290, fees: 0 }];
  assert.equal(trancheStatus(plan, rows, at('2026-08-05')).tranchesDone, 1);
});

// ── derivePositions ──────────────────────────────────────────

test('deposit then buy leaves the expected cash and cost basis', () => {
  const { positions, cash } = derivePositions([
    { sleeve: 'CASH', instrument: 'CASH', side: 'DEPOSIT', qty: 500000, price: 1, fees: 0 },
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'BUY', qty: 100, price: 280, fees: 20 },
  ]);
  assert.equal(cash, 500000 - (100 * 280 + 20));
  assert.equal(positions.length, 1);
  assert.equal(positions[0].qty, 100);
  assert.equal(positions[0].invested, 28020);
  assert.equal(positions[0].avgCost, 280.2, 'fees are part of the basis');
});

test('BUYs at different prices average into one cost basis', () => {
  const { positions } = derivePositions([
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'BUY', qty: 100, price: 280, fees: 0 },
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'BUY', qty: 100, price: 320, fees: 0 },
  ]);
  assert.equal(positions[0].qty, 200);
  assert.equal(positions[0].avgCost, 300);
});

test('SELL reduces basis at average cost, leaving avgCost unchanged', () => {
  const { positions, cash } = derivePositions([
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'BUY', qty: 100, price: 280, fees: 0 },
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'BUY', qty: 100, price: 320, fees: 0 },
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'SELL', qty: 50, price: 400, fees: 0 },
  ]);
  assert.equal(positions[0].qty, 150);
  assert.equal(positions[0].invested, 45000, '60000 - 50*300');
  assert.equal(positions[0].avgCost, 300, 'selling at a profit must not move the basis');
  assert.equal(cash, -60000 + 20000);
});

test('a fully closed position drops out of positions', () => {
  const { positions, cash } = derivePositions([
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'BUY', qty: 100, price: 280, fees: 0 },
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'SELL', qty: 100, price: 300, fees: 0 },
  ]);
  assert.equal(positions.length, 0);
  assert.equal(cash, 2000, 'the realised gain stays in cash');
});

test('WITHDRAW reduces cash by qty * price plus fees', () => {
  const { cash } = derivePositions([
    { sleeve: 'CASH', instrument: 'CASH', side: 'DEPOSIT', qty: 100000, price: 1, fees: 0 },
    { sleeve: 'CASH', instrument: 'CASH', side: 'WITHDRAW', qty: 30000, price: 1, fees: 15 },
  ]);
  assert.equal(cash, 69985);
});

test('the same instrument in two sleeves is tracked separately', () => {
  const { positions } = derivePositions([
    { sleeve: 'BETA_CORE', instrument: 'NIFTYBEES', side: 'BUY', qty: 100, price: 280, fees: 0 },
    { sleeve: 'FACTOR_EQ', instrument: 'NIFTYBEES', side: 'BUY', qty: 50, price: 280, fees: 0 },
  ]);
  assert.equal(positions.length, 2);
  assert.deepEqual(positions.map(p => p.sleeve).sort(), ['BETA_CORE', 'FACTOR_EQ']);
});

test('an empty ledger derives nothing and zero cash', () => {
  const { positions, cash } = derivePositions([]);
  assert.equal(positions.length, 0);
  assert.equal(cash, 0);
});
