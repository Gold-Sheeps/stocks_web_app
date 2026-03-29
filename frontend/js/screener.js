/* screener.js - Enhanced Edition */

(function () {
    // Use CONFIG from window if available
    const API_BASE = window.CONFIG?.API_BASE || "http://localhost:8010/api/v1";

    // Application State
    const state = {
        page: 1,
        limit: 50,
        total: 0,
        items: [],
        loading: false,
        usdJpyRate: null,
        sortKey: 'final_rank_score',  // デフォルト: 投資判断に最適な総合スコア順
        sortDir: 'desc',
        filters: {
            minPrice: null,
            maxPrice: null,
            minRS: null,
            minTotalScore: 70,
            searchQuery: '',
            aiMode: 'off',
            market: 'US',
            currencyMode: 'local'
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
            total: safeNumber(total, 0),
            freshness: data?.freshness ?? null,
            usdJpyRate: safeNumber(data?.usd_jpy_rate, 0) || null
        };
    }

    function applyUrlPresets() {
        const params = new URLSearchParams(window.location.search || "");
        const market = (params.get("market") || "US").trim().toUpperCase();
        const currency = (params.get("currency") || "").trim().toLowerCase();
        const aiMode = (params.get("ai_mode") || "").trim().toLowerCase();
        const symbol = (params.get("symbol") || "").trim();

        // Set active market via toggle button
        const mkt = market === "ALL" ? "all" : market;
        setActiveMarket(mkt);

        const currencyEl = $("currencyMode");
        if (currencyEl && ["local", "usd", "jpy"].includes(currency)) {
            currencyEl.value = currency;
        }

        const aiModeEl = $("aiMode");
        if (aiModeEl && ["off", "balanced", "high_precision", "strict"].includes(aiMode)) {
            aiModeEl.value = aiMode;
        }

        const searchEl = $("searchInput");
        if (searchEl && symbol) {
            searchEl.value = symbol;
        }
    }

    function renderFreshness(freshness) {
        const el = $("screenerFreshness");
        if (!el) return;
        if (!freshness) {
            el.textContent = "";
            return;
        }
        const status = freshness.is_fresh ? "Fresh" : "Stale";
        const cls = freshness.is_fresh ? "text-success" : "text-warning";
        const tzLabel = freshness.market_timezone
            ? ` [${freshness.market_timezone}]`
            : "";
        el.innerHTML = `
            <span style="font-weight:700;">Data Freshness:</span>
            <span class="${cls}" style="font-weight:700;">${status}</span>
            <span> latest=${freshness.latest_date_max || 'N/A'} / expected=${freshness.expected_latest_date || 'N/A'} / stale=${freshness.symbols_stale || 0}${tzLabel}</span>
        `;
    }

    // Set active market button and update state
    function setActiveMarket(market) {
        const mkt = String(market || 'US').trim().toUpperCase() === 'ALL'
            ? 'all'
            : String(market || 'US').trim().toUpperCase();
        state.filters.market = mkt;

        // Update button styles
        document.querySelectorAll('.market-btn').forEach(btn => {
            const bm = btn.dataset.market;
            btn.classList.toggle('active', bm === mkt || (bm === 'all' && mkt === 'all'));
        });

        // Update page title
        const labelEl = $("pageMarketLabel");
        if (mkt === 'JP') {
            if (labelEl) labelEl.textContent = "JP Stock Screener";
            document.title = "JP Screener - Trading System";
        } else if (mkt === 'all') {
            if (labelEl) labelEl.textContent = "Global Stock Screener";
            document.title = "Global Screener - Trading System";
        } else {
            if (labelEl) labelEl.textContent = "Stock Screener";
            document.title = "Screener - Trading System";
        }
    }

    // Update filters from form
    function updateFilters() {
        const priceMin = $("priceMin")?.value;
        const priceMax = $("priceMax")?.value;
        const minRS = $("minRS")?.value;
        const minScore = $("minTotalScore")?.value;
        const search = $("searchInput")?.value;
        const aiMode = $("aiMode")?.value;
        const currencyMode = $("currencyMode")?.value;

        state.filters = {
            minPrice: priceMin ? parseFloat(priceMin) : null,
            maxPrice: priceMax ? parseFloat(priceMax) : null,
            minRS: minRS ? parseFloat(minRS) : null,
            minTotalScore: minScore ? parseFloat(minScore) : 70,
            searchQuery: search ? search.trim() : '',
            aiMode: aiMode || 'off',
            market: state.filters.market || 'US',  // preserve market from toggle buttons
            currencyMode: currencyMode || 'local'
        };
    }

    function marketFromSymbol(symbol) {
        const s = String(symbol || '').toUpperCase();
        if (s.startsWith('JP:')) return 'JP';
        if (s.startsWith('US:')) return 'US';
        return 'US';
    }

    function formatDisplayPrice(value, market, currencyMode) {
        const n = Number(value);
        if (!Number.isFinite(n) || n <= 0) return "-";

        const mode = String(currencyMode || 'local').toLowerCase();
        const mkt = String(market || '').toUpperCase();
        const fx = Number(state.usdJpyRate || 0);

        let out = n;
        let prefix = '';

        if (mode === 'usd') {
            if (mkt === 'JP' && fx > 0) out = n / fx;
            prefix = '$';
        } else if (mode === 'jpy') {
            if (mkt === 'US' && fx > 0) out = n * fx;
            prefix = '¥';
        } else {
            prefix = mkt === 'JP' ? '¥' : '$';
        }

        const formatted = window.UI ? window.UI.formatNumber(out, 2) : out.toFixed(2);
        return `${prefix}${formatted}`;
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

    function badgeHtml(text, tone = 'neutral') {
        const map = {
            good: 'rgba(16,185,129,0.15);color:#34d399;border:1px solid rgba(16,185,129,0.35);',
            warn: 'rgba(245,158,11,0.15);color:#fbbf24;border:1px solid rgba(245,158,11,0.35);',
            bad: 'rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.35);',
            neutral: 'rgba(148,163,184,0.12);color:#cbd5e1;border:1px solid rgba(148,163,184,0.25);'
        };
        return `<span style="display:inline-flex;align-items:center;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:700;${map[tone] || map.neutral}">${text || '-'}</span>`;
    }

    function renderFinalRankScore(score, actionLabel) {
        if (score === null || score === undefined) {
            return '<span style="color:var(--text-muted);">-</span>';
        }
        const v = Number(score);
        if (actionLabel === 'BLOCKED') {
            return '<span style="color:#ef4444;font-size:0.8rem;">🚫 0</span>';
        }
        // 色: 70+ 緑, 50-70 黄, 未満 グレー
        const color = v >= 70 ? '#4ade80' : v >= 50 ? '#fbbf24' : '#94a3b8';
        return `<span style="color:${color};">${v.toFixed(1)}</span>`;
    }

    function renderInstitutionalScore(score, source, flags) {
        if (score === null || score === undefined) {
            const hasMissingData = Array.isArray(flags) && flags.includes('insufficient_data');
            return `<span style="color:var(--text-muted);font-size:0.78rem;" title="${hasMissingData ? 'データ不足' : 'N/A'}">-</span>`;
        }
        const v = Number(score);
        const color = v >= 70 ? '#4ade80' : v >= 50 ? '#fbbf24' : '#94a3b8';
        const srcLabel = source === 'evidence' ? ' [E]' : source === 'footprint' ? ' [F]' : '';
        const flagsText = Array.isArray(flags) && flags.length > 0
            ? '\n' + flags.join('\n')
            : '';
        const tip = `I Score: ${v.toFixed(1)} (${source || 'none'})${flagsText}`;
        return `<span title="${tip}" style="color:${color};font-family:var(--font-mono);font-size:0.85rem;cursor:default;">${v.toFixed(1)}${srcLabel}</span>`;
    }

    function renderClass15Direction(c15) {
        if (!c15 || !c15.direction) {
            return '<span style="color:var(--text-muted);font-size:0.78rem;">-</span>';
        }
        const dirStyles = {
            Up:   { text: '↑ Up',   bg: 'rgba(34,197,94,0.15)',  color: '#4ade80', border: 'rgba(34,197,94,0.35)' },
            Flat: { text: '→ Flat', bg: 'rgba(148,163,184,0.1)', color: '#94a3b8', border: 'rgba(148,163,184,0.25)' },
            Down: { text: '↓ Down', bg: 'rgba(239,68,68,0.15)',  color: '#f87171', border: 'rgba(239,68,68,0.35)' },
        };
        const s = dirStyles[c15.direction] || dirStyles.Flat;
        const u = c15.prob_up  != null ? (c15.prob_up  * 100).toFixed(1) : '--';
        const f = c15.prob_flat != null ? (c15.prob_flat * 100).toFixed(1) : '--';
        const d = c15.prob_down != null ? (c15.prob_down * 100).toFixed(1) : '--';
        const tip = `15日: Up ${u}% / Flat ${f}% / Down ${d}%`;
        return `<span title="${tip}" style="display:inline-flex;align-items:center;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:600;background:${s.bg};color:${s.color};border:1px solid ${s.border};cursor:default;">${s.text}</span>`;
    }

    function renderActionLabel(label, gateReasons, predictedClass) {
        // AI未予測: gate非発動 かつ predictedClass が null → "AI-" で明示
        const noGate = !Array.isArray(gateReasons) || gateReasons.length === 0;
        const noPrediction = predictedClass === null || predictedClass === undefined;
        if (noGate && noPrediction && (!label || label === 'WATCH' || label === 'HOLD')) {
            return '<span title="AI予測未実行" style="display:inline-flex;align-items:center;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:600;background:rgba(71,85,105,0.2);color:#64748b;border:1px dashed rgba(100,116,139,0.4);">AI-</span>';
        }
        if (!label) return '<span style="color:var(--text-muted);font-size:0.8rem;">-</span>';
        const styles = {
            BUY:     'rgba(34,197,94,0.15);color:#4ade80;border:1px solid rgba(34,197,94,0.35);',
            HOLD:    'rgba(148,163,184,0.1);color:#94a3b8;border:1px solid rgba(148,163,184,0.25);',
            SELL:    'rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.35);',
            WATCH:   'rgba(245,158,11,0.15);color:#fbbf24;border:1px solid rgba(245,158,11,0.35);',
            BLOCKED: 'rgba(239,68,68,0.15);color:#f87171;border:1px solid rgba(239,68,68,0.35);',
        };
        const s = styles[label] || styles.HOLD;
        // WATCH の内訳を gate_reasons から判別して表示テキスト・ツールチップに反映
        let displayText = label;
        let tooltipText = '';
        if (label === 'BLOCKED') {
            displayText = '🚫 BLOCKED';
            tooltipText = '市場停止中: 新規エントリー不推奨';
        } else if (label === 'WATCH') {
            const gr = Array.isArray(gateReasons) ? gateReasons : [];
            if (gr.includes('market_regime_blocked')) {
                displayText = '⛔ WATCH';
                tooltipText = '市場: 新規買い停止 (条件変更でBLOCKED相当)';
            } else if (gr.includes('market_regime_caution') && gr.includes('prob_below_caution_threshold')) {
                displayText = '⚠ WATCH';
                tooltipText = '市場: 注意モード + AI確率が基準未満 (p<0.6)';
            } else if (gr.includes('market_regime_caution')) {
                displayText = '⚠ WATCH';
                tooltipText = '市場: 注意モード';
            } else {
                displayText = 'WATCH';
                tooltipText = 'モデル判断: 買いシグナルなし (様子見)';
            }
        } else if (label === 'HOLD') {
            tooltipText = 'モデル判断: 積極買い推奨なし (NO_TRADE)';
        }
        const titleAttr = tooltipText ? ` title="${tooltipText}"` : '';
        return `<span${titleAttr} style="display:inline-flex;align-items:center;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700;${s}">${displayText}</span>`;
    }

    function mTone(val) {
        if (val === '買い可') return 'good';
        if (val === '注意') return 'warn';
        if (val === '新規買い停止') return 'bad';
        return 'neutral';
    }

    // ---- Sort helpers ----
    function getSortValue(row, key) {
        switch (key) {
            case 'symbol': return (row.symbol_key ?? row.symbol ?? '').toLowerCase();
            case 'name':   return (row.name ?? '').toLowerCase();
            case 'rs_rating':        return row.rs_rating ?? -1;
            case 'total_score':      return row.total_score ?? row.total ?? -1;
            case 'price':            return row.price ?? row.current_price ?? -1;
            case 'pivot':            return Number(row.pivot) || -1;
            case 'change_pct':       return row.change_pct ?? -9999;
            case 'dist_52w_high_pct': return row.dist_52w_high_pct ?? -9999;
            case 'canslim_pass_count':  return row.canslim_pass_count ?? -1;
            case 'institutional_score': return row.institutional_score ?? -1;
            case 'ai_p_up5_2w':        return row.ai_p_up5_2w ?? -1;
            case 'final_rank_score':    return row.final_rank_score ?? -1;
            default: return -1;
        }
    }

    function getSortedItems() {
        if (!state.sortKey) return state.items;
        const key = state.sortKey;
        const dir = state.sortDir === 'asc' ? 1 : -1;
        return [...state.items].sort((a, b) => {
            const av = getSortValue(a, key);
            const bv = getSortValue(b, key);
            if (typeof av === 'string' && typeof bv === 'string') {
                return dir * av.localeCompare(bv);
            }
            return dir * (Number(av) - Number(bv));
        });
    }

    function updateSortHeaders() {
        document.querySelectorAll('#resultsTable thead th[data-sort]').forEach(th => {
            const key = th.dataset.sort;
            let icon = th.querySelector('.sort-icon');
            if (!icon) {
                icon = document.createElement('span');
                icon.className = 'sort-icon';
                th.appendChild(icon);
            }
            if (key === state.sortKey) {
                icon.textContent = state.sortDir === 'asc' ? '\u25b2' : '\u25bc';
                icon.classList.add('active');
            } else {
                icon.textContent = '\u21c5';
                icon.classList.remove('active');
            }
        });
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
                    <td colspan="20" style="padding: 40px; text-align: center;">
                    <div style="font-size: 3rem; opacity: 0.3; margin-bottom: 12px;">-</div>
                    <div style="color: var(--text-muted); font-size: 0.95rem;">
                        No data found. Change filters and search again.
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
            return;
        }

        const frag = document.createDocumentFragment();
        updateSortHeaders();

        getSortedItems().forEach((row, idx) => {
            const tr = document.createElement("tr");

            // Add staggered animation delay
            tr.style.opacity = '0';
            tr.style.animation = `fadeInRow 0.4s ease-in forwards`;
            tr.style.animationDelay = `${idx * 0.05}s`;

            // Extract data with fallbacks
            const symbol = row.symbol_key ?? row.symbol ?? "-";
            const market = marketFromSymbol(symbol);
            const name = row.name ?? "-";
            const rs = safeNumber(row.rs_rating, 0);
            const total = safeNumber(row.total_score ?? row.total, 0);
            const price = safeNumber(row.price ?? row.current_price, 0);
            const pivot = Number(row.pivot);
            const changePct = safeNumber(row.change_pct, 0);
            const distTo52wHigh = safeNumber(row.dist_52w_high_pct, 0);

            // Format values using UI utilities
            const priceStr = formatDisplayPrice(price, market, state.filters.currencyMode);

            const chgStr = window.UI ?
                window.UI.formatPct(changePct) :
                `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%`;

            const pivotStr = Number.isFinite(pivot) && pivot > 0
                ? formatDisplayPrice(pivot, market, state.filters.currencyMode)
                : "-";

            const distStr = `${distTo52wHigh.toFixed(2)}%`;
            const mJudgment = row.m_judgment ?? row.market_summary?.m_judgment ?? '-';
            const ddDisplay = row.dd_count_display ?? '-';
            const vixMode = row.vix_mode ?? row.market_summary?.vix_mode ?? '-';
            const breakout = row.breakout_effectiveness ?? '-';
            const canslim = row.canslim_pass_count_display ?? (row.canslim_pass_count != null ? `${row.canslim_pass_count}/7` : '-');
            const canslimCriteria = row.canslim_criteria ?? null;
            const canslimCriteriaHtml = (() => {
                const letters = ['C', 'A', 'N', 'S', 'L', 'I', 'M'];
                if (!canslimCriteria) return `<span style="font-family:var(--font-mono)">${canslim}</span>`;
                const badges = letters.map(l => {
                    const pass = canslimCriteria[l];
                    const cls = pass ? 'canslim-pass' : 'canslim-fail';
                    return `<span class="canslim-letter ${cls}" title="${l}: ${pass ? '通過' : '未通過'}">${l}</span>`;
                });
                return `<span class="canslim-letters">${badges.join('')}</span>`;
            })();
            const overall = row.overall_grade ?? '-';
            const aiP = (row.ai_p_up5_2w != null && Number.isFinite(Number(row.ai_p_up5_2w))) ? `${Number(row.ai_p_up5_2w).toFixed(1)}%` : '-';
            const ai3 = row.ai_3class_summary ?? '-';

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
                        ${market === 'JP' ? symbol.replace('JP:', '') : symbol}
                    </a>
                </td>
                <td style="font-size: 0.85rem;">
                    <span class="text-muted">${name}</span>
                    ${row.sector ? `<br><span style="font-size:0.7rem;color:var(--primary);opacity:0.7;">${row.sector}</span>` : ''}
                </td>
                <td class="text-right ${rsScoreClass}" style="font-weight: 700; font-family: var(--font-mono);">
                    ${rs}
                </td>
                <td class="text-right ${totalScoreClass}" style="font-weight: 700; font-family: var(--font-mono);">
                    ${total}
                </td>
                <td class="text-right" style="font-family: var(--font-mono); font-weight: 600;">
                    ${priceStr}
                </td>
                <td class="text-right" style="font-family: var(--font-mono); font-weight: 600;">
                    ${pivotStr}
                </td>
                <td class="text-right">${chgStr}</td>
                <td class="text-right" style="font-family: var(--font-mono);">${distStr}</td>
                <td>${badgeHtml(mJudgment, mTone(mJudgment))}</td>
                <td style="font-family: var(--font-mono);">${ddDisplay}</td>
                <td>${badgeHtml(vixMode, vixMode === 'RiskOff' ? 'bad' : (vixMode === 'Caution' ? 'warn' : 'good'))}</td>
                <td>${badgeHtml(breakout, breakout === '有効' ? 'good' : (breakout === 'だまし警戒' ? 'bad' : 'warn'))}</td>
                <td style="white-space:nowrap;">${canslimCriteriaHtml}</td>
                <td class="text-right">${renderInstitutionalScore(row.institutional_score, row.institutional_score_source, row.institutional_flags)}</td>
                <td>${badgeHtml(overall, overall === 'A' ? 'good' : (overall === '見送り' ? 'bad' : 'warn'))}</td>
                <td class="text-right" style="font-family:var(--font-mono);font-weight:700;">${renderFinalRankScore(row.final_rank_score, row.action_label)}</td>
                <td>${renderActionLabel(row.action_label, row.gate_reasons, row.predicted_class)}</td>
                <td class="text-right" style="font-family: var(--font-mono);">${aiP}</td>
                <td style="font-family: var(--font-mono); font-size: 0.8rem;">${ai3}</td>
                <td>${renderClass15Direction(row.class15)}</td>
                <td>${signalsHtml}</td>
                <td>
                  <button onclick="setAlertFromScreener('${symbol}', ${price})" title="Set Alert"
                    style="background:rgba(99,102,241,0.12);border:1px solid rgba(99,102,241,0.3);color:#818cf8;border-radius:6px;padding:3px 8px;cursor:pointer;font-size:0.75rem;font-weight:700;transition:all 0.2s;"
                    onmouseover="this.style.background='rgba(99,102,241,0.25)'" onmouseout="this.style.background='rgba(99,102,241,0.12)'">
                    + Alert
                  </button>
                </td>
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

                .canslim-letters {
                    display: inline-flex;
                    gap: 2px;
                    font-family: var(--font-mono);
                }

                .canslim-letter {
                    display: inline-block;
                    width: 18px;
                    height: 18px;
                    line-height: 18px;
                    text-align: center;
                    font-size: 0.7rem;
                    font-weight: 700;
                    border-radius: 3px;
                    cursor: default;
                }

                .canslim-pass {
                    background: rgba(16, 185, 129, 0.2);
                    color: #10b981;
                    border: 1px solid rgba(16, 185, 129, 0.4);
                }

                .canslim-fail {
                    background: rgba(107, 114, 128, 0.1);
                    color: rgba(107, 114, 128, 0.5);
                    border: 1px solid rgba(107, 114, 128, 0.2);
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
            url.searchParams.set("limit", String(state.limit));
            url.searchParams.set("offset", String((state.page - 1) * state.limit));

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
            const effectiveMinTotalScore = state.filters.searchQuery ? 0 : state.filters.minTotalScore;
            if (effectiveMinTotalScore !== null) {
                url.searchParams.set("min_total_score", String(effectiveMinTotalScore));
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
            if (state.filters.market && state.filters.market !== "all") {
                url.searchParams.set("market", state.filters.market);
            }
            if (state.filters.aiMode && state.filters.aiMode !== "off") {
                url.searchParams.set("ai_mode", state.filters.aiMode);
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

            const { items, total, freshness, usdJpyRate } = normalizeScreenerResponse(data);
            state.items = items;
            state.total = total;
            state.usdJpyRate = usdJpyRate;

            renderMeta();
            renderFreshness(freshness);
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
                window.UI.showToast(`${total} stocks found`, 'success', 2000);
            }

        } catch (e) {
            console.error('[Screener] Error:', e);

            state.items = [];
            state.total = 0;
            renderMeta();
            renderTable();
            renderFreshness(null);

            const resultsCard = $("resultsCard");
            if (resultsCard) {
                resultsCard.style.display = 'block';
            }
            const freshnessEl = $("screenerFreshness");
            if (freshnessEl) {
                freshnessEl.innerHTML = `<span class="text-danger">Failed to load screener data: ${String(e.message || e)}</span>`;
            }

            // Error toast
            if (window.UI) {
                window.UI.showToast(`Error: ${e.message}`, 'error');
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
        const inputs = ['priceMin', 'priceMax', 'minRS', 'minTotalScore', 'searchInput', 'volumeMin', 'rsiFilter', 'aiMode', 'currencyMode'];
        inputs.forEach(id => {
            const el = $(id);
            if (el) {
                if (el.id === 'rsiFilter') el.value = '';
                else if (el.id === 'aiMode') el.value = 'off';
                else if (el.id === 'currencyMode') el.value = 'local';
                else if (el.id === 'minRS') el.value = '';
                else if (el.id === 'minTotalScore') el.value = '70';
                else el.value = '';
            }
        });

        setActiveMarket('US'); // Reset market to US
        updateFilters();
        state.page = 1;
        state.sortKey = 'final_rank_score';
        state.sortDir = 'desc';
        fetchScreener();

        if (window.UI) {
            window.UI.showToast('Filters have been reset', 'info');
        }
    }

    // ---- Filter Presets (localStorage) ----
    const PRESET_STORAGE_KEY = 'screener_preset_v1';

    function savePreset() {
        const preset = {
            priceMin: $("priceMin")?.value || '',
            priceMax: $("priceMax")?.value || '',
            minRS: $("minRS")?.value || '',
            minTotalScore: $("minTotalScore")?.value || '70',
            rsiFilter: $("rsiFilter")?.value || '',
            volumeMin: $("volumeMin")?.value || '',
            aiMode: $("aiMode")?.value || 'off',
            currencyMode: $("currencyMode")?.value || 'local',
            market: state.filters.market || 'US',
            sortKey: state.sortKey,
            sortDir: state.sortDir,
        };
        localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(preset));
        if (window.UI) window.UI.showToast('フィルターを保存しました', 'success', 2000);
    }

    function loadPreset() {
        const raw = localStorage.getItem(PRESET_STORAGE_KEY);
        if (!raw) {
            if (window.UI) window.UI.showToast('保存済みプリセットがありません', 'warning');
            return;
        }
        try {
            const p = JSON.parse(raw);
            if ($("priceMin")) $("priceMin").value = p.priceMin || '';
            if ($("priceMax")) $("priceMax").value = p.priceMax || '';
            if ($("minRS")) $("minRS").value = p.minRS || '';
            if ($("minTotalScore")) $("minTotalScore").value = p.minTotalScore || '70';
            if ($("rsiFilter")) $("rsiFilter").value = p.rsiFilter || '';
            if ($("volumeMin")) $("volumeMin").value = p.volumeMin || '';
            if ($("aiMode")) $("aiMode").value = p.aiMode || 'off';
            if ($("currencyMode")) $("currencyMode").value = p.currencyMode || 'local';
            if (p.market) setActiveMarket(p.market);
            state.sortKey = p.sortKey || null;
            state.sortDir = p.sortDir || 'desc';
            updateFilters();
            state.page = 1;
            fetchScreener();
            if (window.UI) window.UI.showToast('プリセットを読み込みました', 'success', 2000);
        } catch (e) {
            if (window.UI) window.UI.showToast('プリセットの読み込みに失敗しました', 'error');
        }
    }

    // Export CSV (Requirement 8: BOM + Timestamp)
    function exportCSV() {
        if (!state.items.length) {
            if (window.UI) window.UI.showToast('No data to export', 'warning');
            return;
        }

        // Columns per requirement: Rank, Symbol, Name, RS, Total, Price, Pivot, Change, Dist52W, Signals
        const headers = ['Rank', 'Symbol', 'Name', 'RS Rating', 'Total Score', 'Price', 'Pivot', 'Change %', 'Dist 52W High', 'M判定', 'DD', 'VIX', 'Breakout', 'CAN-SLIM', '総合', 'Final Rank', 'AI判定', 'AI +5% (5日)', 'AI 3Class', '15日AI', 'Signals'];
        const rows = state.items.map((row, idx) => {
            const rank = (idx + 1) + (state.page - 1) * state.limit;
            return [
                rank,
                row.symbol ?? '',
                row.name ?? '',
                row.rs_rating ?? '',
                row.total_score ?? '',
                row.price ?? '',
                row.pivot ?? '',
                row.change_pct ? row.change_pct.toFixed(2) : '0.00',
                row.dist_52w_high_pct ? row.dist_52w_high_pct.toFixed(2) : '0.00',
                row.m_judgment ?? '',
                row.dd_count_display ?? '',
                row.vix_mode ?? '',
                row.breakout_effectiveness ?? '',
                row.canslim_pass_count_display ?? (row.canslim_pass_count != null ? `${row.canslim_pass_count}/7` : ''),
                row.overall_grade ?? '',
                row.final_rank_score != null ? Number(row.final_rank_score).toFixed(1) : '',
                row.action_label ?? '',
                row.ai_p_up5_2w != null ? Number(row.ai_p_up5_2w).toFixed(1) : '',
                row.ai_3class_summary ?? '',
                row.class15 ? (row.class15.direction || '') : '',
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

        if (window.UI) window.UI.showToast(`CSV saved: ${filename}`, 'success');
    }

    // Diagnostics (Requirement 9, 10, 11)
    async function showDiagnostics() {
        const modal = $("diagModal");
        const content = $("diagContent");
        if (!modal || !content) return;

        modal.style.display = "flex";
        content.innerHTML = '<div style="padding: 24px; text-align: center;">Loading diagnostics...</div>';

        try {
            const resp = await fetch(`${API_BASE}/system/diagnostics/screener`);
            const data = await resp.json();

            let html = `<div style="padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); margin-bottom: 20px;">
                Overall Status: <span class="${data.status === 'ok' ? 'text-success' : (data.status === 'error' ? 'text-danger' : 'text-warning')}" style="font-weight:700;">${data.status.toUpperCase()}</span>
            </div>`;

            // Contract Check (Requirement 10)
            const cc = data.api_contract_check;
            html += `<div style="margin-bottom: 16px; border-left: 3px solid ${cc.status === 'ok' ? 'var(--success)' : 'var(--warning)'}; padding-left: 12px; background: rgba(255,255,255,0.02); padding-top: 8px; padding-bottom: 8px;">
                <div style="font-weight:700;">API Contract: ${cc.status.toUpperCase()}</div>
                ${cc.missing_query_params?.length > 0
                    ? `<div class="text-warning">Missing Params: ${cc.missing_query_params.join(', ')}</div>`
                    : '<div class="text-success">[OK] Query parameters verified</div>'}
                ${cc.missing_response_fields.length > 0
                    ? `<div class="text-warning">Missing Keys: ${cc.missing_response_fields.join(', ')}</div>`
                    : '<div class="text-success">[OK] Response fields verified</div>'}
            </div>`;

            // Query Logic Check (New)
            const ql = data.query_logic_check;
            if (ql) {
                html += `<div style="margin-bottom: 16px; border-left: 3px solid ${ql.price_filter_applied_to_latest_close ? 'var(--success)' : 'var(--danger)'}; padding-left: 12px; background: rgba(255,255,255,0.02); padding-top: 8px; padding-bottom: 8px;">
                    <div style="font-weight:700;">Query Logic: ${ql.price_filter_applied_to_latest_close ? 'OK' : 'ERROR'}</div>
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
                <div style="font-weight:700;">Database Integrity: ${dbi.status.toUpperCase()}</div>
                ${dbi.missing_columns?.length > 0
                    ? `<div class="text-danger" style="margin: 4px 0;">Missing Columns: ${dbi.missing_columns.join(', ')}</div>`
                    : '<div class="text-success">[OK] Columns verified via information_schema</div>'}
                <div style="font-size: 0.8rem; margin-top: 8px; color: var(--text-muted);">
                    ${Object.entries(dbi.tables || {}).map(([t, v]) => `${t}: ${v.rows.toLocaleString()} rows`).join(' | ')}
                </div>
            </div>`;

            // Freshness
            const df = data.data_freshness_check;
            html += `<div style="border-left: 3px solid var(--primary); padding-left: 12px; background: rgba(255,255,255,0.02); padding-top: 8px; padding-bottom: 8px;">
                <div style="font-weight:700;">Data Freshness</div>
                <div style="font-size: 0.8rem;">
                    ${Object.entries(df.latest_dates || {}).map(([t, d]) => `<div>${t}: ${d || 'N/A'} (<span class="${df.staleness_days[t] > 4 ? 'text-warning' : ''}">${df.staleness_days[t]}d ago</span>)</div>`).join('')}
                </div>
            </div>`;

            content.innerHTML = html;
        } catch (err) {
            content.innerHTML = `<div class="text-danger" style="padding: 20px;">Failed to load diagnostics: ${err.message}</div>`;
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

        // Preset save/load buttons
        const btnSavePreset = $("btnSavePreset");
        if (btnSavePreset) {
            btnSavePreset.addEventListener("click", savePreset);
        }
        const btnLoadPreset = $("btnLoadPreset");
        if (btnLoadPreset) {
            btnLoadPreset.addEventListener("click", loadPreset);
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
        const inputs = document.querySelectorAll("#priceMin, #priceMax, #minRS, #minTotalScore, #searchInput, #aiMode, #marketFilter, #currencyMode");
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

        const aiMode = $("aiMode");
        if (aiMode) {
            aiMode.addEventListener("change", () => {
                state.page = 1;
                fetchScreener();
            });
        }

        // Market toggle buttons
        document.querySelectorAll('.market-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                setActiveMarket(btn.dataset.market);
                state.page = 1;
                fetchScreener();
            });
        });

        const currencyMode = $("currencyMode");
        if (currencyMode) {
            currencyMode.addEventListener("change", () => {
                updateFilters();
                renderTable();
            });
        }

        // Column sort: click header to sort, click again to reverse
        document.querySelectorAll("#resultsTable thead th[data-sort]").forEach(th => {
            th.addEventListener("click", () => {
                const key = th.dataset.sort;
                if (state.sortKey === key) {
                    state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
                } else {
                    state.sortKey = key;
                    state.sortDir = (key === "symbol" || key === "name") ? "asc" : "desc";
                }
                renderTable();
            });
        });
    }

    // Initialize on DOM ready
    function init() {
        console.log('[Screener] Initializing...');
        applyUrlPresets();
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

// Global: called from screener table rows
window.setAlertFromScreener = function(symbol, price) {
    const url = `alerts.html?symbol=${encodeURIComponent(symbol)}&condition=price_above&value=${price > 0 ? (price * 1.05).toFixed(2) : ''}`;
    window.location.href = url;
};
