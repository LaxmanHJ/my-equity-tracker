/* CATALAN dashboard — vanilla, same-origin, no build step.
   Reports the state of the study; it never computes anything itself. */

const $ = (id) => document.getElementById(id);
const fmt = (n) => (n === null || n === undefined ? '—' : n.toLocaleString('en-IN'));

async function getJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

function renderMeta(summary) {
  $('meta-study').textContent = summary.study;
  $('meta-universe').textContent = summary.universe;
  $('meta-phase').textContent = summary.phase;
}

function renderFreshness(cov, scores) {
  const stale = cov.days_since_last_announcement;
  const el = $('stat-stale');
  el.textContent = stale === null ? '—' : stale;
  // The collector is meant to run daily; anything past a long weekend is a
  // problem worth seeing without reading a log.
  el.className = 'stat-value ' + (stale === null ? '' : stale > 4 ? 'is-stale' : 'is-fresh');

  $('stat-total').textContent = fmt(cov.announcements);
  $('stat-symbols').textContent = fmt(cov.symbols);
  $('stat-unscored').textContent = fmt(scores.unscored_announcements);

  $('stat-range').textContent = cov.first_announcement
    ? `${cov.first_announcement} → ${cov.last_announcement}`
    : 'no announcements collected yet — run: python3 -m catalan.data.collect_daily';
}

function renderGates(summary) {
  const host = $('gates');
  host.innerHTML = '';
  summary.gate_order.forEach((id) => {
    const g = summary.gates[id];
    const isShip = id === 'C2';
    const row = document.createElement('div');
    row.className = 'gate' + (isShip ? ' is-ship' : '');
    row.innerHTML = `
      <span class="gate-id">${id}</span>
      <span class="gate-name">${g.name}
        <span class="gate-ref">
          ${g.tradable ? '' : '<span class="untradable">not tradable · </span>'}paper: ${g.paper_reference}
        </span>
      </span>
      <span class="gate-status">${g.value === null ? g.status : g.value}</span>`;
    host.appendChild(row);
  });
}

function renderCoverage(cov) {
  const host = $('coverage');
  host.innerHTML = '';
  if (!cov.by_month.length) {
    host.innerHTML = '<p class="cov-empty">No corpus yet. Phase 1a collector has not written a row.</p>';
    return;
  }
  const peak = Math.max(...cov.by_month.map((m) => m.announcements));
  cov.by_month.forEach((m) => {
    const bar = document.createElement('div');
    bar.className = 'cov-bar';
    bar.style.height = `${Math.max(2, (m.announcements / peak) * 88)}px`;
    bar.title = `${m.month}: ${fmt(m.announcements)} announcements, ${fmt(m.symbols)} symbols`;
    host.appendChild(bar);
  });
}

function renderRuns(cov) {
  const body = $('runs');
  if (!cov.recent_runs.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">No ingest runs recorded.</td></tr>';
    return;
  }
  body.innerHTML = cov.recent_runs.map((r) => `
    <tr>
      <td>${r.source}</td>
      <td>${r.window_from} → ${r.window_to}</td>
      <td class="num">${fmt(r.rows_fetched)}</td>
      <td class="num">${fmt(r.rows_inserted)}</td>
      <td class="s-${r.status}">${r.status}${r.detail ? ` · ${r.detail.slice(0, 60)}` : ''}</td>
      <td>${r.finished_at || '—'}</td>
    </tr>`).join('');
}

function renderScoring(scores, pend) {
  $('stat-pending').textContent = fmt(pend.pending_after_filters);
  $('stat-filtered').textContent = fmt(pend.pending_unfiltered - pend.pending_after_filters);
  $('stat-arms').textContent = fmt(scores.arms.length);

  const body = $('arms');
  if (!scores.arms.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">Nothing scored yet.</td></tr>';
    return;
  }
  body.innerHTML = scores.arms.map((a) => `
    <tr>
      <td>${a.model_id}</td>
      <td>${a.prompt_hash}</td>
      <td class="num">${fmt(a.scored)}</td>
      <td class="num">${fmt(a.runs)}</td>
    </tr>`).join('');
}

function setupManualEntry() {
  const form = $('manual-form');
  const out = $('manual-result');
  const btn = $('m-submit');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btn.disabled = true;
    btn.textContent = 'Scoring…';
    out.hidden = true;

    const symbol = $('m-symbol').value.trim();
    try {
      const res = await fetch('/api/study/score', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company: $('m-company').value.trim(),
          headline: $('m-headline').value.trim(),
          symbol: symbol || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      out.className = `manual-result v-${(data.verdict || 'error').toLowerCase()}`;
      out.innerHTML = `
        <span class="verdict">${data.verdict}</span>
        <p>${data.rationale || '<em>no rationale returned</em>'}</p>
        <p class="prov">${data.model_id} · prompt ${data.prompt_hash} ·
          ${data.persisted ? `saved as announcement #${data.announcement_id}`
                           : 'not saved (no symbol given)'}</p>`;
      out.hidden = false;
      if (data.persisted) load();
    } catch (err) {
      out.className = 'manual-result v-error';
      out.innerHTML = `<span class="verdict">ERROR</span><p>${err.message}</p>`;
      out.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Score headline';
    }
  });
}

async function load() {
  try {
    const [summary, cov, scores, pend] = await Promise.all([
      getJSON('/api/study/summary'),
      getJSON('/api/study/coverage'),
      getJSON('/api/study/scores'),
      getJSON('/api/study/pending'),
    ]);
    renderMeta(summary);
    renderFreshness(cov, scores);
    renderGates(summary);
    renderScoring(scores, pend);
    renderCoverage(cov);
    renderRuns(cov);
  } catch (err) {
    $('stat-range').textContent = `failed to load: ${err.message}`;
  }
}

setupManualEntry();
load();
