/* js/stock_detail.js - Enhanced Edition */

// Immediate Execution Log
console.log("[StockDetail] Script loaded", location.href);
window.__SD_LOADED__ = true;

class StockDetailApp {
    constructor() {
        const params = new URLSearchParams(window.location.search);
        const raw = params.get('symbol') || params.get('symbol_key') || params.get('ticker');
        this.symbol = raw ? decodeURIComponent(raw).trim() : null;

        this.state = {
            summary: null,
            chartData: null,
            chartType: 'line',
            chartInstance: null,
            volumeInstance: null,
            loading: false,
            fundLoaded: false,
            ratiosLoaded: false
        };

        this.API_BASE = window.CONFIG?.API_BASE || 'http://localhost:8000/api/v1';

        // Initialize DOM display
        this.updateSymbolDisplay();
    }

    updateSymbolDisplay() {
        const symbolEl = document.getElementById("sdSymbol");
        if (symbolEl) {
            symbolEl.textContent = this.symbol || "--:----";
        }
    }

    async init() {
        if (!window.CONFIG || !window.CONFIG.API_BASE) {
            this.showFatal("CONFIGが見つかりません。");
            return;
        }
        if (!this.symbol) {
            this.showFatal("銘柄コードが指定されていません");
            return;
        }

        if (window.UI) window.UI.showLoading();

        await this.fetchSummary();
        this.setupTabs();
        this.setupSearch();
        this.setupTradePlan();
        this.loadTradePlan();

        await this.loadChartDataAll();
        this.initMainChartLine(this.state.chartData);
        this.initVolumeChart(this.state.chartData);
        this.setupChartToggle();

        if (window.UI) window.UI.hideLoading();
    }

