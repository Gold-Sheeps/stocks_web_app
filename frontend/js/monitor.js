/* frontend/js/monitor.js */

/**
 * Monitor Screen JavaScript Logic
 * Handles data fetching, normalization, and rendering for the market dashboard.
 */

// Store previous values for flash animations
const previousValues = {};
let pieChart = null;

/**
 * Normalize symbol keys for matching
 * e.g., "US:^DJI" -> "^DJI"
 */
function normalizeSymbol(symbol) {
    if (!symbol) return '';
    // Standardize symbols for comparison
    return symbol.replace(/^(US:|JP:|CRYPTO:)/i, '').replace(/\s+/g, '').toUpperCase();
}

/**
 * Generate dummy sparkline data
 */
function generateSparklineData() {
    return Array.from({ length: 20 }, () => 100 + (Math.random() - 0.5) * 5);
}

/**
 * Create SVG Sparkline
 */
function createSparkline(data, color) {
    if (!data || data.length === 0) return '';
    const width = 80, height = 24, padding = 2;
    const max = Math.max(...data), min = Math.min(...data), range = max - min || 1;
    const points = data.map((v, i) => {
        const x = (i / (data.length - 1)) * (width - padding * 2) + padding;
        const y = height - padding - ((v - min) / range) * (height - padding * 2);
        return `${x},${y}`;
    }).join(' ');
    return `<svg class="sparkline" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${points}" opacity="0.7"/></svg>`;
}

/**
 * Create/Update Portfolio Pie Chart
 */
function updatePortfolioChart() {
    const ctx = document.getElementById('portfolioPieChart');
    if (!ctx) return;

    // Placeholder for actual allocation data
    const data = {
        labels: ['Equities', 'Cash', 'Others'],
        datasets: [{
            data: [75, 20, 5],
            backgroundColor: [
                'rgba(99, 102, 241, 0.8)',
                'rgba(16, 185, 129, 0.8)',
                'rgba(245, 158, 11, 0.8)'
            ],
            borderColor: 'rgba(255, 255, 255, 0.1)',
            borderWidth: 1
        }]
    };

    if (pieChart) {
        pieChart.destroy();
    }

    pieChart = new Chart(ctx, {
        type: 'doughnut',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: '70%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: 'rgba(255,255,255,0.7)', font: { size: 10 } }
                }
            }
        }
    });
}

/**
 * Render a single market item row
 */
function renderMarketItem(item) {
    const isMissing = item.current_price === undefined || item.current_price === null || parseFloat(item.current_price) === 0;
    const itemKey = `${item.symbol}_${item.name}`;

    let priceDisplay = "N/A";
    let changeDisplay = "";
    let changeClass = "";
    let arrow = "";
    let sparklineHtml = "";
    let priceClass = isMissing ? "market-value na" : "market-value";

    if (!isMissing) {
        const price = parseFloat(item.current_price);
        priceDisplay = UI.formatNumber(price);
        if (item.format === "rate") priceDisplay += "%";

        const change = parseFloat(item.change_pct);
        if (!isNaN(change)) {
            if (change > 0) {
                changeDisplay = `+${change.toFixed(2)}%`;
                changeClass = "text-up-dim";
                arrow = "▲";
                sparklineHtml = `<div class="sparkline-container">${createSparkline(generateSparklineData(), '#86efac')}</div>`;
            } else if (change < 0) {
                changeDisplay = `${change.toFixed(2)}%`;
                changeClass = "text-down-dim";
                arrow = "▼";
                sparklineHtml = `<div class="sparkline-container">${createSparkline(generateSparklineData(), '#fca5a5')}</div>`;
            } else {
                changeDisplay = "0.00%";
                sparklineHtml = `<div class="sparkline-container">${createSparkline(generateSparklineData(), '#666')}</div>`;
            }
        }
    }

    const displaySymbol = (item.symbol || "").replace(/^(US:|JP:)/, '');

    return `
        <div class="market-row" data-key="${itemKey}">
            <div class="market-cell-name">
                <div class="market-name">${item.name}</div>
                <div class="market-symbol">${displaySymbol}</div>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                ${sparklineHtml}
                <div class="market-cell-right">
                    <div class="${priceClass}">${priceDisplay}</div>
                    <div class="market-change ${changeClass}">${arrow} ${changeDisplay}</div>
                </div>
            </div>
        </div>`;
}

/**
 * Merge targets with API data and render group
 */
