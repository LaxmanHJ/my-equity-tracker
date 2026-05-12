#!/usr/bin/env node
/**
 * Upsert industry mapping for Nifty 200 constituents into stock_fundamentals.
 *
 * Source: data/nifty200_constituents.json (parsed from NSE's ind_nifty200list.csv).
 * Behaviour: INSERT new rows; UPDATE company_name + industry on conflict(symbol).
 * Path B from session 2026-05-09 — standardize entire universe on NSE's broad
 * sector taxonomy so sector_rotation peer groups have real depth (10–48 names
 * per sector instead of 1–3). Reclassifies the 15 pre-existing rows.
 *
 * Usage:
 *   node scripts/upsert-nifty200-industries.mjs --dry-run
 *   node scripts/upsert-nifty200-industries.mjs
 */
import 'dotenv/config';
import fs from 'fs';
import { createClient } from '@libsql/client';

const db = createClient({
    url: process.env.TURSO_DATABASE_URL,
    authToken: process.env.TURSO_AUTH_TOKEN,
});

const args = process.argv.slice(2);
const DRY_RUN = args.includes('--dry-run');

const data = JSON.parse(fs.readFileSync('./data/nifty200_constituents.json', 'utf8'));
const rows = data.constituents.filter(c => !c.symbol.startsWith('DUMMY'));

console.log(`\n${'═'.repeat(70)}`);
console.log(`  Nifty 200 industry upsert  ${DRY_RUN ? '(DRY RUN)' : '⚠️  LIVE'}`);
console.log(`  Symbols: ${rows.length}`);
console.log(`${'═'.repeat(70)}\n`);

const before = await db.execute({
    sql: `SELECT symbol, industry FROM stock_fundamentals WHERE symbol IN (${rows.map(() => '?').join(',')})`,
    args: rows.map(r => r.symbol),
});
const existingMap = Object.fromEntries(before.rows.map(r => [r.symbol, r.industry]));
console.log(`  pre-state: ${before.rows.length} of ${rows.length} symbols already have a row in stock_fundamentals`);

let inserts = 0, updates = 0, unchanged = 0;
const stmts = [];
for (const r of rows) {
    const cur = existingMap[r.symbol];
    if (cur === undefined) {
        inserts++;
    } else if (cur !== r.industry) {
        updates++;
    } else {
        unchanged++;
        continue;
    }
    stmts.push({
        sql: `INSERT INTO stock_fundamentals (symbol, company_name, industry)
              VALUES (?, ?, ?)
              ON CONFLICT(symbol) DO UPDATE SET
                  company_name = excluded.company_name,
                  industry     = excluded.industry`,
        args: [r.symbol, r.name, r.industry],
    });
}

console.log(`  changes: ${inserts} insert, ${updates} update, ${unchanged} unchanged`);

if (updates > 0) {
    console.log(`\n  reclassifications:`);
    for (const r of rows) {
        const cur = existingMap[r.symbol];
        if (cur !== undefined && cur !== r.industry) {
            console.log(`    ${r.symbol.padEnd(14)} ${String(cur).padEnd(35)} → ${r.industry}`);
        }
    }
}

if (DRY_RUN) {
    console.log(`\n  DRY RUN — no DB writes\n`);
    process.exit(0);
}

if (stmts.length > 0) {
    const BATCH = 200;
    for (let i = 0; i < stmts.length; i += BATCH) {
        await db.batch(stmts.slice(i, i + BATCH), 'write');
    }
    console.log(`\n  ✓ wrote ${stmts.length} statements\n`);
}
process.exit(0);
