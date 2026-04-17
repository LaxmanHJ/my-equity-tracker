/**
 * Backfill 15-min intraday candles from Angel One.
 *
 * Angel's intraday history reaches ~2018 (verified 2026-04-18: 5y/3y/2y/1y/8y return
 * data; 10y+ returns empty). Chunked in 180-day windows to stay well under Angel's
 * 200-day cap per call.
 *
 * Usage:
 *   node scripts/backfill_intraday.js              # all portfolio stocks + NIFTY, 8y
 *   node scripts/backfill_intraday.js --years 2    # shorter window
 *   node scripts/backfill_intraday.js --only INFY,NIFTY 50
 */
import { fetchOHLC } from '../src/services/angelOneHistorical.js';
import { saveIntradayCandles, getIntradayTsRange } from '../src/database/db.js';
import { portfolio } from '../src/config/portfolio.js';

const argv = process.argv.slice(2);
const getArg = (flag) => {
    const i = argv.indexOf(flag);
    return i >= 0 ? argv[i + 1] : null;
};
const YEARS = Number(getArg('--years') || 8);
const CHUNK_DAYS = Number(getArg('--chunk') || 180);
const ONLY = getArg('--only')?.split(',').map(s => s.trim()).filter(Boolean);

function daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
}
function isoToday() { return new Date().toISOString().slice(0, 10); }
function dateFmt(d) { return d.toISOString().slice(0, 10); }
function addDays(dateStr, n) {
    const d = new Date(dateStr + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + n);
    return dateFmt(d);
}

function buildChunks(fromDate, toDate, chunkDays) {
    const chunks = [];
    let cursor = fromDate;
    while (cursor <= toDate) {
        const chunkEnd = addDays(cursor, chunkDays - 1);
        chunks.push({ from: cursor, to: chunkEnd > toDate ? toDate : chunkEnd });
        cursor = addDays(chunkEnd, 1);
    }
    return chunks;
}

/**
 * Build the list of missing [from,to] windows given the requested range and
 * whatever is already in the DB. Covers three cases:
 *   - no existing rows                 → one window [fromDate, toDate]
 *   - existing rows inside the window  → up to two windows (older gap + newer gap)
 *   - existing rows fully cover it     → empty list (no-op)
 */
function computeMissingWindows(existingMin, existingMax, fromDate, toDate) {
    if (!existingMin || !existingMax) return [{ from: fromDate, to: toDate }];

    const haveFrom = existingMin.slice(0, 10);
    const haveTo   = existingMax.slice(0, 10);
    const windows = [];

    if (fromDate < haveFrom) {
        windows.push({ from: fromDate, to: addDays(haveFrom, -1) });
    }
    if (toDate > haveTo) {
        windows.push({ from: addDays(haveTo, 1), to: toDate });
    }
    return windows;
}

async function fetchAndSaveWindow(symbol, storageKey, window, chunks, label) {
    let totalFetched = 0, totalSaved = 0;
    for (const [idx, ch] of chunks.entries()) {
        try {
            const bars = await fetchOHLC(symbol, ch.from, ch.to, 'FIFTEEN_MINUTE');
            if (bars.length > 0) {
                const saved = await saveIntradayCandles(storageKey, bars);
                totalFetched += bars.length;
                totalSaved += saved;
                console.log(`[${storageKey}${label}] chunk ${idx + 1}/${chunks.length} ${ch.from}→${ch.to}: ${bars.length} bars`);
            } else {
                console.log(`[${storageKey}${label}] chunk ${idx + 1}/${chunks.length} ${ch.from}→${ch.to}: empty (pre-availability or holiday range)`);
            }
        } catch (e) {
            console.error(`[${storageKey}${label}] chunk ${idx + 1}/${chunks.length} ${ch.from}→${ch.to} FAILED: ${e.message}`);
        }
    }
    return { totalFetched, totalSaved };
}

async function backfillSymbol(symbol, storageKey, fromDate, toDate) {
    const { min_ts, max_ts } = await getIntradayTsRange(storageKey);
    const windows = computeMissingWindows(min_ts, max_ts, fromDate, toDate);

    if (windows.length === 0) {
        console.log(`[${storageKey}] already covers ${fromDate} → ${toDate} (db: ${min_ts?.slice(0,10)} → ${max_ts?.slice(0,10)})`);
        return { fetched: 0, saved: 0 };
    }

    if (min_ts || max_ts) {
        console.log(`[${storageKey}] existing range ${min_ts?.slice(0,10)} → ${max_ts?.slice(0,10)}; filling ${windows.length} gap(s)`);
    }

    let fetched = 0, saved = 0;
    for (const [i, w] of windows.entries()) {
        const chunks = buildChunks(w.from, w.to, CHUNK_DAYS);
        const label = windows.length > 1 ? (i === 0 && min_ts && w.to < min_ts.slice(0,10) ? ':older' : ':newer') : '';
        const r = await fetchAndSaveWindow(symbol, storageKey, w, chunks, label);
        fetched += r.totalFetched;
        saved += r.totalSaved;
    }
    return { fetched, saved };
}

async function main() {
    const fromDate = daysAgo(YEARS * 365);
    const toDate = isoToday();
    console.log(`Backfill window: ${fromDate} → ${toDate}  (${YEARS}y, ${CHUNK_DAYS}d chunks)`);

    // target list: portfolio stocks + NIFTY 50
    // storageKey is what we write into intraday_candles.symbol; keep it consistent with price_history
    const targets = portfolio.map(p => ({
        fetch: p.displaySymbol,
        storage: p.displaySymbol
    }));
    targets.push({ fetch: 'NIFTY 50', storage: '^NSEI' });

    const filtered = ONLY ? targets.filter(t => ONLY.includes(t.fetch) || ONLY.includes(t.storage)) : targets;
    console.log(`Targets (${filtered.length}): ${filtered.map(t => t.storage).join(', ')}\n`);

    const t0 = Date.now();
    const summary = [];
    for (const t of filtered) {
        try {
            const r = await backfillSymbol(t.fetch, t.storage, fromDate, toDate);
            summary.push({ symbol: t.storage, ...r });
        } catch (e) {
            console.error(`[${t.storage}] aborted: ${e.message}`);
            summary.push({ symbol: t.storage, fetched: 0, saved: 0, error: e.message });
        }
    }
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`\n── Summary (${secs}s) ──`);
    console.table(summary);
}

main().catch(err => {
    console.error('Backfill failed:', err);
    process.exit(1);
});
