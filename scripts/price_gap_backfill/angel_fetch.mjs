#!/usr/bin/env node
/**
 * Angel One daily-candle fetcher for backfill.py. READ-ONLY: it never touches
 * Turso. Normally backfill.py runs it; you don't call it by hand.
 *
 *   node scripts/price_gap_backfill/angel_fetch.mjs <job.json> <out.json>
 *
 * job.json: { "requests": [{ "symbol", "from", "to" }], "overrides": {...}, "gap_ms": 400 }
 * out.json: { data: {sym: [[date,o,h,l,c,v], ...]}, resolved, unresolved, errors, fetched_at }
 *
 * Deliberate differences from src/services/angelOneHistorical.js:
 *  - fromdate is sent as "YYYY-MM-DD 00:00". The repo helper sends "09:15", and
 *    Angel then drops the from-day's candle, which is stamped 00:00. Verified
 *    2026-09-29: "09-24 00:00" returns 09-24 and 09-25, "09-24 09:15" returns
 *    only 09-25. backfill.py also starts several days early, so a regression here
 *    cannot silently lose a needed session.
 *  - Tokens resolve from the scrip master downloaded into MEMORY each run.
 *    angelScripMaster.getToken() rewrites the tracked data/angel_scrip_master.json
 *    whenever its cache is older than 24h; this tool must not change repo files.
 *  - Explicit overrides (symbol_overrides.json) take precedence over "<SYM>-EQ".
 * Auth is reused unchanged from src/services/angelOneAuth.js; it reads .env from
 * the cwd, so run this from the repo root.
 */
import { readFileSync, writeFileSync } from 'fs';
import axios from 'axios';
import { getSession, getAuthHeaders, refreshSession, invalidateSession } from '../../src/services/angelOneAuth.js';

const SCRIP_URL = 'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json';
const CANDLE_URL = 'https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData';
const CHUNK_DAYS = 1500;           // Angel caps ONE_DAY requests at ~2000 days
const MAX_CONSECUTIVE_FAILS = 15;  // abort the run instead of hammering a broken API
const MAX_NETWORK_FAILS = 3;       // no HTTP response at all (DNS/offline) -> network is down, abort
const INDEX_ALIASES = { '^NSEI': 'NIFTY', '^NSEBANK': 'BANKNIFTY', '^BSESN': 'SENSEX', 'INDIA VIX': 'INDIA VIX' };

const [jobPath, outPath] = process.argv.slice(2);
if (!jobPath || !outPath) { console.error('usage: angel_fetch.mjs <job.json> <out.json>'); process.exit(2); }
const job = JSON.parse(readFileSync(jobPath, 'utf8'));
const GAP_MS = Math.max(350, job.gap_ms || 400);   // <= ~2.5-2.85 requests/s
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── scrip master (in memory) ────────────────────────────────────────────────
async function loadScrip() {
    const res = await axios.get(SCRIP_URL, { timeout: 120000, responseType: 'json' });
    const rows = Array.isArray(res.data) ? res.data : [];
    if (!rows.length) throw new Error('scrip master empty');
    const bySymbol = new Map(), eq = new Map(), idx = new Map(), byName = new Map();
    for (const r of rows) {
        if (r.exch_seg === 'NSE') {
            bySymbol.set(r.symbol, r);
            if (r.symbol?.endsWith('-EQ')) eq.set(r.symbol.slice(0, -3), r);
            if (r.name) { const k = String(r.name).toUpperCase(); if (!byName.has(k)) byName.set(k, []); byName.get(k).push(r); }
        }
        if (r.instrumenttype === 'AMXIDX' && r.name && (r.exch_seg === 'NSE' || r.exch_seg === 'BSE')) {
            const k = String(r.name).toUpperCase();
            if (!idx.has(k)) idx.set(k, r);
        }
    }
    return { bySymbol, eq, idx, byName };
}

function resolve(sym, scrip, overrides) {
    const ov = overrides[sym];
    if (ov) {
        const r = scrip.bySymbol.get(ov.angel_symbol);
        if (!r) return { error: `override ${ov.angel_symbol} not in scrip master` };
        const warn = ov.token_seen && String(r.token) !== String(ov.token_seen)
            ? `token changed ${ov.token_seen} -> ${r.token}` : null;
        return { exchange: r.exch_seg, token: String(r.token), angel_symbol: r.symbol, via: 'override', warn };
    }
    if (INDEX_ALIASES[sym]) {
        const r = scrip.idx.get(INDEX_ALIASES[sym]);
        return r ? { exchange: r.exch_seg, token: String(r.token), angel_symbol: r.symbol, via: 'index' } : { error: 'index not found' };
    }
    const r = scrip.eq.get(sym);
    if (r) return { exchange: 'NSE', token: String(r.token), angel_symbol: r.symbol, via: 'EQ' };
    // Not found: offer same-name instruments (series moves, e.g. HFCL-BE) as hints only.
    const cands = (scrip.byName.get(sym) || []).filter((x) => /-[A-Z0-9]{2}$/.test(x.symbol))
        .map((x) => `${x.symbol} (token ${x.token})`);
    return { error: 'not in scrip master as -EQ', candidates: cands };
}

