/**
 * Sync Orchestrator — the single implementation of the EOD data sync.
 *
 * Extracted from the POST /api/portfolio/sync route handler (2026-07-25) so the
 * same pipeline can run from three entry points without drift:
 *   1. Express route (Force Sync button)        → src/routes/api.js
 *   2. Standalone worker (GitHub Actions cron)  → scripts/syncWorker.js
 *   3. In-process node-cron fallback            → src/server.js (ENABLE_LOCAL_CRON=1)
 *
 * Behaviour is unchanged from the route-handler version: every sub-sync is
 * tracked in `subSyncs` and reported truthfully (partial failures don't claim
 * blanket success — the 2026-06-10 staleness incident is why), and the final
 * freshness report is attached so callers can alert on stale tables.
 *
 * The Python quant engine (QUANT_ENGINE_URL) must be reachable for the fii /
 * vix / sentiment / eod / freshness steps; each degrades to ok:false with an
 * explanatory detail if it is not.
 */
import {
  getAllQuotes,
  fetchBulkDealsToday,
  fetchPCRAndOIBuildup,
} from './stockData.js';
import { syncRecentIntraday } from './intradaySync.js';
import { collectIVSnapshot } from './optionChainService.js';
import { settleDuePaperCondors } from './paperCondorService.js';
import { fetchDailyOHLC } from './angelOneHistorical.js';
import { getFundInstrumentsWithLastDate, savePriceHistory } from '../database/db.js';
import { computeBackfillWindow } from './syncSummary.js';

const QUANT_ENGINE_URL = process.env.QUANT_ENGINE_URL || 'http://localhost:5001';

/**
 * Run the full EOD sync pipeline.
 *
 * @returns {Promise<{success: boolean, synced: number, subSyncs: object, freshness: object|null}>}
 *          Never throws for sub-sync failures — only if the initial holdings
 *          sync (the one step everything else depends on) fails.
 */
