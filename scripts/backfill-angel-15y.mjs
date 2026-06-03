#!/usr/bin/env node
/**
 * Backfill 15 years of clean daily OHLCV from Angel One SmartAPI.
 *
 * Usage:
 *   node scripts/backfill-angel-15y.mjs --dry-run                 # Fetch + report, no DB writes
 *   node scripts/backfill-angel-15y.mjs --dry-run --symbol INFY   # Single symbol dry run
 *   node scripts/backfill-angel-15y.mjs                           # Full backfill (DELETE + INSERT)
 *   node scripts/backfill-angel-15y.mjs --symbol TATAELXSI        # Single symbol backfill
 *   node scripts/backfill-angel-15y.mjs --dry-run --symbols A,B,C # Explicit list
 *   node scripts/backfill-angel-15y.mjs --dry-run --file syms.txt # One symbol per line
 *   node scripts/backfill-angel-15y.mjs --symbol ARE&M --store-as AMARAJABAT  # rename: fetch new, store old
 */
import 'dotenv/config';
import { readFileSync } from 'fs';
import { fetchDailyOHLC } from '../src/services/angelOneHistorical.js';
import { initDatabase, savePriceHistory } from '../src/database/db.js';
import { createClient } from '@libsql/client';

const db = createClient({
    url: process.env.TURSO_DATABASE_URL,
    authToken: process.env.TURSO_AUTH_TOKEN,
});

const ALL_SYMBOLS = [
    'ADANIPOWER', 'APLLTD', 'AWL', 'BAJAJHIND', 'BANDHANBNK',
    'ETERNAL', 'INFY', 'JIOFIN', 'REPCOHOME', 'TANLA',
    'TATAELXSI', 'TATAPOWER', 'TATASTEEL', 'TMCV', 'TMPV'
];

const args = process.argv.slice(2);
const DRY_RUN = args.includes('--dry-run');
const argVal = (flag) => { const i = args.indexOf(flag); return i !== -1 ? args[i + 1] : null; };

const SINGLE_SYMBOL = argVal('--symbol')?.toUpperCase() || null;
const LIST_ARG = argVal('--symbols');
const FILE_ARG = argVal('--file');
// For pure renames: fetch under the new ticker, store under the membership symbol.
// Only valid with a single --symbol (the new ticker).
const STORE_AS = argVal('--store-as')?.toUpperCase() || null;

// Precedence: --symbol > --symbols > --file > ALL_SYMBOLS
let symbols;
if (SINGLE_SYMBOL) {
    symbols = [SINGLE_SYMBOL];
} else if (LIST_ARG) {
    symbols = LIST_ARG.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
} else if (FILE_ARG) {
    symbols = readFileSync(FILE_ARG, 'utf8')
        .split(/[\r\n,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
} else {
    symbols = ALL_SYMBOLS;
}

const START_DATE = '2011-01-01';
const CHUNK_DAYS = 2000;

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

(async () => {
    await initDatabase();
    const today = todayStr();
    const chunks = buildChunks(START_DATE, today);

    console.log(`\n${'═'.repeat(70)}`);
    console.log(`  Angel One 15y OHLCV Backfill  ${DRY_RUN ? '(DRY RUN — no DB writes)' : '⚠️  LIVE — will DELETE + INSERT'}`);
    console.log(`  Symbols: ${symbols.length}  |  Range: ${START_DATE} → ${today}  |  Chunks: ${chunks.length}`);
    console.log(`${'═'.repeat(70)}\n`);

    const report = [];
    let totalRows = 0;

    for (const sym of symbols) {
        const t0 = Date.now();
        let allCandles = [];

        for (const chunk of chunks) {
            try {
                const rows = await fetchDailyOHLC(sym, chunk.from, chunk.to);
                allCandles.push(...rows);
            } catch (e) {
                if (e.message?.includes('Symbol not found')) {
                    console.warn(`  ⚠ ${sym}: not in scrip master — skipping`);
                    break;
                }
                console.warn(`  ⚠ ${sym} chunk ${chunk.from}→${chunk.to}: ${e.message}`);
            }
        }

        // Deduplicate by date (chunks may overlap by a day)
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

        report.push({ symbol: sym, rows: deduped.length, firstDate, lastDate, ms });
        totalRows += deduped.length;

        if (deduped.length === 0) {
            console.log(`  ✗ ${sym.padEnd(12)} — 0 candles returned`);
            continue;
        }

        console.log(`  ✓ ${sym.padEnd(12)} — ${String(deduped.length).padStart(5)} candles  (${firstDate} → ${lastDate})  [${ms}ms]`);

        if (!DRY_RUN) {
            // Pure-rename support: fetched under `sym`, persisted under the
            // membership symbol when --store-as is given.
            const storeSym = STORE_AS || sym;
            // Delete existing rows for the stored symbol
            await db.execute({
                sql: 'DELETE FROM price_history WHERE symbol = ?',
                args: [storeSym]
            });
            // Insert fresh data
            await savePriceHistory(storeSym, deduped);
            const as = storeSym !== sym ? ` as ${storeSym}` : '';
            console.log(`    ↳ DB: deleted old rows, inserted ${deduped.length} fresh rows${as}`);
        }
    }

    // Summary
    console.log(`\n${'─'.repeat(70)}`);
    console.log(`  Summary`);
    console.log(`${'─'.repeat(70)}`);
    console.log(`  ${'Symbol'.padEnd(14)} ${'Rows'.padStart(6)}   ${'First Date'.padEnd(12)} ${'Last Date'.padEnd(12)} ${'Time'.padStart(6)}`);
    console.log(`  ${'─'.repeat(56)}`);
    for (const r of report) {
        const flag = r.rows < 500 ? ' ⚠ (newer listing?)' : '';
        console.log(`  ${r.symbol.padEnd(14)} ${String(r.rows).padStart(6)}   ${r.firstDate.padEnd(12)} ${r.lastDate.padEnd(12)} ${String(r.ms + 'ms').padStart(6)}${flag}`);
    }
    console.log(`  ${'─'.repeat(56)}`);
    console.log(`  Total: ${totalRows} rows across ${report.filter(r => r.rows > 0).length} symbols`);
    console.log(`  Mode: ${DRY_RUN ? 'DRY RUN (no changes)' : 'LIVE (DB updated)'}\n`);

    process.exit(0);
})().catch(err => {
    console.error('\n❌ FATAL:', err.message);
    if (err.stack) console.error(err.stack);
    process.exit(1);
});
