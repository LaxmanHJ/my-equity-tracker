/**
 * Stock Portfolio Analyzer - Frontend Application
 */

const API_BASE = '/api';

// State
let portfolioData = null;
let charts = {};

// =============================================
// Initialization
// =============================================

document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initModals();
  loadPortfolio();
  checkMarketStatus();

  // Auto-refresh every 5 minutes
  setInterval(loadPortfolio, 5 * 60 * 1000);
});

// =============================================
// Navigation
// =============================================

function initNavigation() {
  const navLinks = document.querySelectorAll('.nav-link');

  navLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      const sectionId = link.dataset.section;

      // Only hijack navigation if it's a single-page section link
      if (!sectionId) {
        return; // Let normal HTML href navigation happen
      }

      e.preventDefault();

      // Update active states
      navLinks.forEach(l => l.classList.remove('active'));
      link.classList.add('active');

      // Show section if it exists on current page
      document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
      const sectionEl = document.getElementById(sectionId);
      if (sectionEl) {
        sectionEl.classList.add('active');
      }

      // Load section data
      if (sectionId === 'quant') { loadQuantScores(); loadICWeights(); }
      if (sectionId === 'index-analysis') loadIndexAnalysis();
      if (sectionId === 'signal-quality') loadSignalQuality();
    });
  });

  // Fetch Fundamentals button
  document.getElementById('fetchFundamentalsBtn').addEventListener('click', fetchFundamentals);
  document.getElementById('forceSyncBtn').addEventListener('click', forceSyncPortfolio);
}

// =============================================
// News
// =============================================

async function loadNews() {
  const container = document.getElementById("newsContainer");
  container.innerHTML = "Loading news...";

  try {
    const response = await fetch(`${API_BASE}/news`);
    const result = await response.json();

    const news = result.news;

    if (!news || news.length === 0) {
      container.innerHTML = "<p>No news available</p>";
      return;
    }

    container.innerHTML = news.map(article => `
      <div style="margin-bottom:20px;border-bottom:1px solid #333;padding-bottom:15px;">
        <h3>${article.title}</h3>
        <p style="color:#94a3b8;font-size:14px;">
          ${new Date(article.published_at).toLocaleString()}
        </p>
        <p>${article.description || ""}</p>
        <a href="${article.url}" target="_blank">Read more →</a>
      </div>
    `).join("");

  } catch (error) {
    console.error(error);
    container.innerHTML = "<p>Failed to load news</p>";
  }
}

// =============================================
// Portfolio Loading
// =============================================

async function loadPortfolio() {
  try {
    const response = await fetch(`${API_BASE}/portfolio`);
    if (!response.ok) throw new Error('Failed to fetch portfolio');

    portfolioData = await response.json();
    renderPortfolio(portfolioData);
    renderCharts(portfolioData);
    populateStockSelectors(portfolioData.holdings);
    updateLastUpdated();

  } catch (error) {
    console.error('Error loading portfolio:', error);
    showToast('Failed to load portfolio data', 'error');
  }
}

async function forceSyncPortfolio() {
  const btn = document.getElementById('forceSyncBtn');
  try {
    btn.innerHTML = '<span class="loading-spinner" style="display:inline-block;width:12px;height:12px;border:2px solid #fff;border-bottom-color:transparent;border-radius:50%;margin-right:5px;animation:spin 0.8s linear infinite;"></span> Syncing...';
    btn.disabled = true;

    // Phase 1: Write fresh OHLCV data to SQLite (single source of truth)
    const syncRes = await fetch(`${API_BASE}/portfolio/sync`, { method: 'POST' });
    if (!syncRes.ok) throw new Error('Failed to sync data to database');
    const syncData = await syncRes.json();
    console.log(`[ForceSync] DB updated: ${syncData.synced} holdings`);

    // Phase 2: Re-fetch portfolio summary from the now-updated DB
    const portfolioRes = await fetch(`${API_BASE}/portfolio`);
    if (!portfolioRes.ok) throw new Error('Failed to load portfolio from DB');
    portfolioData = await portfolioRes.json();
    renderPortfolio(portfolioData);
    renderCharts(portfolioData);
    populateStockSelectors(portfolioData.holdings);
    updateLastUpdated();

    // Phase 3: Re-fetch Quant Signals from the same updated DB
    const quantSection = document.getElementById('quant');
    if (quantSection) {
      await loadQuantScores();
    }

    showToast('Successfully synchronized all pages from latest market data', 'success');

  } catch (error) {
    console.error('Error force syncing portfolio:', error);
    showToast('Failed to synchronize market data', 'error');
  } finally {
    btn.innerHTML = '<span class="btn-icon">⚡</span> Force Sync';
    btn.disabled = false;
  }
}

