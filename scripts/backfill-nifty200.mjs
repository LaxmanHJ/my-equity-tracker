#!/usr/bin/env node
/**
 * Backfill 15 years of daily OHLCV for Nifty 200 constituents from Angel One.
 *
 * Reads the symbol list from data/nifty200_constituents.json (pinned, generated
 * once from NSE's ind_nifty200list.csv). Idempotent: uses INSERT OR REPLACE on
 * (symbol, date) UNIQUE — re-runs are safe and only re-fetch symbols that fail
 * the freshness check.
 *
 * Usage:
 *   node scripts/backfill-nifty200.mjs --dry-run                  # No DB writes
 *   node scripts/backfill-nifty200.mjs --dry-run --limit 10       # First 10 symbols only
 *   node scripts/backfill-nifty200.mjs --start-from RELIANCE      # Resume from a symbol
 *   node scripts/backfill-nifty200.mjs --force                    # Disable skip-if-fresh
 *   node scripts/backfill-nifty200.mjs                            # Full backfill
 *
 * Skip-if-fresh: a symbol is skipped if Turso already has >= MIN_FRESH_BARS
 * (1500) AND last_date is within MAX_STALE_DAYS (7) of today. Existing 14
 * portfolio stocks satisfy this, so they aren't re-fetched.
 */
import 'dotenv/config';
import fs from 'fs';
import { createClient } from '@libsql/client';
import { fetchDailyOHLC } from '../src/services/angelOneHistorical.js';
import { getTokens } from '../src/services/angelScripMaster.js';

const db = createClient({
    url: process.env.TURSO_DATABASE_URL,
    authToken: process.env.TURSO_AUTH_TOKEN,
});

const CONSTITUENTS_PATH = './data/nifty200_constituents.json';
const START_DATE = '2011-01-01';
const CHUNK_DAYS = 2000;
const MIN_FRESH_BARS = 1500;
const MAX_STALE_DAYS = 7;
const BATCH_SIZE = 500;

const args = process.argv.slice(2);
const DRY_RUN = args.includes('--dry-run');
const FORCE = args.includes('--force');
const limitIdx = args.indexOf('--limit');
const LIMIT = limitIdx !== -1 ? parseInt(args[limitIdx + 1], 10) : null;
const startIdx = args.indexOf('--start-from');
const START_FROM = startIdx !== -1 ? args[startIdx + 1]?.toUpperCase() : null;

