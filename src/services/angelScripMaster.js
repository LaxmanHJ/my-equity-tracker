import axios from 'axios';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CACHE_FILE = path.resolve(__dirname, '../../data/angel_scrip_master.json');
const SCRIP_URL = 'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json';
const MAX_CACHE_AGE_MS = 24 * 60 * 60 * 1000;

let tokenMap = null; // { "RELIANCE": { token: "2885", name, exch_seg, ... } }

function isCacheFresh() {
    try {
        const stat = fs.statSync(CACHE_FILE);
        return (Date.now() - stat.mtimeMs) < MAX_CACHE_AGE_MS;
    } catch { return false; }
}

function buildTokenMap(rows) {
    const map = {};
    for (const row of rows) {
        if (row.exch_seg !== 'NSE') continue;

        // NSE-EQ cash equities, keyed by base symbol (e.g. "RELIANCE")
        if (row.symbol?.endsWith('-EQ')) {
            const baseSymbol = row.symbol.replace(/-EQ$/, '');
            map[baseSymbol] = {
                token: String(row.token),
                name: row.name,
                symbol: row.symbol,
                exch_seg: row.exch_seg,
                instrumenttype: 'EQ',
                lotsize: row.lotsize,
                tick_size: row.tick_size
            };
            continue;
        }

        // AMXIDX indices (Nifty 50, Bank Nifty, India VIX, etc.), keyed by `name`
        // e.g. name "NIFTY" → token 99926000 (Nifty 50)
        if (row.instrumenttype === 'AMXIDX' && row.name) {
            const key = String(row.name).toUpperCase();
            if (!map[key]) {
                map[key] = {
                    token: String(row.token),
                    name: row.name,
                    symbol: row.symbol,
                    exch_seg: row.exch_seg,
                    instrumenttype: 'AMXIDX',
                    lotsize: row.lotsize,
                    tick_size: row.tick_size
                };
            }
        }
    }
    return map;
}

// Aliases for how the rest of the codebase refers to indices.
// Keys are what callers pass (e.g. "^NSEI"); values are scrip-master keys.
const SYMBOL_ALIASES = {
    '^NSEI': 'NIFTY',
    'NSEI': 'NIFTY',
    'NIFTY 50': 'NIFTY',
    'NIFTY50': 'NIFTY',
    '^NSEBANK': 'BANKNIFTY',
    'NIFTY BANK': 'BANKNIFTY',
    'INDIA VIX': 'INDIA VIX',
    'INDIAVIX': 'INDIA VIX'
};

export async function refreshScripMaster() {
    console.log('[scripMaster] Downloading scrip master JSON...');
    const res = await axios.get(SCRIP_URL, { timeout: 60000, responseType: 'json' });
    const rows = Array.isArray(res.data) ? res.data : [];
    if (rows.length === 0) throw new Error('Scrip master returned empty dataset');

    const map = buildTokenMap(rows);
    fs.mkdirSync(path.dirname(CACHE_FILE), { recursive: true });
    fs.writeFileSync(CACHE_FILE, JSON.stringify(map));
    tokenMap = map;
    console.log(`[scripMaster] Cached ${Object.keys(map).length} NSE-EQ symbols`);
    return map;
}

export async function loadScripMaster(force = false) {
    if (tokenMap && !force) return tokenMap;
    if (!force && isCacheFresh()) {
        try {
            tokenMap = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8'));
            console.log(`[scripMaster] Loaded ${Object.keys(tokenMap).length} symbols from cache`);
            return tokenMap;
        } catch (e) {
            console.warn('[scripMaster] Cache read failed, re-downloading:', e.message);
        }
    }
    return refreshScripMaster();
}

/**
 * Resolve a symbol (with or without .NS/.BO/-EQ suffix) to its Angel scrip info.
 * @param {string} symbol e.g. "RELIANCE", "RELIANCE.NS", "RELIANCE-EQ"
 */
export async function getToken(symbol) {
    await loadScripMaster();
    const raw = String(symbol).trim();
    const base = raw
        .replace(/\.(NS|BO|BSE)$/i, '')
        .replace(/-EQ$/i, '')
        .toUpperCase();
    const aliased = SYMBOL_ALIASES[raw.toUpperCase()] || SYMBOL_ALIASES[base] || base;
    const entry = tokenMap[aliased];
    if (!entry) throw new Error(`Symbol not found in scrip master: ${symbol} (base: ${base}, aliased: ${aliased})`);
    return entry;
}

export async function getTokens(symbols) {
    await loadScripMaster();
    const out = {};
    const missing = [];
    for (const s of symbols) {
        try { out[s] = await getToken(s); }
        catch { missing.push(s); }
    }
    if (missing.length) console.warn('[scripMaster] Missing tokens for:', missing.join(', '));
    return { tokens: out, missing };
}

// CLI: node src/services/angelScripMaster.js RELIANCE TCS INFY
if (process.argv[1]?.includes('angelScripMaster')) {
    const syms = process.argv.slice(2);
    if (syms.length === 0) {
        console.error('Usage: node src/services/angelScripMaster.js SYMBOL [SYMBOL...]');
        process.exit(1);
    }
    (async () => {
        for (const s of syms) {
            try { console.log(s, '→', await getToken(s)); }
            catch (e) { console.error(s, '→', e.message); }
        }
    })();
}

export default { loadScripMaster, refreshScripMaster, getToken, getTokens };
