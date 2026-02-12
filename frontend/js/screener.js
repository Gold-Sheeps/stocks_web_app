(function () {
    const API_BASE = (window.CONFIG && window.CONFIG.API_BASE) ? window.CONFIG.API_BASE : "http://localhost:8000/api/v1";

    const state = {
        page: 1,
        limit: 50,
        total: 0,
        items: [],
        loading: false,
    };

    function $(id) { return document.getElementById(id); }

    function safeNumber(x, fallback = 0) {
        const n = Number(x);
        return Number.isFinite(n) ? n : fallback;
    }

    function normalizeScreenerResponse(data) {
        // いろんな形に対応（total_count / total / count / total_stocks, items / results / data）
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

        return { items: Array.isArray(items) ? items : [], total: safeNumber(total, 0) };
    }

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

    function renderTable() {
        const tbody = document.querySelector("#resultsTable tbody");
        if (!tbody) return;

        tbody.innerHTML = "";

        if (!state.items.length) {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td colspan="9" style="padding:12px;color:var(--text-muted);text-align:center;">該当データなし（条件を緩めて再検索してください）</td>`;
            tbody.appendChild(tr);
            return;
        }

        const frag = document.createDocumentFragment();
        state.items.forEach((row, idx) => {
            const tr = document.createElement("tr");

            const symbol = row.symbol_key ?? row.symbol ?? "-";
            const name = row.name ?? "-";
            const rs = row.rs_rating ?? row.rs_score ?? "-";
            const total = row.total_score ?? row.total ?? "-";
            const price = row.price ?? row.current_price ?? null;

            const priceStr = (price && price > 0) ? (typeof UI !== 'undefined' ? UI.formatNumber(price) : price.toFixed(2)) : "-";
            const chgStr = (typeof UI !== 'undefined') ? UI.formatPct(row.change_pct) : (row.change_pct + '%');
            const distStr = (typeof UI !== 'undefined') ? UI.formatNumber(row.dist_to_52w_high_pct, 2) + '%' : (row.dist_to_52w_high_pct + '%');

            const totalScoreClass = Number(total) >= 80 ? 'text-success' : (Number(total) >= 60 ? 'text-warning' : 'text-muted');
            const rsScoreClass = Number(rs) >= 80 ? 'text-success' : 'text-muted';

            const signalsList = row.signals || [];
            const signalsHtml = signalsList.length > 0
                ? signalsList.map(s => `<span class="badge badge-accent">${s}</span>`).join(' ')
                : '-';

            tr.innerHTML = `
        <td>${(idx + 1) + (state.page - 1) * state.limit}</td>
        <td><a href="stock_detail.html?symbol=${symbol}" class="text-main font-bold" style="text-decoration:none;">${symbol}</a></td>
        <td class="text-muted text-sm">${name}</td>
        <td class="text-right ${rsScoreClass} font-bold">${rs}</td>
        <td class="text-right ${totalScoreClass} font-bold">${total}</td>
        <td class="text-right num">${priceStr}</td>
        <td class="text-right">${chgStr}</td>
        <td class="text-right num">${distStr}</td>
        <td>${signalsHtml}</td>
      `;
            frag.appendChild(tr);
        });
        tbody.appendChild(frag);
    }

    async function fetchScreener() {
        if (state.loading) return;
        state.loading = true;

        // Show spinner if exists
        const loadingEl = $("loading");
        if (loadingEl) loadingEl.style.display = 'block';

        try {
            const url = new URL(`${API_BASE}/screener/scan`);
            url.searchParams.set("page", String(state.page));
            url.searchParams.set("limit", String(state.limit));

            // Add filter params if they exist
            const priceMin = $("priceMin")?.value;
            const priceMax = $("priceMax")?.value;
            if (priceMin) url.searchParams.set("min_price", priceMin);
            if (priceMax) url.searchParams.set("max_price", priceMax);

            const ctrl = new AbortController();
            const t = setTimeout(() => ctrl.abort(), 15000);

            const res = await fetch(url.toString(), { signal: ctrl.signal });
            clearTimeout(t);

            if (!res.ok) throw new Error(`Screener API failed: ${res.status}`);

            const data = await res.json();
            console.log("[Screener raw response]", data);

            const { items, total } = normalizeScreenerResponse(data);
            state.items = items;
            state.total = total;

            renderMeta();
            renderTable();

            const resultsCard = $("resultsCard");
            if (resultsCard) resultsCard.style.display = 'block';

        } catch (e) {
            console.error(e);
            state.items = [];
            state.total = 0;
            renderMeta();
            renderTable();

            if (typeof UI !== 'undefined') UI.showToast(`Error: ${String(e)}`, 'error');
        } finally {
            state.loading = false;
            if (loadingEl) loadingEl.style.display = 'none';
        }
    }

    // Init
    window.addEventListener("DOMContentLoaded", () => {
        const btn = $("btnSearch");
        if (btn) btn.addEventListener("click", () => { state.page = 1; fetchScreener(); });

        const prev = $("btnPrev");
        if (prev) prev.addEventListener("click", () => {
            if (state.page > 1) {
                state.page--;
                fetchScreener();
            }
        });

        const next = $("btnNext");
        if (next) next.addEventListener("click", () => {
            const totalPages = Math.max(1, Math.ceil(state.total / state.limit));
            if (state.page < totalPages) {
                state.page++;
                fetchScreener();
            }
        });

        // Enter key on inputs
        const inputs = document.querySelectorAll("#priceMin, #priceMax, #searchInput");
        inputs.forEach(input => {
            input.addEventListener("keyup", (e) => {
                if (e.key === "Enter") {
                    state.page = 1;
                    fetchScreener();
                }
            });
        });

        fetchScreener();
    });
})();
