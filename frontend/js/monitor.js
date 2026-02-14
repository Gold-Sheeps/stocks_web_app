/**
 * monitor.js - Dashboard logic for Market Monitor
 */

class MonitorApp {
    constructor() {
        this.refreshInterval = 30000; // 30 seconds
        this.timer = null;
    }

    async init() {
        console.log("[Monitor] Initializing...");
        await this.fetchAndRender();
        this.startAutoRefresh();
    }

    async fetchAndRender() {
        try {
            const response = await fetch(`${CONFIG.API_BASE}/monitor/`);
            if (!response.ok) throw new Error(`API Error: ${response.status}`);
            const data = await response.json();

            this.renderAlerts(data.alerts);
            this.renderPortfolio(data.portfolio);
            this.renderIndices(data.indices);
            this.renderWatchlist(data.watchlist);
            this.updateTimestamp();

            // Fade in content on first load
            const content = document.getElementById('content');
            if (content) content.style.opacity = 1;

        } catch (error) {
            console.error("[Monitor] Fetch failed:", error);
            UI.showNotification("データの取得に失敗しました", "error");
        }
    }

    startAutoRefresh() {
        if (this.timer) clearInterval(this.timer);
        this.timer = setInterval(() => this.fetchAndRender(), this.refreshInterval);
    }

    renderAlerts(alerts) {
        const container = document.getElementById('alerts-container');
        if (!container) return;

        if (!alerts || alerts.length === 0) {
            container.innerHTML = '';
            container.style.display = 'none';
            return;
        }

        container.style.display = 'block';
        container.innerHTML = alerts.map(item => {
            const isSurge = item.alert_type === 'SURGE';
            const colorClass = isSurge ? 'bg-success-subtle border-success' : 'bg-danger-subtle border-danger';
            const icon = isSurge ? '🚀' : '⚠️';
            const label = isSurge ? '急騰中' : '急落中';

            return `
                <div class="alert ${colorClass} d-flex align-items-center mb-2 fade-in" role="alert">
                    <span class="me-3 fs-4">${icon}</span>
                    <div>
                        <strong>${item.symbol} (${item.name || '-'})</strong> が${label}です。 
                        現在の変化率: <span class="fw-bold">${UI.formatPct(item.change_pct)}</span>
                    </div>
                </div>
            `;
        }).join('');
    }

    renderPortfolio(portfolio) {
        if (!portfolio) return;

        UI.updateElementText('totalValue', UI.formatCurrency(portfolio.total_value_jpy, 'JPY'));
        UI.updateElementText('gainLoss', UI.formatCurrency(portfolio.total_gain_loss_jpy, 'JPY'));

        const gainLossPctEl = document.getElementById('gainLossPct');
        if (gainLossPctEl) {
            gainLossPctEl.innerHTML = UI.formatPct(portfolio.total_gain_loss_pct);
        }

        UI.updateElementText('ytdGainLoss', UI.formatCurrency(portfolio.ytd_gain_loss_jpy || 0, 'JPY'));
        const ytdGainLossPctEl = document.getElementById('ytdGainLossPct');
        if (ytdGainLossPctEl) {
            ytdGainLossPctEl.innerHTML = UI.formatPct(portfolio.ytd_gain_loss_pct || 0);
        }
    }

    renderIndices(indices) {
        const container = document.getElementById('indices-container');
        if (!container) return;

        if (!indices || indices.length === 0) {
            container.innerHTML = '<div class="text-muted p-3">指数データがありません</div>';
            return;
        }

        container.innerHTML = indices.map(item => {
            const changeClass = parseFloat(item.change_pct) >= 0 ? 'text-up-dim' : 'text-down-dim';
            const arrow = parseFloat(item.change_pct) >= 0 ? '▲' : '▼';

            return `
                <div class="market-row glass-panel px-3 py-2">
                    <div class="market-cell-name">
                        <div class="market-name">${item.name}</div>
                        <div class="market-symbol text-xs">${item.symbol}</div>
                    </div>
                    <div class="market-cell-right">
                        <div class="market-value fw-bold">${UI.formatNumber(item.current_price)}</div>
                        <div class="market-change ${changeClass} text-xs">
                            ${arrow} ${Math.abs(item.change_pct).toFixed(2)}%
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    renderWatchlist(watchlist) {
        const tbody = document.getElementById('watchlist-tbody');
        if (!tbody) return;

        if (!watchlist || watchlist.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center p-4">ウォッチリストに銘柄がありません</td></tr>';
            return;
        }

        tbody.innerHTML = watchlist.map(item => {
            const changeVal = parseFloat(item.change_pct || 0);
            const changeClass = changeVal > 0 ? 'text-success' : (changeVal < 0 ? 'text-danger' : '');

            // Alert highlighting
            let rowStyle = "";
            if (item.alert_type === 'SURGE') rowStyle = 'style="background-color: rgba(34, 197, 94, 0.1);"';
            if (item.alert_type === 'PLUNGE') rowStyle = 'style="background-color: rgba(239, 68, 68, 0.1);"';

            return `
                <tr ${rowStyle}>
                    <td>
                        <div class="fw-bold">${item.symbol}</div>
                        <div class="text-muted text-xs">${item.name || '-'}</div>
                    </td>
                    <td class="text-end fw-mono">${item.current_price ? UI.formatNumber(item.current_price) : '-'}</td>
                    <td class="text-end fw-bold ${changeClass}">${UI.formatPct(item.change_pct)}</td>
                    <td class="text-center">${item.rsi ? parseFloat(item.rsi).toFixed(1) : '-'}</td>
                    <td class="text-center">${item.volume_ratio ? parseFloat(item.volume_ratio).toFixed(2) : '-'}</td>
                    <td class="text-center">
                        <span class="badge border bg-dark text-light">${item.status || 'Research'}</span>
                    </td>
                </tr>
            `;
        }).join('');
    }

    updateTimestamp() {
        const el = document.getElementById('lastUpdated');
        if (el) {
            const now = new Date();
            el.textContent = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')} 更新`;
        }
    }
}

// Global initialization
document.addEventListener('DOMContentLoaded', () => {
    const app = new MonitorApp();
    app.init();
});
