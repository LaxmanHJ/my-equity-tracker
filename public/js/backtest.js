document.addEventListener('DOMContentLoaded', () => {
    // Set default end date to today
    const endInput = document.getElementById('endDate');
    const today = new Date().toISOString().split('T')[0];
    endInput.value = today;

    // Form Submits
    const form = document.getElementById('backtestForm');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await runSimulation();
    });
});

let equityChartInstance   = null;
let drawdownChartInstance = null;

async function runSimulation() {
    const symbol = document.getElementById('symbol').value;
    const strategy = document.getElementById('strategy').value;
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const capital = document.getElementById('capital').value;

    const runBtn = document.getElementById('runBtn');
    const loadingArea = document.getElementById('loadingArea');
    const resultsArea = document.getElementById('resultsArea');
    const errorArea = document.getElementById('errorArea');

    // UI state
    runBtn.disabled = true;
    loadingArea.style.display = 'block';
    resultsArea.style.display = 'none';
    errorArea.style.display = 'none';

    try {
        const response = await fetch('/api/quant/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: symbol,
                strategy: strategy,
                start_date: startDate,
                end_date: endDate,
                initial_capital: parseFloat(capital)
            })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || err.error || 'Server error');
        }

        const data = await response.json();

        // Render UI
        updateMetrics(data.metrics, data.baseline_metrics, data.trade_stats);
        renderChart(data.chart_data, data.baseline_data, data.strategy, data.symbol);
        renderDrawdownChart(data.drawdown_data);
        renderTradeLog(data.trade_log);

        resultsArea.style.display = 'block';
    } catch (err) {
        console.error("Backtest Error:", err);
        document.getElementById('errorMsg').textContent = err.message;
        errorArea.style.display = 'block';
    } finally {
        runBtn.disabled = false;
        loadingArea.style.display = 'none';
    }
}

function updateMetrics(metrics, baselineMetrics, tradeStats) {
    if (!metrics || Object.keys(metrics).length === 0) {
        throw new Error('Not enough trading data in the selected date range. Try a wider range (at least 3 months).');
    }
    const formatPct = val => val == null ? 'N/A' : `${val > 0 ? '+' : ''}${val.toFixed(2)}%`;
    const formatNum = val => val == null ? 'N/A' : val.toFixed(2);

    // Strategy cards
    document.getElementById('resTotalReturn').textContent = formatPct(metrics.total_return_pct);
    document.getElementById('resTotalReturn').className = `card-value ${metrics.total_return_pct >= 0 ? 'positive' : 'negative'}`;
    document.getElementById('resCagr').textContent     = formatPct(metrics.cagr_pct);
    document.getElementById('resMaxDd').textContent    = formatPct(metrics.max_drawdown_pct);
    document.getElementById('resSharpe').textContent   = formatNum(metrics.sharpe_ratio);
    document.getElementById('resCalmar').textContent   = formatNum(metrics.calmar_ratio);

    // Trade stats
    if (tradeStats) {
        document.getElementById('resWinRate').textContent     = tradeStats.win_rate_pct != null ? `${tradeStats.win_rate_pct.toFixed(1)}%` : 'N/A';
        document.getElementById('resProfitFactor').textContent = formatNum(tradeStats.profit_factor);
        document.getElementById('resTradeCount').textContent  = tradeStats.trade_count ?? '—';
    }

    // Benchmark row
    if (baselineMetrics) {
        document.getElementById('bncReturn').textContent = formatPct(baselineMetrics.total_return_pct);
        document.getElementById('bncCagr').textContent   = formatPct(baselineMetrics.cagr_pct);
        document.getElementById('bncMaxDd').textContent  = formatPct(baselineMetrics.max_drawdown_pct);
        document.getElementById('bncSharpe').textContent = formatNum(baselineMetrics.sharpe_ratio);
        document.getElementById('bncCalmar').textContent = formatNum(baselineMetrics.calmar_ratio);
    }
}

function renderDrawdownChart(drawdownData) {
    const ctx = document.getElementById('drawdownChart').getContext('2d');

    if (drawdownChartInstance) {
        drawdownChartInstance.destroy();
    }

    drawdownChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Drawdown %',
                data: drawdownData,
                borderColor: 'rgba(239, 68, 68, 0.8)',
                backgroundColor: 'rgba(239, 68, 68, 0.15)',
                borderWidth: 1.5,
                fill: true,
                pointRadius: 0,
                pointHitRadius: 10,
                tension: 0.1,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: ctx => `Drawdown: ${ctx.parsed.y.toFixed(2)}%`
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'month' },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: 'rgba(255,255,255,0.5)' }
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: {
                        color: 'rgba(255,255,255,0.5)',
                        callback: v => `${v.toFixed(1)}%`
                    }
                }
            }
        }
    });
}

function renderTradeLog(trades) {
    const tbody      = document.getElementById('tradeTableBody');
    const noTradesMsg = document.getElementById('noTradesMsg');
    tbody.innerHTML  = '';

    if (!trades || trades.length === 0) {
        noTradesMsg.style.display = 'block';
        return;
    }
    noTradesMsg.style.display = 'none';

    trades.forEach((t, i) => {
        const win  = t.pnl_pct >= 0;
        const color = win ? 'var(--success-color, #10b981)' : 'var(--danger-color, #ef4444)';
        const sign  = win ? '+' : '';
        tbody.innerHTML += `
            <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                <td style="padding: 0.5rem 0.75rem; color: var(--text-secondary);">${i + 1}</td>
                <td style="padding: 0.5rem 0.75rem;">${t.entry_date}</td>
                <td style="padding: 0.5rem 0.75rem;">${t.exit_date}</td>
                <td style="padding: 0.5rem 0.75rem; text-align: right;">₹${t.entry_price.toLocaleString()}</td>
                <td style="padding: 0.5rem 0.75rem; text-align: right;">₹${t.exit_price.toLocaleString()}</td>
                <td style="padding: 0.5rem 0.75rem; text-align: right; color: ${color}; font-weight: 600;">${sign}${t.pnl_pct.toFixed(2)}%</td>
                <td style="padding: 0.5rem 0.75rem; text-align: right; color: var(--text-secondary);">${t.holding_days}d</td>
            </tr>`;
    });
}

function renderChart(chartData, baselineData, strategyName, symbol) {
    const ctx = document.getElementById('equityChart').getContext('2d');

    if (equityChartInstance) {
        equityChartInstance.destroy();
    }

    equityChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [
                {
                    label: `${strategyName} Equity Curve`,
                    data: chartData,
                    borderColor: '#10b981', // positive green
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    pointRadius: 0,
                    pointHitRadius: 10,
                    tension: 0.1
                },
                {
                    label: `${symbol} Buy & Hold`,
                    data: baselineData,
                    borderColor: 'rgba(255, 255, 255, 0.45)',
                    backgroundColor: 'rgba(255, 255, 255, 0.03)',
                    borderWidth: 1.5,
                    borderDash: [6, 3],
                    fill: false,
                    pointRadius: 0,
                    pointHitRadius: 10,
                    tension: 0.1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                legend: {
                    labels: { color: 'rgba(255, 255, 255, 0.7)' }
                },
                tooltip: {
                    callbacks: {
                        label: (context) => `${context.dataset.label}: ₹${context.parsed.y.toLocaleString()}`
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'month' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: 'rgba(255, 255, 255, 0.5)' }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: 'rgba(255, 255, 255, 0.5)',
                        callback: function (value) {
                            return '₹' + value.toLocaleString();
                        }
                    }
                }
            }
        }
    });
}
