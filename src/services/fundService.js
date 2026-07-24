/**
 * Fund Service — the new multi-sleeve book (2026-07-25).
 *
 * The fund_ledger table is the single source of truth; everything here is
 * derived from it on demand. No broker orders ever originate from this module —
 * the user executes manually and records fills.
 *
 * Sleeves (see wiki/concepts/fund_design.md):
 *   BETA_CORE  — broad index fund, staged entry (live)
 *   FACTOR_EQ  — monthly 12-1 momentum book (gated on Study A)
 *   VRP        — SIC-92 defined-risk premium selling (gated on 60-day collection)
 *   LS_FUT     — market-neutral stock-futures pilot (gated on Study C)
 *   CASH       — uninvested capital (DEPOSIT/WITHDRAW rows)
 *
 * Design constraint inherited from the 2026-06 research program: no strategy
 * with holding period under ~1 month. The regime dial appears here as a
 * sizing overlay only — never as entry timing (regime timing measured at
 * Jensen α −1.2%/yr; only drawdown control survived).
 */
import {
  getFundLedger,
  getFundConfig,
  setFundConfig,
  saveFundNav,
  getPriceHistory,
  saveRiskAlerts,
  getIVCollectionStatus,
} from '../database/db.js';
import { readFile } from 'fs/promises';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { runRiskChecks } from '../risk/riskManager.js';
import { createEodPriceProvider } from '../risk/priceProvider.js';
import { fundRiskLimits } from '../config/riskLimits.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STUDY_A_PATH = join(__dirname, '../../data/monthly_momentum_study.json');

export const SLEEVES = ['BETA_CORE', 'FACTOR_EQ', 'VRP', 'LS_FUT', 'CASH'];

export const DEFAULT_CONFIG = {
  sleeve_budgets: {          // % of total capital
    BETA_CORE: 55,
    FACTOR_EQ: 25,
    VRP: 12,
    LS_FUT: 8,
  },
  tranche_plan: {
    // Staged entry for BETA_CORE: N equal monthly tranches, fixed day-of-month,
    // level-independent by design (regime study: no timing skill exists).
    instrument: 'NIFTYBEES',   // default broad-index ETF; user can change
    tranches_total: 6,
    day_of_month: 1,
    started: null,             // ISO date of first tranche, null until begun
  },
};