// =============================================
// Fetch Fundamentals (RapidAPI)
// =============================================
async function fetchFundamentals() {
  const btn = document.getElementById('fetchFundamentalsBtn');
  try {
    btn.innerHTML = '<span class="loading-spinner" style="display:inline-block;width:12px;height:12px;border:2px solid #fff;border-bottom-color:transparent;border-radius:50%;margin-right:5px;animation:spin 0.8s linear infinite;"></span> Fetching...';
    btn.disabled = true;

    showToast('📊 Fetching fundamentals from RapidAPI... This may take 15-20 seconds', 'info');

    const res = await fetch(`${API_BASE}/fundamentals/sync`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to sync fundamentals');
    const data = await res.json();

    const { summary } = data;
    showToast(`📊 Fundamentals synced: ${summary.success}/${summary.total} stocks fetched successfully`, 'success');

  } catch (error) {
    console.error('Error fetching fundamentals:', error);
    showToast('Failed to fetch fundamentals data', 'error');
  } finally {
    btn.innerHTML = '<span class="btn-icon">📊</span> Fetch Fundamentals';
    btn.disabled = false;
  }
}

// =============================================
// Render Fundamentals Card (Analysis Tab)
// =============================================
function renderFundamentalsCard(f) {
  const fmt = (v, decimals = 2) => v != null ? Number(v).toFixed(decimals) : 'N/A';
  const fmtPct = (v) => v != null ? `${Number(v).toFixed(2)}%` : 'N/A';
  const fmtCr = (v) => v != null ? `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr` : 'N/A';
  const valColor = (v, goodAbove, badBelow) => {
    if (v == null) return 'var(--text-muted)';
    if (goodAbove !== undefined && v >= goodAbove) return 'var(--success)';
    if (badBelow !== undefined && v <= badBelow) return 'var(--danger)';
    return 'var(--text-primary)';
  };

  const staleness = f.fetched_at
    ? `<span style="font-size:0.75rem;color:var(--text-muted);margin-left:0.5rem;">Last updated: ${new Date(f.fetched_at + 'Z').toLocaleDateString('en-IN')}</span>`
    : '';

  const peerRows = (f.peers || []).slice(0, 5).map(p => `
    <tr>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color)">${p.peer_name}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmt(p.peer_pe)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmt(p.peer_pb)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmtPct(p.peer_npm_ttm)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmtCr(p.peer_market_cap)}</td>
    </tr>
  `).join('');

  const annualFin = (f.financials || []).filter(x => x.statement_type === 'Annual').slice(0, 4);
  const finRows = annualFin.map(yr => `
    <tr>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color)">${yr.fiscal_year}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmtCr(yr.revenue)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmtCr(yr.net_income)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmt(yr.eps_diluted)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmtCr(yr.total_debt)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid var(--border-color);text-align:right">${fmtCr(yr.free_cash_flow)}</td>
    </tr>
  `).join('');

  return `
    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg); border: 1px solid rgba(139,92,246,0.2);">
      <h3 style="margin-bottom: var(--space-md); display: flex; align-items: center; gap: 0.5rem;">
        📊 Fundamentals — ${f.company_name || 'Unknown'} <span style="font-size:0.85rem;color:var(--text-muted);font-weight:400;">(${f.industry || '—'})</span>
        ${staleness}
      </h3>

      <!-- Valuation -->
      <div style="margin-bottom: var(--space-lg);">
        <h4 style="font-size:0.9rem; color: var(--accent); margin-bottom: var(--space-sm);">Valuation</h4>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: var(--space-md);">
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">P/E Ratio</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.pe_ratio, undefined, 0)}">${fmt(f.pe_ratio)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">P/B Ratio</div><div style="font-size: 1.15rem; font-weight: 600;">${fmt(f.pb_ratio)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">EPS (Diluted)</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.eps_diluted, 0, 0)}">${fmt(f.eps_diluted)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Dividend Yield</div><div style="font-size: 1.15rem; font-weight: 600;">${fmtPct(f.dividend_yield)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Mkt Cap</div><div style="font-size: 1.15rem; font-weight: 600;">${fmtCr(f.market_cap)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">52W Range</div><div style="font-size: 1.15rem; font-weight: 600;">₹${fmt(f.year_low, 0)} — ₹${fmt(f.year_high, 0)}</div></div>
        </div>
      </div>

      <!-- Profitability -->
      <div style="margin-bottom: var(--space-lg);">
        <h4 style="font-size:0.9rem; color: var(--accent); margin-bottom: var(--space-sm);">Profitability</h4>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--space-md);">
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Net Profit Margin (TTM)</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.net_profit_margin_ttm, 5, 0)}">${fmtPct(f.net_profit_margin_ttm)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">NPM (5Y Avg)</div><div style="font-size: 1.15rem; font-weight: 600;">${fmtPct(f.net_profit_margin_5y_avg)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Gross Margin (TTM)</div><div style="font-size: 1.15rem; font-weight: 600;">${fmtPct(f.gross_margin_ttm)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">ROE (5Y Avg)</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.roe_5y_avg, 15, 5)}">${fmtPct(f.roe_5y_avg)}</div></div>
        </div>
      </div>

      <!-- Growth -->
      <div style="margin-bottom: var(--space-lg);">
        <h4 style="font-size:0.9rem; color: var(--accent); margin-bottom: var(--space-sm);">Growth</h4>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--space-md);">
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Revenue Growth (5Y)</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.revenue_growth_5y, 10, 0)}">${fmtPct(f.revenue_growth_5y)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">EPS Growth (5Y)</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.eps_growth_5y, 10, 0)}">${fmtPct(f.eps_growth_5y)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Revenue Growth (3Y)</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.revenue_growth_3y, 10, 0)}">${fmtPct(f.revenue_growth_3y)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">EPS Growth (3Y)</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.eps_growth_3y, 10, 0)}">${fmtPct(f.eps_growth_3y)}</div></div>
        </div>
      </div>

      <!-- Financial Strength -->
      <div style="margin-bottom: var(--space-lg);">
        <h4 style="font-size:0.9rem; color: var(--accent); margin-bottom: var(--space-sm);">Financial Strength</h4>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--space-md);">
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Debt/Equity</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.debt_to_equity, undefined, 1)}">${fmt(f.debt_to_equity)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Current Ratio</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.current_ratio, 1.5, 1)}">${fmt(f.current_ratio)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Interest Coverage</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.interest_coverage, 3, 1.5)}">${fmt(f.interest_coverage)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Free Cash Flow</div><div style="font-size: 1.15rem; font-weight: 600; color: ${valColor(f.free_cash_flow, 0, 0)}">${fmtCr(f.free_cash_flow)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Beta</div><div style="font-size: 1.15rem; font-weight: 600;">${fmt(f.beta)}</div></div>
          <div><div style="color: var(--text-muted); font-size: 0.75rem;">Payout Ratio</div><div style="font-size: 1.15rem; font-weight: 600;">${fmtPct(f.payout_ratio)}</div></div>
        </div>
      </div>

      ${peerRows ? `
      <div style="margin-bottom: var(--space-lg);">
        <h4 style="font-size:0.9rem; color: var(--accent); margin-bottom: var(--space-sm);">Peer Comparison</h4>
        <div style="overflow-x: auto;">
          <table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
            <thead><tr style="border-bottom:2px solid var(--border-color);">
              <th style="padding:8px 10px;text-align:left;">Company</th>
              <th style="padding:8px 10px;text-align:right;">P/E</th>
              <th style="padding:8px 10px;text-align:right;">P/B</th>
              <th style="padding:8px 10px;text-align:right;">NPM (TTM)</th>
              <th style="padding:8px 10px;text-align:right;">Mkt Cap</th>
            </tr></thead>
            <tbody>${peerRows}</tbody>
          </table>
        </div>
      </div>` : ''}

      ${finRows ? `
      <div>
        <h4 style="font-size:0.9rem; color: var(--accent); margin-bottom: var(--space-sm);">Historical Financials (Annual)</h4>
        <div style="overflow-x: auto;">
          <table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
            <thead><tr style="border-bottom:2px solid var(--border-color);">
              <th style="padding:8px 10px;text-align:left;">FY</th>
              <th style="padding:8px 10px;text-align:right;">Revenue</th>
              <th style="padding:8px 10px;text-align:right;">Net Income</th>
              <th style="padding:8px 10px;text-align:right;">EPS</th>
              <th style="padding:8px 10px;text-align:right;">Total Debt</th>
              <th style="padding:8px 10px;text-align:right;">FCF</th>
            </tr></thead>
            <tbody>${finRows}</tbody>
          </table>
        </div>
      </div>` : ''}
    </div>
  `;
}

function renderPortfolio(data) {
  const { holdings, summary } = data;

  // Update summary cards
  document.getElementById('investedAmount').textContent = formatCurrency(summary.totalInvested);
  document.getElementById('currentValue').textContent = formatCurrency(summary.currentValue);

  const plElement = document.getElementById('totalPL');
  plElement.textContent = formatCurrency(summary.totalProfitLoss);
  plElement.className = `card-value ${summary.totalProfitLoss >= 0 ? 'positive' : 'negative'}`;

  // Calculate today's change
  let todayChange = 0;
  let todayChangePercent = 0;
  holdings.forEach(h => {
    if (h.changePercent) todayChangePercent += h.changePercent;
  });
  todayChangePercent = todayChangePercent / holdings.length;

  const todayElement = document.getElementById('todayChange');
  todayElement.textContent = `${todayChangePercent >= 0 ? '+' : ''}${todayChangePercent.toFixed(2)}%`;
  todayElement.className = `card-value ${todayChangePercent >= 0 ? 'positive' : 'negative'}`;

  // Render holdings table
  const tbody = document.getElementById('holdingsBody');
  tbody.innerHTML = holdings.map(stock => `
    <tr>
      <td>
        <div class="stock-name">
          <span class="stock-symbol">${stock.displaySymbol}</span>
          <span class="stock-full-name">${stock.name}</span>
        </div>
      </td>
      <td>
        <div>${stock.quantity}</div>
        <div style="font-size: 0.85rem; color: var(--text-muted);">${formatCurrency(stock.avgPrice)}</div>
      </td>
      <td>
        <div>${formatCurrency(stock.price)}</div>
        <div style="font-size: 0.85rem;" class="${stock.changePercent >= 0 ? 'change-positive' : 'change-negative'}">
          ${stock.changePercent >= 0 ? '+' : ''}${stock.changePercent.toFixed(2)}%
        </div>
      </td>
      <td>${formatCurrency(stock.invested)}</td>
      <td>${formatCurrency(stock.currentValue)}</td>
      <td>
        <div class="price-change ${stock.profitLoss >= 0 ? 'change-positive' : 'change-negative'}">
          ${formatCurrency(stock.profitLoss)}
        </div>
        <div style="font-size: 0.85rem;" class="${stock.profitLossPercent >= 0 ? 'change-positive' : 'change-negative'}">
           ${stock.profitLossPercent >= 0 ? '+' : ''}${stock.profitLossPercent.toFixed(2)}%
        </div>
      </td>
      <td>
        <button class="action-btn" onclick="viewAnalysis('${stock.displaySymbol}')">
          Analyze
        </button>
      </td>
    </tr>
  `).join('');
}

function renderRangeBar(current, low, high) {
  if (!low || !high || low === high) return '';
  const percent = ((current - low) / (high - low)) * 100;
  return `
    <div class="range-bar">
      <div class="range-fill" style="width: ${Math.min(100, Math.max(0, percent))}%"></div>
      <div class="range-marker" style="left: ${Math.min(100, Math.max(0, percent))}%"></div>
    </div>
  `;
}

// =============================================
// Charts
// =============================================

function renderCharts(data) {
  renderSectorChart(data.holdings);
  renderPerformersChart(data.holdings);
}

function renderSectorChart(holdings) {
  const ctx = document.getElementById('sectorChart');
  if (!ctx) return;

  // Group by sector
  const sectors = {};
  holdings.forEach(h => {
    if (!sectors[h.sector]) sectors[h.sector] = 0;
    sectors[h.sector]++;
  });

  // Destroy existing chart
  if (charts.sector) charts.sector.destroy();

  charts.sector = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: Object.keys(sectors),
      datasets: [{
        data: Object.values(sectors),
        backgroundColor: [
          '#6366f1', '#8b5cf6', '#a855f7', '#d946ef',
          '#ec4899', '#f43f5e', '#f97316', '#eab308',
          '#22c55e', '#14b8a6', '#06b6d4', '#3b82f6'
        ],
        borderWidth: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: {
            color: '#94a3b8',
            font: { family: 'Inter', size: 11 },
            padding: 15
          }
        }
      }
    }
  });
}

function renderPerformersChart(holdings) {
  const ctx = document.getElementById('performersChart');
  if (!ctx) return;

  // Sort by change percent
  const sorted = [...holdings].sort((a, b) => b.changePercent - a.changePercent);
  const top5 = sorted.slice(0, 5);

  // Destroy existing chart
  if (charts.performers) charts.performers.destroy();

  charts.performers = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: top5.map(h => h.displaySymbol),
      datasets: [{
        label: 'Change %',
        data: top5.map(h => h.changePercent),
        backgroundColor: top5.map(h => h.changePercent >= 0 ? '#10b981' : '#ef4444'),
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: {
        legend: { display: false }
      },
      scales: {
        x: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#94a3b8' }
        },
        y: {
          grid: { display: false },
          ticks: { color: '#f1f5f9' }
        }
      }
    }
  });
}

// =============================================
// Technical Analysis
// =============================================

function populateStockSelectors(holdings) {
  const selectors = [
    document.getElementById('analysisStockSelect'),
    document.getElementById('alertStock')
  ];

  selectors.forEach(select => {
    if (!select) return;
    const currentValue = select.value;
    select.innerHTML = '<option value="">Select a stock...</option>';
    holdings.forEach(h => {
      const option = document.createElement('option');
      option.value = h.displaySymbol;
      option.textContent = `${h.displaySymbol} - ${h.name}`;
      select.appendChild(option);
    });
    select.value = currentValue;
  });

  // Add change listener
  const analysisSelect = document.getElementById('analysisStockSelect');
  if (analysisSelect) {
    analysisSelect.addEventListener('change', () => {
      if (analysisSelect.value) loadAnalysis(analysisSelect.value);
    });
  }
}

async function loadAnalysis(symbol) {
  const container = document.getElementById('analysisContent');
  container.innerHTML = '<div class="loading-spinner"></div>';

  try {
    const [technicalRes, riskRes, sicilianRes, fundRes, analystRes, shareholdingRes, newsRes, momentumRes, bulkDealsRes] = await Promise.all([
      fetch(`${API_BASE}/analysis/technicals/${symbol}`),
      fetch(`${API_BASE}/analysis/risk/${symbol}`),
      fetch(`${API_BASE}/sicilian/${symbol}`).catch(() => null),
      fetch(`${API_BASE}/fundamentals/${symbol}`).catch(() => null),
      fetch(`${API_BASE}/fundamentals/${symbol}/analyst`).catch(() => null),
      fetch(`${API_BASE}/fundamentals/${symbol}/shareholding`).catch(() => null),
      fetch(`${API_BASE}/fundamentals/${symbol}/news`).catch(() => null),
      fetch(`${API_BASE}/sectors/momentum`).catch(() => null),
      fetch(`${API_BASE}/bulk-deals/${symbol}`).catch(() => null)
    ]);

    const technical = await technicalRes.json();
    const risk = await riskRes.json();
    const sicilian = sicilianRes && sicilianRes.ok ? await sicilianRes.json() : null;
    const fundamentals = fundRes && fundRes.ok ? await fundRes.json() : null;
    const analystRatings = analystRes && analystRes.ok ? await analystRes.json() : null;
    const shareholding = shareholdingRes && shareholdingRes.ok ? await shareholdingRes.json() : null;
    const news = newsRes && newsRes.ok ? await newsRes.json() : null;
    const momentum = momentumRes && momentumRes.ok ? await momentumRes.json() : null;
    const bulkDealsData = bulkDealsRes && bulkDealsRes.ok ? await bulkDealsRes.json() : null;

    renderAnalysis(technical, risk, sicilian, fundamentals, analystRatings, shareholding, news, momentum, bulkDealsData);

  } catch (error) {
    console.error('Error loading analysis:', error);
    container.innerHTML = '<div class="empty-state glass"><p>Failed to load analysis</p></div>';
  }
}

