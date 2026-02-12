/* js/stock_detail.js - Phase 5-1 Strict Bootstrapping */

// 1. Immediate Execution Log
console.log("[StockDetail] script loaded", location.href);
window.__SD_LOADED__ = true;

class StockDetailApp {
    constructor() {
        // 2. Immediate Symbol Parsing & Display (Before init/async)
        const params = new URLSearchParams(window.location.search);
        // Robust param parsing
        const raw = params.get('symbol') || params.get('symbol_key') || params.get('s') || params.get('ticker');

        console.log("[StockDetail] raw symbol param:", raw);

        this.symbol = raw ? decodeURIComponent(raw).trim() : null;

        // Immediate DOM Update
        const symbolEl = document.getElementById("sdSymbol");
        if (symbolEl) {
            symbolEl.textContent = this.symbol || "--:----";
        }

        this.state = {
            summary: null,
            chartLoaded: false,
            fundLoaded: false
        };

        // Start Logic with Safe Guard
        try {
            this.init();
        } catch (e) {
            this.showFatal(`Init Error: ${e.message}`);
        }
    }

    async init() {
        // 3. CONFIG Validation
        if (!window.CONFIG || !window.CONFIG.API_BASE) {
            this.showFatal("CONFIG missing: config.js not loaded or API_BASE undefined. Check /js/config.js 200 OK.");
            return;
        }
        console.log("[StockDetail] API_BASE:", window.CONFIG.API_BASE);

        if (!this.symbol) {
            this.showFatal("No symbol provided in URL (e.g. ?symbol=US:NVDA)");
            return;
        }

        console.log(`[StockDetail] Init for ${this.symbol}`);

        // 4. Initial Summary Load (Fast) using Phase 5-1 Endpoint
        await this.fetchSummary();

        // 5. Setup Tabs
        this.setupTabs();
    }

    async fetchSummary() {
        try {
            // Encode Component is critical for "US:NVDA"
            const url = `${CONFIG.API_BASE}/stock/${encodeURIComponent(this.symbol)}/summary`;
            console.log("[StockDetail] fetch:", url);

            const res = await fetch(url);

            if (!res.ok) {
                // Handle 404 cleanly
                if (res.status === 404) {
                    this.showFatal(`Stock not found: ${this.symbol}`);
                    return;
                }
                throw new Error(`API Error: ${res.status}`);
            }

            const data = await res.json();
            this.state.summary = data;

            this.render(data);

        } catch (e) {
            console.error(e);
            this.showFatal(`Failed to fetch: ${url}<br>Reason: ${e.message}`);
        }
    }

    render(data) {
        const info = data.stock_info;
        const q = data.data_quality;
        const signals = data.signal_summary;
        const ind = data.indicators;

        // Render Name (Proof of Data Load)
        document.getElementById('sdName').textContent = info.name;

        // --- Price Logic (Strict) ---
        // Rule: price_valid === false OR close <= 0 -> Show Alert, "-" everywhere
        const priceEl = document.getElementById('sdPrice');
        const changeEl = document.getElementById('sdChange');
        const alertEl = document.getElementById('sdAlert');

        if (!q.price_valid || !info.current_price || info.current_price <= 0) {
            // INVALID PRICE STATE
            priceEl.textContent = '-';
            priceEl.classList.add('text-muted');
            changeEl.innerHTML = '<span class="badge badge-neutral">NO DATA</span>';

            // Show Alert
            alertEl.innerHTML = `
                <div class="alert-title">⚠️ データ欠損 / Data Missing</div>
                <div class="alert-message">
                    <p>この銘柄の価格データが取得できていません。(${this.symbol})</p>
                    <ul class="mt-2 text-sm" style="list-style:disc; padding-left:20px;">
                        <li>データプロバイダからデータが返却されませんでした。</li>
                        <li>新規上場、またはティッカー変更の可能性があります。</li>
                        <li><strong>14日以内の自動再取得</strong>により復旧する可能性があります。</li>
                    </ul>
                    <div class="mt-2 text-xs text-muted">API Status: ${q.price_reason || 'Unknown'}</div>
                </div>
            `;
            alertEl.classList.remove('alert--hidden');
            alertEl.classList.add('alert--warning');

            // Force signals to NO_DATA visually
            this.renderChips({ overall: 'NO_DATA', reasons: [] });
            this.renderCardsInvalid();

        } else {
            // VALID PRICE STATE
            priceEl.textContent = UI.formatCurrency(info.current_price, info.currency);
            priceEl.classList.remove('text-muted');

            // Change Pct (Strict: no NaN)
            if (info.change_pct !== null && !isNaN(info.change_pct)) {
                changeEl.innerHTML = UI.formatPct(info.change_pct);
            } else {
                changeEl.textContent = '-';
            }

            // YTD
            const ytdVal = (info.ytd_change_pct !== null && !isNaN(info.ytd_change_pct))
                ? UI.formatPct(info.ytd_change_pct) : '-';
            const ytdEl = document.getElementById('sdYtd');
            if (ytdEl) ytdEl.innerHTML = `YTD: ${ytdVal}`;

            // Hide Alert
            alertEl.classList.add('alert--hidden');

            // Render Signals & Cards
            this.renderChips(signals);
            this.renderCards(ind, signals);
        }
    }

