import axios from 'axios';
import { getSession, getAuthHeaders, refreshSession } from './angelOneAuth.js';
import { sectorForSymbol } from '../config/fnoSectorMap.js';
import { loadFnoUniverse } from './angelScripMaster.js';

const BASE_URL = 'https://apiconnect.angelone.in/rest/secure/angelbroking/marketData/v1';

const PCR_URL = `${BASE_URL}/putCallRatio`;
const OI_BUILDUP_URL = `${BASE_URL}/OIBuildup`;
const GAINERS_LOSERS_URL = `${BASE_URL}/gainersLosers`;
const QUOTE_URL = 'https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/';

const QUOTE_BATCH_SIZE = 50;       // Angel hard cap: 50 tokens per quote request
const MOVERS_CACHE_TTL_MS = 60_000; // serve a cached scan for 60s to avoid re-hammering on every page load

const MIN_GAP_MS = 1100;
const RATE_LIMIT_BACKOFF_MS = 1500;
let lastCallAt = 0;

async function rateLimit() {
    const wait = lastCallAt + MIN_GAP_MS - Date.now();
    if (wait > 0) await new Promise(r => setTimeout(r, wait));
    lastCallAt = Date.now();
}

async function apiCall(method, url, body = null, retried = false) {
    await rateLimit();
    const session = await getSession();
    const config = {
        method,
        url,
        headers: getAuthHeaders(session.jwtToken),
        timeout: 20000,
    };
    if (body) config.data = body;

    try {
        const res = await axios(config);
        if (!res.data?.status) {
            const code = res.data?.errorcode;
            if (!retried && (code === 'AB1010' || code === 'AB1004' || code === 'AB8050')) {
                await refreshSession();
                return apiCall(method, url, body, true);
            }
            throw new Error(`Angel API error [${code}]: ${res.data?.message || JSON.stringify(res.data)}`);
        }
        return res.data.data;
    } catch (err) {
        if (!retried && err.response?.status === 401) {
            await refreshSession();
            return apiCall(method, url, body, true);
        }
        if (!retried && (err.response?.status === 403 || err.response?.status === 429)) {
            await new Promise(r => setTimeout(r, RATE_LIMIT_BACKOFF_MS));
            return apiCall(method, url, body, true);
        }
        throw err;
    }
}

/**
 * Fetch Put-Call Ratio. GET, no params.
 * Returns PCR data for NIFTY/BANKNIFTY (exact shape TBD — first live call reveals it).
 */
export async function fetchPCR() {
    const data = await apiCall('get', PCR_URL);
    return data;
}

/**
 * Fetch OI Buildup for a given category and expiry.
 * @param {'Long Built Up'|'Short Built Up'|'Short Covering'|'Long Unwinding'} datatype
 * @param {'NEAR'|'NEXT'|'FAR'} expirytype - NEAR=current month, NEXT=next month
 */
export async function fetchOIBuildup(datatype = 'Long Built Up', expirytype = 'NEAR') {
    const data = await apiCall('post', OI_BUILDUP_URL, { expirytype, datatype });
    return data;
}

/**
 * Fetch all 4 OI buildup categories in one go.
 */
export async function fetchAllOIBuildup(expirytype = 'NEAR') {
    const categories = ['Long Built Up', 'Short Built Up', 'Short Covering', 'Long Unwinding'];
    const results = {};
    for (const cat of categories) {
        try {
            results[cat] = await fetchOIBuildup(cat, expirytype);
        } catch (e) {
            console.warn(`[marketData] OI Buildup "${cat}" failed:`, e.message);
            results[cat] = null;
        }
    }
    return results;
}

/**
 * Fetch top gainers or losers in derivatives segment.
 * @param {'gainers'|'losers'} datatype
 * @param {'NEAR'|'NEXT'|'FAR'} expirytype
 */
export async function fetchGainersLosers(datatype = 'gainers', expirytype = 'NEAR') {
    const data = await apiCall('post', GAINERS_LOSERS_URL, { datatype, expirytype });
    return data;
}

/**
 * Fetch full-mode quotes for a list of NSE tokens (max 50 per request).
 * @param {string[]} tokens NSE scrip tokens
 * @returns {Promise<Array>} the `fetched` rows (tradingSymbol, ltp, netChange, percentChange, …)
 */