    async fetchSummary() {
        this.state.loading = true;
        try {
            const url = `${this.API_BASE}/stock/${encodeURIComponent(this.symbol)}/summary`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`API Error: ${res.status}`);
            const data = await res.json();
            this.state.summary = data;
            this.render(data);
        } catch (e) {
            console.error("[StockDetail] Fetch error:", e);
            this.showFatal(`データの取得に失敗しました: ${e.message}`);
        } finally {
            this.state.loading = false;
        }
    }

    async loadChartDataAll() {
        try {
            const url = `${this.API_BASE}/stock/${encodeURIComponent(this.symbol)}/price`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`API ${res.status}`);
            const data = await res.json();
            this.state.chartData = data;

            if (data && data.length > 0) {
                console.log(`[Chart] rows=${data.length}, range=${data[0].date}..${data[data.length - 1].date}`);
            } else {
                console.warn("[Chart] No price data returned.");
            }
        } catch (e) {
            console.error("[Chart] History load error:", e);
            this.state.chartData = [];
        }
    }

    initMainChartLine(data) {
        const ctx = document.getElementById('mainPriceChart');
        if (!ctx || !data || data.length === 0) return;

        if (this.state.chartInstance) this.state.chartInstance.destroy();

        const labels = data.map(d => d.date);
        const closes = data.map(d => d.close);

        this.state.chartInstance = new window.Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Price',
                    data: closes,
                    borderColor: 'rgba(99, 102, 241, 1)',
                    backgroundColor: 'rgba(99, 102, 241, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { intersect: false, mode: 'index' },
                scales: {
                    x: { grid: { display: false } },
                    y: { position: 'left' }
                },
                plugins: {
                    zoom: {
                        zoom: { wheel: { enabled: true }, mode: 'x' },
                        pan: { enabled: true, mode: 'x' }
                    }
                }
            }
        });
    }

    initMainChartCandle(data) {
        const ctx = document.getElementById('mainPriceChart');
        if (!ctx || !data || data.length === 0) return;

        if (!window.financialReady) {
            console.warn('Financial plugin not ready, falling back to line');
            this.initMainChartLine(data);
            return;
        }

        if (this.state.chartInstance) this.state.chartInstance.destroy();

        const candleData = data.map(d => ({
            x: d.date,
            o: d.open,
            h: d.high,
            l: d.low,
            c: d.close
        }));

        this.state.chartInstance = new window.Chart(ctx, {
            type: 'candlestick',
            data: {
                datasets: [{
                    label: 'Price',
                    data: candleData
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { type: 'timeseries' },
                    y: { position: 'left' }
                },
                plugins: {
                    zoom: {
                        zoom: { wheel: { enabled: true }, mode: 'x' },
                        pan: { enabled: true, mode: 'x' }
                    }
                }
            }
        });
    }

    initVolumeChart(data) {
        const ctx = document.getElementById('volumeChart');
        if (!ctx || !data || data.length === 0) return;

        if (this.state.volumeInstance) this.state.volumeInstance.destroy();

        const labels = data.map(d => d.date);
        const volumes = data.map(d => d.volume);

        this.state.volumeInstance = new window.Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Volume',
                    data: volumes,
                    backgroundColor: 'rgba(99, 102, 241, 0.4)',
                    borderColor: 'rgba(99, 102, 241, 0.8)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { mode: 'index', intersect: false }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { display: false } },
                    y: { position: 'left', grid: { display: false } }
                }
            }
        });
    }

    setupChartToggle() {
        const btn = document.getElementById('btnToggleChartType');
        if (!btn) return;
        btn.addEventListener('click', () => {
            try {
                this.state.chartType = (this.state.chartType === 'line') ? 'candlestick' : 'line';
                console.log('[Chart] Toggling to:', this.state.chartType);

                if (this.state.chartType === 'candlestick') {
                    this.initMainChartCandle(this.state.chartData);
                } else {
                    this.initMainChartLine(this.state.chartData);
                }
            } catch (err) {
                console.error("[Chart] Toggle failed, falling back to line:", err);
                if (window.UI?.showToast) window.UI.showToast('チャート切替に失敗しました。折れ線に戻します。', 'error');
                this.state.chartType = 'line';
                this.initMainChartLine(this.state.chartData);
            }
        });
    }

    render(data) {
        const info = data.stock_info || {};
        const quality = data.data_quality || {};
        const signals = data.signal_summary || {};
        const indicators = data.indicators || {};

        const nameEl = document.getElementById('sdName');
        if (nameEl) nameEl.textContent = info.name || 'Unknown';

        if (!quality.price_valid || !info.current_price) {
            this.renderInvalidState(quality);
        } else {
            this.renderValidState(info, signals, indicators);
        }
    }

    renderValidState(info, signals, indicators) {
        const priceEl = document.getElementById('sdPrice');
        if (priceEl) {
            priceEl.textContent = window.UI ? window.UI.formatCurrency(info.current_price, info.currency) : `$${info.current_price}`;
        }
        const changeEl = document.getElementById('sdChange');
        if (changeEl) {
            changeEl.innerHTML = window.UI ? window.UI.formatPct(info.change_pct) : `${info.change_pct}%`;
        }
        this.renderChips(signals);
        this.renderIndicatorCards(indicators, signals);
        const alertEl = document.getElementById('sdAlert');
        if (alertEl) alertEl.classList.add('alert--hidden');
    }

    renderInvalidState(quality) {
        const priceEl = document.getElementById('sdPrice');
        if (priceEl) priceEl.textContent = '-';
        const alertEl = document.getElementById('sdAlert');
        if (alertEl) {
            alertEl.innerHTML = `⚠️ データ欠損: ${quality.price_reason || '不明'}`;
            alertEl.classList.remove('alert--hidden');
        }
    }

    renderChips(signals) {
        const container = document.getElementById('sdReasons');
        if (!container) return;
        container.innerHTML = '';
        if (signals.reasons) {
            signals.reasons.forEach(r => {
                const chip = document.createElement('span');
                chip.className = 'chip';
                chip.textContent = r.message;
                container.appendChild(chip);
            });
        }
    }

    renderIndicatorCards(indicators, signals) {
        const trend = document.getElementById('sdTrend');
        if (trend) trend.innerHTML = `MA20: ${indicators.ma20 || '-'}, MA50: ${indicators.ma50 || '-'}`;
        const momentum = document.getElementById('sdMomentum');
        if (momentum) momentum.innerHTML = `RSI: ${indicators.rsi || '-'}`;
        const risk = document.getElementById('sdRisk');
        if (risk) risk.innerHTML = `Beta: ${indicators.beta || '-'}`;
    }

    setupTabs() {
        const tabs = document.querySelectorAll('.tab');
        const panels = document.querySelectorAll('.tab-panel');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.getAttribute('data-tab');
                tabs.forEach(t => t.classList.remove('active', 'is-active'));
                panels.forEach(p => p.classList.remove('active', 'is-active'));
                tab.classList.add('active', 'is-active');
                const panel = document.getElementById(`tab-${target}`);
                if (panel) {
                    panel.classList.add('active', 'is-active');
                    this.handleLazyLoad(target);
                }
            });
        });
    }

    handleLazyLoad(tabName) {
        if (tabName === 'fundamentals' && !this.state.fundLoaded) {
            this.state.fundLoaded = true;
            this.loadFundamentalsData();
        }
        if (tabName === 'ratios' && !this.state.ratiosLoaded) {
            this.state.ratiosLoaded = true;
            this.loadRatiosData();
        }
    }

    loadFundamentalsData() { console.log("Loading fundamentals..."); }
    loadRatiosData() { console.log("Loading ratios..."); }

    setupSearch() {
        const input = document.getElementById('sdSearchInput');
        const btn = document.getElementById('sdSearchGo');
        if (!input || !btn) return;
        const handle = () => {
            const raw = input.value.trim();
            if (!raw) return;
            const symbol = raw.includes(':') ? raw.toUpperCase() : 'US:' + raw.toUpperCase();
            window.location.href = `stock_detail.html?symbol=${encodeURIComponent(symbol)}`;
        };
        btn.onclick = handle;
        input.onkeydown = (e) => { if (e.key === 'Enter') handle(); };
    }

    setupTradePlan() {
        const saveBtn = document.getElementById('tpSave');
        const clearBtn = document.getElementById('tpClear');
        if (saveBtn) saveBtn.onclick = () => this.saveTradePlan();
        if (clearBtn) clearBtn.onclick = () => this.clearTradePlan();
    }

    getSymbolForPlan() { return this.symbol || '--'; }

    loadTradePlan() {
        const symbol = this.getSymbolForPlan();
        if (!symbol || symbol === '--') return;
        const saved = localStorage.getItem(`tradePlan:${symbol}`);
        if (!saved) return;
        const plan = JSON.parse(saved);
        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.value = val || '';
        };
        setVal('tpEntry', plan.entry);
        setVal('tpStop', plan.stop);
        setVal('tpTakeProfit', plan.takeProfit);
        setVal('tpPositionSize', plan.positionSize);
        setVal('tpNotes', plan.notes);
    }

    saveTradePlan() {
        const symbol = this.getSymbolForPlan();
        if (!symbol || symbol === '--') return;
        const plan = {
            entry: document.getElementById('tpEntry')?.value,
            stop: document.getElementById('tpStop')?.value,
            takeProfit: document.getElementById('tpTakeProfit')?.value,
            positionSize: document.getElementById('tpPositionSize')?.value,
            notes: document.getElementById('tpNotes')?.value,
            savedAt: new Date().toISOString()
        };
        localStorage.setItem(`tradePlan:${symbol}`, JSON.stringify(plan));
        if (window.UI) window.UI.showToast('保存しました', 'success');
    }

    clearTradePlan() {
        const symbol = this.getSymbolForPlan();
        localStorage.removeItem(`tradePlan:${symbol}`);
        const ids = ['tpEntry', 'tpStop', 'tpTakeProfit', 'tpPositionSize', 'tpNotes'];
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        if (window.UI) window.UI.showToast('クリアしました', 'info');
    }

    showFatal(msg) {
        console.error("FATAL:", msg);
        const alertEl = document.getElementById('sdAlert');
        if (alertEl) {
            alertEl.textContent = msg;
            alertEl.classList.remove('alert--hidden');
        }
    }
}

window.StockDetailApp = StockDetailApp;

// ---- Hotfix: Trade Panel toggle (minimal, global) ----
(function registerTradePanelToggle() {
    window.toggleTradePanel = function () {
        const body = document.getElementById('tpBody');
        const toggleBtn = document.getElementById('tpToggle');
        if (!body || !toggleBtn) return;

        const isOpen = body.classList.toggle('active');
        toggleBtn.textContent = isOpen ? '閉じる' : '開く';
    };
})();
