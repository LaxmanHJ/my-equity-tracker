/**
 * Daily intraday top-up — keeps intraday_candles fresh from the Force Sync button.
 *
 * Fetches the trailing N days of 15-min candles from Angel One for the
 * portfolio + NIFTY and upserts them. The window is small (default 5 days,
 * well under Angel's 200-day cap) so no chunking is needed; the UPSERT in
 * saveIntradayCandles makes repeated presses idempotent. For historical
 * gap-fills beyond the trailing window use scripts/backfill_intraday.js.
 */
import { fetchOHLC } from './angelOneHistorical.js';
import { saveIntradayCandles } from '../database/db.js';
import { portfolio } from '../config/portfolio.js';

function daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
}

export async function syncRecentIntraday(days = 5) {
    const fromDate = daysAgo(days);
    const toDate = new Date().toISOString().slice(0, 10);

    const targets = portfolio.map(p => ({ fetch: p.displaySymbol, storage: p.displaySymbol }));
    targets.push({ fetch: 'NIFTY 50', storage: '^NSEI' });

    let saved = 0;
    const errors = [];
    for (const t of targets) {
        try {
            const bars = await fetchOHLC(t.fetch, fromDate, toDate, 'FIFTEEN_MINUTE');
            if (bars.length > 0) {
                saved += await saveIntradayCandles(t.storage, bars);
            }
        } catch (e) {
            errors.push(`${t.storage}: ${e.message}`);
        }
    }
    return { fromDate, toDate, symbols: targets.length, saved, errors };
}