function renderGroup(containerId, targets, apiData) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!apiData || apiData.length === 0) {
        container.innerHTML = targets.map(t => renderMarketItem({ ...t, current_price: null })).join('');
        return;
    }

    const itemsHtml = targets.map(target => {
        const targetNorm = normalizeSymbol(target.symbol);

        // Find in API data
        const found = apiData.find(d => {
            const dNorm = normalizeSymbol(d.symbol);
            const dNameNorm = normalizeSymbol(d.name);
            return (dNorm && dNorm === targetNorm) || (dNameNorm && dNameNorm === normalizeSymbol(target.key));
        });

        const item = found ? { ...found, format: target.format } : {
            name: target.key,
            symbol: target.symbol,
            current_price: null,
            change_pct: null,
            format: target.format
        };

        const itemKey = `${item.symbol}_${item.name}`;

        // Flash animation logic
        setTimeout(() => {
            const row = container.querySelector(`[data-key="${itemKey}"]`);
            if (row && previousValues[itemKey] !== undefined && item.current_price !== null) {
                const diff = parseFloat(item.current_price) - parseFloat(previousValues[itemKey]);
                if (diff > 0) {
                    row.classList.add('flash-up');
                    setTimeout(() => row.classList.remove('flash-up'), 800);
                } else if (diff < 0) {
                    row.classList.add('flash-down');
                    setTimeout(() => row.classList.remove('flash-down'), 800);
                }
            }
            if (item.current_price !== null) previousValues[itemKey] = item.current_price;
        }, 0);

        return renderMarketItem(item);
    }).join('');

    container.innerHTML = itemsHtml;
}

// Target Definitions
const TARGETS = {
    indices: [
        { key: 'Dow Jones', symbol: 'US:^DJI' },
        { key: 'S&P 500', symbol: 'US:^GSPC' },
        { key: 'NASDAQ', symbol: 'US:^IXIC' },
        { key: 'Russell 2000', symbol: 'US:^RUT' },
        { key: 'Nikkei 225', symbol: 'JP:^N225' },
        { key: 'VIX', symbol: 'US:^VIX' }
    ],
    rates: [
        { key: 'US 10Y Yield', symbol: 'US:^TNX', format: 'rate' },
        { key: 'US 5Y Yield', symbol: 'US:^FVX', format: 'rate' },
        { key: 'US 3Y Yield', symbol: 'US:^IRX', format: 'rate' }
    ],
    fx: [
        { key: 'USD/JPY', symbol: 'US:USDJPY=X' },
        { key: 'EUR/USD', symbol: 'US:EURUSD=X' },
        { key: 'GBP/USD', symbol: 'US:GBPUSD=X' }
    ],
    commodities: [
        { key: 'Gold', symbol: 'US:GLD' },
        { key: 'Silver', symbol: 'US:SLV' },
        { key: 'WTI Crude', symbol: 'US:CL=F' },
        { key: 'Platinum', symbol: 'US:PL=F' },
        { key: 'Copper', symbol: 'US:HG=F' },
        { key: 'Palladium', symbol: 'US:PA=F' }
    ],
    crypto: [
        { key: 'Bitcoin', symbol: 'BTC-USD' },
        { key: 'Ethereum', symbol: 'ETH-USD' }
    ]
};

/**
 * Main Load Function
 */
async function loadMonitorData() {
    const content = document.getElementById('content');
    try {
        const response = await fetch(`${CONFIG.API_BASE}/monitor/`);
        if (!response.ok) throw new Error(`API Error: ${response.status}`);
        const data = await response.json();

        // Render sections
        renderGroup('indices', TARGETS.indices, data.indices || []);
        renderGroup('rates', TARGETS.rates, data.rates || []);
        renderGroup('fxRates', TARGETS.fx, data.fx_rates || []);
        renderGroup('commodities', TARGETS.commodities, data.metals || []);
        renderGroup('crypto', TARGETS.crypto, data.crypto || []);

        // Portfolio Summary
        if (data.portfolio) {
            document.getElementById('totalValue').textContent = UI.formatCurrency(data.portfolio.total_value_jpy, 'JPY');
            document.getElementById('gainLoss').textContent = UI.formatCurrency(data.portfolio.total_gain_loss_jpy, 'JPY');
            document.getElementById('gainLossPct').innerHTML = UI.formatPct(data.portfolio.total_gain_loss_pct);

            if (data.portfolio.ytd_gain_loss_jpy !== null) {
                document.getElementById('ytdGainLoss').textContent = UI.formatCurrency(data.portfolio.ytd_gain_loss_jpy, 'JPY');
                document.getElementById('ytdGainLossPct').innerHTML = UI.formatPct(data.portfolio.ytd_gain_loss_pct);
            }
        }

        // Last Updated
        const date = new Date(data.last_updated || new Date());
        document.getElementById('lastUpdated').textContent = date.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        // Chart
        updatePortfolioChart();

    } catch (error) {
        console.error('[Monitor] Failed to load data:', error);
    } finally {
        // GUARANTEED: Reveal content regardless of success/fail
        if (content) {
            content.style.opacity = 1;
        }
    }
}

// Initial Load
document.addEventListener('DOMContentLoaded', () => {
    loadMonitorData();
    // Auto Refresh every 30s
    setInterval(loadMonitorData, 30000);
});
