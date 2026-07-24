/**
 * Sync Summary — pure, dependency-free.
 *
 * Extracted from syncOrchestrator.js / syncWorker.js so the date-window and
 * pass/fail logic can be unit-tested without booting Turso, Angel One, or the
 * Python engine. Same rationale as convictionGates.js.
 *
 * Nothing here does I/O. Callers pass in `today` explicitly so tests are
 * deterministic and the caller controls the timezone convention (we use the
 * ISO date of the run, IST-agnostic, because every table is keyed by
 * trade date, not timestamp).
 */

const DAY_MS = 86_400_000;

/**
 * Gap-fill window for an instrument's daily OHLC.
 *
 * @param {string|null} lastDate — ISO date of the newest cached bar, null if none
 * @param {string} today — ISO date of the run
 * @param {number} coldStartDays — lookback when nothing is cached
 * @returns {{from: string, skip: boolean}} skip=true when already current
 */
export function computeBackfillWindow(lastDate, today, coldStartDays = 400) {
  if (!lastDate) {
    const from = new Date(Date.parse(today) - coldStartDays * DAY_MS)
      .toISOString().slice(0, 10);
    return { from, skip: false };
  }
  // Resume from the day after the newest cached bar.
  const from = new Date(Date.parse(lastDate) + DAY_MS).toISOString().slice(0, 10);
  // ISO dates compare correctly as strings.
  return { from, skip: from > today };
}

/**
 * Reduce a runEodSync() result to the facts a caller acts on.
 *
 * Exit codes (used by scripts/syncWorker.js, and therefore by the GitHub
 * Actions run status):
 *   0 — synced, every monitored table fresh
 *   1 — synced, but at least one table is still stale (the alert)
 *   2 — reserved for a thrown sync (handled by the caller's catch)
 */
export function summarizeSync(result) {
  const failedSubSyncs = Object.entries(result?.subSyncs ?? {})
    .filter(([, s]) => !s?.ok)
    .map(([name]) => name);

  const tables = result?.freshness?.tables ?? {};
  const staleTables = Object.entries(tables)
    .filter(([, t]) => t?.stale)
    .map(([name, t]) => ({ name, maxDate: t.max_date ?? null }));

  return {
    failedSubSyncs,
    staleTables,
    anyStale: staleTables.length > 0,
    exitCode: staleTables.length > 0 ? 1 : 0,
  };
}
