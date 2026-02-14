/* screener.js - Enhanced Edition */

(function () {
    // Use CONFIG from window if available
    const API_BASE = window.CONFIG?.API_BASE || "http://localhost:8000/api/v1";

    // Application State
    const state = {
        page: 1,
        limit: 50,
        total: 0,
        items: [],
        loading: false,
        filters: {
            minPrice: null,
            maxPrice: null,
            minRS: null,
            minTotalScore: null,
            searchQuery: ''
        }
    };

    // DOM Element Cache
    const elements = {};

    // Utility Functions
    function $(id) {
        if (!elements[id]) {
            elements[id] = document.getElementById(id);
        }
        return elements[id];
    }

    function safeNumber(x, fallback = 0) {
        const n = Number(x);
        return Number.isFinite(n) ? n : fallback;
    }

    function normalizeScreenerResponse(data) {
        // Support multiple response formats
        const items =
            data?.items ??
            data?.results ??
            data?.data ??
            data?.stocks ??
            [];

        const total =
            data?.total_count ??
            data?.total ??
            data?.count ??
            data?.total_stocks ??
            (Array.isArray(items) ? items.length : 0);

        return {
            items: Array.isArray(items) ? items : [],
            total: safeNumber(total, 0)
        };
    }

    // Update filters from form
    function updateFilters() {
        const priceMin = $("priceMin")?.value;
        const priceMax = $("priceMax")?.value;
        const minRS = $("minRS")?.value;
        const minScore = $("minTotalScore")?.value;
        const search = $("searchInput")?.value;

        state.filters = {
            minPrice: priceMin ? parseFloat(priceMin) : null,
            maxPrice: priceMax ? parseFloat(priceMax) : null,
            minRS: minRS ? parseFloat(minRS) : null,
            minTotalScore: minScore ? parseFloat(minScore) : null,
            searchQuery: search ? search.trim() : ''
        };
    }

    // Render metadata (pagination info)
    function renderMeta() {
        const total = state.total;
        const limit = state.limit;
        const totalPages = Math.max(1, Math.ceil(total / limit));
        const page = Math.min(state.page, totalPages);

        const start = total === 0 ? 0 : (page - 1) * limit + 1;
        const end = Math.min(page * limit, total);

        const totalEl = $("resultTotal");
        const rangeEl = $("resultRange");
        const pageEl = $("pageIndicator");

        if (totalEl) totalEl.textContent = String(total);
        if (rangeEl) rangeEl.textContent = `${start}-${end}`;
        if (pageEl) pageEl.textContent = `${page} / ${totalPages}`;

        const prev = $("btnPrev");
        const next = $("btnNext");
        if (prev) prev.disabled = page <= 1;
        if (next) next.disabled = page >= totalPages;
    }

    // Get score badge class
    function getScoreBadgeClass(score) {
        const num = safeNumber(score, 0);
        if (num >= 90) return 'excellent';
        if (num >= 80) return 'good';
        if (num >= 60) return 'average';
        return 'poor';
    }

    // Get score color class
    function getScoreColorClass(score) {
        const num = safeNumber(score, 0);
        if (num >= 80) return 'text-success';
        if (num >= 60) return 'text-warning';
        return 'text-muted';
    }

    // Render results table with animations
    function renderTable() {
        const tbody = document.querySelector("#resultsTable tbody");
        if (!tbody) return;

        tbody.innerHTML = "";

        // Empty state
        if (!state.items.length) {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td colspan="9" style="padding: 40px; text-align: center;">
                    <div style="font-size: 3rem; opacity: 0.3; margin-bottom: 12px;">📊</div>
                    <div style="color: var(--text-muted); font-size: 0.95rem;">
                        該当データなし（条件を緩めて再検索してください）
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
            return;
        }

        const frag = document.createDocumentFragment();

        state.items.forEach((row, idx) => {
            const tr = document.createElement("tr");

            // Add staggered animation delay
            tr.style.opacity = '0';
            tr.style.animation = `fadeInRow 0.4s ease-in forwards`;
            tr.style.animationDelay = `${idx * 0.05}s`;

            // Extract data with fallbacks
            const symbol = row.symbol_key ?? row.symbol ?? "-";
            const name = row.name ?? "-";
            const rs = safeNumber(row.rs_rating ?? row.rs_score, 0);
            const total = safeNumber(row.total_score ?? row.total, 0);
            const price = safeNumber(row.price ?? row.current_price, 0);
            const changePct = safeNumber(row.change_pct, 0);
            const distTo52wHigh = safeNumber(row.dist_to_52w_high_pct, 0);

            // Format values using UI utilities
            const priceStr = price > 0 ?
                (window.UI ? window.UI.formatNumber(price, 2) : price.toFixed(2)) :
                "-";

            const chgStr = window.UI ?
                window.UI.formatPct(changePct) :
                `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%`;

            const distStr = `${distTo52wHigh.toFixed(2)}%`;

            // Score classes
            const totalScoreClass = getScoreColorClass(total);
            const rsScoreClass = getScoreColorClass(rs);

            // Rank badge
            const rank = (idx + 1) + (state.page - 1) * state.limit;
            const rankBadgeClass = rank <= 10 ? 'rank-top' : 'rank-normal';

            // Signals
            const signalsList = row.signals || [];
            const signalsHtml = signalsList.length > 0
                ? signalsList.map(s => `<span class="badge badge-accent">${s}</span>`).join(' ')
                : '<span class="text-muted">-</span>';

            tr.innerHTML = `
                <td>
                    <span class="rank-badge ${rankBadgeClass}">${rank}</span>
                </td>
                <td>
                    <a href="stock_detail.html?symbol=${symbol}" 
                       class="symbol-link" 
                       style="text-decoration: none; font-weight: 700; font-family: var(--font-mono); color: var(--primary);">
                        ${symbol}
                    </a>
                </td>
                <td class="text-muted" style="font-size: 0.85rem;">${name}</td>
                <td class="text-right ${rsScoreClass}" style="font-weight: 700; font-family: var(--font-mono);">
                    ${rs}
                </td>
                <td class="text-right ${totalScoreClass}" style="font-weight: 700; font-family: var(--font-mono);">
                    ${total}
                </td>
                <td class="text-right" style="font-family: var(--font-mono); font-weight: 600;">
                    $${priceStr}
                </td>
                <td class="text-right">${chgStr}</td>
                <td class="text-right" style="font-family: var(--font-mono);">${distStr}</td>
                <td>${signalsHtml}</td>
            `;

            frag.appendChild(tr);
        });

        tbody.appendChild(frag);

        // Add row animation CSS if not exists
        if (!document.getElementById('screener-animations')) {
            const style = document.createElement('style');
            style.id = 'screener-animations';
            style.textContent = `
                @keyframes fadeInRow {
                    from {
                        opacity: 0;
                        transform: translateY(10px);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0);
                    }
                }
                
                .rank-badge {
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    width: 32px;
                    height: 32px;
                    border-radius: 6px;
                    font-size: 0.8rem;
                    font-weight: 700;
                    font-family: var(--font-mono);
                }
                
                .rank-top {
                    background: linear-gradient(135deg, rgba(251, 191, 36, 0.2), rgba(251, 191, 36, 0.05));
                    color: #fbbf24;
                    border: 1px solid rgba(251, 191, 36, 0.3);
                    box-shadow: 0 0 12px rgba(251, 191, 36, 0.2);
                }
                
                .rank-normal {
                    background: rgba(99, 102, 241, 0.1);
                    color: var(--primary);
                    border: 1px solid rgba(99, 102, 241, 0.2);
                }
                
                .symbol-link {
                    transition: all 0.2s ease;
                    display: inline-block;
                }
                
                .symbol-link:hover {
                    color: var(--primary) !important;
                    transform: translateX(4px);
                    text-shadow: 0 0 12px rgba(99, 102, 241, 0.5);
                }
                
                #resultsTable tbody tr {
                    transition: all 0.2s ease;
                }
                
                #resultsTable tbody tr:hover {
                    background: linear-gradient(90deg, 
                        rgba(99, 102, 241, 0.05) 0%, 
                        rgba(99, 102, 241, 0.02) 100%);
                    transform: translateX(4px);
                    box-shadow: -4px 0 0 var(--primary);
                }
            `;
            document.head.appendChild(style);
        }
    }

    // Fetch screener data from API
    async function fetchScreener() {
        if (state.loading) return;

        state.loading = true;
        updateFilters();

        // Show loading using UI utility
        if (window.UI) {
            window.UI.showLoading();
        } else {
            const loadingEl = $("loading");
            if (loadingEl) loadingEl.style.display = 'flex';
        }

        try {
            const url = new URL(`${API_BASE}/screener/scan`);
            url.searchParams.set("page", String(state.page));
            url.searchParams.set("limit", String(state.limit));

            // Add filter parameters
            if (state.filters.minPrice !== null) {
                url.searchParams.set("min_price", String(state.filters.minPrice));
            }
            if (state.filters.maxPrice !== null) {
                url.searchParams.set("max_price", String(state.filters.maxPrice));
            }
            if (state.filters.minRS !== null) {
                url.searchParams.set("min_rs", String(state.filters.minRS));
            }
            if (state.filters.minTotalScore !== null) {
                url.searchParams.set("min_total_score", String(state.filters.minTotalScore));
            }

            const volumeMin = $("volumeMin")?.value;
            if (volumeMin) {
                url.searchParams.set("volume_min", volumeMin);
            }

            const rsiFilter = $("rsiFilter")?.value;
            if (rsiFilter && rsiFilter !== "all") {
                url.searchParams.set("rsi_filter", rsiFilter);
            }

            if (state.filters.searchQuery) {
                url.searchParams.set("symbol", state.filters.searchQuery);
            }

            // Fetch with timeout
            const ctrl = new AbortController();
            const timeout = setTimeout(() => ctrl.abort(), 20000);

            const res = await fetch(url.toString(), {
                signal: ctrl.signal,
                headers: {
                    'Accept': 'application/json'
                }
            });

            clearTimeout(timeout);

            if (!res.ok) {
                throw new Error(`API returned ${res.status}: ${res.statusText}`);
            }

            const data = await res.json();
            console.log("[Screener] API Response:", data);

            const { items, total } = normalizeScreenerResponse(data);
            state.items = items;
            state.total = total;

            renderMeta();
            renderTable();

            // Show results card
            const resultsCard = $("resultsCard");
            if (resultsCard) {
                resultsCard.style.display = 'block';
                if (window.UI) {
                    window.UI.fadeIn(resultsCard, 300);
                }
            }

            // Success toast
            if (window.UI) {
                window.UI.showToast(`${total}件の銘柄が見つかりました`, 'success', 2000);
            }

        } catch (e) {
            console.error('[Screener] Error:', e);

            state.items = [];
            state.total = 0;
            renderMeta();
            renderTable();

            // Error toast
            if (window.UI) {
                window.UI.showToast(`エラー: ${e.message}`, 'error');
            } else {
                alert(`Error: ${e.message}`);
            }
        } finally {
            state.loading = false;

            if (window.UI) {
                window.UI.hideLoading();
            } else {
                const loadingEl = $("loading");
                if (loadingEl) loadingEl.style.display = 'none';
            }
        }
    }

    // Reset filters
    function resetFilters() {
        const inputs = ['priceMin', 'priceMax', 'minRS', 'minTotalScore', 'searchInput', 'volumeMin', 'rsiFilter'];
        inputs.forEach(id => {
            const el = $(id);
            if (el) {
                if (el.id === 'rsiFilter') el.value = 'all';
                else if (el.id === 'minRS') el.value = '85';
                else if (el.id === 'minTotalScore') el.value = '70';
                else el.value = '';
            }
        });

        updateFilters(); // Synchronize state
        state.page = 1;
        fetchScreener();

        if (window.UI) {
            window.UI.showToast('検索条件をリセットしました', 'info');
        }
    }

    // Export CSV (Requirement 8: BOM + Timestamp)
    function exportCSV() {
        if (!state.items.length) {
            if (window.UI) window.UI.showToast('エクスポートするデータがありません', 'warning');
            return;
        }

        // Columns per requirement: Rank, Symbol, Name, RS, Total, Price, Change, Dist52W, Signals
        const headers = ['Rank', 'Symbol', 'Name', 'RS Score', 'Total Score', 'Price', 'Change %', 'Dist 52W High', 'Signals'];
        const rows = state.items.map((row, idx) => {
            const rank = (idx + 1) + (state.page - 1) * state.limit;
            return [
                rank,
                row.symbol ?? '',
                row.name ?? '',
                row.rs_score ?? '',
                row.total_score ?? '',
                row.price ?? '',
                row.change_pct ? row.change_pct.toFixed(2) : '0.00',
                row.dist_52w_high_pct ? row.dist_52w_high_pct.toFixed(2) : '0.00',
                (row.signals || []).join('; ')
            ];
        });

        // Add UTF-8 BOM for Excel
        const csvContent = "\uFEFF" + [headers, ...rows]
            .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
            .join('\n');

        const now = new Date();
        const timestamp = now.getFullYear() +
            String(now.getMonth() + 1).padStart(2, '0') +
            String(now.getDate()).padStart(2, '0') + '_' +
            String(now.getHours()).padStart(2, '0') +
            String(now.getMinutes()).padStart(2, '0');

        const filename = `screener_${timestamp}.csv`;
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.setAttribute('download', filename);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        if (window.UI) window.UI.showToast(`CSVを保存しました: ${filename}`, 'success');
    }

    // Diagnostics (Requirement 9, 10, 11)
    async function showDiagnostics() {
        const modal = $("diagModal");
        const content = $("diagContent");
        if (!modal || !content) return;

        modal.style.display = "flex";
        content.innerHTML = '<div style="padding: 24px; text-align: center;">🏥 診断実行中...</div>';

        try {
            const resp = await fetch(`${API_BASE}/system/diagnostics/screener`);
            const data = await resp.json();

            let html = `<div style="padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); margin-bottom: 20px;">
                Overall Status: <span class="${data.status === 'ok' ? 'text-success' : (data.status === 'error' ? 'text-danger' : 'text-warning')}" style="font-weight:700;">${data.status.toUpperCase()}</span>
            </div>`;

            // Contract Check (Requirement 10)
            const cc = data.api_contract_check;
            html += `<div style="margin-bottom: 16px; border-left: 3px solid ${cc.status === 'ok' ? 'var(--success)' : 'var(--warning)'}; padding-left: 12px; background: rgba(255,255,255,0.02); padding-top: 8px; padding-bottom: 8px;">
                <div style="font-weight:700;">📋 API Contract: ${cc.status.toUpperCase()}</div>
                ${cc.missing_query_params?.length > 0
                    ? `<div class="text-warning">Missing Params: ${cc.missing_query_params.join(', ')}</div>`
                    : '<div class="text-success">✓ Query parameters verified</div>'}
                ${cc.missing_response_fields.length > 0
                    ? `<div class="text-warning">Missing Keys: ${cc.missing_response_fields.join(', ')}</div>`
                    : '<div class="text-success">✓ Response fields verified</div>'}
            </div>`;

            // Query Logic Check (New)
            const ql = data.query_logic_check;
            if (ql) {
                html += `<div style="margin-bottom: 16px; border-left: 3px solid ${ql.price_filter_applied_to_latest_close ? 'var(--success)' : 'var(--danger)'}; padding-left: 12px; background: rgba(255,255,255,0.02); padding-top: 8px; padding-bottom: 8px;">
                    <div style="font-weight:700;">🔍 Query Logic: ${ql.price_filter_applied_to_latest_close ? 'OK' : 'ERROR'}</div>
                    <div>Latest Price Date Used: <span class="text-primary">${ql.latest_price_date_used || 'N/A'}</span></div>
                    ${ql.sample_item_debug ? `
                        <div style="font-size: 0.8rem; margin-top: 8px; padding: 8px; background: rgba(0,0,0,0.2); border-radius: 4px;">
                            <strong>Sample Debug:</strong> ${ql.sample_item_debug.symbol}<br>
                            Price: $${ql.sample_item_debug.price} (Raw: ${ql.sample_item_debug.raw_close})<br>
                            Date: ${ql.sample_item_debug.price_date}<br>
                            Filters: min=${ql.sample_item_debug.min_price || 'none'}, max=${ql.sample_item_debug.max_price || 'none'}
                        </div>
                    ` : ''}
                </div>`;
            }

            // DB Integrity (Requirement 9)
            const dbi = data.db_integrity_check;
            html += `<div style="margin-bottom: 16px; border-left: 3px solid ${dbi.status === 'error' ? 'var(--danger)' : 'var(--primary)'}; padding-left: 12px; background: rgba(255,255,255,0.02); padding-top: 8px; padding-bottom: 8px;">
                <div style="font-weight:700;">🗄️ Database Integrity: ${dbi.status.toUpperCase()}</div>
                ${dbi.missing_columns?.length > 0
                    ? `<div class="text-danger" style="margin: 4px 0;">Missing Columns: ${dbi.missing_columns.join(', ')}</div>`
                    : '<div class="text-success">✓ Columns verified via information_schema</div>'}
                <div style="font-size: 0.8rem; margin-top: 8px; color: var(--text-muted);">
                    ${Object.entries(dbi.tables || {}).map(([t, v]) => `${t}: ${v.rows.toLocaleString()} rows`).join(' | ')}
                </div>
            </div>`;

            // Freshness
            const df = data.data_freshness_check;
            html += `<div style="border-left: 3px solid var(--primary); padding-left: 12px; background: rgba(255,255,255,0.02); padding-top: 8px; padding-bottom: 8px;">
                <div style="font-weight:700;">⏱️ Data Freshness</div>
                <div style="font-size: 0.8rem;">
                    ${Object.entries(df.latest_dates || {}).map(([t, d]) => `<div>${t}: ${d || 'N/A'} (<span class="${df.staleness_days[t] > 4 ? 'text-warning' : ''}">${df.staleness_days[t]}d ago</span>)</div>`).join('')}
                </div>
            </div>`;

            content.innerHTML = html;
        } catch (err) {
            content.innerHTML = `<div class="text-danger" style="padding: 20px;">診断に失敗しました: ${err.message}</div>`;
        }
    }

    // Initialize event listeners
    function initEventListeners() {
        // Search button
        const btnSearch = $("btnSearch");
        if (btnSearch) {
            btnSearch.addEventListener("click", () => {
                state.page = 1;
                fetchScreener();
            });
        }

        // Reset button
        const btnReset = $("btnReset");
        if (btnReset) {
            btnReset.addEventListener("click", resetFilters);
        }

        // Export button
        const btnExport = $("btnExport");
        if (btnExport) {
            btnExport.addEventListener("click", exportCSV);
        }

        // Diagnostics button
        const btnDiag = $("btnDiag");
        if (btnDiag) {
            btnDiag.addEventListener("click", showDiagnostics);
        }

        // Pagination
        const btnPrev = $("btnPrev");
        if (btnPrev) {
            btnPrev.addEventListener("click", () => {
                if (state.page > 1) {
                    state.page--;
                    fetchScreener();
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        }

        const btnNext = $("btnNext");
        if (btnNext) {
            btnNext.addEventListener("click", () => {
                const totalPages = Math.max(1, Math.ceil(state.total / state.limit));
                if (state.page < totalPages) {
                    state.page++;
                    fetchScreener();
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        }

        // Enter key on inputs
        const inputs = document.querySelectorAll("#priceMin, #priceMax, #minRS, #minTotalScore, #searchInput");
        inputs.forEach(input => {
            input.addEventListener("keyup", (e) => {
                if (e.key === "Enter") {
                    state.page = 1;
                    fetchScreener();
                }
            });
        });

        // Debounced search input
        const searchInput = $("searchInput");
        if (searchInput && window.UI && window.UI.debounce) {
            searchInput.addEventListener("input", window.UI.debounce(() => {
                state.page = 1;
                fetchScreener();
            }, 500));
        }
    }

    // Initialize on DOM ready
    function init() {
        console.log('[Screener] Initializing...');
        initEventListeners();
        fetchScreener(); // Initial load
    }

    // Wait for DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Global glue functions (Required by HTML onclick)
    window.searchSymbol = function () {
        const input = $("searchInput");
        if (input) state.filters.searchQuery = input.value.trim();
        state.page = 1;
        fetchScreener();
    };

    window.clearSearch = function () {
        const input = $("searchInput");
        if (input) {
            input.value = '';
            state.filters.searchQuery = '';
        }
        state.page = 1;
        fetchScreener(); // Requirement 7: Keep other filters
    };

    // Expose for debugging
    window.ScreenerState = state;
})();