function renderAnalysis(technical, risk, sicilian, fundamentals, analystRatings, shareholding, news, momentumData, bulkDealsData) {
  const container = document.getElementById('analysisContent');
  const { signals, indicators } = technical.analysis.signals;
  const cmp = technical.analysis.cmp;

  // ── The Sicilian card ─────────────────────────────────
  const sicilianHtml = sicilian && sicilian.verdict && sicilian.verdict !== 'INSUFFICIENT_DATA'
    ? renderSicilianCard(sicilian)
    : (sicilian?.verdict === 'INSUFFICIENT_DATA'
      ? `<div class="glass" style="padding:var(--space-lg);margin-bottom:var(--space-lg);text-align:center;border:1px solid var(--border-color);">
           <h3 style="margin-bottom:0.5rem;">🏴 The Sicilian</h3>
           <p style="color:var(--text-muted);">Insufficient data for this stock</p>
         </div>`
      : `<div class="glass" style="padding:var(--space-lg);margin-bottom:var(--space-lg);text-align:center;border:1px solid var(--border-color);">
           <h3 style="margin-bottom:0.5rem;">🏴 The Sicilian</h3>
           <p style="color:var(--text-muted);">Engine offline — start with: <code>python3 -m quant_engine.main</code></p>
         </div>`);

  // ── Fundamentals card ─────────────────────────────────
  const fundamentalsHtml = fundamentals ? renderFundamentalsCard(fundamentals) : '';

  // Sector Momentum Badge
  let sectorBadgeHtml = '';
  if (momentumData && fundamentals && fundamentals.industry) {
    const sectorInfo = momentumData.find(m => m.industry === fundamentals.industry);
    if (sectorInfo) {
      const score = sectorInfo.momentum_score;
      const isBullish = score > 1;
      const isBearish = score < -1;
      const bColor = isBullish ? '#10b981' : isBearish ? '#ef4444' : '#f59e0b';
      const bText = isBullish ? 'Bullish' : isBearish ? 'Bearish' : 'Neutral';

      sectorBadgeHtml = `
        <div style="margin-top: 1rem; padding: 0.8rem; background: var(--bg-tertiary); border-radius: 8px; display: inline-flex; align-items: center; gap: 0.8rem; border: 1px solid ${bColor}44;">
          <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Sector Momentum (${fundamentals.industry})</div>
          <div style="font-size: 0.9rem; font-weight: 700; color: ${bColor};">${score > 0 ? '+' : ''}${score.toFixed(2)}% (${bText})</div>
        </div>
      `;
    }
  }

  container.innerHTML = `
    ${sicilianHtml}

    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg);">
      <h3 style="margin-bottom: var(--space-md);">Trading Signals - ${technical.symbol} , CMP - ₹${cmp?.toLocaleString('en-IN')}</h3>
      <div class="signal-cards">
        ${signals.map(s => `
          <div class="signal-card glass">
            <div class="signal-type">${s.type}</div>
            <div class="signal-value ${s.signal.toLowerCase().includes('bull') ? 'bullish' :
      s.signal.toLowerCase().includes('bear') ? 'bearish' : 'neutral'}">
              ${s.signal}
            </div>
            <div class="signal-description">${s.description}</div>
          </div>
        `).join('')}
      </div>
      ${sectorBadgeHtml}
    </div>
    
    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg);">
      <h3 style="margin-bottom: var(--space-md);">Technical Indicators</h3>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--space-md);">
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">RSI (14)</div>
          <div style="font-size: 1.25rem; font-weight: 600; color: ${indicators.rsi < 30 ? 'var(--success)' : indicators.rsi > 70 ? 'var(--danger)' : 'var(--text-primary)'
    }">${indicators.rsi?.toFixed(2) || 'N/A'}</div>
        </div>
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">MACD Histogram</div>
          <div style="font-size: 1.25rem; font-weight: 600; color: ${indicators.macd?.histogram > 0 ? 'var(--success)' : 'var(--danger)'
    }">${indicators.macd?.histogram?.toFixed(4) || 'N/A'}</div>
        </div>
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">SMA 20</div>
          <div style="font-size: 1.25rem; font-weight: 600;">₹${indicators.sma20?.toFixed(2) || 'N/A'}</div>
        </div>
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">SMA 50</div>
          <div style="font-size: 1.25rem; font-weight: 600;">₹${indicators.sma50?.toFixed(2) || 'N/A'}</div>
        </div>
      </div>
    </div>

    ${analystRatings ? renderAnalystConsensus(analystRatings) : ''}
    ${shareholding && shareholding.length > 0 ? renderShareholdingPattern(shareholding) : ''}
    ${bulkDealsData && bulkDealsData.deals && bulkDealsData.deals.length > 0 ? renderBulkDeals(bulkDealsData.deals) : ''}
    ${fundamentalsHtml}
    
    
    ${risk.risk ? `
    <div class="glass" style="padding: var(--space-lg);">
      <h3 style="margin-bottom: var(--space-md);">Risk Metrics</h3>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--space-lg);">
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">Beta (vs NIFTY 50)</div>
          <div style="font-size: 1.25rem; font-weight: 600;">${risk.risk.beta?.value?.toFixed(2) || 'N/A'}</div>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">${risk.risk.beta?.interpretation || ''}</div>
        </div>
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">Annualized Volatility</div>
          <div style="font-size: 1.25rem; font-weight: 600;">${risk.risk.volatility?.annualized?.toFixed(1)}%</div>
        </div>
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">Sharpe Ratio</div>
          <div style="font-size: 1.25rem; font-weight: 600;">${risk.risk.sharpeRatio?.value?.toFixed(2) || 'N/A'}</div>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">${risk.risk.sharpeRatio?.interpretation || ''}</div>
        </div>
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">Max Drawdown</div>
          <div style="font-size: 1.25rem; font-weight: 600; color: var(--danger);">${risk.risk.maxDrawdown?.percent}%</div>
        </div>
        <div>
          <div style="color: var(--text-muted); font-size: 0.8rem;">Value at Risk (95%)</div>
          <div style="font-size: 1.25rem; font-weight: 600;">${risk.risk.valueAtRisk?.varPercent}%</div>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">${risk.risk.valueAtRisk?.description || ''}</div>
        </div>
      </div>
    </div>
    ` : ''}
        ${news && news.length > 0 ? renderRecentNews(news) : ''}
  `;


}


/**
 * Render The Sicilian verdict card — the crown jewel of the analysis page.
 */
function renderSicilianCard(s) {
  const verdictConfig = {
    BUY: { emoji: '🟢', color: '#10b981', bg: 'rgba(16,185,129,0.08)', border: 'rgba(16,185,129,0.25)', label: 'BUY', targetLabel: 'Next-Day Entry Price' },
    SELL: { emoji: '🔴', color: '#ef4444', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.25)', label: 'SELL', targetLabel: 'Next-Day Exit Price' },
    HOLD: { emoji: '⚪', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.25)', label: 'HOLD', targetLabel: 'Fair Value' },
  };
  const v = verdictConfig[s.verdict] || verdictConfig.HOLD;

  // Score color gradient
  const scoreColor = s.sicilian_score > 0 ? '#10b981' : s.sicilian_score < 0 ? '#ef4444' : '#f59e0b';
  const scorePct = ((s.sicilian_score + 1) / 2 * 100).toFixed(0);

  // Confidence color
  const confColor = s.confidence >= 75 ? '#10b981' : s.confidence >= 50 ? '#f59e0b' : '#ef4444';

  // Sub-score bars
  const subScoreBars = Object.entries(s.sub_scores).map(([key, val]) => {
    const pct = ((val + 1) / 2 * 100).toFixed(0);
    const barColor = val > 0.1 ? '#10b981' : val < -0.1 ? '#ef4444' : '#6b7280';
    const weight = s.weights?.[key] ? `${(s.weights[key] * 100).toFixed(0)}%` : '';
    const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    return `
      <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:6px;">
        <span style="width:130px;font-size:0.75rem;color:var(--text-muted);text-transform:uppercase;">${label}</span>
        <div style="flex:1;height:8px;background:var(--bg-tertiary);border-radius:4px;overflow:hidden;position:relative;">
          <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:rgba(255,255,255,0.15);"></div>
          <div style="width:${pct}%;height:100%;background:${barColor};border-radius:4px;transition:width 0.6s ease;"></div>
        </div>
        <span style="width:45px;font-size:0.75rem;color:${barColor};text-align:right;font-weight:600;">${val > 0 ? '+' : ''}${val.toFixed(2)}</span>
        <span style="width:30px;font-size:0.65rem;color:var(--text-muted);text-align:right;">${weight}</span>
      </div>`;
  }).join('');

  // Support/resistance levels
  const sr = s.support_resistance || {};
  const levelsHtml = (sr.sma20 || sr.bollinger_upper) ? `
    <div style="margin-top:1rem;padding-top:1rem;border-top:1px solid var(--border-color);">
      <div style="font-size:0.75rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:0.5rem;">Key Levels</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:0.5rem;font-size:0.8rem;">
        ${sr.bollinger_upper ? `<div><span style="color:var(--text-muted);">BB Upper:</span> <strong>₹${sr.bollinger_upper.toLocaleString('en-IN')}</strong></div>` : ''}
        ${sr.sma20 ? `<div><span style="color:var(--text-muted);">SMA 20:</span> <strong>₹${sr.sma20.toLocaleString('en-IN')}</strong></div>` : ''}
        ${sr.bollinger_middle ? `<div><span style="color:var(--text-muted);">BB Mid:</span> <strong>₹${sr.bollinger_middle.toLocaleString('en-IN')}</strong></div>` : ''}
        ${sr.sma50 ? `<div><span style="color:var(--text-muted);">SMA 50:</span> <strong>₹${sr.sma50.toLocaleString('en-IN')}</strong></div>` : ''}
        ${sr.bollinger_lower ? `<div><span style="color:var(--text-muted);">BB Lower:</span> <strong>₹${sr.bollinger_lower.toLocaleString('en-IN')}</strong></div>` : ''}
      </div>
    </div>` : '';

  return `
    <div style="background:${v.bg};border:2px solid ${v.border};border-radius:16px;padding:1.5rem;margin-bottom:var(--space-lg);position:relative;overflow:hidden;">
      <!-- Decorative accent bar -->
      <div style="position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,${v.color},transparent);"></div>

      <!-- Header Row -->
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1.2rem;flex-wrap:wrap;gap:1rem;">
        <div>
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">
            <span style="font-size:1.5rem;">🏴</span>
            <h3 style="margin:0;font-size:1.4rem;font-weight:700;">The Sicilian</h3>
          </div>
          <div style="font-size:0.8rem;color:var(--text-muted);">${s.symbol} • CMP ₹${s.current_price?.toLocaleString('en-IN')} • ${s.data_points} data points</div>
        </div>

        <!-- Verdict Badge -->
        <div style="text-align:center;">
          <div style="display:inline-block;padding:12px 28px;border-radius:12px;background:${v.color};color:#fff;font-weight:800;font-size:1.5rem;letter-spacing:1px;box-shadow:0 4px 15px ${v.color}44;">
            ${v.emoji} ${v.label}
          </div>
        </div>
      </div>

      <!-- Metrics Row -->
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:1.2rem;">
        <!-- Score -->
        <div style="text-align:center;padding:1rem;background:var(--bg-secondary);border-radius:12px;border:1px solid var(--border-color);">
          <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:0.3rem;">Sicilian Score</div>
          <div style="font-size:1.8rem;font-weight:800;color:${scoreColor};">${s.sicilian_score > 0 ? '+' : ''}${s.sicilian_score.toFixed(2)}</div>
          <div style="height:6px;background:var(--bg-tertiary);border-radius:3px;margin-top:0.4rem;overflow:hidden;">
            <div style="width:${scorePct}%;height:100%;background:${scoreColor};border-radius:3px;transition:width 0.6s;"></div>
          </div>
        </div>

        <!-- ML Probabilities -->
        ${s.ml_probabilities ? (() => {
          const p = s.ml_probabilities;
          const rows = [
            { label: 'BUY',  val: p.BUY  ?? 0, color: '#10b981' },
            { label: 'HOLD', val: p.HOLD ?? 0, color: '#f59e0b' },
            { label: 'SELL', val: p.SELL ?? 0, color: '#ef4444' },
          ];
          return `
          <div style="padding:1rem;background:var(--bg-secondary);border-radius:12px;border:1px solid var(--border-color);">
            <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:0.6rem;">ML Signal</div>
            ${rows.map(r => `
              <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.35rem;">
                <span style="font-size:0.65rem;color:${r.color};font-weight:700;width:28px;">${r.label}</span>
                <div style="flex:1;height:6px;background:var(--bg-tertiary);border-radius:3px;overflow:hidden;">
                  <div style="width:${(r.val*100).toFixed(1)}%;height:100%;background:${r.color};border-radius:3px;transition:width 0.6s;"></div>
                </div>
                <span style="font-size:0.7rem;color:var(--text-muted);width:34px;text-align:right;">${(r.val*100).toFixed(1)}%</span>
              </div>`).join('')}
          </div>`;
        })() : ''}

        <!-- Confidence -->
        <div style="text-align:center;padding:1rem;background:var(--bg-secondary);border-radius:12px;border:1px solid var(--border-color);">
          <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:0.3rem;">Confidence</div>
          <div style="font-size:1.8rem;font-weight:800;color:${confColor};">${s.confidence}%</div>
          <div style="height:6px;background:var(--bg-tertiary);border-radius:3px;margin-top:0.4rem;overflow:hidden;">
            <div style="width:${s.confidence}%;height:100%;background:${confColor};border-radius:3px;transition:width 0.6s;"></div>
          </div>
        </div>

        <!-- Target Price -->
        <div style="text-align:center;padding:1rem;background:var(--bg-secondary);border-radius:12px;border:1px solid var(--border-color);">
          <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:0.3rem;">${v.targetLabel}</div>
          <div style="font-size:1.8rem;font-weight:800;color:${v.color};">₹${s.target_price?.toLocaleString('en-IN')}</div>
          <div style="font-size:0.75rem;color:var(--text-muted);margin-top:0.3rem;">
            ${s.target_type === 'entry' ? '↓ Buy at or below' : s.target_type === 'exit' ? '↑ Sell at or above' : '≈ Fair value'}
          </div>
        </div>
      </div>

      <!-- Reasoning -->
      <div style="padding:0.8rem 1rem;background:var(--bg-secondary);border-radius:10px;margin-bottom:1rem;border:1px solid var(--border-color);">
        <div style="font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:0.3rem;">Reasoning</div>
        <div style="font-size:0.85rem;color:var(--text-primary);">${s.reasoning}</div>
      </div>

      <!-- Sub-Score Breakdown -->
      <div style="padding:1rem;background:var(--bg-secondary);border-radius:10px;border:1px solid var(--border-color);">
        <div style="font-size:0.75rem;color:var(--text-muted);text-transform:uppercase;margin-bottom:0.8rem;">Sub-Score Breakdown</div>
        ${subScoreBars}
      </div>

      ${levelsHtml}
    </div>`;
}

