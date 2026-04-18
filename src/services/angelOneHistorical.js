import axios from 'axios';
import { getSession, getAuthHeaders, refreshSession, invalidateSession } from './angelOneAuth.js';
import { getToken } from './angelScripMaster.js';

const CANDLE_URL = 'https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData';

const MIN_GAP_MS = 350; // ≤3 rps, small safety margin
let lastCallAt = 0;

// Angel returns these codes/messages when the JWT is invalid or expired.
// AB1010 / AB1004 / AB8050 are documented session errors; the message text
// "Invalid Token" / "session expired" is sometimes returned with a different
// or missing errorcode, so we match both.
const TOKEN_ERROR_CODES = new Set(['AB1010', 'AB1004', 'AB8050']);
const TOKEN_ERROR_MESSAGE_RE = /invalid token|token.*(expired|invalid)|session.*(expired|invalid)|unauthori[sz]ed/i;

function isTokenError(code, message) {
    if (code && TOKEN_ERROR_CODES.has(code)) return true;
    if (message && TOKEN_ERROR_MESSAGE_RE.test(message)) return true;
    return false;
}

async function rateLimit() {
    const wait = lastCallAt + MIN_GAP_MS - Date.now();
    if (wait > 0) await new Promise(r => setTimeout(r, wait));
    lastCallAt = Date.now();
}

/**
 * Format Date → "YYYY-MM-DD HH:MM" (Angel expects local IST-ish format).
 */
function fmt(d) {
    if (typeof d === 'string') {
        if (/^\d{4}-\d{2}-\d{2}$/.test(d)) return `${d} 09:15`;
        if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(d)) return d;
        d = new Date(d);
    }
    if (!(d instanceof Date) || isNaN(d)) throw new Error(`Invalid date: ${d}`);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Call getCandleData with auto-retry on session expiry.
 */
async function callCandle(body, retried = false) {
    await rateLimit();
    const session = await getSession();
    try {
        const res = await axios.post(CANDLE_URL, body, {
            headers: getAuthHeaders(session.jwtToken),
            timeout: 20000
        });
        if (!res.data?.status) {
            const code = res.data?.errorcode;
            const message = res.data?.message;
            if (!retried && isTokenError(code, message)) {
                console.warn(`[angelHist] Token error (code=${code || 'none'}, msg="${message || ''}"), invalidating session and re-authenticating`);
                await invalidateSession();
                await refreshSession();
                return callCandle(body, true);
            }
            throw new Error(`Angel API error: ${message || JSON.stringify(res.data)}`);
        }
        return res.data.data || [];
    } catch (err) {
        const httpStatus = err.response?.status;
        const bodyMsg = err.response?.data?.message;
        const bodyCode = err.response?.data?.errorcode;
        if (!retried && (httpStatus === 401 || httpStatus === 403 || isTokenError(bodyCode, bodyMsg))) {
            console.warn(`[angelHist] Token error on HTTP ${httpStatus || '?'} (code=${bodyCode || 'none'}, msg="${bodyMsg || err.message}"), invalidating session and re-authenticating`);
            await invalidateSession();
            await refreshSession();
            return callCandle(body, true);
        }
        throw err;
    }
}

/**
 * Fetch daily OHLC for a symbol.
 * @param {string} symbol - e.g. "RELIANCE" or "RELIANCE.NS"
 * @param {string|Date} fromDate - inclusive
 * @param {string|Date} toDate - inclusive
 * @returns {Promise<Array<{date, open, high, low, close, volume}>>}
 */
export async function fetchDailyOHLC(symbol, fromDate, toDate) {
    const scrip = await getToken(symbol);
    const body = {
        exchange: scrip.exch_seg, // NSE for equities / NSE-AMXIDX, BSE for BSE-AMXIDX (e.g. SENSEX)
        symboltoken: scrip.token,
        interval: 'ONE_DAY',
        fromdate: fmt(fromDate),
        todate: fmt(toDate)
    };
    const rows = await callCandle(body);
    // Each row: [timestamp, open, high, low, close, volume]
    return rows.map(r => ({
        date: r[0], // ISO with +05:30 offset from Angel
        open: Number(r[1]),
        high: Number(r[2]),
        low: Number(r[3]),
        close: Number(r[4]),
        volume: Number(r[5])
    }));
}

export async function fetchOHLC(symbol, fromDate, toDate, interval = 'ONE_DAY') {
    const scrip = await getToken(symbol);
    const rows = await callCandle({
        exchange: scrip.exch_seg,
        symboltoken: scrip.token,
        interval,
        fromdate: fmt(fromDate),
        todate: fmt(toDate)
    });
    return rows.map(r => ({
        date: r[0],
        open: Number(r[1]),
        high: Number(r[2]),
        low: Number(r[3]),
        close: Number(r[4]),
        volume: Number(r[5])
    }));
}

// CLI: node src/services/angelOneHistorical.js RELIANCE 2026-04-01 2026-04-16
if (process.argv[1]?.includes('angelOneHistorical')) {
    const [sym, from, to] = process.argv.slice(2);
    if (!sym || !from || !to) {
        console.error('Usage: node src/services/angelOneHistorical.js SYMBOL FROM_DATE TO_DATE');
        process.exit(1);
    }
    fetchDailyOHLC(sym, from, to)
        .then(rows => { console.log(`Got ${rows.length} candles`); console.table(rows); })
        .catch(err => { console.error('Error:', err.response?.data || err.message); process.exit(1); });
}

export default { fetchDailyOHLC, fetchOHLC };