function addDays(dateStr, n) {
    const d = new Date(dateStr + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
}

function todayStr() {
    return new Date().toISOString().slice(0, 10);
}

function buildChunks(from, to) {
    const chunks = [];
    let cursor = from;
    while (cursor < to) {
        const chunkEnd = addDays(cursor, CHUNK_DAYS);
        chunks.push({ from: cursor, to: chunkEnd < to ? chunkEnd : to });
        cursor = addDays(chunkEnd, 1);
    }
    return chunks;
}

function normalizeDate(d) {
    return typeof d === 'string' ? d.slice(0, 10) : d;
}

function daysBetween(a, b) {
    return Math.floor((new Date(b) - new Date(a)) / 86400000);
}

async function getFreshness(symbol) {
    const r = await db.execute({
        sql: `SELECT COUNT(*) AS bars, MAX(date) AS last_date
              FROM price_history WHERE symbol = ?`,
        args: [symbol],
    });
    const row = r.rows[0];
    return { bars: Number(row.bars || 0), lastDate: row.last_date || null };
}

async function batchUpsert(symbol, candles) {
    for (let i = 0; i < candles.length; i += BATCH_SIZE) {
        const slice = candles.slice(i, i + BATCH_SIZE);
        const stmts = slice.map(c => ({
            sql: `INSERT OR REPLACE INTO price_history
                  (symbol, date, open, high, low, close, volume, adj_close)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
            args: [symbol, c.date, c.open, c.high, c.low, c.close, c.volume, null],
        }));
        await db.batch(stmts, 'write');
    }
}

(async () => {
    // Load constituent list and drop placeholder DUMMYVEDL* names
    const data = JSON.parse(fs.readFileSync(CONSTITUENTS_PATH, 'utf8'));
    let symbols = data.constituents
        .map(c => c.symbol)
        .filter(s => !s.startsWith('DUMMY'));

    // Pre-flight: scrip-master coverage. Drop unresolvable names (already
    // verified to be just the dummies, but check again every run in case the
    // CSV is updated).
    const { tokens, missing } = await getTokens(symbols);
    if (missing.length) {
        console.warn(`[preflight] dropping ${missing.length} unresolvable symbols:`, missing.join(', '));
        symbols = symbols.filter(s => tokens[s]);
    }

    if (START_FROM) {
        const i = symbols.indexOf(START_FROM);
        if (i === -1) throw new Error(`--start-from symbol not in list: ${START_FROM}`);
        symbols = symbols.slice(i);
    }
    if (LIMIT) symbols = symbols.slice(0, LIMIT);

    const today = todayStr();
    const chunks = buildChunks(START_DATE, today);

    console.log(`\n${'═'.repeat(70)}`);
    console.log(`  Nifty 200 Backfill   ${DRY_RUN ? '(DRY RUN — no DB writes)' : '⚠️  LIVE'}`);
    console.log(`  Symbols: ${symbols.length}  |  Range: ${START_DATE} → ${today}  |  Chunks/sym: ${chunks.length}`);
    console.log(`  Skip-if-fresh: ${FORCE ? 'DISABLED (--force)' : `bars>=${MIN_FRESH_BARS} AND last_date within ${MAX_STALE_DAYS}d`}`);
    console.log(`${'═'.repeat(70)}\n`);

    const tStart = Date.now();
    const report = [];
    let totalRows = 0;
    let skipped = 0;

    for (let idx = 0; idx < symbols.length; idx++) {
        const sym = symbols[idx];
        const t0 = Date.now();
        const prefix = `[${String(idx + 1).padStart(3)}/${symbols.length}]`;

        // Skip-if-fresh check
        if (!FORCE) {
            try {
                const f = await getFreshness(sym);
                if (f.bars >= MIN_FRESH_BARS && f.lastDate &&
                    daysBetween(f.lastDate, today) <= MAX_STALE_DAYS) {
                    console.log(`${prefix} ⏭  ${sym.padEnd(12)} — fresh (${f.bars} bars, last ${f.lastDate})`);
                    skipped++;
                    report.push({ symbol: sym, rows: f.bars, firstDate: '(skipped)', lastDate: f.lastDate, ms: 0, action: 'skip' });
                    continue;
                }
            } catch (e) {
                console.warn(`${prefix} freshness check failed for ${sym}: ${e.message}`);
            }
        }

        let allCandles = [];
        let firstError = null;
        for (const chunk of chunks) {
            try {
                const rows = await fetchDailyOHLC(sym, chunk.from, chunk.to);
                allCandles.push(...rows);
            } catch (e) {
                if (e.message?.includes('Symbol not found')) {
                    console.warn(`${prefix} ⚠ ${sym}: not in scrip master — skipping`);
                    firstError = e;
                    break;
                }
                if (!firstError) firstError = e;
                console.warn(`${prefix} ⚠ ${sym} chunk ${chunk.from}→${chunk.to}: ${e.message}`);
            }
        }

        const seen = new Set();
        const deduped = [];
        for (const c of allCandles) {
            const d = normalizeDate(c.date);
            if (!seen.has(d)) {
                seen.add(d);
                deduped.push({ ...c, date: d });
            }
        }
        deduped.sort((a, b) => a.date.localeCompare(b.date));

        const firstDate = deduped.length ? deduped[0].date : '—';
        const lastDate = deduped.length ? deduped[deduped.length - 1].date : '—';
        const ms = Date.now() - t0;

        if (deduped.length === 0) {
            console.log(`${prefix} ✗ ${sym.padEnd(12)} — 0 candles  ${firstError ? `(${firstError.message})` : ''}`);
            report.push({ symbol: sym, rows: 0, firstDate, lastDate, ms, action: 'failed' });
            continue;
        }

        if (!DRY_RUN) {
            await batchUpsert(sym, deduped);
        }

        totalRows += deduped.length;
        const flag = deduped.length < 500 ? ' (newer listing?)' : '';
        const tag = DRY_RUN ? 'fetched' : 'upserted';
        console.log(`${prefix} ✓ ${sym.padEnd(12)} — ${String(deduped.length).padStart(5)} candles  (${firstDate} → ${lastDate})  [${ms}ms ${tag}]${flag}`);
        report.push({ symbol: sym, rows: deduped.length, firstDate, lastDate, ms, action: DRY_RUN ? 'fetched' : 'upserted' });
    }

    const elapsed = ((Date.now() - tStart) / 1000).toFixed(1);
    console.log(`\n${'─'.repeat(70)}`);
    console.log(`  Summary  (elapsed ${elapsed}s)`);
    console.log(`${'─'.repeat(70)}`);
    const upserted = report.filter(r => r.action === 'upserted').length;
    const fetched = report.filter(r => r.action === 'fetched').length;
    const failed = report.filter(r => r.action === 'failed').length;
    console.log(`  symbols processed: ${symbols.length}`);
    console.log(`    skipped (fresh): ${skipped}`);
    console.log(`    fetched:         ${DRY_RUN ? fetched : upserted}`);
    console.log(`    failed:          ${failed}`);
    console.log(`    rows ${DRY_RUN ? 'fetched' : 'upserted'}: ${totalRows}`);
    if (failed) {
        console.log(`\n  Failed symbols: ${report.filter(r => r.action === 'failed').map(r => r.symbol).join(', ')}`);
    }
    console.log(`  Mode: ${DRY_RUN ? 'DRY RUN (no changes)' : 'LIVE (DB updated)'}\n`);

    process.exit(0);
})().catch(err => {
    console.error('\n❌ FATAL:', err.message);
    if (err.stack) console.error(err.stack);
    process.exit(1);
});
