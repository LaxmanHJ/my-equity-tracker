import axios from 'axios';
import { getSession, getAuthHeaders, refreshSession } from './angelOneAuth.js';
import { getToken } from './angelScripMaster.js';

const CANDLE_URL = 'https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData';

const MIN_GAP_MS = 350; // ≤3 rps, small safety margin
let lastCallAt = 0;

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
            if (!retried && (code === 'AB1010' || code === 'AB1004' || code === 'AB8050')) {
                console.warn('[angelHist] Session-ish error, refreshing and retrying:', code);
                await refreshSession();
                return callCandle(body, true);
            }
            throw new Error(`Angel API error: ${res.data?.message || JSON.stringify(res.data)}`);
        }
        return res.data.data || [];
    } catch (err) {
        if (!retried && err.response?.status === 401) {
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
        exchange: 'NSE',
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
        exchange: 'NSE',
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
