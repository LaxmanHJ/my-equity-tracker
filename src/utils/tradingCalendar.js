/**
 * Trading Calendar helpers.
 *
 * For now only handles weekends. Indian market holidays would need a
 * hard-coded list per year — add when needed.
 */

/**
 * Next trading day after a given YYYY-MM-DD date. Skips Sat/Sun.
 */
export function nextTradingDay(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  d.setUTCDate(d.getUTCDate() + 1);
  while (d.getUTCDay() === 0 || d.getUTCDay() === 6) {
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return d.toISOString().slice(0, 10);
}

/**
 * Today's date in YYYY-MM-DD format (local time).
 */
export function todayStr() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
