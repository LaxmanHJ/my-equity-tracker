/**
 * Paper iron-condor book (SIC-92 education + calibration-validation).
 *
 * Opens a SIMULATED defined-risk monthly structure from the latest collected
 * chain snapshot and settles it automatically at expiry from ^NSEI's close.
 * Zero broker interaction — this is bookkeeping that (a) teaches the
 * mechanics and (b) produces live-observed cycles to validate the September
 * phase-2 calibration against.
 *
 * Structure (the declared phase-2 default, nothing tunable here):
 *   SELL the ~25Δ call and ~25Δ put (collect premium)
 *   BUY  the ~10Δ call and ~10Δ put (wings — cap the disaster)
 *
 * Leg premiums are Black-Scholes prices computed from each strike's
 * COLLECTED implied volatility plus a live NIFTY spot quote — labeled
 * model-priced, not traded quotes. Settlement is exact (intrinsic value at
 * expiry close), so paper P&L error is confined to entry pricing.
 */
import { getToken } from './angelScripMaster.js';
import { fetchQuotes } from './angelOneMarketData.js';
import {
  getLatestChainSnapshot, insertPaperCondor, getOpenPaperCondors,
  settlePaperCondor, getNiftyCloseOnOrAfter, getPaperCondors,
} from '../database/db.js';

const LOT_SIZE = 75;           // NIFTY F&O lot (2025-26 contract spec)
const RISK_FREE = 0.065;
const MONTHS = { JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5, JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11 };

function expiryToIso(expiry) {
  // "07JUL2026" → "2026-07-07"
  const d = expiry.slice(0, 2), m = expiry.slice(2, 5), y = expiry.slice(5);
  return `${y}-${String(MONTHS[m] + 1).padStart(2, '0')}-${d}`;
}

// Abramowitz–Stegun normal CDF approximation (|err| < 7.5e-8)
function normCdf(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989422804014327 * Math.exp(-x * x / 2);
  let p = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return x >= 0 ? 1 - p : p;
}

function bsPrice(type, spot, strike, ivPct, days) {
  const T = Math.max(days, 0.5) / 365;
  const sigma = ivPct / 100;
  const d1 = (Math.log(spot / strike) + (RISK_FREE + sigma * sigma / 2) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  if (type === 'CE') {
    return spot * normCdf(d1) - strike * Math.exp(-RISK_FREE * T) * normCdf(d2);
  }
  return strike * Math.exp(-RISK_FREE * T) * normCdf(-d2) - spot * normCdf(-d1);
}

function strikeAtDelta(rows, type, target) {
  const side = rows.filter(r => r.option_type === type && Number.isFinite(r.delta) && r.iv > 0);
  if (!side.length) return null;
  return side.reduce((a, r) => Math.abs(r.delta - target) < Math.abs(a.delta - target) ? r : a);
}

async function fetchNiftySpot() {
  const scrip = await getToken('NIFTY 50');
  const quotes = await fetchQuotes([scrip.token]);
  const ltp = Number(quotes?.[0]?.ltp);
  if (!Number.isFinite(ltp) || ltp <= 0) throw new Error('NIFTY spot quote unavailable');
  return ltp;
}

export async function openPaperCondor(name = 'NIFTY') {
  const snap = await getLatestChainSnapshot(name);
  if (!snap) return { ok: false, error: 'no chain snapshot collected yet — press Force Sync first' };

  const expiryIso = expiryToIso(snap.expiry);
  const open = await getOpenPaperCondors();
  if (open.some(c => c.name === name && c.expiry_iso === expiryIso)) {
    return { ok: false, error: `paper condor already open for ${snap.expiry}` };
  }

  const sc = strikeAtDelta(snap.rows, 'CE', 0.25);
  const sp = strikeAtDelta(snap.rows, 'PE', -0.25);
  const lc = strikeAtDelta(snap.rows, 'CE', 0.10);
  const lp = strikeAtDelta(snap.rows, 'PE', -0.10);
  if (!sc || !sp || !lc || !lp) return { ok: false, error: 'chain too thin to select strikes' };
  if (lc.strike <= sc.strike || lp.strike >= sp.strike) {
    return { ok: false, error: 'degenerate strikes (wings not beyond shorts) — chain likely stale' };
  }

  const spot = await fetchNiftySpot();
  const dte = Math.max(Math.round((new Date(expiryIso) - new Date()) / 86400000), 1);

  const prem = {
    sc: bsPrice('CE', spot, sc.strike, sc.iv, dte),
    sp: bsPrice('PE', spot, sp.strike, sp.iv, dte),
    lc: bsPrice('CE', spot, lc.strike, lc.iv, dte),
    lp: bsPrice('PE', spot, lp.strike, lp.iv, dte),
  };
  const netCredit = prem.sc + prem.sp - prem.lc - prem.lp;
  // Max loss: widest side's width minus the credit (condor worst case)
  const width = Math.max(lc.strike - sc.strike, sp.strike - lp.strike);
  const maxLossPts = width - netCredit;

  const condor = {
    name, openedDate: new Date().toISOString().slice(0, 10),
    expiry: snap.expiry, expiryIso, dte, spot,
    atmIv: null, lotSize: LOT_SIZE,
    shortCallStrike: sc.strike, shortCallPrem: +prem.sc.toFixed(2),
    shortPutStrike: sp.strike, shortPutPrem: +prem.sp.toFixed(2),
    longCallStrike: lc.strike, longCallPrem: +prem.lc.toFixed(2),
    longPutStrike: lp.strike, longPutPrem: +prem.lp.toFixed(2),
    netCreditPts: +netCredit.toFixed(2),
    creditRupees: +(netCredit * LOT_SIZE).toFixed(0),
    maxLossRupees: +(maxLossPts * LOT_SIZE).toFixed(0),
  };
  await insertPaperCondor(condor);
  return { ok: true, ...condor };
}

function condorPayoffPts(c, settle) {
  const intr = {
    sc: Math.max(0, settle - c.short_call_strike),
    sp: Math.max(0, c.short_put_strike - settle),
    lc: Math.max(0, settle - c.long_call_strike),
    lp: Math.max(0, c.long_put_strike - settle),
  };
  return c.net_credit_pts - intr.sc - intr.sp + intr.lc + intr.lp;
}

/** Settle any OPEN condor whose expiry has passed, using ^NSEI close on
 *  (or first bar after) the expiry date. Called from Force Sync. */
export async function settleDuePaperCondors() {
  const today = new Date().toISOString().slice(0, 10);
  const open = await getOpenPaperCondors();
  const settled = [];
  for (const c of open) {
    if (c.expiry_iso > today) continue;
    const bar = await getNiftyCloseOnOrAfter(c.expiry_iso);
    if (!bar) continue; // expiry close not ingested yet — retry next press
    const pnlPts = condorPayoffPts(c, Number(bar.close));
    const pnl = +(pnlPts * c.lot_size).toFixed(0);
    await settlePaperCondor(c.id, bar.date, Number(bar.close), pnl);
    settled.push({ id: c.id, expiry: c.expiry, settleSpot: Number(bar.close), pnlRupees: pnl });
  }
  return settled;
}

export async function getPaperBook() {
  const rows = await getPaperCondors();
  const totalPnl = rows.filter(r => r.status === 'SETTLED')
    .reduce((s, r) => s + (Number(r.pnl_rupees) || 0), 0);
  return { positions: rows, settledPnlRupees: totalPnl, lotSize: LOT_SIZE };
}