// =============================================
// New Component Renderers
// =============================================

function renderAnalystConsensus(data) {
  if (!data || !data.total_analysts) return '';

  const total = data.total_analysts;
  const metrics = [
    { label: 'Strong Buy', value: data.strong_buy, color: '#02552E' },
    { label: 'Buy', value: data.buy, color: '#06AA5A' },
    { label: 'Hold', value: data.hold, color: '#898989' },
    { label: 'Sell', value: data.sell, color: '#FF0000' },
    { label: 'Strong Sell', value: data.strong_sell, color: '#B40000' }
  ];

  const segments = metrics.map(m => {
    if (m.value === 0) return '';
    const pct = (m.value / total * 100).toFixed(1);
    return `<div style="width: ${pct}%; background: ${m.color}; height: 100%;" title="${m.label}: ${m.value} (${pct}%)"></div>`;
  }).join('');

  const legend = metrics.map(m => `
    <div style="text-align: center; flex: 1;">
      <div style="font-size: 1.2rem; font-weight: 700; color: ${m.color};">${m.value}</div>
      <div style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase;">${m.label}</div>
    </div>
  `).join('');

  return `
    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg);">
      <div style="display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: var(--space-md);">
        <h3 style="margin: 0;">Analyst Consensus</h3>
        ${data.mean_rating ? `<div style="font-size: 0.85rem; color: var(--text-muted);">Mean Rating: <strong>${data.mean_rating.toFixed(2)}</strong> (1=Strong Buy, 5=Strong Sell)</div>` : ''}
      </div>
      
      <div style="display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-bottom: 1rem; background: var(--bg-tertiary);">
        ${segments}
      </div>
      
      <div style="display: flex; justify-content: space-between; gap: 0.5rem;">
        ${legend}
      </div>
      
      ${data.risk_category ? `
        <div style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center;">
          <span style="font-size: 0.8rem; color: var(--text-muted);">Risk Meter Category</span>
          <span style="font-size: 0.85rem; font-weight: 600; padding: 4px 10px; background: var(--bg-tertiary); border-radius: 12px;">${data.risk_category}</span>
        </div>
      ` : ''}
    </div>
  `;
}

