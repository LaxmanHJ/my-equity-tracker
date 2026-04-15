#!/usr/bin/env node
/**
 * Standalone smoke test for the Angel One OHLC pipeline.
 * Runs: auth → scrip master → daily OHLC for a few symbols.
 *
 * Usage:
 *   node scripts/test-angel-ohlc.mjs
 *   node scripts/test-angel-ohlc.mjs RELIANCE TCS INFY
 *
 * Requires in .env:
 *   ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_CLIENT_PIN,
 *   ANGEL_TOTP_SECRET (base32 seed from 2FA setup)
 */
import 'dotenv/config';
import { getSession } from '../src/services/angelOneAuth.js';
import { loadScripMaster, getToken } from '../src/services/angelScripMaster.js';
import { fetchDailyOHLC } from '../src/services/angelOneHistorical.js';

const SYMBOLS = process.argv.slice(2).length
    ? process.argv.slice(2)
    : ['RELIANCE', 'TCS', 'INFY'];

function daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
}

function section(label) {
    console.log(`\n${'─'.repeat(60)}\n▸ ${label}\n${'─'.repeat(60)}`);
}

function assert(cond, msg) {
    if (!cond) { console.error(`  ✗ FAIL: ${msg}`); process.exit(1); }
    console.log(`  ✓ ${msg}`);
}

(async () => {
    const t0 = Date.now();

    section('1. Environment check');
    for (const k of ['ANGEL_API_KEY', 'ANGEL_CLIENT_ID', 'ANGEL_CLIENT_PIN', 'ANGEL_TOTP_SECRET']) {
        assert(!!process.env[k], `${k} is set`);
    }

    section('2. Authentication (generateSession)');
    const session = await getSession(true);
    assert(!!session.jwtToken, 'JWT token returned');
    assert(!!session.refreshToken, 'refresh token returned');
    assert(!!session.feedToken, 'feed token returned');
    console.log(`  JWT: ${session.jwtToken.slice(0, 24)}...`);
    console.log(`  Expires: ${new Date(session.expiresAt).toISOString()}`);

    section('3. Scrip master download');
    const map = await loadScripMaster(true);
    assert(Object.keys(map).length > 1000, `loaded ${Object.keys(map).length} NSE-EQ symbols`);

    section('4. Symbol token resolution');
    for (const s of SYMBOLS) {
        try {
            const scrip = await getToken(s);
            console.log(`  ${s.padEnd(12)} → token=${scrip.token}  name=${scrip.name}`);
        } catch (e) {
            console.error(`  ${s.padEnd(12)} → NOT FOUND: ${e.message}`);
        }
    }

    section('5. Daily OHLC fetch (last 30 days)');
    const from = daysAgo(30);
    const to = daysAgo(0);
    const results = {};
    for (const s of SYMBOLS) {
        try {
            const t = Date.now();
            const rows = await fetchDailyOHLC(s, from, to);
            results[s] = rows;
            console.log(`  ${s.padEnd(12)} → ${rows.length} candles in ${Date.now() - t}ms`);
            if (rows.length) {
                const last = rows[rows.length - 1];
                console.log(`    latest: ${last.date}  O=${last.open} H=${last.high} L=${last.low} C=${last.close} V=${last.volume}`);
            }
        } catch (e) {
            console.error(`  ${s.padEnd(12)} → ERROR: ${e.response?.data?.message || e.message}`);
        }
    }

    section('6. Sanity checks');
    const firstSym = SYMBOLS.find(s => results[s]?.length);
    if (firstSym) {
        const rows = results[firstSym];
        assert(rows.length >= 15, `${firstSym} has ≥15 trading days of candles`);
        const r = rows[0];
        assert(r.open > 0 && r.close > 0 && r.volume >= 0, 'OHLCV values look sane');
        assert(r.high >= r.low, 'high ≥ low');
        assert(r.high >= r.open && r.high >= r.close, 'high ≥ open & close');
        assert(r.low <= r.open && r.low <= r.close, 'low ≤ open & close');
    } else {
        console.error('  ✗ No symbol returned any candles — cannot validate');
        process.exit(1);
    }

    console.log(`\n✅ ALL CHECKS PASSED in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`);
})().catch(err => {
    console.error('\n❌ FAILED:', err.response?.data || err.message);
    if (err.stack) console.error(err.stack);
    process.exit(1);
});
