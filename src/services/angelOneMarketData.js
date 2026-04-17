import axios from 'axios';
import { getSession, getAuthHeaders, refreshSession } from './angelOneAuth.js';

const BASE_URL = 'https://apiconnect.angelone.in/rest/secure/angelbroking/marketData/v1';

const PCR_URL = `${BASE_URL}/putCallRatio`;
const OI_BUILDUP_URL = `${BASE_URL}/OIBuildup`;
const GAINERS_LOSERS_URL = `${BASE_URL}/gainersLosers`;

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

// CLI: node src/services/angelOneMarketData.js [pcr|oi|gainers]
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
            const data = await fetchGainersLosers('gainers', 'NEAR');
            console.log(JSON.stringify(data, null, 2));
        }
    })().catch(err => { console.error('Error:', err.response?.data || err.message); process.exit(1); });
}

export default { fetchPCR, fetchOIBuildup, fetchAllOIBuildup, fetchGainersLosers };
