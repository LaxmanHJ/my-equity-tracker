/**
 * Standalone EOD sync worker — entry point for the GitHub Actions cron
 * (.github/workflows/eod-sync.yml) and for manual laptop runs:
 *
 *   node scripts/syncWorker.js
 *
 * Runs the exact same pipeline as the dashboard's Force Sync button
 * (src/services/syncOrchestrator.js). Expects the Python quant engine to be
 * reachable at QUANT_ENGINE_URL (the workflow boots it before this script).
 *
 * Exit codes:
 *   0 — sync ran and no monitored table is stale
 *   1 — sync ran but the freshness report still shows stale tables
 *       (visible as a red run in GitHub Actions — that is the alert)
 *   2 — sync itself failed (holdings step threw)
 */
import 'dotenv/config';
import { runEodSync } from '../src/services/syncOrchestrator.js';
import { summarizeSync } from '../src/services/syncSummary.js';

const started = Date.now();

try {
  const result = await runEodSync();
  const { failedSubSyncs, staleTables, exitCode } = summarizeSync(result);

  console.log('\n========== SYNC WORKER SUMMARY ==========');
  console.log(`duration: ${((Date.now() - started) / 1000).toFixed(1)}s`);
  console.log(`holdings synced: ${result.synced}`);
  console.log(`sub-syncs failed: ${failedSubSyncs.length ? failedSubSyncs.join(', ') : 'none'}`);
  console.log('freshness:', JSON.stringify(result.freshness, null, 2));

  if (exitCode !== 0) {
    const stale = staleTables.map(t => `${t.name} (max ${t.maxDate ?? 'empty'})`);
    console.error(`\n❌ STALE TABLES AFTER SYNC: ${stale.join(', ')}`);
    process.exit(exitCode);
  }

  console.log('\n✅ All monitored tables fresh.');
  process.exit(0);
} catch (error) {
  console.error('\n❌ Sync worker failed:', error);
  process.exit(2);
}
