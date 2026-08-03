/**
 * Fund Math — pure, dependency-free.
 *
 * Extracted from fundService.js so position derivation and the tranche schedule
 * can be unit-tested without booting Turso (db.js runs `await initDatabase()` at
 * import time, so anything importing it needs credentials and a network round
 * trip). Same rationale as syncSummary.js and convictionGates.js.
 *
 * Nothing here does I/O. `trancheStatus` takes `now` explicitly so tests are
 * deterministic.
 *
 * Dates are handled in LOCAL parts throughout, never via toISOString() — the
 * fund runs on IST and ISO conversion shifts local midnight back a day.
 */

/** Format a Date as YYYY-MM-DD from local parts. */
export function fmtLocalDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Parse YYYY-MM-DD into a local-midnight Date (new Date(str) would parse as UTC). */
export function parseLocalDate(str) {
  const [y, m, d] = String(str).split('-').map(Number);
  if (!y || !m || !d) return null;
  const dt = new Date(y, m - 1, d);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

/**
 * The scheduled date of tranche `i` (0-based) counted from the anchor month.
 * Day-of-month is clamped to the target month's length so a plan with
 * day_of_month 31 doesn't silently overflow into the following month.
 */
export function scheduledDate(anchor, monthsOffset, dayOfMonth) {
  const y = anchor.getFullYear();
  const m = anchor.getMonth() + monthsOffset;
  const lastDayOfMonth = new Date(y, m + 1, 0).getDate();
  return new Date(y, m, Math.min(dayOfMonth, lastDayOfMonth));
}

/**
 * Derive open positions per sleeve from the ledger.
 * Returns { positions: [{sleeve, instrument, qty, avgCost, invested}], cash }
 * avgCost is average-cost basis (BUYs only); SELLs reduce qty FIFO-agnostically.
 */
export function derivePositions(ledgerRows) {
  const bySleeveInstrument = new Map();
  let cash = 0;

  for (const row of ledgerRows) {
    const qty = Number(row.qty);
    const price = Number(row.price);
    const fees = Number(row.fees) || 0;

    if (row.side === 'DEPOSIT') { cash += qty * price - fees; continue; }
    if (row.side === 'WITHDRAW') { cash -= qty * price + fees; continue; }

    const key = `${row.sleeve}|${row.instrument}`;
    if (!bySleeveInstrument.has(key)) {
      bySleeveInstrument.set(key, { sleeve: row.sleeve, instrument: row.instrument, qty: 0, costTotal: 0 });
    }
    const pos = bySleeveInstrument.get(key);

    if (row.side === 'BUY') {
      pos.qty += qty;
      pos.costTotal += qty * price + fees;
      cash -= qty * price + fees;
    } else { // SELL
      // Reduce cost basis proportionally (average-cost method)
      const avgCost = pos.qty > 0 ? pos.costTotal / pos.qty : 0;
      pos.qty -= qty;
      pos.costTotal -= qty * avgCost;
      cash += qty * price - fees;
    }
  }

  const positions = [...bySleeveInstrument.values()]
    .filter(p => p.qty > 1e-9)
    .map(p => ({
      sleeve: p.sleeve,
      instrument: p.instrument,
      qty: p.qty,
      avgCost: p.costTotal / p.qty,
      invested: p.costTotal,
    }));

  return { positions, cash };
}

/**
 * Tranche status for the BETA_CORE staged entry.
 *
 * The schedule is anchored on `tranchePlan.started` (the ISO date of tranche 1),
 * NOT on the bare calendar. That distinction matters: before this was anchored,
 * a missed month simply vanished — `nextDue` rolled forward to the next
 * occurrence of day_of_month and nothing recorded that a tranche had been
 * skipped. The whole point of the sleeve is level-independent staged entry, so a
 * slipped tranche has to be visible.
 *
 * `tranchesDone` counts BETA_CORE BUY rows. That's deliberately crude: two
 * partial fills recorded on the same day read as two tranches. Recording each
 * tranche as a single row keeps the count honest.
 *
 * @param {object} tranchePlan — { instrument, tranches_total, day_of_month, started }
 * @param {object[]} betaCoreLedgerRows — ledger rows already filtered to BETA_CORE
 * @param {Date} [now] — injectable for tests
 */
export function trancheStatus(tranchePlan, betaCoreLedgerRows, now = new Date()) {
  const buys = betaCoreLedgerRows.filter(r => r.side === 'BUY');
  const done = buys.length;
  const total = tranchePlan.tranches_total;
  const dayOfMonth = tranchePlan.day_of_month;

  const totalQty = buys.reduce((s, r) => s + Number(r.qty), 0);
  const totalCost = buys.reduce((s, r) => s + Number(r.qty) * Number(r.price) + (Number(r.fees) || 0), 0);

  // Compare at day granularity — a tranche due today is due, not overdue.
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const anchor = tranchePlan.started ? parseLocalDate(tranchePlan.started) : null;

  const base = {
    instrument: tranchePlan.instrument,
    tranchesDone: done,
    tranchesTotal: total,
    complete: done >= total,
    avgEntry: totalQty > 0 ? totalCost / totalQty : null,
    totalQty,
    started: tranchePlan.started ?? null,
  };

  if (!anchor) {
    // Schedule not begun. Next due is the upcoming day_of_month; nothing can be
    // overdue because nothing has been committed to yet.
    let next = scheduledDate(today, 0, dayOfMonth);
    if (next < today) next = scheduledDate(today, 1, dayOfMonth);
    return {
      ...base,
      notStarted: true,
      expected: 0,
      overdue: 0,
      nextDue: done >= total ? null : fmtLocalDate(next),
    };
  }

  // How many tranches should have been executed by today, capped at the plan.
  let expected = 0;
  for (let i = 0; i < total; i++) {
    if (scheduledDate(anchor, i, dayOfMonth) <= today) expected++;
  }

  return {
    ...base,
    notStarted: false,
    expected,
    overdue: Math.max(0, expected - done),
    // Points at the date the next tranche is owed, counted from the anchor —
    // so a late tranche keeps showing the date it was actually due.
    nextDue: done >= total ? null : fmtLocalDate(scheduledDate(anchor, done, dayOfMonth)),
  };
}