// ── candles ─────────────────────────────────────────────────────────────────
let last = 0;
async function candles(inst, from, to, attempt = 1) {
    const wait = last + GAP_MS - Date.now();
    if (wait > 0) await sleep(wait);
    last = Date.now();
    const session = await getSession();
    try {
        const res = await axios.post(CANDLE_URL, {
            exchange: inst.exchange, symboltoken: inst.token, interval: 'ONE_DAY',
            fromdate: `${from} 00:00`, todate: `${to} 15:30`,
        }, { headers: getAuthHeaders(session.jwtToken), timeout: 20000 });
        if (!res.data?.status) {
            const msg = `${res.data?.errorcode || ''} ${res.data?.message || ''}`.trim();
            if (attempt === 1 && /AB1010|AB1004|AB8050|token|session|unauthori/i.test(msg)) {
                await invalidateSession(); await refreshSession();
                return candles(inst, from, to, attempt + 1);
            }
            const err = new Error(`angel: ${msg}`); err.api = true; err.retryable = /rate|exceed|too many|try after/i.test(msg); throw err;
        }
        return res.data.data || [];
    } catch (e) {
        const st = e.response?.status;
        if (attempt === 1 && (st === 401 || st === 403)) {
            await invalidateSession(); await refreshSession();
            return candles(inst, from, to, attempt + 1);
        }
        const retryable = e.retryable || !st || st === 429 || st >= 500;
        if (retryable && attempt < 5) {
            const back = 1000 * 2 ** attempt;   // 2s 4s 8s 16s
            console.warn(`  retry ${inst.angel_symbol} in ${back}ms: ${st || ''} ${e.message}`);
            await sleep(back);
            return candles(inst, from, to, attempt + 1);
        }
        throw e;
    }
}

function addDays(iso, n) { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }

const scrip = await loadScrip();
const out = { fetched_at: new Date().toISOString(), data: {}, resolved: {}, unresolved: {}, errors: {} };
let fails = 0, netFails = 0, n = 0;
for (const { symbol, from, to } of job.requests) {
    n++;
    const inst = resolve(symbol, scrip, job.overrides || {});
    if (inst.error) { out.unresolved[symbol] = inst; continue; }
    out.resolved[symbol] = inst;
    if (inst.warn) console.warn(`  ! ${symbol}: ${inst.warn}`);
    try {
        const seen = new Map();
        for (let a = from; a <= to; a = addDays(a, CHUNK_DAYS)) {
            const b = addDays(a, CHUNK_DAYS - 1) < to ? addDays(a, CHUNK_DAYS - 1) : to;
            for (const r of await candles(inst, a, b)) {
                const d = String(r[0]).slice(0, 10);
                if (!seen.has(d)) seen.set(d, [d, +r[1], +r[2], +r[3], +r[4], +r[5]]);
                else out.errors[symbol] = `duplicate candle date ${d} from Angel`;
            }
        }
        out.data[symbol] = [...seen.values()].sort((x, y) => x[0].localeCompare(y[0]));
        fails = 0; netFails = 0;
    } catch (e) {
        out.errors[symbol] = e.message;
        console.warn(`  x ${symbol}: ${e.message}`);
        netFails = (e.response || e.api) ? 0 : netFails + 1;
        if (netFails >= MAX_NETWORK_FAILS) { out.aborted = `network unreachable (${netFails} symbols in a row with no HTTP response) — last: ${e.message}`; break; }
        if (++fails >= MAX_CONSECUTIVE_FAILS) { out.aborted = `${fails} consecutive failures — last: ${e.message}`; break; }
    }
    if (n % 50 === 0) console.log(`  fetched ${n}/${job.requests.length}`);
}
writeFileSync(outPath, JSON.stringify(out));
console.log(`angel_fetch: ${Object.keys(out.data).length} ok, ${Object.keys(out.errors).length} errors, ` +
    `${Object.keys(out.unresolved).length} unresolved${out.aborted ? ' — ABORTED: ' + out.aborted : ''}`);
process.exit(out.aborted ? 3 : 0);