    renderChips(signals) {
        const container = document.getElementById('sdReasons');
        if (!container) return;
        container.innerHTML = '';

        if (!signals || signals.overall === 'NO_DATA') {
            container.innerHTML = `<span class="chip chip--neutral">NO_DATA</span>`;
            return;
        }

        // 1. Overall Badge
        const overallParams = this.getStatusParams(signals.overall);
        container.innerHTML += `<span class="chip ${overallParams.cls}">${signals.overall}</span>`;

        // 2. Reasons
        if (signals.reasons) {
            signals.reasons.forEach(r => {
                const p = this.getStatusParams(r.status);
                container.innerHTML += `
                    <div class="chip ${p.cls}">
                        <span>${p.icon} ${r.message}</span>
                    </div>
                `;
            });
        }
    }

    renderCards(ind, signals) {
        // Helpers
        const kv = (k, v, dec = 2, suf = '') => `
            <div class="kv-row">
                <span class="kv-key">${k}</span>
                <span class="kv-val">${this.fmt(v, dec, suf)}</span>
            </div>`;

        // Trend
        const trendReason = signals.reasons.find(r => r.key.includes('trend'));
        const trendStatus = trendReason ? trendReason.status : 'NEUTRAL';
        this.setPanelBorder('cardTrend', trendStatus);

        const elTrend = document.getElementById('sdTrend');
        if (elTrend) {
            elTrend.innerHTML = `
                ${kv('SMA 50', ind.ma50)}
                ${kv('SMA 200', ind.sma200)}
                ${kv('EMA 21', ind.ema21)}
                ${kv('Pivot', ind.pivot)}
            `;
        }

        // Momentum
        const momReason = signals.reasons.find(r => r.key.includes('rsi') || r.key.includes('macd'));
        const momStatus = momReason ? momReason.status : 'NEUTRAL';
        this.setPanelBorder('cardMomentum', momStatus);

        const elMom = document.getElementById('sdMomentum');
        if (elMom) {
            elMom.innerHTML = `
                ${kv('RSI (14)', ind.rsi, 1)}
                ${kv('MACD', ind.macd)}
                ${kv('Signal', ind.signal)}
                ${kv('Stochastic', null)} <span class="text-xs text-muted">(Phase 5-2)</span>
            `;
        }

        // Risk / Volatility
        const rsReason = signals.reasons.find(r => r.key.includes('rs'));
        const riskStatus = rsReason ? rsReason.status : 'NEUTRAL';
        this.setPanelBorder('cardRisk', riskStatus);

        const elRisk = document.getElementById('sdRisk');
        if (elRisk) {
            elRisk.innerHTML = `
                ${kv('ATR (14)', ind.atr14)}
                ${kv('52W High', ind.high_52w)}
                ${kv('Dist to High', ind.dist_52w_high_pct, 1, '%')}
                ${kv('RS Rating', ind.rs_rating, 0, '', true)}
            `;
        }
    }

    renderCardsInvalid() {
        ['cardTrend', 'cardMomentum', 'cardRisk'].forEach(id => {
            this.setPanelBorder(id, 'nodata');
            const el = document.getElementById(id.replace('card', 'sd'));
            if (el) el.innerHTML = '<div class="muted">No Data</div>';
        });
    }

    // --- Helpers ---

    showFatal(msg) {
        const a = document.getElementById("sdAlert");
        if (a) {
            a.innerHTML = `<div class="alert-title">🚫 System Error</div><div>${msg}</div>`;
            a.classList.remove("alert--hidden");
            a.classList.add("alert--warning"); // Ensure it looks like warning
        }
        console.error("[StockDetail:FATAL]", msg);
    }

    setPanelBorder(id, status) {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.remove('border-success', 'border-danger', 'border-caution', 'border-neutral', 'border-nodata');

        switch (status) {
            case 'BUY': el.classList.add('border-success'); break;
            case 'SELL': el.classList.add('border-danger'); break;
            case 'CAUTION': el.classList.add('border-caution'); break;
            case 'NO_DATA': el.classList.add('border-nodata'); break;
            default: el.classList.add('border-neutral');
        }
    }

    getStatusParams(status) {
        switch (status) {
            case 'BUY': return { cls: 'chip--success', icon: '✅' };
            case 'SELL': return { cls: 'chip--danger', icon: '🔻' };
            case 'CAUTION': return { cls: 'chip--caution', icon: '⚠️' };
            case 'NO_DATA': return { cls: 'chip--neutral', icon: '🌑' };
            default: return { cls: 'chip--neutral', icon: '🔹' };
        }
    }

    fmt(val, dec = 2, suf = '', isInt = false) {
        if (val === null || val === undefined || isNaN(val)) return '-';
        if (val === 0) return '-';
        return UI.formatNumber(val, dec) + suf;
    }

    setupTabs() {
        document.querySelectorAll('.tab').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.tab').forEach(b => b.classList.remove('is-active'));
                e.target.classList.add('is-active');

                const targetId = e.target.dataset.tab;
                document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('is-active'));
                const targetPanel = document.getElementById(`tab-${targetId}`);
                if (targetPanel) targetPanel.classList.add('is-active');

                // Lazy Load
                if (targetId === 'chart' && !this.state.chartLoaded) {
                    console.log("[StockDetail] Lazy loading Chart... (Phase 5-2)");
                    this.state.chartLoaded = true;
                }
                if (targetId === 'fundamentals' && !this.state.fundLoaded) {
                    console.log("[StockDetail] Lazy loading Fundamentals... (Phase 5-3)");
                    this.state.fundLoaded = true;
                }
            });
        });
    }
}

// Start
window.addEventListener('DOMContentLoaded', () => {
    window.stockDetailApp = new StockDetailApp();
});
