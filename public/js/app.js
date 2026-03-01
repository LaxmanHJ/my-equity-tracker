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
      if (sectionId === 'correlation') loadCorrelationData();
      if (sectionId === 'alerts') loadAlerts();
    });
  });

  // Refresh button
  document.getElementById('refreshBtn').addEventListener('click', loadPortfolio);
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

function renderPortfolio(data) {
  const { holdings, summary } = data;

  // Update summary cards
  document.getElementById('portfolioValue').textContent = formatCurrency(summary.currentValue);

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

  document.getElementById('holdingsCount').textContent = holdings.length;

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
