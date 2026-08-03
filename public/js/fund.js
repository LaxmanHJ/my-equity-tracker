/**
 * Fund page — renders /api/fund/overview and manages the ledger.
 * Pure vanilla JS, same pattern as the rest of the app.
 */

const fmtINR = (v) =>
  v == null ? '—' : '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const fmtNum = (v, d = 2) =>
  v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: d });
const fmtPct = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)}%`);
const cls = (v) => (v == null ? '' : v >= 0 ? 'pos' : 'neg');

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `${res.status}`);
  return data;
}

// ── Overview ────────────────────────────────────────────────

async function loadOverview() {
  const o = await fetchJSON('/api/fund/overview');

  document.getElementById('asOf').textContent = `as of ${new Date(o.asOf).toLocaleString()}`;

  // NAV
  document.getElementById('navValue').textContent = fmtINR(o.nav);
  document.getElementById('navBreakdown').textContent =
    `invested ${fmtINR(o.investedValue)} · cash ${fmtINR(o.cash)}`;

  // Sleeves
  const sleeveBars = document.getElementById('sleeveBars');
  if (!o.sleeves.length) {
    sleeveBars.innerHTML = '<span style="color:var(--text-muted); font-size:0.82rem;">No budgets configured.</span>';
  } else {
    sleeveBars.innerHTML = o.sleeves.map((s) => {
      const width = Math.min(100, s.actualPct);
      return `
        <div class="sleeve-row">
          <div class="sleeve-head">
            <span>${s.sleeve}${s.overBudget ? ' <span class="pill pill-warn">over</span>' : ''}</span>
            <span>${fmtPct(s.actualPct)} / ${s.budgetPct}%</span>
          </div>
          <div class="sleeve-bar">
            <div class="sleeve-fill" style="width:${width}%"></div>
            <div class="sleeve-budget-mark" style="left:${Math.min(100, s.budgetPct)}%"></div>
          </div>
        </div>`;
    }).join('');
  }

  // Tranche card
  const t = o.tranche;
  document.getElementById('trancheCard').innerHTML = t.complete
    ? `<div class="pill pill-ok">Complete</div>
       <div style="margin-top:0.6rem; font-size:0.84rem;">
         ${t.tranchesTotal}/${t.tranchesTotal} tranches in ${t.instrument} ·
         avg entry ${fmtNum(t.avgEntry)}</div>`
    : `${t.notStarted
         ? '<div class="pill pill-muted">Not started</div>'
         : t.overdue > 0
           ? `<div class="pill pill-warn">${t.overdue} overdue</div>`
           : '<div class="pill pill-ok">On schedule</div>'}
       <div style="font-size:0.84rem; line-height:1.7; margin-top:0.6rem;">
         <strong>${t.tranchesDone}/${t.tranchesTotal}</strong> tranches done in <strong>${t.instrument}</strong><br/>
         ${t.notStarted
           ? 'Schedule begins with the first recorded buy.<br/>'
           : `Due by now: <strong>${t.expected}</strong> · started ${t.started}<br/>`}
         Next due: <strong>${t.nextDue ?? '—'}</strong><br/>
         ${t.avgEntry ? `Avg entry: ${fmtNum(t.avgEntry)} · qty ${fmtNum(t.totalQty, 0)}` : 'No tranches recorded yet.'}
       </div>
       <div class="progress-outer"><div class="progress-inner" style="width:${(100 * t.tranchesDone) / t.tranchesTotal}%"></div></div>
       <div style="font-size:0.7rem; color:var(--text-muted); margin-top:0.5rem;">
         Level-independent by design — the regime-timing study measured no timing skill.
       </div>`;

  // Gates
  const g = o.gates;
  const vrpPct = Math.min(100, (100 * g.vrp.daysCollected) / g.vrp.daysRequired);
  document.getElementById('gatesCard').innerHTML = `
    <div class="gate-row" style="display:block;">
      <div style="display:flex; justify-content:space-between;">
        <span>${g.vrp.label}</span>
        <span><strong>${g.vrp.daysCollected}/${g.vrp.daysRequired}</strong> days</span>
      </div>
      <div class="progress-outer"><div class="progress-inner" style="width:${vrpPct}%"></div></div>
      <div style="font-size:0.7rem; color:var(--text-muted); margin-top:0.35rem;">
        last snapshot ${g.vrp.lastDate ?? '—'} · ${g.vrp.rule}</div>
    </div>
    ${['studyA', 'studyB', 'studyC'].map((k) => `
      <div class="gate-row" style="display:block;">
        <div style="display:flex; justify-content:space-between;">
          <span>${g[k].label}</span>
          <span class="pill ${g[k].status === 'PASS' ? 'pill-ok' : g[k].status === 'FAIL' ? 'pill-crit' : 'pill-muted'}">${g[k].status.replace('_', ' ')}</span>
        </div>
        ${g[k].detail ? `<div style="font-size:0.7rem; color:var(--text-muted); margin-top:0.3rem;">
          LS net Sharpe ${g[k].detail.lsNetSharpe} · PSR ${g[k].detail.psr} → ${g[k].detail.consequence}</div>` : ''}
      </div>`).join('')}
  `;

  // Risk flags
  const riskCard = document.getElementById('riskCard');
  const flags = [...o.riskFlags, ...(o.stopLossAlerts || [])];
  riskCard.innerHTML = flags.length
    ? flags.map((f) => `<div class="risk-flag ${f.severity}">${f.message}</div>`).join('')
    : '<span class="pill pill-ok">All clear</span>';

  // Positions
  const tbody = document.querySelector('#positionsTable tbody');
  if (!o.positions.length) {
    tbody.innerHTML = '<tr><td colspan="9" style="color:var(--text-muted);">No positions yet — record your first fill below.</td></tr>';
  } else {
    tbody.innerHTML = o.positions.map((p) => {
      const weight = o.nav > 0 ? (100 * p.marketValue) / o.nav : 0;
      return `
        <tr>
          <td>${p.sleeve}</td>
          <td><strong>${p.instrument}</strong></td>
          <td class="num">${fmtNum(p.qty, 4)}</td>
          <td class="num">${fmtNum(p.avgCost)}</td>
          <td class="num">${p.lastPrice ? fmtNum(p.lastPrice) : '<span title="No price history tracked">n/a</span>'}</td>
          <td class="num">${fmtINR(p.marketValue)}</td>
          <td class="num ${cls(p.pnl)}">${p.pnl == null ? '—' : fmtINR(p.pnl)}</td>
          <td class="num ${cls(p.pnlPct)}">${p.pnlPct == null ? '—' : fmtPct(p.pnlPct)}</td>
          <td class="num">${fmtPct(weight)}</td>
        </tr>`;
    }).join('');
  }
}

// ── Ledger ──────────────────────────────────────────────────

async function loadLedger() {
  const rows = await fetchJSON('/api/fund/ledger');
  const tbody = document.querySelector('#ledgerTable tbody');
  tbody.innerHTML = rows.length
    ? rows.slice().reverse().map((r) => `
        <tr>
          <td>${r.trade_date}</td><td>${r.sleeve}</td><td>${r.instrument}</td>
          <td><span class="pill ${r.side === 'BUY' || r.side === 'DEPOSIT' ? 'pill-ok' : 'pill-info'}">${r.side}</span></td>
          <td class="num">${fmtNum(r.qty, 4)}</td>
          <td class="num">${fmtNum(r.price)}</td>
          <td class="num">${fmtNum(r.fees)}</td>
          <td><button class="btn-del" data-id="${r.id}" title="Delete entry">✕</button></td>
        </tr>`).join('')
    : '<tr><td colspan="8" style="color:var(--text-muted);">Empty ledger.</td></tr>';

  tbody.querySelectorAll('.btn-del').forEach((btn) =>
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this ledger entry?')) return;
      try {
        await fetchJSON(`/api/fund/ledger/${btn.dataset.id}`, { method: 'DELETE' });
        await refresh();
      } catch (e) {
        showMsg(`Delete failed: ${e.message}`, true);
      }
    })
  );
}

function showMsg(text, isError) {
  const el = document.getElementById('ledgerMsg');
  el.textContent = text;
  el.style.color = isError ? 'var(--danger)' : 'var(--success)';
  setTimeout(() => { el.textContent = ''; }, 6000);
}

document.getElementById('ledgerForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const body = Object.fromEntries(new FormData(form).entries());
  try {
    await fetchJSON('/api/fund/ledger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    showMsg('Recorded.');
    form.reset();
    form.querySelector('[name=fees]').value = '0';
    await refresh();
  } catch (e) {
    showMsg(`Failed: ${e.message}`, true);
  }
});

async function refresh() {
  try {
    await Promise.all([loadOverview(), loadLedger()]);
  } catch (e) {
    console.error('Fund page load failed:', e);
    document.getElementById('riskCard').innerHTML =
      `<div class="risk-flag critical">Failed to load: ${e.message}</div>`;
  }
}

refresh();