export async function runEodSync() {
  // Track each sub-sync so the response body tells the caller exactly what
  // happened, instead of forcing them to read server console logs.
  const subSyncs = {
    holdings:  { ok: false, detail: null },
    fii:       { ok: false, detail: null },
    bulkDeals: { ok: false, detail: null },
    pcrOi:     { ok: false, detail: null },
    vix:       { ok: false, detail: null },
    sentiment: { ok: false, detail: null },
    eod:       { ok: false, detail: null },
    intraday:  { ok: false, detail: null },
    ivChain:   { ok: false, detail: null },
    fundInstruments: { ok: false, detail: null },
  };

  console.log('[ForceSync] Starting full portfolio + index data refresh into SQLite...');
  const quotes = await getAllQuotes(true); // fetches from RapidAPI/AlphaVantage → writes to SQLite
  console.log(`[ForceSync] ✅ Synced ${quotes.length} holdings to SQLite`);
  subSyncs.holdings = { ok: true, detail: { synced: quotes.length } };

  // Fetch today's FII/DII cash flows via Python engine (session-based, more reliable)
  try {
    const fiiRes = await fetch(`${QUANT_ENGINE_URL}/api/sync/fii`, { method: 'POST' });
    const fiiData = await fiiRes.json();
    if (fiiData.success) {
      console.log(`[ForceSync] ✅ FII/DII synced`);
      subSyncs.fii = { ok: true, detail: fiiData };
    } else {
      console.warn(`[ForceSync] ⚠️ FII/DII sync: ${fiiData.error}`);
      subSyncs.fii = { ok: false, detail: { error: fiiData.error } };
    }
  } catch (e) {
    console.warn('[ForceSync] ⚠️ FII/DII sync skipped (quant engine unavailable):', e.message);
    subSyncs.fii = { ok: false, detail: { error: `quant engine unavailable: ${e.message}` } };
  }

  // Fetch today's bulk/block deals — accumulates institutional activity data over time
  try {
    await fetchBulkDealsToday();
    subSyncs.bulkDeals = { ok: true, detail: null };
  } catch (e) {
    subSyncs.bulkDeals = { ok: false, detail: { error: e.message } };
  }

  // Fetch PCR + OI Buildup from Angel One. PCR routinely fails outside
  // market hours ("No data available") — partial status is reported so the
  // response doesn't claim ok while pcr_history quietly falls behind.
  try {
    const pcrOiStatus = await fetchPCRAndOIBuildup();
    subSyncs.pcrOi = { ok: pcrOiStatus.pcr.ok && pcrOiStatus.oi.ok, detail: pcrOiStatus };
  } catch (e) {
    subSyncs.pcrOi = { ok: false, detail: { error: e.message } };
  }

  // Fetch today's India VIX from NSE and upsert into market_regime
  try {
    const vixRes = await fetch(`${QUANT_ENGINE_URL}/api/sync/vix`, { method: 'POST' });
    const vixData = await vixRes.json();
    if (vixData.success) {
      console.log(`[ForceSync] ✅ VIX synced: ${vixData.date} = ${vixData.india_vix}`);
      subSyncs.vix = { ok: true, detail: { date: vixData.date, india_vix: vixData.india_vix } };
    } else {
      console.warn(`[ForceSync] ⚠️ VIX sync failed: ${vixData.error}`);
      subSyncs.vix = { ok: false, detail: { error: vixData.error } };
    }
  } catch (e) {
    console.warn('[ForceSync] ⚠️ VIX sync skipped (quant engine unavailable):', e.message);
    subSyncs.vix = { ok: false, detail: { error: `quant engine unavailable: ${e.message}` } };
  }

  // Score today's sentiment from stock_news (and NewsAPI if configured)
  // and upsert into sentiment_daily. Runs after stock_news is populated by
  // getAllQuotes above, so the same force-sync picks up news → score in
  // one pass. Idempotent: UPSERT on (symbol, date).
  try {
    const sentRes = await fetch(
      `${QUANT_ENGINE_URL}/api/sync/sentiment?days=1`,
      { method: 'POST' },
    );
    const sentData = await sentRes.json();
    if (sentData.success) {
      console.log(
        `[ForceSync] ✅ Sentiment synced: ${sentData.symbols} symbols, ` +
        `${sentData.articles} articles, ${sentData.rows_written} rows ` +
        `(scorers: ${(sentData.available_scorers || []).join(',') || 'none'})`,
      );
      subSyncs.sentiment = {
        ok: true,
        detail: {
          symbols:           sentData.symbols,
          articles:          sentData.articles,
          rows_written:      sentData.rows_written,
          available_scorers: sentData.available_scorers,
        },
      };
    } else {
      console.warn(`[ForceSync] ⚠️ Sentiment sync failed: ${sentData.error}`);
      subSyncs.sentiment = {
        ok: false,
        detail: {
          error:             sentData.error,
          available_scorers: sentData.available_scorers,
        },
      };
    }
  } catch (e) {
    console.warn('[ForceSync] ⚠️ Sentiment sync skipped (quant engine unavailable):', e.message);
    subSyncs.sentiment = { ok: false, detail: { error: `quant engine unavailable: ${e.message}` } };
  }

  // Gap-fill sector_indices + delivery_data from each table's max(date) → today.
  // Self-healing: skipped days are caught up on the next press (2026-06-10
  // staleness incident — these tables sat frozen for 2.5 months unnoticed).
  try {
    const eodRes = await fetch(`${QUANT_ENGINE_URL}/api/sync/eod`, { method: 'POST' });
    const eodData = await eodRes.json();
    subSyncs.eod = { ok: !!eodData.success, detail: eodData.tables };
    console.log(`[ForceSync] ${eodData.success ? '✅' : '⚠️'} EOD gap-fill:`, JSON.stringify(eodData.tables));
  } catch (e) {
    console.warn('[ForceSync] ⚠️ EOD gap-fill skipped (quant engine unavailable):', e.message);
    subSyncs.eod = { ok: false, detail: { error: `quant engine unavailable: ${e.message}` } };
  }

  // Top up trailing 15-min candles from Angel One (portfolio + NIFTY).
  try {
    const intradayResult = await syncRecentIntraday();
    subSyncs.intraday = { ok: intradayResult.errors.length === 0, detail: intradayResult };
    console.log(`[ForceSync] ✅ Intraday top-up: ${intradayResult.saved} candles, ${intradayResult.errors.length} errors`);
  } catch (e) {
    console.warn('[ForceSync] ⚠️ Intraday top-up failed:', e.message);
    subSyncs.intraday = { ok: false, detail: { error: e.message } };
  }

  // NIFTY ~30-DTE option-chain IV snapshot (SIC-92 forward collection).
  // Angel serves greeks only around market hours — failures are expected
  // on late-evening presses and reported truthfully, not swallowed.
  try {
    const ivResult = await collectIVSnapshot('NIFTY');
    subSyncs.ivChain = { ok: !!ivResult.ok, detail: ivResult };
    console.log(`[ForceSync] ${ivResult.ok ? '✅' : '⚠️'} IV chain:`, JSON.stringify(ivResult));
  } catch (e) {
    console.warn('[ForceSync] ⚠️ IV chain snapshot failed:', e.message);
    subSyncs.ivChain = { ok: false, detail: { error: e.message } };
  }

  // Gap-fill daily OHLC for instruments held in the fund ledger (ETFs like
  // NIFTYBEES are not part of the legacy portfolio sync, so without this step
  // the fund book would mark to stale cost instead of market).
  try {
    const instruments = await getFundInstrumentsWithLastDate();
    const today = new Date().toISOString().slice(0, 10);
    const results = [];
    for (const { instrument, lastDate } of instruments) {
      try {
        // Day after the last cached bar, or a 400d lookback for new instruments.
        // Window logic is unit-tested in tests/syncSummary.test.mjs.
        const { from, skip } = computeBackfillWindow(lastDate, today);
        if (skip) { results.push({ instrument, saved: 0, upToDate: true }); continue; }
        const rows = await fetchDailyOHLC(instrument, from, today);
        const normalized = rows.map(r => ({ ...r, date: String(r.date).slice(0, 10) }));
        await savePriceHistory(instrument, normalized);
        results.push({ instrument, saved: normalized.length });
      } catch (e) {
        results.push({ instrument, error: e.message });
      }
    }
    const errs = results.filter(r => r.error);
    subSyncs.fundInstruments = { ok: errs.length === 0, detail: results };
    if (instruments.length) {
      console.log(`[ForceSync] ${errs.length === 0 ? '✅' : '⚠️'} Fund instruments:`, JSON.stringify(results));
    }
  } catch (e) {
    console.warn('[ForceSync] ⚠️ Fund instrument sync failed:', e.message);
    subSyncs.fundInstruments = { ok: false, detail: { error: e.message } };
  }

  // Settle any paper condors whose expiry has passed (needs the expiry-day
  // ^NSEI bar, which the holdings sync above just refreshed).
  try {
    const settled = await settleDuePaperCondors();
    if (settled.length) console.log('[ForceSync] ✅ Paper condors settled:', JSON.stringify(settled));
  } catch (e) {
    console.warn('[ForceSync] ⚠️ Paper condor settlement failed:', e.message);
  }

  // Freshness report — surfaces any table still running on stale data so
  // the frontend can warn instead of silently ffilling frozen features.
  let freshness = null;
  try {
    const freshRes = await fetch(`${QUANT_ENGINE_URL}/api/data/freshness`);
    freshness = await freshRes.json();
    if (freshness.any_stale) {
      const staleNames = Object.entries(freshness.tables)
        .filter(([, t]) => t.stale)
        .map(([name, t]) => `${name} (${t.max_date ?? 'empty'})`);
      console.warn(`[ForceSync] ⚠️ STALE TABLES: ${staleNames.join(', ')}`);
    }
  } catch (e) {
    console.warn('[ForceSync] ⚠️ Freshness check skipped:', e.message);
  }

  return {
    success: true,
    synced: quotes.length,
    subSyncs,
    freshness,
  };
}
