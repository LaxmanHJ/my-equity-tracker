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
import { derivePositions, trancheStatus } from './fundMath.js';

// Re-exported so existing importers keep working; the implementations live in
// fundMath.js (pure, no Turso) so they can be unit-tested.
export { derivePositions, trancheStatus };

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

/**
 * Ensure defaults exist without clobbering user edits.
 *
 * Missing subfields are merged in too, not just missing top-level keys. Checking
 * only the top level meant that once `tranche_plan` was persisted, any subfield
 * added to DEFAULT_CONFIG afterwards would never reach the stored object — the
 * key existed, so seeding was skipped and the new field stayed undefined forever.
 * User-set values always win; only absent subfields are filled.
 */
export async function ensureFundConfig() {
  const config = await getFundConfig();
  const changedKeys = [];

  for (const [key, defaults] of Object.entries(DEFAULT_CONFIG)) {
    if (!(key in config)) {
      await setFundConfig(key, defaults);
      config[key] = defaults;
      changedKeys.push(key);
      continue;
    }

    // Key exists — backfill any subfields the stored object is missing.
    const stored = config[key];
    if (!isPlainObject(defaults) || !isPlainObject(stored)) continue;

    const missing = Object.entries(defaults).filter(([sub]) => !(sub in stored));
    if (missing.length === 0) continue;

    const merged = { ...Object.fromEntries(missing), ...stored };
    await setFundConfig(key, merged);
    config[key] = merged;
    changedKeys.push(`${key}.{${missing.map(([sub]) => sub).join(',')}}`);
  }

  if (changedKeys.length > 0) console.log(`[Fund] Seeded default config: ${changedKeys.join(', ')}`);
  return config;
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

/** Latest close for an instrument from price_history (null if we don't track it). */
async function latestClose(instrument) {
  const rows = await getPriceHistory(instrument, 30);
  if (!rows?.length) return null;
  const last = rows[rows.length - 1];
  return { price: Number(last.close), date: last.date };
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
  // Tranche status — computed before the risk-flag flush so a slipped tranche
  // is persisted alongside the other alerts.
  const tranche = trancheStatus(config.tranche_plan, ledger.filter(r => r.sleeve === 'BETA_CORE'));
  if (tranche.overdue > 0) {
    riskFlags.push({
      type: 'FUND_TRANCHE_OVERDUE', severity: 'warning', symbol: tranche.instrument,
      message: `${tranche.overdue} BETA_CORE tranche${tranche.overdue > 1 ? 's' : ''} overdue — `
        + `${tranche.tranchesDone}/${tranche.tranchesTotal} recorded, ${tranche.expected} due by now`,
    });
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
