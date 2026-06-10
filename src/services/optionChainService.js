/**
 * NIFTY option-chain IV collector (SIC-92 phase 2 data feed).
 *
 * The VRP phase-1 study proved the premium exists (t=5.84, 15y) but the
 * tradeable phase needs DEFINED-RISK structures, whose cap cost can only be
 * priced from real chains. Historical chains aren't freely available, so we
 * accumulate daily snapshots forward via Angel's optionGreek endpoint —
 * every Force Sync press stores the ~30-day NIFTY chain + an ATM-IV/skew
 * summary row.
 *
 * Expiry discovery: NSE has shuffled F&O expiry weekdays repeatedly (2025
 * SEBI standardization), so nothing is hardcoded — candidate dates in the
 * [20, 45]-DTE window are tried in |DTE−30| order (Thu/Tue first, the
 * historical conventions) until Angel returns a non-empty chain.
 *
 * ATM/skew are delta-based (no spot quote needed):
 *   atm_iv    = mean of call-IV at Δ≈+0.50 and put-IV at Δ≈−0.50
 *   rr25_skew = put-IV at Δ≈−0.25 − call-IV at Δ≈+0.25  (risk reversal)
 */
import axios from 'axios';
import { getSession, getAuthHeaders, refreshSession } from './angelOneAuth.js';
import { saveOptionChainSnapshot, saveIVDaily } from '../database/db.js';

const GREEK_URL = 'https://apiconnect.angelone.in/rest/secure/angelbroking/marketData/v1/optionGreek';
const MIN_GAP_MS = 1100;
let lastCallAt = 0;

async function rateLimit() {
    const wait = lastCallAt + MIN_GAP_MS - Date.now();
    if (wait > 0) await new Promise(r => setTimeout(r, wait));
    lastCallAt = Date.now();
}

async function apiCall(body, retried = false) {
    await rateLimit();
    const session = await getSession();
    try {
        const res = await axios.post(GREEK_URL, body, {
            headers: getAuthHeaders(session.jwtToken),
            timeout: 20000,
        });
        if (!res.data?.status) {
            const code = res.data?.errorcode;
            if (!retried && (code === 'AB1010' || code === 'AB1004' || code === 'AB8050')) {
                await refreshSession();
                return apiCall(body, true);
            }
            // "No Data Available" = wrong expiry candidate (or after-hours) —
            // an empty chain, not an error: the discovery loop must continue.
            if (code === 'AB9019' || /no data/i.test(res.data?.message || '')) {
                return [];
            }
            throw new Error(`Angel optionGreek [${code}]: ${res.data?.message}`);
        }
        return res.data.data || [];
    } catch (err) {
        if (!retried && err.response?.status === 401) {
            await refreshSession();
            return apiCall(body, true);
        }
        throw err;
    }
}

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

function angelExpiryFormat(d) {
    return `${String(d.getDate()).padStart(2, '0')}${MONTHS[d.getMonth()]}${d.getFullYear()}`;
}

/** Candidate expiries in the [20,45]-DTE window, |DTE-30| order, Thu/Tue first. */
function candidateExpiries() {
    const today = new Date();
    const candidates = [];
    for (let dte = 20; dte <= 45; dte++) {
        const d = new Date(today);
        d.setDate(d.getDate() + dte);
        const dow = d.getDay(); // 0 Sun .. 6 Sat
        if (dow === 0 || dow === 6) continue;
        const dayPriority = (dow === 4 || dow === 2) ? 0 : 1; // Thu/Tue first
        candidates.push({ date: d, dte, sort: dayPriority * 100 + Math.abs(dte - 30) });
    }
    return candidates.sort((a, b) => a.sort - b.sort);
}

function ivAtDelta(rows, type, targetDelta) {
    const side = rows.filter(r => r.optionType === type && Number.isFinite(r.delta) && r.iv > 0);
    if (side.length === 0) return null;
    const best = side.reduce((acc, r) =>
        Math.abs(r.delta - targetDelta) < Math.abs(acc.delta - targetDelta) ? r : acc);
    // Reject if the nearest delta is far off-target (illiquid / stale chain)
    return Math.abs(best.delta - targetDelta) <= 0.15 ? best.iv : null;
}

export async function collectIVSnapshot(name = 'NIFTY') {
    let chain = null;
    let expiry = null;
    let dte = null;
    const tried = [];
    for (const cand of candidateExpiries()) {
        const expiryStr = angelExpiryFormat(cand.date);
        tried.push(expiryStr);
        const data = await apiCall({ name, expirydate: expiryStr });
        if (Array.isArray(data) && data.length > 0) {
            chain = data;
            expiry = expiryStr;
            dte = cand.dte;
            break;
        }
        if (tried.length >= 10) break; // bound the discovery cost per press
    }
    if (!chain) {
        return { ok: false, error: `no chain found (tried ${tried.length} expiries)` };
    }

    const rows = chain.map(r => ({
        strike: Number(r.strikePrice),
        optionType: String(r.optionType || '').toUpperCase(),
        iv: Number(r.impliedVolatility),
        delta: Number(r.delta),
        gamma: Number(r.gamma),
        theta: Number(r.theta),
        vega: Number(r.vega),
        volume: Number(r.tradeVolume) || 0,
    })).filter(r => Number.isFinite(r.strike) && Number.isFinite(r.iv) && r.iv > 0);

    const today = new Date().toISOString().slice(0, 10);
    const callAtm = ivAtDelta(rows, 'CE', 0.5);
    const putAtm = ivAtDelta(rows, 'PE', -0.5);
    const atmIv = (callAtm != null && putAtm != null) ? (callAtm + putAtm) / 2
        : (callAtm ?? putAtm);
    const put25 = ivAtDelta(rows, 'PE', -0.25);
    const call25 = ivAtDelta(rows, 'CE', 0.25);
    const rr25 = (put25 != null && call25 != null) ? put25 - call25 : null;

    await saveOptionChainSnapshot(today, name, expiry, rows);
    await saveIVDaily({
        date: today, name, expiry, dte,
        atmIv: atmIv != null ? Number(atmIv.toFixed(2)) : null,
        rr25Skew: rr25 != null ? Number(rr25.toFixed(2)) : null,
        nStrikes: rows.length,
    });

    return { ok: true, expiry, dte, atmIv, rr25Skew: rr25, nStrikes: rows.length };
}