function renderShareholdingPattern(data) {
  if (!data || data.length === 0) return '';

  // Group by holding_date to find the latest quarter
  const dates = [...new Set(data.map(d => d.holding_date))].sort().reverse();
  const latestDate = dates[0];

  if (!latestDate) return '';
  const latestData = data.filter(d => d.holding_date === latestDate);

  return `
    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg);">
      <div style="display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: var(--space-md);">
        <h3 style="margin: 0;">Shareholding Pattern</h3>
        <div style="font-size: 0.8rem; color: var(--text-muted);">As of ${latestDate}</div>
      </div>
      
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--space-md);">
        ${latestData.map(d => `
          <div style="padding: 1rem; background: var(--bg-secondary); border-radius: 8px; border: 1px solid var(--border-color);">
            <div style="color: var(--text-muted); font-size: 0.8rem; margin-bottom: 0.3rem;">${d.category}</div>
            <div style="font-size: 1.5rem; font-weight: 700;">${d.percentage}%</div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

function renderBulkDeals(deals) {
  if (!deals || deals.length === 0) return '';

  const typeColor = (t) => {
    const upper = (t || '').toUpperCase();
    if (upper === 'BUY') return 'var(--success)';
    if (upper === 'SELL') return 'var(--danger)';
    return 'var(--text-secondary)';
  };
  const dealBadge = (d) => d === 'BULK' ? '#f59e0b' : '#8b5cf6';

  return `
    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg);">
      <h3 style="margin-bottom: var(--space-md);">Bulk &amp; Block Deals</h3>
      <div style="overflow-x: auto;">
        <table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">
          <thead>
            <tr style="color: var(--text-muted); border-bottom: 1px solid var(--border-color);">
              <th style="text-align: left; padding: 0.5rem 0.75rem;">Date</th>
              <th style="text-align: left; padding: 0.5rem 0.75rem;">Type</th>
              <th style="text-align: left; padding: 0.5rem 0.75rem;">Client</th>
              <th style="text-align: center; padding: 0.5rem 0.75rem;">B/S</th>
              <th style="text-align: right; padding: 0.5rem 0.75rem;">Qty</th>
              <th style="text-align: right; padding: 0.5rem 0.75rem;">Price (₹)</th>
            </tr>
          </thead>
          <tbody>
            ${deals.map(d => `
              <tr style="border-bottom: 1px solid var(--border-color)22;">
                <td style="padding: 0.5rem 0.75rem; color: var(--text-secondary);">${d.date}</td>
                <td style="padding: 0.5rem 0.75rem;">
                  <span style="font-size: 0.7rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; background: ${dealBadge(d.deal_type)}22; color: ${dealBadge(d.deal_type)};">
                    ${d.deal_type}
                  </span>
                </td>
                <td style="padding: 0.5rem 0.75rem; max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${d.client_name}">${d.client_name || '—'}</td>
                <td style="padding: 0.5rem 0.75rem; text-align: center; font-weight: 700; color: ${typeColor(d.trade_type)};">${d.trade_type || '—'}</td>
                <td style="padding: 0.5rem 0.75rem; text-align: right;">${d.quantity ? Number(d.quantity).toLocaleString('en-IN') : '—'}</td>
                <td style="padding: 0.5rem 0.75rem; text-align: right;">₹${d.price ? Number(d.price).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderRecentNews(news) {
  if (!news || news.length === 0) return '';

  const sortedNews = [...news].sort((a, b) => new Date(b.news_date) - new Date(a.news_date));

  return `
    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg);">
      <h3 style="margin-bottom: var(--space-md);">Recent News</h3>
      <div style="display: flex; flex-direction: column; gap: 1rem;">
        ${sortedNews.map(article => {
    const dateStr = new Date(article.news_date).toLocaleDateString();
    return `
          <a href="${article.url}" target="_blank" style="display: flex; gap: 1rem; text-decoration: none; color: inherit; padding: 1rem; background: var(--bg-secondary); border-radius: 8px; border: 1px solid var(--border-color); transition: border-color 0.2s;">
            ${article.thumbnail_url ? `<img src="${article.thumbnail_url}" style="width: 80px; height: 80px; object-fit: cover; border-radius: 6px;" alt="News thumbnail">` : ''}
            <div style="display: flex; flex-direction: column; justify-content: space-between;">
              <div style="font-weight: 600; font-size: 0.95rem; line-height: 1.4; margin-bottom: 0.5rem; color: var(--text-primary);">${article.headline}</div>
              <div style="display: flex; gap: 1rem; font-size: 0.75rem; color: var(--text-muted);">
                ${article.source && article.source !== 'undefined' ? `<span>${article.source}</span>` : ''}
                <span>${dateStr !== 'Invalid Date' ? dateStr : article.news_date}</span>
              </div>
            </div>
          </a>
        `}).join('')}
      </div>
    </div>
  `;
}

function viewAnalysis(symbol) {
  // Switch to analysis tab
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  document.querySelector('[data-section="analysis"]').classList.add('active');
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('analysis').classList.add('active');

  // Load analysis
  document.getElementById('analysisStockSelect').value = symbol;
  loadAnalysis(symbol);
}

// =============================================
// Correlation
// =============================================

async function loadCorrelationData() {
  try {
    const [diversification, highCorr, matrixData] = await Promise.all([
      fetch(`${API_BASE}/analysis/diversification`).then(r => r.json()),
      fetch(`${API_BASE}/analysis/high-correlations`).then(r => r.json()),
      fetch(`${API_BASE}/analysis/correlation`).then(r => r.json())
    ]);

    renderDiversification(diversification);
    renderHighCorrelations(highCorr);
    renderCorrelationMatrix(matrixData);

  } catch (error) {
    console.error('Error loading correlation data:', error);
  }
}

function renderDiversification(data) {
  const container = document.getElementById('diversificationCard');
  const levelClass = data.diversificationLevel.toLowerCase();

  container.innerHTML = `
    <div class="diversification-level">
      <span class="level-badge ${levelClass}">${data.diversificationLevel}</span>
      <span>Portfolio Diversification</span>
    </div>
    <p style="color: var(--text-secondary); margin-bottom: var(--space-md);">${data.recommendation}</p>
    <div style="display: flex; gap: var(--space-xl);">
      <div>
        <div style="color: var(--text-muted); font-size: 0.8rem;">Avg Correlation</div>
        <div style="font-size: 1.5rem; font-weight: 600;">${data.averageCorrelation.toFixed(2)}</div>
      </div>
      <div>
        <div style="color: var(--text-muted); font-size: 0.8rem;">Period</div>
        <div style="font-size: 1.5rem; font-weight: 600;">${data.period}</div>
      </div>
    </div>
  `;
}

function renderHighCorrelations(data) {
  const container = document.getElementById('highCorrelations');

  if (data.pairs.length === 0) {
    container.innerHTML = `
      <h3>Highly Correlated Pairs</h3>
      <div class="empty-state">
        <span class="empty-icon">✅</span>
        <p>No highly correlated pairs found</p>
        <p class="empty-hint">Your portfolio is well diversified!</p>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <h3>Highly Correlated Pairs (|r| >= ${data.threshold})</h3>
    ${data.pairs.map(pair => `
      <div class="correlation-pair">
        <div class="pair-stocks">
          ${pair.stockA.displaySymbol} ↔ ${pair.stockB.displaySymbol}
        </div>
        <div>
          <span class="pair-value ${pair.correlation > 0 ? 'positive' : 'negative'}">
            ${pair.correlation.toFixed(2)}
          </span>
        </div>
      </div>
    `).join('')}
  `;
}

function renderCorrelationMatrix(data) {
  const container = document.getElementById('correlationMatrix');

  if (!data || !data.matrix || data.matrix.length === 0) {
    container.innerHTML = '<h3>Correlation Matrix</h3><p>No data available to calculate correlations.</p>';
    return;
  }

  let html = `
    <h3>Correlation Matrix</h3>
    <div style="overflow-x: auto; margin-top: var(--space-md);">
      <table class="data-table" style="font-size: 0.85rem; text-align: center;">
        <thead>
          <tr>
            <th style="text-align: left;">Stock</th>`;

  // Headers
  data.symbols.forEach(s => {
    html += `<th>${s.displaySymbol || s.symbol}</th>`;
  });
  html += '</tr></thead><tbody>';

  // Rows
  data.matrix.forEach(row => {
    html += `<tr><td style="text-align: left;"><strong>${row.displaySymbol || row.symbol}</strong></td>`;
    data.symbols.forEach(col => {
      const val = row.correlations[col.symbol];
      if (val === null || val === undefined) {
        html += `<td>-</td>`;
      } else if (col.symbol === row.symbol) {
        html += `<td style="color: var(--text-muted);">-</td>`;
      } else {
        const opacity = Math.abs(val);
        // Conditional background colors for correlation strength
        const bgColor = val > 0 ? `rgba(16, 185, 129, ${opacity * 0.7})` : `rgba(239, 68, 68, ${opacity * 0.7})`;
        html += `<td style="background-color: ${bgColor}; color: ${opacity > 0.5 ? '#fff' : 'inherit'}; border-radius: 4px;">
                    ${val.toFixed(2)}
                  </td>`;
      }
    });
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// =============================================
// Alerts
// =============================================

function initModals() {
  const alertModal = document.getElementById('alertModal');
  const newAlertBtn = document.getElementById('newAlertBtn');
  const closeBtn = document.getElementById('closeAlertModal');
  const cancelBtn = document.getElementById('cancelAlert');
  const alertForm = document.getElementById('alertForm');

  newAlertBtn?.addEventListener('click', () => alertModal.classList.add('active'));
  closeBtn?.addEventListener('click', () => alertModal.classList.remove('active'));
  cancelBtn?.addEventListener('click', () => alertModal.classList.remove('active'));

  alertModal?.addEventListener('click', (e) => {
    if (e.target === alertModal) alertModal.classList.remove('active');
  });

  alertForm?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const symbol = document.getElementById('alertStock').value;
    const direction = document.getElementById('alertDirection').value;
    const threshold = parseFloat(document.getElementById('alertThreshold').value);

    try {
      const response = await fetch(`${API_BASE}/alerts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, direction, threshold })
      });

      if (response.ok) {
        showToast('Alert created successfully', 'success');
        alertModal.classList.remove('active');
        alertForm.reset();
        loadAlerts();
      } else {
        throw new Error('Failed to create alert');
      }
    } catch (error) {
      showToast('Failed to create alert', 'error');
    }
  });
}

async function loadAlerts() {
  try {
    const response = await fetch(`${API_BASE}/alerts`);
    const data = await response.json();
    renderAlerts(data.alerts);
  } catch (error) {
    console.error('Error loading alerts:', error);
  }
}

function renderAlerts(alerts) {
  const container = document.getElementById('alertsList');

  if (alerts.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <span class="empty-icon">🔔</span>
        <p>No active alerts</p>
        <p class="empty-hint">Create alerts to get notified when stock prices hit your targets</p>
      </div>
    `;
    return;
  }

  container.innerHTML = alerts.map(alert => `
    <div class="alert-item">
      <div class="alert-info">
        <span class="alert-stock">${alert.symbol}</span>
        <span class="alert-condition">
          Price goes ${alert.direction} ₹${alert.threshold}
        </span>
      </div>
      <button class="alert-delete" onclick="deleteAlert(${alert.id})">🗑️</button>
    </div>
  `).join('');
}

async function deleteAlert(id) {
  try {
    await fetch(`${API_BASE}/alerts/${id}`, { method: 'DELETE' });
    showToast('Alert deleted', 'success');
    loadAlerts();
  } catch (error) {
    showToast('Failed to delete alert', 'error');
  }
}

// =============================================
// Market Status
// =============================================

function checkMarketStatus() {
  const now = new Date();
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
  const day = ist.getDay();
  const hours = ist.getHours();
  const minutes = ist.getMinutes();
  const time = hours * 60 + minutes;

  const marketOpen = 9 * 60 + 15;
  const marketClose = 15 * 60 + 30;

  const isOpen = day >= 1 && day <= 5 && time >= marketOpen && time <= marketClose;

  const statusEl = document.getElementById('marketStatus');
  statusEl.className = `market-status ${isOpen ? 'open' : 'closed'}`;
  statusEl.querySelector('.status-text').textContent = isOpen ? 'Market Open' : 'Market Closed';
}

// =============================================
// Utilities
// =============================================

function formatCurrency(amount) {
  if (amount === null || amount === undefined) return '₹0';
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  }).format(amount);
}

function updateLastUpdated() {
  document.getElementById('lastUpdated').textContent = new Date().toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata'
  });
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => toast.remove(), 3000);
}

// Make functions available globally
window.viewAnalysis = viewAnalysis;
window.deleteAlert = deleteAlert;

// =============================================
// Index Analysis (Markov + Mean Reversion)
// =============================================

async function loadIndexAnalysis() {
  const container = document.getElementById('indexAnalysisContent');
  container.innerHTML = '<div class="glass" style="padding:2rem;text-align:center;"><div class="loading-spinner"></div><p style="color:var(--text-muted);margin-top:1rem;">Loading index analysis...</p></div>';

  try {
    const response = await fetch(`${API_BASE}/index-analysis`);
    if (!response.ok) throw new Error('Quant engine unavailable');
    const data = await response.json();

    container.innerHTML = '';
    for (const [key, index] of Object.entries(data)) {
      container.innerHTML += renderIndexCard(index);
    }
  } catch (error) {
    console.error('Error loading index analysis:', error);
    container.innerHTML = `
      <div class="glass" style="padding:2rem;text-align:center;">
        <p style="color:var(--danger);">⚠️ Quant Engine offline</p>
        <p style="color:var(--text-muted);font-size:0.85rem;">Start it with: <code>python3 -m quant_engine.main</code></p>
        <p style="color:var(--text-muted);font-size:0.85rem;margin-top:0.5rem;">Also ensure index data is synced via: <code>POST /api/sync/indexes</code></p>
      </div>`;
  }
}

function renderIndexCard(index) {
  if (index.error && !index.price) {
    return `
      <div class="glass" style="padding:1.5rem;">
        <h3>${index.name}</h3>
        <p style="color:var(--text-muted);">${index.error}</p>
      </div>`;
  }

  const markov = index.markov || {};
  const mr = index.mean_reversion || {};

  // Regime badge colors
  const regimeColors = { Bull: '#10b981', Sideways: '#f59e0b', Bear: '#ef4444', Unknown: '#6b7280' };
  const regimeColor = regimeColors[markov.current_regime] || '#6b7280';

  // MR signal colors
  const signalColors = {
    OVERSOLD_BUY: '#10b981', MILD_OVERSOLD: '#34d399',
    NEUTRAL: '#6b7280',
    MILD_OVERBOUGHT: '#f97316', OVERBOUGHT_SELL: '#ef4444',
    INSUFFICIENT_DATA: '#6b7280', NO_DATA: '#6b7280'
  };
  const signalColor = signalColors[mr.signal] || '#6b7280';
  const signalLabel = (mr.signal || 'N/A').replace(/_/g, ' ');

  // Transition matrix HTML
  let matrixHtml = '';
  if (markov.transition_matrix && markov.transition_matrix.length > 0) {
    matrixHtml = `
      <div style="margin-top:1rem;">
        <div style="color:var(--text-muted);font-size:0.75rem;text-transform:uppercase;margin-bottom:0.5rem;">Transition Probabilities</div>
        <table style="width:100%;border-collapse:collapse;font-size:0.8rem;text-align:center;">
          <thead>
            <tr>
              <th style="padding:4px;color:var(--text-muted);text-align:left;">From → To</th>
              <th style="padding:4px;color:#ef4444;">Bear</th>
              <th style="padding:4px;color:#f59e0b;">Sideways</th>
              <th style="padding:4px;color:#10b981;">Bull</th>
            </tr>
          </thead>
          <tbody>
            ${markov.transition_matrix.map(row => {
      const fromColor = regimeColors[row.from];
      return `<tr>
                <td style="padding:4px;text-align:left;color:${fromColor};font-weight:600;">${row.from}</td>
                ${['Bear', 'Sideways', 'Bull'].map(to => {
        const prob = row.to[to];
        const opacity = Math.max(0.1, prob);
        const bgColor = regimeColors[to];
        return `<td style="padding:4px;background:${bgColor}${Math.round(opacity * 40).toString(16).padStart(2, '0')};border-radius:4px;">${(prob * 100).toFixed(1)}%</td>`;
      }).join('')}
              </tr>`;
    }).join('')}
          </tbody>
        </table>
      </div>`;
  }

  // Regime distribution
  let distHtml = '';
  if (markov.regime_distribution) {
    distHtml = `
      <div style="margin-top:1rem;">
        <div style="color:var(--text-muted);font-size:0.75rem;text-transform:uppercase;margin-bottom:0.5rem;">Regime Distribution (1Y)</div>
        <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;gap:2px;">
          ${Object.entries(markov.regime_distribution).map(([regime, pct]) => `
            <div style="width:${pct}%;background:${regimeColors[regime]};" title="${regime}: ${pct}%"></div>
          `).join('')}
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:4px;font-size:0.7rem;color:var(--text-muted);">
          ${Object.entries(markov.regime_distribution).map(([regime, pct]) => `
            <span><span style="color:${regimeColors[regime]};">●</span> ${regime} ${pct}%</span>
          `).join('')}
        </div>
      </div>`;
  }

  // Bollinger Band gauge
  const bbPct = mr.bollinger_pct !== undefined ? (mr.bollinger_pct * 100).toFixed(0) : 50;

  return `
    <div class="glass index-card" style="padding:1.5rem;margin-bottom:1.5rem;">
      <!-- Header -->
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
        <div>
          <h3 style="margin:0;font-size:1.3rem;">${index.name}</h3>
          <span style="color:var(--text-muted);font-size:0.85rem;">₹${index.price?.toLocaleString('en-IN') || 'N/A'} • ${index.data_points || 0} data points</span>
        </div>
        <div style="text-align:right;">
          <span style="display:inline-block;padding:6px 16px;border-radius:20px;background:${regimeColor}22;color:${regimeColor};font-weight:700;font-size:0.9rem;border:1px solid ${regimeColor}44;">
            ${markov.current_regime || 'Unknown'}
          </span>
          ${markov.regime_streak ? `<div style="font-size:0.75rem;color:var(--text-muted);margin-top:4px;">${markov.regime_streak} day streak</div>` : ''}
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;">
        <!-- Markov Column -->
        <div style="border-right:1px solid var(--border-color);padding-right:1.5rem;">
          <div style="font-size:0.85rem;font-weight:600;margin-bottom:0.5rem;">🔗 Markov Chain</div>
          
          <!-- Next Day Prediction -->
          ${markov.next_day_probabilities ? `
            <div style="color:var(--text-muted);font-size:0.75rem;text-transform:uppercase;margin-bottom:0.3rem;">Next Day Prediction</div>
            <div style="display:flex;gap:0.5rem;margin-bottom:0.5rem;">
              ${Object.entries(markov.next_day_probabilities).map(([regime, prob]) => `
                <div style="flex:1;text-align:center;padding:8px;border-radius:8px;background:${regimeColors[regime]}15;border:1px solid ${regimeColors[regime]}33;">
                  <div style="font-size:1.1rem;font-weight:700;color:${regimeColors[regime]};">${(prob * 100).toFixed(1)}%</div>
                  <div style="font-size:0.7rem;color:var(--text-muted);">${regime}</div>
                </div>
              `).join('')}
            </div>
          ` : ''}

          ${matrixHtml}
          ${distHtml}
        </div>

        <!-- Mean Reversion Column -->
        <div>
          <div style="font-size:0.85rem;font-weight:600;margin-bottom:0.5rem;">📐 Mean Reversion</div>
          
          <!-- Signal Badge -->
          <div style="margin-bottom:1rem;">
            <span style="display:inline-block;padding:6px 14px;border-radius:8px;background:${signalColor}22;color:${signalColor};font-weight:600;font-size:0.85rem;border:1px solid ${signalColor}44;">
              ${signalLabel}
            </span>
            ${mr.strength !== undefined ? `<span style="font-size:0.8rem;color:var(--text-muted);margin-left:8px;">Strength: ${(mr.strength * 100).toFixed(0)}%</span>` : ''}
          </div>

          <!-- Metrics Grid -->
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.8rem;">
            <div>
              <div style="color:var(--text-muted);font-size:0.7rem;text-transform:uppercase;">Z-Score (20d)</div>
              <div style="font-size:1.1rem;font-weight:600;color:${mr.z_score_20 > 1.5 ? '#ef4444' : mr.z_score_20 < -1.5 ? '#10b981' : 'var(--text-primary)'}">${mr.z_score_20?.toFixed(2) || 'N/A'}</div>
            </div>
            <div>
              <div style="color:var(--text-muted);font-size:0.7rem;text-transform:uppercase;">Z-Score (50d)</div>
              <div style="font-size:1.1rem;font-weight:600;color:${mr.z_score_50 > 1.5 ? '#ef4444' : mr.z_score_50 < -1.5 ? '#10b981' : 'var(--text-primary)'}">${mr.z_score_50?.toFixed(2) || 'N/A'}</div>
            </div>
            <div>
              <div style="color:var(--text-muted);font-size:0.7rem;text-transform:uppercase;">RSI</div>
              <div style="font-size:1.1rem;font-weight:600;color:${mr.rsi > 70 ? '#ef4444' : mr.rsi < 30 ? '#10b981' : 'var(--text-primary)'}">${mr.rsi?.toFixed(1) || 'N/A'}</div>
            </div>
            <div>
              <div style="color:var(--text-muted);font-size:0.7rem;text-transform:uppercase;">Bollinger %</div>
              <div style="font-size:1.1rem;font-weight:600;">${bbPct}%</div>
            </div>
          </div>

          <!-- SMA Levels -->
          ${mr.sma_20 ? `
          <div style="margin-top:1rem;padding-top:0.8rem;border-top:1px solid var(--border-color);">
            <div style="color:var(--text-muted);font-size:0.7rem;text-transform:uppercase;margin-bottom:0.3rem;">Key Levels</div>
            <div style="display:flex;justify-content:space-between;font-size:0.8rem;">
              <span>SMA 20: <strong>₹${mr.sma_20?.toLocaleString('en-IN')}</strong></span>
              <span>SMA 50: <strong>₹${mr.sma_50?.toLocaleString('en-IN')}</strong></span>
            </div>
            ${mr.upper_band ? `
            <div style="display:flex;justify-content:space-between;font-size:0.8rem;margin-top:4px;color:var(--text-muted);">
              <span>Lower Band: ₹${mr.lower_band?.toLocaleString('en-IN')}</span>
              <span>Upper Band: ₹${mr.upper_band?.toLocaleString('en-IN')}</span>
            </div>` : ''}
          </div>` : ''}
        </div>
      </div>
    </div>`;
}

// =============================================
// Signal Quality — historical diagnostic (quality) + live drift detector
// =============================================

let _sqDiag = null;   // cached /api/ml/diagnostic payload
let _sqLive = null;   // cached /api/quant/signal-quality payload

async function loadSignalQuality() {
  const journalEl = document.getElementById('sqJournalTable');
  const diagMetaEl = document.getElementById('sqDiagMeta');
  journalEl.innerHTML = '<p style="color:var(--text-muted);padding:2rem;text-align:center;">Loading...</p>';

  try {
    const [diagRes, liveRes] = await Promise.all([
      fetch('/api/ml/diagnostic'),
      fetch(`${API_BASE}/signal-quality`),
    ]);
    const diagPayload = diagRes.ok ? await diagRes.json() : null;
    const live        = liveRes.ok ? await liveRes.json() : null;

    _sqDiag = diagPayload?.result || null;
    _sqLive = live;

    renderSignalDiagnostic(_sqDiag);
    renderSignalLiveDrift(_sqLive, _sqDiag);
    renderSignalJournal(journalEl, live?.recent_signals || []);
  } catch (err) {
    console.error('Signal quality load error:', err);
    if (diagMetaEl) diagMetaEl.textContent = 'Failed to load.';
    journalEl.innerHTML = `<p style="color:var(--danger);padding:2rem;text-align:center;">
      Failed to load signal quality — is the Python quant engine running?</p>`;
  }
}

// ── Historical Diagnostic (quality bar) ──────────────────────────────────────
function renderSignalDiagnostic(diag) {
  const metaEl    = document.getElementById('sqDiagMeta');
  const mlCardEl  = document.getElementById('sqDiagML');
  const linCardEl = document.getElementById('sqDiagLinear');
  const verdictEl = document.getElementById('sqDiagVerdict');
  const tableEl   = document.getElementById('sqHorizonTable');

  if (!diag) {
    metaEl.innerHTML = 'Diagnostic not yet computed. Run <code>POST /api/ml/diagnostic</code> or <code>python -m quant_engine.ml.diagnostic</code>.';
    mlCardEl.innerHTML = linCardEl.innerHTML = '';
    verdictEl.innerHTML = '';
    renderSignalDecayChart(null);
    tableEl.innerHTML = '';
    return;
  }

  const mlAgg  = diag.aggregate_pooled?.ml     || {};
  const linAgg = diag.aggregate_pooled?.linear || {};
  const ml20   = mlAgg['20d']  || {};
  const lin20  = linAgg['20d'] || {};

  const computed = diag.computed_at ? new Date(diag.computed_at).toISOString().slice(0, 10) : '?';
  metaEl.innerHTML = `
    Computed ${computed} · ${diag.n_samples_total?.toLocaleString() || '?'} rows ·
    ${diag.n_folds_completed || 0} folds · label = ${diag.label_horizon_days || '?'}d forward return,
    purge = ${diag.purge_days || '?'}d
  `;

  mlCardEl.innerHTML  = buildDiagEngineCard('ML model',     ml20);
  linCardEl.innerHTML = buildDiagEngineCard('Linear (7-factor)', lin20);

  // Verdict banner — which engine has the higher 20d IC on current data
  const mlIC  = ml20.mean_cs_ic;
  const linIC = lin20.mean_cs_ic;
  let verdictHtml = '';
  if (mlIC != null && linIC != null) {
    const better = linIC > mlIC ? 'Linear' : 'ML';
    const edge   = Math.abs((linIC ?? 0) - (mlIC ?? 0));
    const bg     = better === 'ML' ? 'rgba(99,102,241,0.12)' : 'rgba(16,185,129,0.12)';
    const fg     = better === 'ML' ? '#818cf8' : '#10b981';
    verdictHtml = `
      <div style="background:${bg};color:${fg};border-left:3px solid ${fg};padding:0.7rem 1rem;border-radius:6px;">
        <strong>${better}</strong> currently has the higher OOS 20d IC
        (${(linIC ?? 0).toFixed(3)} vs ${(mlIC ?? 0).toFixed(3)} — edge ${edge.toFixed(3)}).
        Treat this as the preferred engine for next-day decisions.
      </div>`;
  }
  verdictEl.innerHTML = verdictHtml;

  renderSignalDecayChart(diag);
  renderDiagHorizonTable(tableEl, diag);
}

function buildDiagEngineCard(title, h) {
  const icColor = v =>
    v == null ? 'var(--text-muted)' :
    v >= 0.05 ? '#10b981' :
    v >= 0.02 ? '#f59e0b' :
    v >= 0    ? '#94a3b8' : '#ef4444';
  const hitColor = v =>
    v == null ? 'var(--text-muted)' :
    v >= 55   ? '#10b981' :
    v >= 50   ? '#f59e0b' : '#ef4444';
  const fmt = (v, d = 3) => v == null ? '—' : v.toFixed(d);

  return `
    <div style="font-size:0.82rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.4rem;">${title}</div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.6rem;">
      <div>
        <div style="font-size:0.72rem;color:var(--text-muted);">IC (20d)</div>
        <div style="font-size:1.15rem;font-weight:700;color:${icColor(h.mean_cs_ic)};">${fmt(h.mean_cs_ic, 3)}</div>
      </div>
      <div>
        <div style="font-size:0.72rem;color:var(--text-muted);">ICIR</div>
        <div style="font-size:1.15rem;font-weight:700;color:var(--text-primary);">${fmt(h.icir, 2)}</div>
      </div>
      <div>
        <div style="font-size:0.72rem;color:var(--text-muted);">Hit rate</div>
        <div style="font-size:1.15rem;font-weight:700;color:${hitColor(h.hit_rate)};">${h.hit_rate != null ? h.hit_rate.toFixed(1) + '%' : '—'}</div>
      </div>
    </div>
    <div style="margin-top:0.6rem;font-size:0.7rem;color:var(--text-muted);">
      n=${h.n_obs?.toLocaleString() ?? 0} OOS rows · ${h.n_dates ?? 0} distinct dates
    </div>`;
}

function renderSignalDecayChart(diag) {
  const ctx = document.getElementById('sqDecayChart');
  if (!ctx) return;
  if (charts.sqDecay) charts.sqDecay.destroy();

  if (!diag) { charts.sqDecay = null; return; }

  const horizons = ['1d', '5d', '10d', '20d'];
  const mlIC  = horizons.map(h => diag.aggregate_pooled?.ml?.[h]?.mean_cs_ic     ?? null);
  const linIC = horizons.map(h => diag.aggregate_pooled?.linear?.[h]?.mean_cs_ic ?? null);

  charts.sqDecay = new Chart(ctx, {
    type: 'line',
    data: {
      labels: horizons,
      datasets: [
        {
          label: 'ML IC',
          data: mlIC,
          borderColor: '#818cf8',
          backgroundColor: 'rgba(129,140,248,0.1)',
          tension: 0.3, pointRadius: 5,
        },
        {
          label: 'Linear IC',
          data: linIC,
          borderColor: '#10b981',
          backgroundColor: 'rgba(16,185,129,0.1)',
          tension: 0.3, pointRadius: 5,
          borderDash: [4, 3],
        },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#94a3b8', font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: c => c.raw == null ? `${c.dataset.label}: N/A` : `${c.dataset.label}: ${c.raw.toFixed(4)}`,
          }
        }
      },
      scales: {
        x: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } },
        y: {
          ticks: { color: '#94a3b8' },
          grid: { color: 'rgba(255,255,255,0.05)' },
          title: { display: true, text: 'Cross-sectional Spearman IC', color: '#64748b', font: { size: 11 } }
        }
      }
    }
  });
}