export async function fetchQuotes(tokens) {
    if (!tokens?.length) return [];
    const data = await apiCall('post', QUOTE_URL, {
        mode: 'FULL',
        exchangeTokens: { NSE: tokens },
    });
    return data?.fetched || [];
}

let moversCache = { at: 0, limit: 0, data: null };

/**
 * Daily biggest winners and losers in the NSE cash (equity) segment.
 *
 * Angel has no equity movers endpoint, so we compute them: scan the F&O stock
 * universe (~210 liquid underlyings), pull each name's cash quote in batches of
 * 50, then rank by intraday % change. Each row is tagged with its sector.
 * Results are cached for 60s so repeated page loads don't re-run the scan.
 *
 * @param {number} limit max rows per side (default 5)
 * @returns {Promise<{gainers: Array, losers: Array, asOf: string, segment: string, universe: number}>}
 */
export async function getEquityMovers(limit = 5) {
    const now = Date.now();
    if (moversCache.data && moversCache.limit === limit && (now - moversCache.at) < MOVERS_CACHE_TTL_MS) {
        return moversCache.data;
    }

    const universe = await loadFnoUniverse();
    const tokens = universe.map(u => u.token);

    const quotes = [];
    for (let i = 0; i < tokens.length; i += QUOTE_BATCH_SIZE) {
        const batch = tokens.slice(i, i + QUOTE_BATCH_SIZE);
        try {
            quotes.push(...await fetchQuotes(batch));
        } catch (e) {
            console.warn(`[marketData] quote batch ${i / QUOTE_BATCH_SIZE} failed:`, e.message);
        }
    }

    const rows = quotes
        .filter(q => Number.isFinite(q.percentChange))
        .map(q => {
            const symbol = String(q.tradingSymbol || '').replace(/-EQ$/i, '');
            return {
                symbol,
                sector: sectorForSymbol(symbol),
                ltp: q.ltp,
                netChange: q.netChange,
                percentChange: q.percentChange,
            };
        });

    const gainers = [...rows].sort((a, b) => b.percentChange - a.percentChange).slice(0, limit);
    const losers = [...rows].sort((a, b) => a.percentChange - b.percentChange).slice(0, limit);

    const result = {
        gainers,
        losers,
        asOf: new Date().toISOString(),
        segment: 'NSE Equity',
        universe: rows.length,
    };
    moversCache = { at: now, limit, data: result };
    return result;
}

// CLI: node src/services/angelOneMarketData.js [pcr|oi|gainers|movers]
if (process.argv[1]?.includes('angelOneMarketData')) {
    const cmd = process.argv[2] || 'pcr';
    (async () => {
        if (cmd === 'pcr') {
            console.log('─── PCR ───');
            const data = await fetchPCR();
            console.log(JSON.stringify(data, null, 2));
        } else if (cmd === 'oi') {
            console.log('─── OI Buildup (all categories, NEAR) ───');
            const data = await fetchAllOIBuildup();
            for (const [cat, rows] of Object.entries(data)) {
                console.log(`\n${cat}: ${rows ? (Array.isArray(rows) ? rows.length + ' entries' : typeof rows) : 'FAILED'}`);
                if (Array.isArray(rows)) console.log(JSON.stringify(rows.slice(0, 3), null, 2));
                else if (rows) console.log(JSON.stringify(rows, null, 2).slice(0, 500));
            }
        } else if (cmd === 'gainers') {
            console.log('─── Top Gainers (NEAR) ───');
            const data = await fetchGainersLosers('PercPriceGainers', 'NEAR');
            console.log(JSON.stringify(data, null, 2));
        } else if (cmd === 'movers') {
            console.log('─── Daily Equity Movers (NSE cash, top 5) ───');
            console.log(JSON.stringify(await getEquityMovers(5), null, 2));
        }
    })().catch(err => { console.error('Error:', err.response?.data || err.message); process.exit(1); });
}

export default { fetchPCR, fetchOIBuildup, fetchAllOIBuildup, fetchGainersLosers, fetchQuotes, getEquityMovers };