/** Ensure defaults exist without clobbering user edits. */
export async function ensureFundConfig() {
  const config = await getFundConfig();
  let changed = false;
  for (const [key, value] of Object.entries(DEFAULT_CONFIG)) {
    if (!(key in config)) {
      await setFundConfig(key, value);
      config[key] = value;
      changed = true;
    }
  }
  if (changed) console.log('[Fund] Seeded default config');
  return config;
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

/** Latest close for an instrument from price_history (null if we don't track it). */
async function latestClose(instrument) {
  const rows = await getPriceHistory(instrument, 30);
  if (!rows?.length) return null;
  const last = rows[rows.length - 1];
  return { price: Number(last.close), date: last.date };
}

/**
 * Tranche status for the BETA_CORE staged entry.
 */
export function trancheStatus(tranchePlan, betaCoreLedgerRows) {
  const buys = betaCoreLedgerRows.filter(r => r.side === 'BUY');
  const done = buys.length;
  const total = tranchePlan.tranches_total;

  // Next due date: day_of_month of the current or next month.
  // Formatted from local parts — toISOString() would shift IST midnight back a day.
  const now = new Date();
  let next = new Date(now.getFullYear(), now.getMonth(), tranchePlan.day_of_month);
  if (next <= now) next = new Date(now.getFullYear(), now.getMonth() + 1, tranchePlan.day_of_month);
  const nextDueStr = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}-${String(next.getDate()).padStart(2, '0')}`;

  const totalQty = buys.reduce((s, r) => s + Number(r.qty), 0);
  const totalCost = buys.reduce((s, r) => s + Number(r.qty) * Number(r.price) + (Number(r.fees) || 0), 0);

  return {
    instrument: tranchePlan.instrument,
    tranchesDone: done,
    tranchesTotal: total,
    complete: done >= total,
    nextDue: done >= total ? null : nextDueStr,
    avgEntry: totalQty > 0 ? totalCost / totalQty : null,
    totalQty,
  };
}

/**
 * Study A gate — read the pre-registered study's verdict from its results file.
 * Ran 2026-07-25: FAIL (LS net Sharpe 0.42 < 0.6, PSR 0.88 < 0.95). Per the
 * declared decision rule, FACTOR_EQ is implemented via the Nifty200 Momentum 30
 * index fund rather than a self-managed book. One run — no re-runs, no variants.
 */
async function studyAGate() {
  const base = { label: 'Study A — monthly 12-1 momentum' };
  try {
    const study = JSON.parse(await readFile(STUDY_A_PATH, 'utf8'));
    return {
      ...base,
      status: study.gates?.verdict ?? 'NOT_RUN',
      detail: {
        lsNetSharpe: study.ls_decile?.net_ann_sharpe,
        psr: study.ls_decile?.psr_net,
        loNetExcessAnnPct: study.long_only_top15?.net_excess_ann_pct,
        computedAt: study.computed_at,
        consequence: study.gates?.verdict === 'PASS'
          ? 'Sleeve 2 = self-managed top-15 momentum book'
          : 'Sleeve 2 = Nifty200 Momentum 30 index fund',
      },
    };
  } catch {
    return { ...base, status: 'NOT_RUN' };
  }
}

/**
 * Full fund overview: positions marked to market, sleeve allocation vs budget,
 * risk flags, tranche status, and gate statuses. Also snapshots NAV once per day.
 */
export async function getFundOverview() {
  const [config, ledger] = await Promise.all([ensureFundConfig(), getFundLedger()]);
  const { positions, cash } = derivePositions(ledger);

  // Mark to market
  const marked = [];
  for (const pos of positions) {
    const px = await latestClose(pos.instrument);
    const marketValue = px ? pos.qty * px.price : pos.invested; // fall back to cost
    marked.push({
      ...pos,
      lastPrice: px?.price ?? null,
      priceDate: px?.date ?? null,
      marketValue,
      pnl: px ? marketValue - pos.invested : null,
      pnlPct: px && pos.invested > 0 ? (100 * (marketValue - pos.invested)) / pos.invested : null,
    });
  }

  const investedValue = marked.reduce((s, p) => s + p.marketValue, 0);
  const nav = investedValue + cash;

  // Sleeve allocation vs budget
  const sleeveValues = {};
  for (const sleeve of SLEEVES) sleeveValues[sleeve] = 0;
  for (const pos of marked) sleeveValues[pos.sleeve] += pos.marketValue;
  sleeveValues.CASH = cash;

  const sleeves = Object.entries(config.sleeve_budgets).map(([sleeve, budgetPct]) => ({
    sleeve,
    budgetPct,
    actualPct: nav > 0 ? (100 * sleeveValues[sleeve]) / nav : 0,
    marketValue: sleeveValues[sleeve],
    overBudget: nav > 0 && (100 * sleeveValues[sleeve]) / nav > budgetPct + fundRiskLimits.sleeveBudgetTolerancePct,
  }));

  // Risk checks — reuse the existing risk manager (circuit breaker, sector
  // concentration, stop-loss) plus fund-specific weight/drawdown limits.
  const riskFlags = [];
  for (const pos of marked) {
    const weightPct = nav > 0 ? (100 * pos.marketValue) / nav : 0;
    // Position caps guard single-name concentration (the TATAELXSI failure
    // mode). A broad-index instrument in BETA_CORE is already diversified —
    // its size is governed by the sleeve budget instead.
    if (pos.sleeve === 'BETA_CORE') {
      // budget check below covers it
    } else if (weightPct > fundRiskLimits.hardPositionPct) {
      riskFlags.push({
        type: 'FUND_POSITION_HARD_CAP', severity: 'critical', symbol: pos.instrument,
        message: `${pos.instrument} is ${weightPct.toFixed(1)}% of fund NAV (hard cap ${fundRiskLimits.hardPositionPct}%)`,
      });
    } else if (weightPct > fundRiskLimits.maxPositionPct) {
      riskFlags.push({
        type: 'FUND_POSITION_CAP', severity: 'warning', symbol: pos.instrument,
        message: `${pos.instrument} is ${weightPct.toFixed(1)}% of fund NAV (cap ${fundRiskLimits.maxPositionPct}%)`,
      });
    }
    if (pos.pnlPct != null && pos.pnlPct < -fundRiskLimits.maxDrawdownAlertPct) {
      riskFlags.push({
        type: 'FUND_POSITION_DRAWDOWN', severity: 'warning', symbol: pos.instrument,
        message: `${pos.instrument} is ${pos.pnlPct.toFixed(1)}% from entry (alert at -${fundRiskLimits.maxDrawdownAlertPct}%)`,
      });
    }
  }
  for (const s of sleeves) {
    if (s.overBudget) {
      riskFlags.push({
        type: 'FUND_SLEEVE_BUDGET', severity: 'warning', symbol: s.sleeve,
        message: `${s.sleeve} at ${s.actualPct.toFixed(1)}% of NAV vs ${s.budgetPct}% budget`,
      });
    }
  }
  // Stop-loss / circuit-breaker / sector checks on equity positions we have bars for
  let riskCheckResult = null;
  if (marked.length > 0) {
    try {
      riskCheckResult = await runRiskChecks(
        marked.map(p => ({ symbol: p.instrument, quantity: p.qty, avgPrice: p.avgCost, sector: 'Fund' })),
        createEodPriceProvider(60),
      );
    } catch (e) {
      console.warn('[Fund] risk checks failed:', e.message);
    }
  }
  if (riskFlags.length > 0) {
    try { await saveRiskAlerts(riskFlags); }
    catch (e) { console.warn('[Fund] saving risk alerts failed:', e.message); }
  }

  // Gates
  const iv = await getIVCollectionStatus('NIFTY');
  const gates = {
    vrp: {
      label: 'SIC-92 VRP collection',
      daysCollected: iv.nDays,
      daysRequired: 60,
      lastDate: iv.lastDate,
      rule: 'At 60 days: price short-strangle+wings at collected quotes; ship iff capped net Sharpe > 0.5',
    },
    studyA: await studyAGate(),
    studyB: { label: 'Study B — event-driven overlay', status: 'NOT_RUN' },
    studyC: { label: 'Study C — market-neutral LS futures', status: 'NOT_RUN' },
  };

  // Tranche status
  const tranche = trancheStatus(config.tranche_plan, ledger.filter(r => r.sleeve === 'BETA_CORE'));

  // NAV snapshot (idempotent per day)
  const today = new Date().toISOString().slice(0, 10);
  try { await saveFundNav(today, sleeveValues); }
  catch (e) { console.warn('[Fund] NAV snapshot failed:', e.message); }

  // Sector concentration is excluded: fund positions carry a placeholder
  // sector ('Fund'), so that check would always report 100% — noise.
  // Real sector tagging arrives with the FACTOR_EQ sleeve (Study A pass).
  const stopLossAlerts = (riskCheckResult?.alerts ?? [])
    .filter(a => a.type !== 'sector_concentration');

  return {
    nav,
    cash,
    investedValue,
    positions: marked,
    sleeves,
    riskFlags,
    stopLossAlerts,
    tranche,
    gates,
    config,
    asOf: new Date().toISOString(),
  };
}