function renderDiagHorizonTable(el, diag) {
  if (!el || !diag) { el.innerHTML = ''; return; }
  const horizons = ['1d', '5d', '10d', '20d'];

  const icBadge = ic => {
    if (ic == null) return '<span style="color:var(--text-muted)">—</span>';
    const color = ic >= 0.05 ? '#10b981' : ic >= 0.02 ? '#f59e0b' : ic >= 0 ? '#94a3b8' : '#ef4444';
    return `<span style="color:${color};font-weight:600;">${ic.toFixed(4)}</span>`;
  };
  const hitBadge = hr => {
    if (hr == null) return '<span style="color:var(--text-muted)">—</span>';
    const color = hr >= 55 ? '#10b981' : hr >= 50 ? '#f59e0b' : '#ef4444';
    return `<span style="color:${color};font-weight:600;">${hr.toFixed(1)}%</span>`;
  };
  const icir = v => v == null ? '—' : v.toFixed(2);

  const row = h => {
    const m = diag.aggregate_pooled?.ml?.[h]     || {};
    const l = diag.aggregate_pooled?.linear?.[h] || {};
    return `
      <tr style="border-bottom:1px solid var(--border-color);">
        <td style="padding:7px 10px;font-weight:600;">${h}</td>
        <td style="padding:7px 10px;text-align:right;">${icBadge(m.mean_cs_ic)}</td>
        <td style="padding:7px 10px;text-align:right;color:var(--text-secondary);">${icir(m.icir)}</td>
        <td style="padding:7px 10px;text-align:right;">${hitBadge(m.hit_rate)}</td>
        <td style="padding:7px 10px;text-align:right;">${icBadge(l.mean_cs_ic)}</td>
        <td style="padding:7px 10px;text-align:right;color:var(--text-secondary);">${icir(l.icir)}</td>
        <td style="padding:7px 10px;text-align:right;">${hitBadge(l.hit_rate)}</td>
      </tr>`;
  };

  el.innerHTML = `
    <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
      <thead>
        <tr style="color:var(--text-muted);text-transform:uppercase;font-size:0.68rem;border-bottom:1px solid var(--border-color);">
          <th rowspan="2" style="padding:6px 10px;text-align:left;vertical-align:bottom;">Horizon</th>
          <th colspan="3" style="padding:6px 10px;text-align:center;border-bottom:1px solid var(--border-color);">ML</th>
          <th colspan="3" style="padding:6px 10px;text-align:center;border-bottom:1px solid var(--border-color);">Linear</th>
        </tr>
        <tr style="color:var(--text-muted);text-transform:uppercase;font-size:0.68rem;border-bottom:1px solid var(--border-color);">
          <th style="padding:6px 10px;text-align:right;">IC</th>
          <th style="padding:6px 10px;text-align:right;">ICIR</th>
          <th style="padding:6px 10px;text-align:right;">Hit</th>
          <th style="padding:6px 10px;text-align:right;">IC</th>
          <th style="padding:6px 10px;text-align:right;">ICIR</th>
          <th style="padding:6px 10px;text-align:right;">Hit</th>
        </tr>
      </thead>
      <tbody>${horizons.map(row).join('')}</tbody>
    </table>`;
}

