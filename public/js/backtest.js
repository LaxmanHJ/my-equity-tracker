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

let equityChartInstance = null;

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
        updateMetrics(data.metrics);
        renderChart(data.chart_data, data.strategy);

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

function updateMetrics(metrics) {
    const formatPct = val => `${val > 0 ? '+' : ''}${val.toFixed(2)}%`;
    const formatNum = val => val.toFixed(2);

    document.getElementById('resTotalReturn').textContent = formatPct(metrics.total_return_pct);
    document.getElementById('resTotalReturn').className = `card-value ${metrics.total_return_pct >= 0 ? 'positive' : 'negative'}`;

    document.getElementById('resCagr').textContent = formatPct(metrics.cagr_pct);

    document.getElementById('resMaxDd').textContent = formatPct(metrics.max_drawdown_pct);

    document.getElementById('resSharpe').textContent = formatNum(metrics.sharpe_ratio);
}

function renderChart(chartData, strategyName) {
    const ctx = document.getElementById('equityChart').getContext('2d');

    if (equityChartInstance) {
        equityChartInstance.destroy();
    }

    equityChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: `${strategyName} Equity Curve`,
                data: chartData,
                borderColor: '#10b981', // positive green
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                borderWidth: 2,
                fill: true,
                pointRadius: 0,
                pointHitRadius: 10,
                tension: 0.1
            }]
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
                        label: (context) => `₹${context.parsed.y.toLocaleString()}`
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
