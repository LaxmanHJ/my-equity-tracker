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
      e.preventDefault();
      const sectionId = link.dataset.section;

      // Update active states
      navLinks.forEach(l => l.classList.remove('active'));
      link.classList.add('active');

      // Show section
      document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
      document.getElementById(sectionId).classList.add('active');

      // Load section data
      if (sectionId === 'quant') loadQuantScores();
      if (sectionId === 'correlation') loadCorrelationData();
      if (sectionId === 'alerts') { loadAlerts(); loadNews(); }
      if (sectionId === 'index-analysis') loadIndexAnalysis();
    });
  });

  // Refresh button
  document.getElementById('refreshBtn').addEventListener('click', loadPortfolio);
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
  try {
    const btn = document.getElementById('forceSyncBtn');
    btn.innerHTML = '<span class="loading-spinner" style="display:inline-block;width:12px;height:12px;border:2px solid #fff;border-bottom-color:transparent;border-radius:50%;margin-right:5px;"></span> Syncing...';
    btn.disabled = true;

    const response = await fetch(`${API_BASE}/portfolio?force=true`);
    if (!response.ok) throw new Error('Failed to force sync portfolio');

    portfolioData = await response.json();
    renderPortfolio(portfolioData);
    renderCharts(portfolioData);
    populateStockSelectors(portfolioData.holdings);
    updateLastUpdated();

    showToast('Successfully synchronized latest market data', 'success');

  } catch (error) {
    console.error('Error force syncing portfolio:', error);
    showToast('Failed to synchronize market data', 'error');
  } finally {
    const btn = document.getElementById('forceSyncBtn');
    btn.innerHTML = '<span class="btn-icon">⚡</span> Force Sync';
    btn.disabled = false;
  }
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
    const [technicalRes, riskRes] = await Promise.all([
      fetch(`${API_BASE}/analysis/technicals/${symbol}`),
      fetch(`${API_BASE}/analysis/risk/${symbol}`)
    ]);

    const technical = await technicalRes.json();
    const risk = await riskRes.json();

    renderAnalysis(technical, risk);

  } catch (error) {
    console.error('Error loading analysis:', error);
    container.innerHTML = '<div class="empty-state glass"><p>Failed to load analysis</p></div>';
  }
}

function renderAnalysis(technical, risk) {
  const container = document.getElementById('analysisContent');
  const { signals, indicators } = technical.analysis.signals;

  container.innerHTML = `
    <div class="glass" style="padding: var(--space-lg); margin-bottom: var(--space-lg);">
      <h3 style="margin-bottom: var(--space-md);">Trading Signals - ${technical.symbol}</h3>
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