// ── Live Drift Detector ──────────────────────────────────────────────────────
function renderSignalLiveDrift(live, diag) {
  const mlEl  = document.getElementById('sqLiveML');
  const linEl = document.getElementById('sqLiveLinear');
  if (!mlEl || !linEl) return;

  const mlBase  = diag?.aggregate_pooled?.ml?.['20d']?.mean_cs_ic     ?? null;
  const linBase = diag?.aggregate_pooled?.linear?.['20d']?.mean_cs_ic ?? null;

  mlEl.innerHTML  = buildLiveCard('ML live',     live?.ml?.summary,     mlBase);
  linEl.innerHTML = buildLiveCard('Linear live', live?.linear?.summary, linBase);
}

function buildLiveCard(title, summary, baselineIC) {
  if (!summary) {
    return `<div style="color:var(--text-muted);font-size:0.85rem;">${title}: no data.</div>`;
  }
  const { mean_ic_20d, hit_rate_20d, settled_20d, eligible_rows, total_signals } = summary;

  let drift = '';
  if (mean_ic_20d != null && baselineIC != null) {
    const delta = mean_ic_20d - baselineIC;
    // Rough IC SE ≈ 1/sqrt(n) per date pooled; flag drift if |delta| > 2×SE.
    const se = settled_20d > 0 ? 1 / Math.sqrt(settled_20d) : 1;
    const z  = delta / se;
    const ok = Math.abs(z) < 2;
    const color = ok ? '#10b981' : '#f59e0b';
    const tag   = ok ? 'within noise' : 'diverges from baseline';
    drift = `<span style="color:${color};font-weight:600;">Δ ${(delta >= 0 ? '+' : '') + delta.toFixed(3)} · ${tag}</span>`;
  } else if (settled_20d != null && settled_20d < 100) {
    drift = `<span style="color:var(--warning);">sample too small for drift check (n=${settled_20d})</span>`;
  }

  const ic  = mean_ic_20d   != null ? mean_ic_20d.toFixed(3)       : '—';
  const hit = hit_rate_20d  != null ? hit_rate_20d.toFixed(1) + '%' : '—';
  const base = baselineIC   != null ? baselineIC.toFixed(3)        : '—';

  return `
    <div style="font-size:0.82rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.5rem;">${title}</div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.6rem;">
      <div>
        <div style="font-size:0.72rem;color:var(--text-muted);">Live IC (20d)</div>
        <div style="font-size:1.05rem;font-weight:700;">${ic}</div>
      </div>
      <div>
        <div style="font-size:0.72rem;color:var(--text-muted);">Hit rate</div>
        <div style="font-size:1.05rem;font-weight:700;">${hit}</div>
      </div>
      <div>
        <div style="font-size:0.72rem;color:var(--text-muted);">Historical baseline</div>
        <div style="font-size:1.05rem;font-weight:700;color:var(--text-secondary);">${base}</div>
      </div>
    </div>
    <div style="margin-top:0.5rem;font-size:0.72rem;color:var(--text-muted);">
      eligible=${eligible_rows?.toLocaleString() ?? 0} · settled@20d=${settled_20d?.toLocaleString() ?? 0} · logged=${total_signals?.toLocaleString() ?? 0}
    </div>
    <div style="margin-top:0.3rem;font-size:0.78rem;">${drift}</div>`;
}

