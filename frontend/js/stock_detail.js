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
            chartData: [],
            chartDataFiltered: [],
            selectedPeriod: '3M',
            timeframe: '1d',
            chartType: 'line',
            chartInstance: null,
            volumeInstance: null,
            loading: false,
            fundLoaded: false,
            ratiosLoaded: false,
            aiPrediction: null,
            marketEnv: null,
            candleFallback: false,
            drawMode: 'none',
            drawLines: [],
            draftLine: null,
            isDrawing: false,
            fallbackViewStart: null,
            fallbackViewEnd: null,
            isPanningFallback: false,
            panStartX: 0,
            panStartStart: 0,
            panStartEnd: 0
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
            this.showFatal("CONFIG is missing.");
            return;
        }

        this.setupTabs();
        this.setupSearch();
        this.setupTradePlan();
        this.setupChartToggle();
        this.setupChartPeriod();
        this.setupTimeframe();
        this.setupDrawingTools();
        this.setupFundamentalsScroll();

        // Menu direct-open (without symbol) should still allow search.
        if (!this.symbol) {
            const alertEl = document.getElementById('sdAlert');
            if (alertEl) {
                alertEl.textContent = "Enter a symbol and press Go.";
                alertEl.classList.remove('alert--hidden');
            }
            return;
        }

        if (window.UI) window.UI.showLoading();

        await this.fetchSummary();
        this.loadTradePlan();
        this.fetchMarketEnvironment();
        this.fetchAiPrediction();

        await this.loadChartDataAll();
        this.applyPeriodAndRender(this.state.selectedPeriod);

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
            this.showFatal(`Failed to fetch data: ${e.message}`);
        } finally {
            this.state.loading = false;
        }
    }

    async loadChartDataAll() {
        try {
            const url = `${this.API_BASE}/stock/${encodeURIComponent(this.symbol)}/price?timeframe=${encodeURIComponent(this.state.timeframe)}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`API ${res.status}`);
            const data = await res.json();
            this.state.chartData = Array.isArray(data) ? data : [];

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
        const closes = data.map(d => Number(d.close));

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

        if (this.state.chartInstance) this.state.chartInstance.destroy();

        // Try native financial plugin first.
        if (!window.financialReady) {
            this.drawCandlestickFallback(data);
            return;
        }

        const labels = data.map(d => d.date);
        const candleData = data.map(d => ({
            o: Number(d.open),
            h: Number(d.high),
            l: Number(d.low),
            c: Number(d.close)
        }));
        try {
            this.state.chartInstance = new window.Chart(ctx, {
                type: 'candlestick',
                data: {
                    labels,
                    datasets: [{
                        label: 'Price',
                        data: candleData
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { type: 'category' },
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
            this.state.candleFallback = false;
        } catch (e) {
            console.warn('[Chart] Candlestick plugin failed, using canvas fallback:', e);
            this.drawCandlestickFallback(data);
        }
    }

    drawCandlestickFallback(data) {
        const canvas = document.getElementById('mainPriceChart');
        if (!canvas || !data || data.length === 0) return;
        if (this.state.chartInstance) {
            this.state.chartInstance.destroy();
            this.state.chartInstance = null;
        }
        const parent = canvas.parentElement;
        const width = Math.max(320, parent ? parent.clientWidth - 8 : 900);
        const height = 380;
        canvas.width = width;
        canvas.height = height;
        canvas.style.width = '100%';
        canvas.style.height = `${height}px`;

        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        ctx.clearRect(0, 0, width, height);

        const pad = { left: 56, right: 16, top: 20, bottom: 28 };
        const plotW = width - pad.left - pad.right;
        const plotH = height - pad.top - pad.bottom;

        const highs = data.map(d => Number(d.high));
        const lows = data.map(d => Number(d.low));
        const maxP = Math.max(...highs);
        const minP = Math.min(...lows);
        const span = Math.max(0.0001, maxP - minP);
        const yOf = (p) => pad.top + ((maxP - p) / span) * plotH;

        // Grid
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = pad.top + (plotH * i) / 4;
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(width - pad.right, y);
            ctx.stroke();
        }

        // Candles
        const n = data.length;
        const step = plotW / Math.max(1, n);
        const bodyW = Math.max(2, Math.min(12, step * 0.65));

        for (let i = 0; i < n; i++) {
            const d = data[i];
            const x = pad.left + step * i + step / 2;
            const o = Number(d.open);
            const h = Number(d.high);
            const l = Number(d.low);
            const c = Number(d.close);
            const up = c >= o;
            const color = up ? '#10b981' : '#ef4444';

            // wick
            ctx.strokeStyle = color;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, yOf(h));
            ctx.lineTo(x, yOf(l));
            ctx.stroke();

            // body
            const yTop = yOf(Math.max(o, c));
            const yBot = yOf(Math.min(o, c));
            const bodyH = Math.max(1, yBot - yTop);
            ctx.fillStyle = up ? 'rgba(16,185,129,0.8)' : 'rgba(239,68,68,0.8)';
            ctx.fillRect(x - bodyW / 2, yTop, bodyW, bodyH);
        }

        // Y labels
        ctx.fillStyle = 'rgba(255,255,255,0.7)';
        ctx.font = '12px monospace';
        ctx.textAlign = 'right';
        for (let i = 0; i <= 4; i++) {
            const p = maxP - (span * i) / 4;
            const y = pad.top + (plotH * i) / 4;
            ctx.fillText(p.toFixed(2), pad.left - 8, y + 4);
        }

        // X labels
        ctx.textAlign = 'left';
        ctx.fillText(data[0].date, pad.left, height - 8);
        ctx.textAlign = 'right';
        ctx.fillText(data[n - 1].date, width - pad.right, height - 8);

        this.state.candleFallback = true;
    }

    initVolumeChart(data) {
        const ctx = document.getElementById('volumeChart');
        if (!ctx || !data || data.length === 0) return;

        if (this.state.volumeInstance) this.state.volumeInstance.destroy();

        const labels = data.map(d => d.date);
        const volumes = data.map(d => Number(d.volume));

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
        this.updateChartTypeButton();
        btn.addEventListener('click', () => {
            try {
                this.state.chartType = (this.state.chartType === 'line') ? 'candlestick' : 'line';
                console.log('[Chart] Toggling to:', this.state.chartType);
                this.updateChartTypeButton();
                this.renderCharts();
            } catch (err) {
                console.error("[Chart] Toggle failed, falling back to line:", err);
                if (window.UI?.showToast) window.UI.showToast('Failed to toggle chart type. Falling back to line chart.', 'error');
                this.state.chartType = 'line';
                this.updateChartTypeButton();
                this.renderCharts();
            }
        });
    }

    setupDrawingTools() {
        const btnNone = document.getElementById('btnDrawNone');
        const btnH = document.getElementById('btnDrawHLine');
        const btnTrend = document.getElementById('btnDrawTrendLine');
        const btnClear = document.getElementById('btnClearLines');
        const btnZoom = document.getElementById('btnResetZoom');
        const overlay = this.getOverlayCanvas();

        const setMode = (mode) => {
            this.state.drawMode = mode;
            this.state.draftLine = null;
            if (btnNone) btnNone.style.background = mode === 'none' ? 'rgba(99, 102, 241, 0.2)' : '';
            if (btnH) btnH.style.background = mode === 'hline' ? 'rgba(245, 158, 11, 0.2)' : '';
            if (btnTrend) btnTrend.style.background = mode === 'trend' ? 'rgba(16, 185, 129, 0.2)' : '';
            if (overlay) overlay.style.pointerEvents = mode === 'none' ? 'none' : 'auto';
            this.redrawOverlay();
        };
        setMode('none');

        if (btnNone) btnNone.addEventListener('click', () => setMode('none'));
        if (btnH) btnH.addEventListener('click', () => setMode('hline'));
        if (btnTrend) btnTrend.addEventListener('click', () => setMode('trend'));
        if (btnClear) {
            btnClear.addEventListener('click', () => {
                this.state.drawLines = [];
                this.state.draftLine = null;
                this.redrawOverlay();
            });
        }
        if (btnZoom) {
            btnZoom.addEventListener('click', () => {
                try {
                    if (this.state.chartInstance?.resetZoom) this.state.chartInstance.resetZoom();
                } catch (e) {
                    console.warn('[Chart] resetZoom failed:', e);
                }
                if (this.state.chartType === 'candlestick' && this.state.candleFallback) {
                    const base = this.getBaseChartData();
                    this.resetFallbackView(base.length);
                }
                this.renderCharts();
            });
        }
        const baseCanvas = document.getElementById('mainPriceChart');
        if (baseCanvas) {
            baseCanvas.addEventListener('wheel', (evt) => this.handleFallbackWheel(evt), { passive: false });
            baseCanvas.addEventListener('mousedown', (evt) => this.handleFallbackPanStart(evt));
            baseCanvas.addEventListener('mousemove', (evt) => this.handleFallbackPanMove(evt));
            baseCanvas.addEventListener('mouseup', () => this.handleFallbackPanEnd());
            baseCanvas.addEventListener('mouseleave', () => this.handleFallbackPanEnd());
        }
        if (overlay) {
            overlay.addEventListener('mousedown', (evt) => {
                if (this.state.drawMode === 'none') return;
                const p = this.getMousePos(overlay, evt);
                this.state.isDrawing = true;
                this.state.draftLine = {
                    type: this.state.drawMode,
                    x1: p.x,
                    y1: p.y,
                    x2: p.x,
                    y2: p.y
                };
                this.redrawOverlay();
            });
            overlay.addEventListener('mousemove', (evt) => {
                if (!this.state.isDrawing || !this.state.draftLine) return;
                const p = this.getMousePos(overlay, evt);
                this.state.draftLine.x2 = p.x;
                this.state.draftLine.y2 = this.state.draftLine.type === 'hline' ? this.state.draftLine.y1 : p.y;
                this.redrawOverlay();
            });
            const finishDraw = () => {
                if (!this.state.isDrawing || !this.state.draftLine) return;
                const l = this.state.draftLine;
                const dx = Math.abs(l.x2 - l.x1);
                const dy = Math.abs(l.y2 - l.y1);
                if (dx > 2 || dy > 2) this.state.drawLines.push({ ...l });
                this.state.draftLine = null;
                this.state.isDrawing = false;
                this.redrawOverlay();
            };
            overlay.addEventListener('mouseup', finishDraw);
            overlay.addEventListener('mouseleave', finishDraw);
        }
        window.addEventListener('resize', () => this.syncOverlayCanvas());
    }

    getOverlayCanvas() {
        const container = document.querySelector('.chart-container');
        if (!container) return null;
        let overlay = document.getElementById('drawOverlay');
        if (!overlay) {
            overlay = document.createElement('canvas');
            overlay.id = 'drawOverlay';
            overlay.style.position = 'absolute';
            overlay.style.inset = '0';
            overlay.style.zIndex = '5';
            overlay.style.pointerEvents = 'none';
            overlay.style.cursor = 'crosshair';
            container.appendChild(overlay);
        }
        this.syncOverlayCanvas();
        return overlay;
    }

    syncOverlayCanvas() {
        const base = document.getElementById('mainPriceChart');
        const overlay = document.getElementById('drawOverlay');
        if (!base || !overlay) return;
        const w = base.clientWidth || base.width || 0;
        const h = base.clientHeight || base.height || 0;
        if (w <= 0 || h <= 0) return;
        if (overlay.width !== w || overlay.height !== h) {
            overlay.width = w;
            overlay.height = h;
        }
        this.redrawOverlay();
    }

    getMousePos(canvas, evt) {
        const r = canvas.getBoundingClientRect();
        return {
            x: evt.clientX - r.left,
            y: evt.clientY - r.top
        };
    }

    getBaseChartData() {
        return this.state.chartDataFiltered && this.state.chartDataFiltered.length > 0
            ? this.state.chartDataFiltered
            : this.state.chartData;
    }

    resetFallbackView(len) {
        this.state.fallbackViewStart = 0;
        this.state.fallbackViewEnd = Math.max(0, len);
    }

    ensureFallbackView(len) {
        if (len <= 0) {
            this.resetFallbackView(0);
            return;
        }
        if (
            this.state.fallbackViewStart === null ||
            this.state.fallbackViewEnd === null ||
            this.state.fallbackViewEnd > len ||
            this.state.fallbackViewStart < 0 ||
            this.state.fallbackViewStart >= this.state.fallbackViewEnd
        ) {
            this.resetFallbackView(len);
        }
    }

    getFallbackVisibleData(data) {
        if (!data || data.length === 0) return [];
        this.ensureFallbackView(data.length);
        const s = Math.max(0, Math.floor(this.state.fallbackViewStart));
        const e = Math.min(data.length, Math.floor(this.state.fallbackViewEnd));
        return data.slice(s, e);
    }

    handleFallbackWheel(evt) {
        if (!(this.state.chartType === 'candlestick' && this.state.candleFallback)) return;
        const base = this.getBaseChartData();
        if (!base || base.length < 20) return;
        evt.preventDefault();

        this.ensureFallbackView(base.length);
        let s = this.state.fallbackViewStart;
        let e = this.state.fallbackViewEnd;
        const len = e - s;
        const minWindow = 20;
        const maxWindow = base.length;
        const zoomIn = evt.deltaY < 0;
        const factor = zoomIn ? 0.85 : 1.15;
        let nextLen = Math.round(len * factor);
        nextLen = Math.max(minWindow, Math.min(maxWindow, nextLen));
        if (nextLen === len) return;

        const canvas = document.getElementById('mainPriceChart');
        const rect = canvas.getBoundingClientRect();
        const px = Math.min(Math.max(evt.clientX - rect.left, 0), rect.width);
        const ratio = rect.width > 0 ? px / rect.width : 0.5;
        const anchor = s + Math.round(len * ratio);

        let ns = anchor - Math.round(nextLen * ratio);
        let ne = ns + nextLen;
        if (ns < 0) {
            ns = 0;
            ne = nextLen;
        }
        if (ne > base.length) {
            ne = base.length;
            ns = ne - nextLen;
        }
        this.state.fallbackViewStart = ns;
        this.state.fallbackViewEnd = ne;
        this.renderCharts();
    }

    handleFallbackPanStart(evt) {
        if (!(this.state.chartType === 'candlestick' && this.state.candleFallback)) return;
        if (this.state.drawMode !== 'none') return;
        const base = this.getBaseChartData();
        if (!base || base.length < 2) return;
        this.ensureFallbackView(base.length);
        this.state.isPanningFallback = true;
        this.state.panStartX = evt.clientX;
        this.state.panStartStart = this.state.fallbackViewStart;
        this.state.panStartEnd = this.state.fallbackViewEnd;
    }

    handleFallbackPanMove(evt) {
        if (!this.state.isPanningFallback) return;
        const base = this.getBaseChartData();
        const canvas = document.getElementById('mainPriceChart');
        if (!base || !canvas) return;

        const width = Math.max(1, canvas.clientWidth || canvas.width || 1);
        const windowLen = Math.max(1, this.state.panStartEnd - this.state.panStartStart);
        const deltaPx = evt.clientX - this.state.panStartX;
        const shiftBars = Math.round((deltaPx / width) * windowLen);

        let ns = this.state.panStartStart - shiftBars;
        let ne = this.state.panStartEnd - shiftBars;
        if (ns < 0) {
            ns = 0;
            ne = windowLen;
        }
        if (ne > base.length) {
            ne = base.length;
            ns = Math.max(0, ne - windowLen);
        }
        this.state.fallbackViewStart = ns;
        this.state.fallbackViewEnd = ne;
        this.renderCharts();
    }

    handleFallbackPanEnd() {
        this.state.isPanningFallback = false;
    }

    redrawOverlay() {
        const overlay = document.getElementById('drawOverlay');
        if (!overlay) return;
        const ctx = overlay.getContext('2d');
        if (!ctx) return;
        ctx.clearRect(0, 0, overlay.width, overlay.height);

        const drawOne = (line, draft = false) => {
            const isH = line.type === 'hline';
            ctx.strokeStyle = isH ? 'rgba(245, 158, 11, 0.95)' : 'rgba(16, 185, 129, 0.95)';
            ctx.lineWidth = draft ? 1.2 : 1.6;
            ctx.setLineDash(draft ? [4, 4] : []);
            ctx.beginPath();
            ctx.moveTo(line.x1, line.y1);
            ctx.lineTo(line.x2, isH ? line.y1 : line.y2);
            ctx.stroke();
            ctx.setLineDash([]);
        };

        this.state.drawLines.forEach((l) => drawOne(l, false));
        if (this.state.draftLine) drawOne(this.state.draftLine, true);
    }

    setupChartPeriod() {
        const btns = document.querySelectorAll('.chart-period-btn');
        if (!btns || btns.length === 0) return;
        btns.forEach((btn) => {
            btn.addEventListener('click', () => {
                const period = btn.getAttribute('data-period') || '3M';
                this.applyPeriodAndRender(period);
            });
        });
    }

    setupTimeframe() {
        const btns = document.querySelectorAll('.tf-btn');
        if (!btns || btns.length === 0) return;
        btns.forEach((btn) => {
            btn.addEventListener('click', async () => {
                const tf = btn.getAttribute('data-tf') || '1d';
                if (tf === this.state.timeframe) return;
                this.state.timeframe = tf;
                btns.forEach((b) => {
                    b.classList.remove('btn-primary', 'active');
                    b.classList.add('btn-ghost');
                });
                btn.classList.add('btn-primary', 'active');
                btn.classList.remove('btn-ghost');

                if (window.UI?.showLoading) window.UI.showLoading();
                await this.loadChartDataAll();
                this.applyPeriodAndRender(this.state.selectedPeriod);
                if (window.UI?.hideLoading) window.UI.hideLoading();
            });
        });
    }

    updateChartTypeButton() {
        const btn = document.getElementById('btnToggleChartType');
        if (!btn) return;
        btn.textContent = this.state.chartType === 'line' ? 'Switch to Candles' : 'Switch to Line';
    }

    applyPeriodAndRender(period) {
        this.state.selectedPeriod = period;
        const dailyMap = {
            '1D': 1,
            '5D': 5,
            '1M': 22,
            '3M': 66,
            '6M': 132,
            '1Y': 252,
            '5Y': 1260,
            'MAX': -1
        };
        const weeklyMap = {
            '1D': 1,
            '5D': 1,
            '1M': 4,
            '3M': 13,
            '6M': 26,
            '1Y': 52,
            '5Y': 260,
            'MAX': -1
        };
        const bars = (this.state.timeframe === '1w' ? weeklyMap : dailyMap)[period] ?? 66;
        const source = Array.isArray(this.state.chartData) ? this.state.chartData : [];
        if (bars === -1) {
            this.state.chartDataFiltered = source.slice();
        } else {
            this.state.chartDataFiltered = source.slice(Math.max(0, source.length - bars));
        }
        this.resetFallbackView(this.state.chartDataFiltered.length);

        const btns = document.querySelectorAll('.chart-period-btn');
        btns.forEach((b) => {
            b.classList.remove('btn-primary', 'active');
            b.classList.add('btn-ghost');
            if (b.getAttribute('data-period') === period) {
                b.classList.add('btn-primary', 'active');
                b.classList.remove('btn-ghost');
            }
        });

        this.renderCharts();
    }

    renderCharts() {
        const baseData = this.getBaseChartData();
        if (!baseData || baseData.length === 0) {
            if (window.UI?.showToast) window.UI.showToast('No chart data available', 'warning');
            return;
        }
        let renderData = baseData;
        if (this.state.chartType === 'candlestick') {
            if (window.financialReady) {
                this.state.candleFallback = false;
                this.initMainChartCandle(baseData);
            } else {
                this.state.candleFallback = true;
                this.ensureFallbackView(baseData.length);
                renderData = this.getFallbackVisibleData(baseData);
                this.drawCandlestickFallback(renderData);
            }
        } else {
            this.state.candleFallback = false;
            this.initMainChartLine(baseData);
            renderData = baseData;
        }
        this.initVolumeChart(renderData);
        this.syncOverlayCanvas();
        this.redrawOverlay();
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
        this.renderTradePanel(data);
        this.renderManualScreeningSummary();
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
        const ytdEl = document.getElementById('sdYtd');
        if (ytdEl) {
            const ytd = (info.ytd_change_pct !== null && info.ytd_change_pct !== undefined)
                ? (window.UI ? window.UI.formatPct(info.ytd_change_pct) : `${info.ytd_change_pct}%`)
                : '-';
            ytdEl.innerHTML = `YTD: ${ytd}`;
        }
        this.renderChips(signals);
        this.renderIndicatorCards(indicators, info);
        const alertEl = document.getElementById('sdAlert');
        if (alertEl) alertEl.classList.add('alert--hidden');
    }

    renderInvalidState(quality) {
        const priceEl = document.getElementById('sdPrice');
        if (priceEl) priceEl.textContent = '-';
        const alertEl = document.getElementById('sdAlert');
        if (alertEl) {
            alertEl.innerHTML = `Invalid data: ${quality.price_reason || 'N/A'}`;
            alertEl.classList.remove('alert--hidden');
        }
    }

    setText(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = value ?? '--';
    }

    renderTradePanel(data) {
        const info = data?.stock_info || {};
        const indicators = data?.indicators || {};
        const signals = data?.signal_summary || {};
        const quality = data?.data_quality || {};

        const cp = Number(info.current_price);
        const atr = Number(indicators.atr14);
        const pivot = Number(indicators.pivot);
        const hasPrice = Number.isFinite(cp) && cp > 0;
        const hasAtr = Number.isFinite(atr) && atr > 0;
        const hasPivot = Number.isFinite(pivot) && pivot > 0;

        // Forecast range: use ATR-based range from current DB indicator snapshot.
        if (hasPrice && hasAtr) {
            const low = cp - atr;
            const high = cp + atr;
            this.setText('tpForecastRange', `${low.toFixed(2)} - ${high.toFixed(2)}`);
        } else if (hasPrice) {
            this.setText('tpForecastRange', `${cp.toFixed(2)} (ATR N/A)`);
        } else {
            this.setText('tpForecastRange', '--');
        }

        // Probability split from signal confidence (-1..1 -> 0..100).
        const conf = Number(signals.confidence);
        const c = Number.isFinite(conf) ? Math.max(-1, Math.min(1, conf)) : 0;
        let pUp = 33;
        let pDown = 33;
        let pFlat = 34;
        if (Number.isFinite(conf)) {
            pUp = Math.max(5, Math.min(90, Math.round(50 + c * 45)));
            pDown = Math.max(5, Math.min(90, Math.round(50 - c * 45)));
            pFlat = Math.max(0, 100 - pUp - pDown);
        }
        this.setText('tpProbUp', `${pUp}%`);
        this.setText('tpProbDown', `${pDown}%`);
        this.setText('tpProbFlat', `${pFlat}%`);
        this.setText('tpConfidence', Number.isFinite(conf) ? `${Math.round((Math.abs(c)) * 100)}%` : '--');

        const reasons = Array.isArray(signals.reasons) ? signals.reasons : [];
        const drivers = reasons.slice(0, 3).map(r => r.message).filter(Boolean);
        this.setText('tpDrivers', drivers.length ? drivers.join(' | ') : 'No signal drivers');

        // Events section (DB-backed quality/freshness from summary response).
        if (quality.latest_indicator_date) {
            this.setText('tpNextEvent', `Indicator snapshot: ${quality.latest_indicator_date}`);
        } else if (quality.latest_price_date) {
            this.setText('tpNextEvent', `Price snapshot: ${quality.latest_price_date}`);
        } else {
            this.setText('tpNextEvent', 'No event table in DB');
        }
        this.setText('tpEventNote', quality.price_reason || 'ok');

        // Pivot analysis from DB indicator snapshot.
        if (hasPivot) {
            this.setText('tpPivot', pivot.toFixed(2));
            if (hasPrice) {
                const diffPct = ((cp - pivot) / pivot) * 100;
                const sign = diffPct > 0 ? '+' : '';
                this.setText('tpPivotDiffPct', `${sign}${diffPct.toFixed(2)}%`);
            } else {
                this.setText('tpPivotDiffPct', '--');
            }
            const lo = pivot;
            const hi = pivot * 1.05;
            this.setText('tpBuyRange', `${lo.toFixed(2)} - ${hi.toFixed(2)}`);
        } else {
            this.setText('tpPivot', '--');
            this.setText('tpPivotDiffPct', '--');
            this.setText('tpBuyRange', '--');
        }
    }

    renderChips(signals) {
        const container = document.getElementById('sdReasons');
        if (!container) return;
        container.innerHTML = '';
        if (signals.reasons) {
            signals.reasons.forEach(r => {
                const chip = document.createElement('span');
                const status = String(r.status || '').toUpperCase();
                const cls = status === 'BUY' ? 'buy' : (status === 'CAUTION' || status === 'SELL' ? 'caution' : 'info');
                chip.className = `chip ${cls}`;
                chip.textContent = r.message;
                container.appendChild(chip);
            });
        }
    }

    renderIndicatorCards(indicators, info) {
        const fmt = (v, d = 2) => {
            if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
            return window.UI ? window.UI.formatNumber(Number(v), d) : Number(v).toFixed(d);
        };
        const pct = (v) => {
            if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
            return window.UI ? window.UI.formatPct(Number(v), true) : `${Number(v).toFixed(2)}%`;
        };
        const cp = Number(info.current_price);
        const hasCp = Number.isFinite(cp) && cp > 0;

        const badge = (status) => {
            const s = String(status || 'NEUTRAL').toUpperCase();
            const color = s === 'BUY' ? '#10b981' : (s === 'SELL' ? '#ef4444' : '#9ca3af');
            return `<span style="font-size:11px; padding:2px 6px; border-radius:999px; border:1px solid ${color}; color:${color}; margin-left:8px;">${s}</span>`;
        };
        const row = (label, value, status = null) => `
            <div class="kv-item">
                <span class="kv-key">${label}</span>
                <span class="kv-value">${value}${status ? badge(status) : ''}</span>
            </div>
        `;

        const trendStatus = (price, ma) => {
            if (!hasCp || ma == null || !Number.isFinite(Number(ma))) return 'NEUTRAL';
            return price >= Number(ma) ? 'BUY' : 'SELL';
        };
        const rsiStatus = (rsi) => {
            const v = Number(rsi);
            if (!Number.isFinite(v)) return 'NEUTRAL';
            if (v < 30) return 'BUY';
            if (v > 70) return 'SELL';
            return 'NEUTRAL';
        };
        const macdStatus = (m, s) => {
            const mv = Number(m), sv = Number(s);
            if (!Number.isFinite(mv) || !Number.isFinite(sv)) return 'NEUTRAL';
            return mv >= sv ? 'BUY' : 'SELL';
        };
        const mfiStatus = (mfi) => {
            const v = Number(mfi);
            if (!Number.isFinite(v)) return 'NEUTRAL';
            if (v < 20) return 'BUY';
            if (v > 80) return 'SELL';
            return 'NEUTRAL';
        };
        const dmiStatus = (pdi, mdi, adx) => {
            const p = Number(pdi), m = Number(mdi), a = Number(adx);
            if (!Number.isFinite(p) || !Number.isFinite(m)) return 'NEUTRAL';
            if (Number.isFinite(a) && a < 20) return 'NEUTRAL';
            return p >= m ? 'BUY' : 'SELL';
        };
        const bbStatus = (pb) => {
            const v = Number(pb);
            if (!Number.isFinite(v)) return 'NEUTRAL';
            if (v < 0.2) return 'BUY';
            if (v > 0.8) return 'SELL';
            return 'NEUTRAL';
        };
        const vwapStatus = (vwap) => {
            const v = Number(vwap);
            if (!hasCp || !Number.isFinite(v)) return 'NEUTRAL';
            return cp >= v ? 'BUY' : 'SELL';
        };
        const ichiStatus = (tenkan, kijun, spanA, spanB) => {
            const t = Number(tenkan), k = Number(kijun), a = Number(spanA), b = Number(spanB);
            if (!hasCp || ![t, k, a, b].every(Number.isFinite)) return 'NEUTRAL';
            const cloudTop = Math.max(a, b);
            const cloudBot = Math.min(a, b);
            if (cp > cloudTop && t >= k) return 'BUY';
            if (cp < cloudBot && t < k) return 'SELL';
            return 'NEUTRAL';
        };

        const trend = document.getElementById('sdTrend');
        if (trend) {
            trend.innerHTML = `
                ${row('SMA20', fmt(indicators.ma20), trendStatus(cp, indicators.ma20))}
                ${row('SMA50', fmt(indicators.ma50), trendStatus(cp, indicators.ma50))}
                ${row('SMA200', fmt(indicators.sma200), trendStatus(cp, indicators.sma200))}
                ${row('EMA21', fmt(indicators.ema21), trendStatus(cp, indicators.ema21))}
                ${row('VWAP20', fmt(indicators.vwap20), vwapStatus(indicators.vwap20))}
                ${row('RS Rating', indicators.rs_rating ?? '-', (indicators.rs_rating ?? 0) >= 80 ? 'BUY' : ((indicators.rs_rating ?? 0) <= 40 ? 'SELL' : 'NEUTRAL'))}
                ${row('Ichimoku', `T:${fmt(indicators.ichimoku_tenkan9)} K:${fmt(indicators.ichimoku_kijun26)}`, ichiStatus(indicators.ichimoku_tenkan9, indicators.ichimoku_kijun26, indicators.ichimoku_senkou_a, indicators.ichimoku_senkou_b))}
            `;
        }

        const momentum = document.getElementById('sdMomentum');
        if (momentum) {
            momentum.innerHTML = `
                ${row('RSI(14)', fmt(indicators.rsi), rsiStatus(indicators.rsi))}
                ${row('MFI(14)', fmt(indicators.mfi14), mfiStatus(indicators.mfi14))}
                ${row('MACD', fmt(indicators.macd, 4), macdStatus(indicators.macd, indicators.signal))}
                ${row('Signal', fmt(indicators.signal, 4))}
                ${row('MACD Hist', fmt(indicators.macd_hist, 4), macdStatus(indicators.macd_hist, 0))}
                ${row('+DI / -DI / ADX', `${fmt(indicators.plus_di14)}/${fmt(indicators.minus_di14)}/${fmt(indicators.adx14)}`, dmiStatus(indicators.plus_di14, indicators.minus_di14, indicators.adx14))}
                ${row('Pivot', fmt(indicators.pivot), trendStatus(cp, indicators.pivot))}
            `;
        }

        const risk = document.getElementById('sdRisk');
        if (risk) {
            risk.innerHTML = `
                ${row('ATR(14)', fmt(indicators.atr14))}
                ${row('BB Upper / Lower', `${fmt(indicators.bb_upper20)} / ${fmt(indicators.bb_lower20)}`)}
                ${row('BB Width', fmt(indicators.bb_width20, 4))}
                ${row('BB %B', fmt(indicators.bb_percent_b, 4), bbStatus(indicators.bb_percent_b))}
                ${row('52W High', fmt(indicators.high_52w))}
                ${row('Dist to 52W High', pct(indicators.dist_52w_high_pct))}
                ${row('OBV', fmt(indicators.obv, 0))}
                ${row('Volume', fmt(info.volume, 0))}
                ${row('Market', info.market || '-')}
            `;
        }
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

    formatFundValue(value, digits = 2) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
        if (window.UI && typeof window.UI.formatNumber === 'function') {
            return window.UI.formatNumber(Number(value), digits);
        }
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits
        });
    }

    formatSigned(value, digits = 2) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
        const n = Number(value);
        const sign = n >= 0 ? '+' : '';
        return `${sign}${this.formatFundValue(n, digits)}`;
    }

    formatSignedPct(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
        const n = Number(value);
        return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
    }

    renderFundamentalsTable(rowsDesc, comparison) {
        const thead = document.getElementById('fundamentalsHead');
        const tbody = document.getElementById('fundamentalsBody');
        if (!thead || !tbody) return;

        const rows = Array.isArray(rowsDesc) ? [...rowsDesc].reverse() : [];
        const metrics = [
            { label: 'Revenue', key: 'revenue', digits: 0 },
            { label: 'Net Income', key: 'net_income', digits: 0 },
            { label: 'EPS (Diluted)', key: 'eps', digits: 2 },
            { label: 'Operating Cash Flow', key: 'operating_cash_flow', digits: 0 },
            { label: 'Investing Cash Flow', key: 'investing_cash_flow', digits: 0 },
            { label: 'Financing Cash Flow', key: 'financing_cash_flow', digits: 0 },
            { label: 'CapEx', key: 'capex', digits: 0 },
            { label: 'Free Cash Flow', key: 'free_cash_flow', digits: 0 }
        ];
        if (rows.length === 0) {
            thead.innerHTML = `
                <tr>
                    <th class="fundamentals-sticky-col">鬆・岼 / Metric</th>
                    <th class="text-right fund-col">No Data</th>
                </tr>
            `;
            tbody.innerHTML = `
                <tr>
                    <td class="fundamentals-sticky-col">Revenue</td>
                    <td class="text-right fund-col">--</td>
                </tr>
            `;
            return;
        }

        thead.innerHTML = `
            <tr>
                <th class="fundamentals-sticky-col">鬆・岼 / Metric</th>
                ${rows.map(r => `<th class="text-right fund-col">${r.period_end_date}</th>`).join('')}
            </tr>
        `;

        const calcQoq = (arr, idx) => {
            if (idx <= 0) return { diff: null, pct: null };
            const cur = arr[idx];
            const prev = arr[idx - 1];
            if (cur === null || cur === undefined || prev === null || prev === undefined) return { diff: null, pct: null };
            const diff = Number(cur) - Number(prev);
            const pct = Number(prev) === 0 ? null : (diff / Number(prev)) * 100;
            return { diff, pct };
        };

        const calcYoy = (arr, dates, idx) => {
            const cur = arr[idx];
            if (cur === null || cur === undefined) return { diff: null, pct: null };
            const d = new Date(`${dates[idx]}T00:00:00`);
            const targetYear = d.getUTCFullYear() - 1;
            const targetMonth = d.getUTCMonth();
            const targetDay = d.getUTCDate();

            let match = -1;
            for (let i = 0; i < dates.length; i += 1) {
                const dd = new Date(`${dates[i]}T00:00:00`);
                if (
                    dd.getUTCFullYear() === targetYear &&
                    dd.getUTCMonth() === targetMonth &&
                    Math.abs(dd.getUTCDate() - targetDay) <= 3
                ) {
                    match = i;
                    break;
                }
            }
            if (match < 0) return { diff: null, pct: null };
            const base = arr[match];
            if (base === null || base === undefined) return { diff: null, pct: null };
            const diff = Number(cur) - Number(base);
            const pct = Number(base) === 0 ? null : (diff / Number(base)) * 100;
            return { diff, pct };
        };

        const compVal = (obj, digits) => `${this.formatSigned(obj?.diff, digits)} (${this.formatSignedPct(obj?.pct)})`;

        // YoY label: show comparison period if available
        const yoyPeriod = comparison?.yoy_period || null;
        const yoyLabel = yoyPeriod ? `YoY vs ${yoyPeriod}` : 'YoY';

        tbody.innerHTML = metrics.map(m => `
            <tr>
                <td class="fundamentals-sticky-col">${m.label}</td>
                ${rows.map(r => `<td class="text-right fund-col">${this.formatFundValue(r[m.key], m.digits)}</td>`).join('')}
            </tr>
            <tr>
                <td class="fundamentals-sticky-col">${m.label} QoQ</td>
                ${(() => {
                    const vals = rows.map(r => r[m.key]);
                    return rows.map((_, i) => `<td class="text-right fund-col">${compVal(calcQoq(vals, i), m.digits)}</td>`).join('');
                })()}
            </tr>
            <tr>
                <td class="fundamentals-sticky-col">${m.label} ${yoyLabel}</td>
                ${(() => {
                    const vals = rows.map(r => r[m.key]);
                    const dates = rows.map(r => r.period_end_date);
                    return rows.map((_, i) => {
                        const clientResult = calcYoy(vals, dates, i);
                        // For the latest column (rightmost), fall back to server-precomputed comparison
                        if (i === rows.length - 1 && (clientResult.diff === null || clientResult.diff === undefined)) {
                            const precomp = comparison?.latest_vs_same_quarter_prev_year?.[m.key];
                            if (precomp && (precomp.diff !== null && precomp.diff !== undefined)) {
                                return `<td class="text-right fund-col" title="vs ${yoyPeriod || 'prior year'}">${compVal(precomp, m.digits)}</td>`;
                            }
                        }
                        return `<td class="text-right fund-col">${compVal(clientResult, m.digits)}</td>`;
                    }).join('');
                })()}
            </tr>
        `).join('');
    }

    async loadFundamentalsData() {
        try {
            const url = `${this.API_BASE}/stock/${encodeURIComponent(this.symbol)}/fundamentals`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`API ${res.status}`);
            const data = await res.json();
            const rows = Array.isArray(data?.rows) ? data.rows : [];

            this.renderFundamentalsTable(rows, data?.comparison || {});
        } catch (e) {
            console.error('[Fundamentals] Load error:', e);
            this.renderFundamentalsTable([], {});
        }
    }
    async loadRatiosData() {
        const fmt = (v, d = 2) => {
            if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
            if (window.UI && typeof window.UI.formatNumber === 'function') return window.UI.formatNumber(Number(v), d);
            return Number(v).toFixed(d);
        };
        const fmtPct = (v) => {
            if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
            return `${Number(v).toFixed(2)}%`;
        };
        const set = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = val;
        };

        try {
            const url = `${this.API_BASE}/stock/${encodeURIComponent(this.symbol)}/ratios`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`API ${res.status}`);
            const data = await res.json();
            const r = data?.ratios || {};
            const m = data?.meta || {};

            set('ratioPe', fmt(r.pe_ratio, 2));
            set('ratioPeSub', `Forward P/E: ${fmt(r.forward_pe, 2)}${m.eps_period ? ` | EPS period: ${m.eps_period}` : ''}`);

            set('ratioRoe', fmtPct(r.roe));
            set('ratioRoeSub', r.roe == null ? 'No DB data' : 'Return on Equity');

            set('ratioRoa', fmtPct(r.roa));
            set('ratioRoaSub', r.roa == null ? 'No DB data' : 'Return on Assets');

            set('ratioNetMargin', fmtPct(r.net_margin));
            set('ratioNetMarginSub', m.margin_period ? `Profit Margin | Period: ${m.margin_period}` : 'No DB data');

            set('ratioEquityRatio', fmtPct(r.equity_ratio));
            set('ratioEquityRatioSub', r.equity_ratio == null ? 'No DB data' : 'Equity / Total Assets');

            set('ratioDebtEquity', fmt(r.debt_equity, 2));
            set('ratioDebtEquitySub', r.debt_equity == null ? 'No DB data' : 'Leverage');

            set('ratioCurrentRatio', fmt(r.current_ratio, 2));
            set('ratioCurrentRatioSub', r.current_ratio == null ? 'No DB data' : 'Liquidity');

            set('ratioQuickRatio', fmt(r.quick_ratio, 2));
            set('ratioQuickRatioSub', r.quick_ratio == null ? 'No DB data' : 'Acid Test');

            set('ratioAssetTurnover', fmt(r.asset_turnover, 2));
            set('ratioAssetTurnoverSub', r.asset_turnover == null ? 'No DB data' : 'Efficiency');
        } catch (e) {
            console.error('[Ratios] Load error:', e);
            [
                'ratioPe', 'ratioRoe', 'ratioRoa', 'ratioNetMargin', 'ratioEquityRatio',
                'ratioDebtEquity', 'ratioCurrentRatio', 'ratioQuickRatio', 'ratioAssetTurnover'
            ].forEach((id) => set(id, '--'));
            [
                'ratioPeSub', 'ratioRoeSub', 'ratioRoaSub', 'ratioNetMarginSub', 'ratioEquityRatioSub',
                'ratioDebtEquitySub', 'ratioCurrentRatioSub', 'ratioQuickRatioSub', 'ratioAssetTurnoverSub'
            ].forEach((id) => set(id, 'Failed to load'));
        }
    }

    setupSearch() {
        const input = document.getElementById('sdSearchInput');
        const btn = document.getElementById('sdSearchGo');
        if (!input || !btn) return;
        const handle = () => {
            const raw = input.value.trim();
            if (!raw) return;
            let symbol = raw.toUpperCase();
            if (!symbol.includes(':')) {
                if (/^\d{4}$/.test(symbol)) {
                    symbol = `JP:${symbol}`;
                } else {
                    symbol = `US:${symbol.replace(/\./g, '-')}`;
                }
            } else {
                const parts = symbol.split(':', 2);
                const market = parts[0];
                const code = parts[1] || '';
                symbol = market === 'US' ? `${market}:${code.replace(/\./g, '-')}` : `${market}:${code}`;
            }
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

    setupFundamentalsScroll() {
        const el = document.querySelector('.fundamentals-scroll');
        if (!el) return;
        let dragging = false;
        let startX = 0;
        let startScroll = 0;

        const onDown = (clientX) => {
            dragging = true;
            el.classList.add('dragging');
            startX = clientX;
            startScroll = el.scrollLeft;
        };
        const onMove = (clientX) => {
            if (!dragging) return;
            const dx = clientX - startX;
            el.scrollLeft = startScroll - dx;
        };
        const onUp = () => {
            dragging = false;
            el.classList.remove('dragging');
        };

        el.addEventListener('mousedown', (e) => onDown(e.clientX));
        window.addEventListener('mousemove', (e) => onMove(e.clientX));
        window.addEventListener('mouseup', onUp);
        el.addEventListener('mouseleave', onUp);

        el.addEventListener('touchstart', (e) => {
            if (!e.touches || e.touches.length === 0) return;
            onDown(e.touches[0].clientX);
        }, { passive: true });
        el.addEventListener('touchmove', (e) => {
            if (!e.touches || e.touches.length === 0) return;
            onMove(e.touches[0].clientX);
        }, { passive: true });
        el.addEventListener('touchend', onUp);
    }

    getSymbolForPlan() { return this.symbol || '--'; }

    getAiSymbolKey() {
        const raw = (this.symbol || '').trim();
        if (!raw) return '';
        return raw.includes(':') ? raw.toUpperCase() : `US:${raw.toUpperCase()}`;
    }

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
        if (window.UI) window.UI.showToast('Trade plan saved', 'success');
    }

    clearTradePlan() {
        const symbol = this.getSymbolForPlan();
        localStorage.removeItem(`tradePlan:${symbol}`);
        const ids = ['tpEntry', 'tpStop', 'tpTakeProfit', 'tpPositionSize', 'tpNotes'];
        ids.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        if (window.UI) window.UI.showToast('Trade plan cleared', 'info');
    }

    async fetchAiPrediction() {
        if (!this.symbol) return;
        try {
            const aiSymbol = this.getAiSymbolKey();
            const url = `${this.API_BASE}/stock/${encodeURIComponent(aiSymbol)}/ai-prediction`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(await getApiErrorMessage(res));
            const data = await res.json();
            this.state.aiPrediction = data;
            this.renderAiPrediction(data);
            this.refreshAiAnalytics().catch(e => console.warn('[AI] refreshAiAnalytics failed:', e));
            this.renderManualScreeningSummary();
        } catch (e) {
            console.warn('[AI] fetchAiPrediction failed:', e);
        }
    }

    async fetchMarketEnvironment() {
        try {
            const url = `${this.API_BASE}/market/environment`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`API ${res.status}`);
            const data = await res.json();
            this.state.marketEnv = data;
            this.renderManualScreeningSummary();
        } catch (e) {
            console.warn('[Market] fetchMarketEnvironment failed:', e);
        }
    }

    renderAiPrediction(data) {
        const probEl        = document.getElementById('tpAiProbUp5');
        const decisionEl    = document.getElementById('tpAiDecision');
        const dateEl        = document.getElementById('tpAiDate');
        const thresholdNote = document.getElementById('tpAiThresholdNote');
        const thresholdPct  = document.getElementById('tpAiThresholdPct');
        const gateNoteEl    = document.getElementById('tpAiGateNote');
        const newsPanelEl   = document.getElementById('tpAiNewsPanel');
        const newsUsageEl   = document.getElementById('tpAiNewsUsage');
        const realizedRowEl = document.getElementById('tpAiRealizedRow');
        const realizedEl    = document.getElementById('tpAiRealized');
        const errRowEl      = document.getElementById('tpAiErrorRow');
        const errEl         = document.getElementById('tpAiProbError');

        if (!data || !data.has_prediction) {
            if (probEl)        { probEl.textContent = '--'; probEl.className = 'tp-value'; }
            if (decisionEl) {
                decisionEl.className = 'tp-value';
                if (data && data.message) {
                    decisionEl.textContent = data.message;
                    decisionEl.style.fontSize = '0.8rem';
                } else {
                    decisionEl.textContent = 'No data';
                    decisionEl.style.fontSize = '';
                }
            }
            if (dateEl)        dateEl.textContent = '--';
            if (gateNoteEl)    gateNoteEl.style.display = 'none';
            if (thresholdNote) thresholdNote.style.display = 'none';
            if (newsPanelEl)   newsPanelEl.style.display = 'none';
            if (newsUsageEl)   newsUsageEl.textContent = '--';
            if (realizedRowEl) realizedRowEl.style.display = 'none';
            if (errRowEl)      errRowEl.style.display = 'none';
            return;
        }

        const pct = (data.p_up5 * 100).toFixed(1);
        const threshold = data.threshold_buy || 0.5;
        const thresholdPctVal = (threshold * 100).toFixed(0);

        // 遒ｺ邇・｡ｨ遉ｺ・郁牡蛻・￠・・
        if (probEl) {
            probEl.textContent = `${pct}%`;
            probEl.className = 'tp-value';
            if (data.p_up5 >= threshold) {
                probEl.classList.add('ai-prob-high');
            } else if (data.p_up5 >= 0.3) {
                probEl.classList.add('ai-prob-medium');
            } else {
                probEl.classList.add('ai-prob-low');
            }
        }

        // 蛻､螳夊｡ｨ遉ｺ
        if (decisionEl) {
            decisionEl.className = 'tp-value';
            decisionEl.style.fontSize = '';
            if (data.decision === 'BUY') {
                decisionEl.textContent = 'BUY';
                decisionEl.classList.add('ai-signal-buy');
            } else if (data.decision === 'GATE_BLOCKED') {
                decisionEl.textContent = 'GATE BLOCKED';
                decisionEl.classList.add('ai-signal-blocked');
            } else if (data.decision === 'CAUTION') {
                decisionEl.textContent = 'CAUTION';
                decisionEl.classList.add('ai-signal-caution');
            } else {
                decisionEl.textContent = 'NO TRADE';
                decisionEl.classList.add('ai-signal-notrade');
            }
        }

        // Gate note (GATE_BLOCKED / CAUTION)
        if (gateNoteEl) {
            if (data.gate_reason) {
                gateNoteEl.textContent = data.gate_reason;
                gateNoteEl.style.display = 'block';
            } else {
                gateNoteEl.style.display = 'none';
            }
        }

        // threshold 陬懆ｶｳ・亥ｸｸ縺ｫ陦ｨ遉ｺ・・
        if (thresholdNote) thresholdNote.style.display = 'flex';
        if (thresholdPct)  thresholdPct.textContent = thresholdPctVal;

        // 莠域ｸｬ譌･
        if (dateEl) dateEl.textContent = data.asof || '--';
        if (realizedRowEl && realizedEl) {
            if (data.actual_up5 != null || data.actual_return_pct_h != null) {
                const up5 = (data.actual_up5 === true) ? 'YES' : (data.actual_up5 === false ? 'NO' : 'n/a');
                const ret = (data.actual_return_pct_h != null) ? `${Number(data.actual_return_pct_h).toFixed(2)}%` : '--';
                realizedEl.textContent = `${up5} (${ret})`;
                realizedRowEl.style.display = 'flex';
            } else {
                realizedRowEl.style.display = 'none';
            }
        }
        if (errRowEl && errEl) {
            if (data.prob_error_up5 != null || data.brier_component != null) {
                const pe = (data.prob_error_up5 != null) ? Number(data.prob_error_up5).toFixed(3) : '--';
                const br = (data.brier_component != null) ? Number(data.brier_component).toFixed(3) : '--';
                errEl.textContent = `${pe} (brier=${br})`;
                errRowEl.style.display = 'flex';
            } else {
                errRowEl.style.display = 'none';
            }
        }
        this.renderAiPredictionNewsUsage(data);
    }
    async refreshAiAnalytics() {
        const panelEl = document.getElementById('tpAiAnalyticsPanel');
        const rankEl = document.getElementById('tpAiErrorRanking');
        const srcEl = document.getElementById('tpAiSourceReport');
        if (!panelEl || !rankEl || !srcEl) return;
        if (!this.symbol) {
            panelEl.style.display = 'none';
            return;
        }
        const aiSymbol = this.getAiSymbolKey();
        const [rankRes, srcRes] = await Promise.all([
            fetch(`${this.API_BASE}/prediction/stock-ai/error-ranking?limit=20&min_realized=2`),
            fetch(`${this.API_BASE}/prediction/stock-ai/source-report?symbol_key=${encodeURIComponent(aiSymbol)}&limit=12&min_obs=2&days_back=${365 * 3}`),
        ]);
        const rank = rankRes.ok ? await rankRes.json() : { items: [] };
        const src = srcRes.ok ? await srcRes.json() : { items: [] };
        panelEl.style.display = 'block';
        this.renderAiSymbolErrorRanking(rank, aiSymbol);
        this.renderAiSourceReport(src, aiSymbol);
    }
    renderAiSymbolErrorRanking(payload, currentSymbol) {
        const el = document.getElementById('tpAiErrorRanking');
        if (!el) return;
        const items = Array.isArray(payload?.items) ? payload.items : [];
        if (!items.length) {
            el.innerHTML = '<div style="color:var(--text-muted);">Symbol Error Ranking: no realized records yet</div>';
            return;
        }
        const top = items.slice(0, 8);
        const current = items.find(x => String(x.symbol_key || '').toUpperCase() === String(currentSymbol || '').toUpperCase());
        const rows = top.map((x, i) => {
            const isCur = String(x.symbol_key || '').toUpperCase() === String(currentSymbol || '').toUpperCase();
            return `<tr${isCur ? ' style="background:rgba(139,92,246,0.12);"' : ''}>
              <td>${i + 1}</td>
              <td>${x.symbol_key || ''}</td>
              <td>${x.realized_count ?? 0}</td>
              <td>${x.avg_brier != null ? Number(x.avg_brier).toFixed(3) : '--'}</td>
              <td>${x.mae_prob != null ? Number(x.mae_prob).toFixed(3) : '--'}</td>
              <td>${x.calibration_gap != null ? Number(x.calibration_gap).toFixed(3) : '--'}</td>
            </tr>`;
        }).join('');
        el.innerHTML = `
          <div style="margin-bottom:4px;"><b>Symbol Error Ranking</b>${current ? ` <span style="color:var(--text-muted);">| Current: ${current.symbol_key} brier=${current.avg_brier != null ? Number(current.avg_brier).toFixed(3) : '--'}</span>` : ''}</div>
          <div style="overflow:auto; max-height:180px;">
            <table style="width:100%; border-collapse:collapse; font-size:0.72rem;">
              <thead><tr style="text-align:left; color:var(--text-muted);"><th>#</th><th>Symbol</th><th>N</th><th>Brier</th><th>MAE</th><th>CalGap</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        `;
    }
    renderAiSourceReport(payload, currentSymbol) {
        const el = document.getElementById('tpAiSourceReport');
        if (!el) return;
        const items = Array.isArray(payload?.items) ? payload.items : [];
        if (!items.length) {
            el.innerHTML = '<div style="color:var(--text-muted);">News Source Contribution: no realized evidence rows yet</div>';
            return;
        }
        const rows = items.slice(0, 8).map((x) => `
          <tr>
            <td>${x.source_domain || x.source_type || ''}</td>
            <td>${x.mentions ?? 0}</td>
            <td>${x.unique_runs ?? 0}</td>
            <td>${x.avg_brier != null ? Number(x.avg_brier).toFixed(3) : '--'}</td>
            <td>${x.avg_abs_prob_error != null ? Number(x.avg_abs_prob_error).toFixed(3) : '--'}</td>
            <td>${x.calibration_gap != null ? Number(x.calibration_gap).toFixed(3) : '--'}</td>
          </tr>
        `).join('');
        el.innerHTML = `
          <div style="margin-bottom:4px;"><b>News Source Performance (Current Symbol)</b> <span style="color:var(--text-muted);">${currentSymbol || ''}</span></div>
          <div style="overflow:auto; max-height:190px;">
            <table style="width:100%; border-collapse:collapse; font-size:0.72rem;">
              <thead><tr style="text-align:left; color:var(--text-muted);"><th>Source</th><th>Mentions</th><th>Runs</th><th>Brier</th><th>MAE</th><th>CalGap</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
          <div style="margin-top:4px; color:var(--text-muted);">CalGap = actual_up5_rate - avg_pred_prob (source appeared in evidence rows)</div>
        `;
    }
    renderAiPredictionNewsUsage(data) {
        const panelEl = document.getElementById('tpAiNewsPanel');
        const el = document.getElementById('tpAiNewsUsage');
        if (!panelEl || !el) return;

        const refresh = data?.external_news_refresh || {};
        const usage = data?.news_usage || {};
        const queries = Array.isArray(refresh.queries) ? refresh.queries : [];
        const docs = Array.isArray(usage.docs_sample) ? usage.docs_sample : [];
        const docsBySource = usage.docs_by_source_type || {};
        const docsBySymbol = usage.docs_by_symbol || {};
        const usageError = usage.error || '';
        const llmUsed = (data?.llm_features_used === true) ? 'Yes' : ((data?.llm_features_used === false) ? 'No' : 'n/a');
        const featVer = data?.feature_set_version || 'n/a';
        const fmtDt = (v) => {
            if (!v) return '--';
            const d = new Date(v);
            if (Number.isNaN(d.getTime())) return String(v);
            return d.toLocaleString('ja-JP');
        };

        panelEl.style.display = 'block';
        el.innerHTML = `
            <div><b>News Refresh:</b> ${refresh.attempted ? 'attempted' : 'n/a'} / ingest=${refresh.news_ingest || 'n/a'} / llm=${refresh.llm_extract || 'n/a'}</div>
            <div style="margin-top:4px;"><b>LLM Features Used:</b> ${llmUsed} <span style="color:var(--text-muted);">(feature_set=${featVer})</span></div>
            <div style="margin-top:4px;"><b>Docs Used (display, last ${usage.window_days || 7}d):</b> ${usage.docs_used_count ?? 0}</div>
            <div style="margin-top:4px;"><b>Docs for As-Of (${usage.asof_date || (data?.asof || '--')}):</b> ${usage.docs_for_asof_count ?? refresh.docs_for_asof ?? 0}${usage.doc_limit ? ` / max ${usage.doc_limit}` : ''}</div>
            <div style="margin-top:4px;"><b>Docs by Symbol:</b> <span style="color:var(--text-muted);">${JSON.stringify(docsBySymbol)}</span></div>
            <div style="margin-top:4px;"><b>Docs by Source:</b> <span style="color:var(--text-muted);">${JSON.stringify(docsBySource)}</span></div>
            ${usageError ? `<div style="margin-top:4px;color:#fca5a5;"><b>News usage error:</b> ${usageError}</div>` : ''}
            <div style="margin-top:6px;"><b>Queries used for refresh:</b></div>
            ${queries.length
                ? `<ul style="margin:4px 0 0 16px; padding:0;">${queries.map(q => `<li><b>${q.symbol_key || ''}</b> [${q.label || 'news'}] : ${q.query || ''}</li>`).join('')}</ul>`
                : '<div style="color:var(--text-muted); margin-top:2px;">No refresh query info</div>'}
            <div style="margin-top:6px;"><b>Sample docs used:</b></div>
            ${docs.length
                ? `<ul style="margin:4px 0 0 16px; padding:0; max-height:180px; overflow:auto;">${docs.map(d => `<li>${fmtDt(d.published_at)} | <b>${d.symbol_key || ''}</b> | ${d.title || '(no title)'}</li>`).join('')}</ul>`
                : '<div style="color:var(--text-muted); margin-top:2px;">No sample docs found</div>'}
        `;
    }
    renderManualScreeningSummary() {
        const mount = document.getElementById('sdManualSummaryMount');
        if (!mount) return;

        const summary = this.state.summary || {};
        const trade = summary.trade_decision_summary || {};
        const canslim = summary.canslim_detail || {};
        const chart = summary.chart_pattern_detail || {};
        const sector = summary.sector_evaluation || {};
        const msum = summary.market_environment_summary || this.state.marketEnv?.summary || {};
        const ai = summary.ai_screening_summary || {};
        const aiLive = this.state.aiPrediction || {};

        const finalDecision = trade.final_decision || '-';
        const gate = trade.market_gate || msum.market_gate || '-';
        const posSize = trade.recommended_position_size_pct;
        const aiP = (trade.ai_plus5_probability_2w_pct ?? ai.p_2w_plus_5_pct);
        const ai3 = trade.ai_3class_probability || ai.ai_3class_probability || ai;
        const ai3Text = ai.display_3class || (
            ai3 && (ai3.up != null)
                ? `Up ${ai3.up}% / Flat ${ai3.flat}% / Down ${ai3.down}%`
                : '-'
        );
        const breakout = trade.breakout_judgment || chart.breakout_judgment || '-';
        const reasons = Array.isArray(trade.buy_block_reasons) ? trade.buy_block_reasons : [];
        const plus = Array.isArray(ai.plus_factors) ? ai.plus_factors : [];
        const minus = Array.isArray(ai.minus_factors) ? ai.minus_factors : [];
        const canslimItems = canslim.items || {};
        const canslimRows = ['C', 'A', 'N', 'S', 'L', 'I', 'M'].map((k) => {
            const item = canslimItems[k] || {};
            const st = item.status;
            const badge = st === true
                ? '<span style="color:#34d399;font-weight:700;">PASS</span>'
                : (st === false
                    ? '<span style="color:#f87171;font-weight:700;">FAIL</span>'
                    : '<span style="color:#cbd5e1;">N/A</span>');
            const note = item.note || (item.value ? JSON.stringify(item.value) : '');
            return `
                <div class="kv-item">
                    <span class="kv-key">${k} (${item.label || '-'})</span>
                    <span class="kv-value">${badge}${note ? ` <span style="font-weight:400;color:var(--text-muted);font-size:12px;">${note}</span>` : ''}</span>
                </div>
            `;
        }).join('');

        const decisionTone = finalDecision === '買い' ? '#34d399' : (finalDecision === '新規買い停止' ? '#f87171' : '#fbbf24');
        const gateTone = gate === 'ON' ? '#34d399' : (gate === 'OFF' ? '#f87171' : '#fbbf24');
        const mJudgment = msum.m_total_judgment || this.state.marketEnv?.summary?.m_judgment || '-';
        const mVix = msum.vix_mode || this.state.marketEnv?.summary?.vix_mode || '-';
        const mDdNas = msum.dd_count?.nasdaq ?? this.state.marketEnv?.summary?.nasdaq_dd_count_5w;
        const mDdSp = msum.dd_count?.sp500 ?? this.state.marketEnv?.summary?.sp500_dd_count_5w;
        const livePUp5 = aiLive?.has_prediction && aiLive?.p_up5 != null ? `${(Number(aiLive.p_up5) * 100).toFixed(1)}%` : null;

        mount.innerHTML = `
            <div class="panel-header mb-4">
                <div class="panel-title">Final Screening Decision</div>
                <a class="btn btn-ghost btn-sm" href="market_dashboard.html" style="text-decoration:none;">Market Dashboard</a>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:14px;">
                <div class="indicator-card" style="padding:14px;">
                    <div class="kv-key">Final Decision</div>
                    <div class="kv-value" style="font-size:1.3rem;color:${decisionTone};">${finalDecision}</div>
                    <div class="kv-key" style="margin-top:6px;">Recent Action: ${trade.recent_action || '-'}</div>
                </div>
                <div class="indicator-card" style="padding:14px;">
                    <div class="kv-key">Market Gate / Size</div>
                    <div class="kv-value" style="color:${gateTone};">${gate}</div>
                    <div class="kv-key" style="margin-top:6px;">Recommended Position: ${posSize != null ? `${posSize}%` : '-'}</div>
                </div>
                <div class="indicator-card" style="padding:14px;">
                    <div class="kv-key">AI +5% Prob (2W)</div>
                    <div class="kv-value">${aiP != null ? `${Number(aiP).toFixed(1)}%` : '--'}</div>
                    <div class="kv-key" style="margin-top:6px;">${ai3Text}</div>
                    ${livePUp5 ? `<div class="kv-key" style="margin-top:6px;">DB AI (Live): ${livePUp5}</div>` : ''}
                </div>
                <div class="indicator-card" style="padding:14px;">
                    <div class="kv-key">Breakout Judgment</div>
                    <div class="kv-value">${breakout}</div>
                    <div class="kv-key" style="margin-top:6px;">Reason: ${(reasons[0] || 'none')}</div>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;">
                <div class="indicator-card" style="padding:14px;">
                    <div class="panel-title" style="font-size:0.95rem;margin-bottom:8px;">CAN-SLIM (${canslim.pass_count ?? 0}/${canslim.total ?? 7})</div>
                    <div class="kv-list">${canslimRows}</div>
                </div>
                <div style="display:grid;grid-template-rows:auto auto auto;gap:12px;">
                    <div class="indicator-card" style="padding:14px;">
                        <div class="panel-title" style="font-size:0.95rem;margin-bottom:8px;">Chart Pattern</div>
                        <div class="kv-list">
                            <div class="kv-item"><span class="kv-key">Base Type</span><span class="kv-value">${chart?.cup_with_handle?.base_type || chart.base_type || '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">Breakout Point</span><span class="kv-value">${chart?.cup_with_handle?.breakout_point ?? '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">Distance from Pivot</span><span class="kv-value">${chart?.cup_with_handle?.distance_from_pivot_pct != null ? Number(chart.cup_with_handle.distance_from_pivot_pct).toFixed(2) + '%' : '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">Breakout Volume Ratio</span><span class="kv-value">${chart?.cup_with_handle?.breakout_volume_ratio ?? '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">2.5x Rule</span><span class="kv-value">${chart?.cup_with_handle?.rule_2_5x_violation ? 'Violation' : 'N/A or Not judged'}</span></div>
                        </div>
                    </div>
                    <div class="indicator-card" style="padding:14px;">
                        <div class="panel-title" style="font-size:0.95rem;margin-bottom:8px;">Sector Evaluation</div>
                        <div class="kv-list">
                            <div class="kv-item"><span class="kv-key">Relative Strength</span><span class="kv-value">${sector.sector_relative_strength ?? '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">Leader Rank</span><span class="kv-value">${sector.sector_leader_rank ?? '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">Substitution Difficulty</span><span class="kv-value">${sector.substitution_difficulty_score ?? '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">Ecosystem Dependency</span><span class="kv-value">${sector.ecosystem_dependency_score ?? '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">Share Gain Score</span><span class="kv-value">${sector.market_share_gain_score ?? '-'}</span></div>
                        </div>
                    </div>
                    <div class="indicator-card" style="padding:14px;">
                        <div class="panel-title" style="font-size:0.95rem;margin-bottom:8px;">Market Environment Summary</div>
                        <div class="kv-list">
                            <div class="kv-item"><span class="kv-key">M Judgment</span><span class="kv-value">${mJudgment}</span></div>
                            <div class="kv-item"><span class="kv-key">DD Count</span><span class="kv-value">NASDAQ ${mDdNas ?? '-'} / SP500 ${mDdSp ?? '-'}</span></div>
                            <div class="kv-item"><span class="kv-key">VIX</span><span class="kv-value">${mVix}${this.state.marketEnv?.summary?.vix_level != null ? ` (${this.state.marketEnv.summary.vix_level})` : ''}</span></div>
                            <div class="kv-item"><span class="kv-key">HO</span><span class="kv-value">${msum.ho_status || this.state.marketEnv?.summary?.ho_alert || '-'}</span></div>
                        </div>
                    </div>
                </div>
            </div>
            <div style="margin-top:12px; display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                <div class="indicator-card" style="padding:14px;">
                    <div class="panel-title" style="font-size:0.95rem;margin-bottom:8px;">AI Positive Factors</div>
                    <ul class="bullets" style="margin:0;">${(plus.length ? plus : ['-']).map(x => `<li>${x}</li>`).join('')}</ul>
                </div>
                <div class="indicator-card" style="padding:14px;">
                    <div class="panel-title" style="font-size:0.95rem;margin-bottom:8px;">AI Negative / Block Reasons</div>
                    <ul class="bullets" style="margin:0;">${([...(minus.length ? minus : []), ...(reasons || [])].slice(0,6).length ? [...(minus || []), ...(reasons || [])].slice(0,6) : ['-']).map(x => `<li>${x}</li>`).join('')}</ul>
                </div>
            </div>
            <div class="muted" style="padding:10px 0 0 0;">${trade.reason_template || 'Additional decision summary will be shown after API response.'}</div>
        `;
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

async function getApiErrorMessage(res) {
    try {
        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            const payload = await res.json();
            if (payload && typeof payload.message === 'string' && payload.message.trim()) {
                return payload.message;
            }
            if (payload && typeof payload.error === 'string' && payload.error.trim()) {
                return payload.error;
            }
        } else {
            const text = await res.text();
            if (text && text.trim()) return text.trim();
        }
    } catch (e) {
        console.warn('[API] failed to parse error payload:', e);
    }
    return `API ${res.status}`;
}

// ---- Hotfix: Trade Panel toggle (minimal, global) ----
(function registerTradePanelToggle() {
    window.toggleTradePanel = function () {
        const body = document.getElementById('tpBody');
        const toggleBtn = document.getElementById('tpToggle');
        if (!body || !toggleBtn) return;

        const isOpen = body.classList.toggle('active');
        toggleBtn.textContent = isOpen ? 'Close' : 'Open';
    };
})();

// ---- AI Prediction: on-demand run ----
async function runAiPrediction() {
    if (!window.app || !window.app.symbol) return;
    const btn     = document.getElementById('tpAiRunBtn');
    const loading = document.getElementById('tpAiLoading');
    if (btn)     { btn.disabled = true; btn.textContent = 'Running...'; }
    if (loading) {
        loading.style.display = 'block';
        loading.innerHTML = '<span>Computing prediction... (may take 30s if fetching data)</span>';
    }
    try {
        const aiSymbol = (typeof window.app.getAiSymbolKey === 'function')
            ? window.app.getAiSymbolKey()
            : ((window.app.symbol || '').includes(':')
                ? String(window.app.symbol).toUpperCase()
                : `US:${String(window.app.symbol || '').toUpperCase()}`);
        const url = `${window.app.API_BASE}/stock/${encodeURIComponent(aiSymbol)}/ai-prediction/run`;
        const res = await fetch(url, { method: 'POST' });
        if (!res.ok) throw new Error(await getApiErrorMessage(res));
        const data = await res.json();
        window.app.state.aiPrediction = data;
        window.app.renderAiPrediction(data);
        if (typeof window.app.refreshAiAnalytics === 'function') {
            window.app.refreshAiAnalytics().catch(e => console.warn('[AI] refreshAiAnalytics failed:', e));
        }
        if (typeof window.app.renderManualScreeningSummary === 'function') {
            window.app.renderManualScreeningSummary();
        }
        if (data && data.has_prediction) {
            if (window.UI) window.UI.showToast('AI prediction updated', 'success');
            const refresh = data.external_news_refresh || {};
            const newsIngestOk = refresh.news_ingest === 'success';
            const llmExtractOk = refresh.llm_extract === 'success';
            // Prefer action-time warning: user just pressed Run Now and can immediately
            // understand whether external news was actually fetched/used.
            if (refresh.attempted && (!newsIngestOk || !llmExtractOk)) {
                const parts = [];
                if (!newsIngestOk) parts.push('external news fetch failed');
                if (!llmExtractOk) parts.push('news signal extraction failed');
                if (window.UI) window.UI.showToast(`Warning: ${parts.join(' / ')} (prediction continued)`, 'warning');
            } else if (data.llm_features_used === false) {
                if (window.UI) window.UI.showToast('Warning: prediction updated without news features', 'warning');
            }
        } else {
            const msg = (data && data.message) ? data.message : 'Prediction data not available';
            if (window.UI) window.UI.showToast(msg, 'error');
        }
    } catch (e) {
        console.error('[AI] runAiPrediction failed:', e);
        if (window.UI) window.UI.showToast('Prediction failed: ' + e.message, 'error');
    } finally {
        if (btn)     { btn.disabled = false; btn.textContent = 'Run Now'; }
        if (loading) loading.style.display = 'none';
    }
}

async function backfillAiPredictionActuals() {
    if (!window.app || !window.app.symbol) return;
    const btn = document.getElementById('tpAiBackfillBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Backfilling...'; }
    try {
        const aiSymbol = (typeof window.app.getAiSymbolKey === 'function')
            ? window.app.getAiSymbolKey()
            : ((window.app.symbol || '').includes(':')
                ? String(window.app.symbol).toUpperCase()
                : `US:${String(window.app.symbol || '').toUpperCase()}`);
        const url = `${window.app.API_BASE}/stock/${encodeURIComponent(aiSymbol)}/ai-prediction/backfill-actuals?horizon_days=10&limit=500`;
        const res = await fetch(url, { method: 'POST' });
        if (!res.ok) throw new Error(await getApiErrorMessage(res));
        const data = await res.json();
        if (window.UI) window.UI.showToast(`Backfill completed: ${data?.updated ?? 0} updated`, 'success');
        if (typeof window.app.fetchAiPrediction === 'function') {
            await window.app.fetchAiPrediction();
        }
    } catch (e) {
        console.error('[AI] backfillAiPredictionActuals failed:', e);
        if (window.UI) window.UI.showToast('Backfill failed: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Backfill'; }
    }
}

// 繧ｰ繝ｭ繝ｼ繝舌Ν縺ｫ譏守､ｺ繧ｨ繧ｯ繧ｹ繝昴・繝茨ｼ・efer 隱ｭ縺ｿ霎ｼ縺ｿ縺ｧ繧・onclick 縺九ｉ蜿ら・蜿ｯ閭ｽ縺ｫ縺吶ｋ・・
window.runAiPrediction = runAiPrediction;
window.backfillAiPredictionActuals = backfillAiPredictionActuals;