function renderSignalJournal(el, signals) {
  if (!signals || !signals.length) {
    el.innerHTML = '<p style="color:var(--text-muted);padding:2rem;text-align:center;">No signals logged yet — run Quant Scores to start building the journal.</p>';
    return;
  }

  const fmtRet = (v, signal, dir) => {
    if (v == null) return '<span style="color:var(--text-muted);font-size:0.75rem;">pending</span>';
    const color = v > 0 ? '#10b981' : v < 0 ? '#ef4444' : '#94a3b8';
    const pct   = `<span style="color:${color};">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
    if (dir === '20d' && signal !== 'HOLD') {
      const win = (signal === 'LONG' && v > 0) || (signal === 'SHORT' && v < 0);
      const badge = win
        ? `<span style="margin-left:4px;font-size:0.65rem;padding:2px 6px;background:rgba(16,185,129,0.15);color:#10b981;border-radius:10px;">WIN</span>`
        : `<span style="margin-left:4px;font-size:0.65rem;padding:2px 6px;background:rgba(239,68,68,0.15);color:#ef4444;border-radius:10px;">LOSS</span>`;
      return pct + badge;
    }
    return pct;
  };

  const signalColor = s => s === 'LONG' ? '#10b981' : s === 'SHORT' ? '#ef4444' : '#94a3b8';

  const signalBadge = (sig) => {
    if (!sig) return '<span style="color:var(--text-muted)">—</span>';
    const bg    = sig === 'LONG' ? 'rgba(16,185,129,0.15)' : sig === 'SHORT' ? 'rgba(239,68,68,0.15)' : 'rgba(148,163,184,0.1)';
    const color = signalColor(sig);
    return `<span style="padding:2px 8px;border-radius:10px;font-size:0.72rem;font-weight:600;background:${bg};color:${color};">${sig}</span>`;
  };

  const disagreeBadge = (ml, lin) => {
    if (!ml || !lin || ml === lin) return '';
    return `<span style="margin-left:4px;font-size:0.65rem;padding:1px 5px;background:rgba(245,158,11,0.15);color:#f59e0b;border-radius:8px;" title="ML and Linear disagree">!</span>`;
  };

  el.innerHTML = `
    <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
      <thead>
        <tr style="color:var(--text-muted);text-transform:uppercase;font-size:0.7rem;border-bottom:1px solid var(--border-color);">
          <th style="padding:6px 10px;text-align:left;">Date</th>
          <th style="padding:6px 10px;text-align:left;">Symbol</th>
          <th style="padding:6px 10px;text-align:center;">ML Signal</th>
          <th style="padding:6px 10px;text-align:center;">Linear</th>
          <th style="padding:6px 10px;text-align:right;">Confidence</th>
          <th style="padding:6px 10px;text-align:right;">1d</th>
          <th style="padding:6px 10px;text-align:right;">5d</th>
          <th style="padding:6px 10px;text-align:right;">10d</th>
          <th style="padding:6px 10px;text-align:right;">20d</th>
        </tr>
      </thead>
      <tbody>
        ${signals.map(s => {
          const conf      = s.ml_confidence != null ? `${s.ml_confidence.toFixed(1)}%` : '—';
          const confColor = s.ml_confidence >= 75 ? '#10b981' : s.ml_confidence >= 55 ? '#f59e0b' : '#94a3b8';
          // WIN/LOSS uses ML signal (primary engine)
          return `
          <tr style="border-bottom:1px solid var(--border-color);">
            <td style="padding:7px 10px;color:var(--text-muted);white-space:nowrap;">${s.signal_date}</td>
            <td style="padding:7px 10px;font-weight:600;">${s.symbol}</td>
            <td style="padding:7px 10px;text-align:center;">${signalBadge(s.signal)}${disagreeBadge(s.signal, s.linear_signal)}</td>
            <td style="padding:7px 10px;text-align:center;">${signalBadge(s.linear_signal)}</td>
            <td style="padding:7px 10px;text-align:right;font-weight:600;color:${confColor};">${conf}</td>
            <td style="padding:7px 10px;text-align:right;">${fmtRet(s.fwd_ret_1d,  s.signal, '1d')}</td>
            <td style="padding:7px 10px;text-align:right;">${fmtRet(s.fwd_ret_5d,  s.signal, '5d')}</td>
            <td style="padding:7px 10px;text-align:right;">${fmtRet(s.fwd_ret_10d, s.signal, '10d')}</td>
            <td style="padding:7px 10px;text-align:right;">${fmtRet(s.fwd_ret_20d, s.signal, '20d')}</td>
          </tr>`; }).join('')}
      </tbody>
    </table>`;
}

// =============================================
// IC Factor Weights
// =============================================

async function loadICWeights() {
  const barsEl   = document.getElementById('sqWeightBars');
  const methodEl = document.getElementById('sqWeightMethod');
  if (!barsEl) return;

  try {
    const res  = await fetch(`${API_BASE}/ic-weights`);
    const data = await res.json();

    const isIC = data.method === 'ic_weighted';
    methodEl.textContent    = isIC ? 'IC-Weighted (adaptive)' : 'Static Fallback';
    methodEl.style.color    = isIC ? 'var(--success)' : 'var(--warning)';
    methodEl.style.background = isIC ? 'rgba(16,185,129,0.1)' : 'rgba(245,158,11,0.1)';

    const weights = data.weights || {};
    const statics = data.static_weights || {};
    const rawIC   = data.raw_ic || {};

    const labels = {
      momentum: 'Momentum', bollinger: 'Bollinger', rsi: 'RSI',
      macd: 'MACD', volatility: 'Volatility', volume: 'Volume',
      relative_strength: 'Rel. Strength'
    };

    barsEl.innerHTML = Object.keys(weights).map(factor => {
      const w      = weights[factor] ?? 0;
      const sw     = statics[factor] ?? 0;
      const ic     = rawIC[factor];
      const icTxt  = ic != null ? (ic >= 0 ? `IC +${ic.toFixed(4)}` : `IC ${ic.toFixed(4)}`) : '';
      const icColor = ic > 0.05 ? '#10b981' : ic > 0.02 ? '#f59e0b' : ic != null ? '#ef4444' : '#64748b';
      const wPct   = (w * 100).toFixed(1);
      const swPct  = (sw * 100).toFixed(1);
      const changed = Math.abs(w - sw) > 0.005;

      return `
        <div style="display:grid;grid-template-columns:110px 1fr 50px 60px;align-items:center;gap:0.5rem;">
          <span style="font-size:0.75rem;color:var(--text-muted);text-transform:uppercase;">${labels[factor] || factor}</span>
          <div style="position:relative;height:8px;background:var(--bg-tertiary);border-radius:4px;overflow:visible;">
            <!-- static weight ghost bar -->
            <div style="position:absolute;top:0;left:0;height:100%;width:${swPct}%;background:rgba(148,163,184,0.2);border-radius:4px;"></div>
            <!-- IC weight bar -->
            <div style="position:absolute;top:0;left:0;height:100%;width:${wPct}%;background:${changed ? 'var(--accent-primary)' : '#475569'};border-radius:4px;transition:width 0.6s ease;"></div>
          </div>
          <span style="font-size:0.72rem;font-weight:600;color:${changed ? 'var(--accent-primary)' : 'var(--text-secondary)'};">${wPct}%</span>
          <span style="font-size:0.68rem;color:${icColor};text-align:right;">${icTxt}</span>
        </div>`;
    }).join('');

  } catch (err) {
    console.error('IC weights load error:', err);
    const barsEl = document.getElementById('sqWeightBars');
    if (barsEl) barsEl.innerHTML = '<p style="color:var(--text-muted);font-size:0.82rem;">Weights unavailable</p>';
  }
}

// =============================================
// Quant Signals
// =============================================

async function loadQuantScores() {
  try {
    const response = await fetch(`${API_BASE}/quant/scores`);
    if (!response.ok) throw new Error('Quant engine unavailable');
    const data = await response.json();

    document.getElementById('quantTotal').textContent = data.summary.total;
    document.getElementById('quantLong').textContent = data.summary.long;
    document.getElementById('quantHold').textContent = data.summary.hold;
    document.getElementById('quantShort').textContent = data.summary.short;

    renderQuantCards(data.stocks);
  } catch (error) {
    console.error('Error loading quant scores:', error);
    document.getElementById('quantCardsGrid').innerHTML = `
      <p style="color:var(--danger);padding:2rem;text-align:center;grid-column:1/-1;">
        ⚠️ Quant Engine offline — start it with: <code>python3 -m quant_engine.main</code>
      </p>`;
  }
}

function renderQuantCards(stocks) {
  const grid = document.getElementById('quantCardsGrid');

  grid.innerHTML = stocks.map((stock, index) => {
    const signalClass = stock.signal === 'LONG' ? 'change-positive'
      : stock.signal === 'SHORT' ? 'change-negative'
        : '';
    const signalEmoji = stock.signal === 'LONG' ? '🟢'
      : stock.signal === 'SHORT' ? '🔴'
        : '⚪';
    const scoreColor = stock.composite_score > 0 ? 'var(--success)' : 'var(--danger)';

    const factorBars = Object.entries(stock.factors).map(([name, data]) => {
      const pct = ((data.score + 1) / 2 * 100).toFixed(0);
      const barColor = data.score > 0.2 ? 'var(--success)'
        : data.score < -0.2 ? 'var(--danger)'
          : 'var(--text-muted)';
      const label = name.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
      return `
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:4px;">
          <span style="width:110px;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;">${label}</span>
          <div style="flex:1;height:6px;background:var(--bg-tertiary);border-radius:3px;overflow:hidden;">
            <div style="width:${pct}%;height:100%;background:${barColor};border-radius:3px;transition:width 0.5s;"></div>
          </div>
          <span style="width:40px;font-size:0.7rem;color:${barColor};text-align:right;">${data.score > 0 ? '+' : ''}${data.score.toFixed(2)}</span>
        </div>`;
    }).join('');

    return `
      <div style="background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:1.2rem;position:relative;overflow:hidden;">
        <div style="position:absolute;top:0;left:0;right:0;height:3px;background:${scoreColor};"></div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.8rem;">
          <div>
            <span style="font-size:0.7rem;color:var(--text-muted);">#${index + 1}</span>
            <h3 style="margin:0;font-size:1.1rem;">${stock.symbol}</h3>
            <span style="font-size:0.8rem;color:var(--text-muted);">₹${stock.price.toLocaleString('en-IN')}</span>
          </div>
          <div style="text-align:right;">
            <div style="font-size:1.4rem;font-weight:700;color:${scoreColor};">${stock.composite_score > 0 ? '+' : ''}${stock.composite_score}</div>
            <span class="${signalClass}" style="font-size:0.8rem;font-weight:600;">${signalEmoji} ${stock.signal}</span>
          </div>
        </div>
        <div style="border-top:1px solid var(--border-color);padding-top:0.8rem;">
          ${factorBars}
        </div>
      </div>`;
  }).join('');
}

