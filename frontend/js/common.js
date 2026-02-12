/* common.js */

// Validation: Ensure CONFIG is loaded
if (typeof window.CONFIG === 'undefined') {
    const errorMsg = 'CRITICAL ERROR: config.js is not loaded. Please ensure js/config.js is included before js/common.js';
    console.error(errorMsg);
    document.body.innerHTML = `<div style="color:red; padding:20px; font-weight:bold; background:white;">${errorMsg}</div>`;
    throw new Error(errorMsg);
}

const CONFIG = window.CONFIG;

// --- UI Utilities ---
const UI = {
    // Toast Notification System
    showToast: (message, type = 'info', duration = 4000) => {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;

        let icon = 'ℹ️';
        if (type === 'success') icon = '✅';
        if (type === 'error') icon = '⚠️';

        toast.innerHTML = `
            <div style="display:flex; align-items:center; gap:12px;">
                <span style="font-size:1.2em;">${icon}</span>
                <span style="color:var(--text-main); font-weight:500;">${message}</span>
            </div>
            <button style="background:none; border:none; color:var(--text-muted); cursor:pointer;" onclick="this.parentElement.remove()">×</button>
        `;

        container.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'fadeOut 0.3s forwards';
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },

    // Formatters
    formatNumber: (num, decimals = 2) => {
        if (num === null || num === undefined || num === '') return '-';
        const val = parseFloat(num);
        if (isNaN(val)) return '-';
        return val.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    },

    formatCurrency: (num, currency = 'USD') => {
        if (num === null || num === undefined || num === '') return '-';
        const val = parseFloat(num);
        if (isNaN(val)) return '-';
        return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency }).format(val);
    },

    formatPct: (num, addSign = true) => {
        if (num === null || num === undefined || num === '') return '-';
        const val = parseFloat(num);
        if (isNaN(val)) return '-';

        const cls = val > 0 ? 'text-success' : (val < 0 ? 'text-danger' : 'text-muted');
        const sign = (addSign && val > 0) ? '+' : '';
        return `<span class="${cls}">${sign}${val.toFixed(2)}%</span>`;
    }
};

// Expose to window
window.Utils = UI; // Backward compatibility
window.UI = UI;

// --- Sidebar Loader & Init ---
(function () {
    async function loadSidebar() {
        const container = document.getElementById("sidebar-container");
        if (!container) return;

        // BUMPED KEY to force reload for Phase 5-1 changes
        const currentVer = "v20260211_neo_01";
        const cacheKey = `sidebar_html_${currentVer}`;

        // Auto-Clear Old Cache (Iterate and remove mismatched versions)
        try {
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                if (key && key.startsWith("sidebar_html_") && key !== cacheKey) {
                    console.log(`[Sidebar] Clearing old cache: ${key}`);
                    sessionStorage.removeItem(key);
                }
            }
        } catch (e) { console.warn("SessionStorage access error", e); }

        const cached = sessionStorage.getItem(cacheKey);
        if (cached) {
            console.log("[Sidebar] loaded from sessionStorage", cacheKey);
            container.innerHTML = cached;
            highlightActiveMenu();
            updateFooterVersion(currentVer);
            return;
        }

        console.log("[Sidebar] fetching /components/sidebar.html");
        try {
            // Added cache buster to timestamp
            const res = await fetch(`/components/sidebar.html?v=${currentVer}`, { cache: "no-store" });
            if (!res.ok) {
                container.innerHTML = `<div style="padding:12px;color:#fff;background:#b91c1c">Sidebar load failed (${res.status})</div>`;
                throw new Error(`sidebar fetch failed: ${res.status}`);
            }
            const html = await res.text();
            sessionStorage.setItem(cacheKey, html);
            container.innerHTML = html;
            highlightActiveMenu();
            updateFooterVersion(currentVer);
        } catch (e) {
            console.error(e);
        }
    }

    function updateFooterVersion(ver) {
        const el = document.getElementById("uiVer");
        if (el) el.textContent = ver || window.CONFIG?.UI_VERSION || "v?";
    }

    function updateFooterVersion() {
        const el = document.getElementById("uiVer");
        if (el && window.CONFIG?.UI_VERSION) el.textContent = window.CONFIG.UI_VERSION;
    }

    function highlightActiveMenu() {
        // Highlight Active Link based on URL
        const currentPath = window.location.pathname.split('/').pop() || 'monitor.html';
        const sidebarContainer = document.getElementById('sidebar-container');
        if (!sidebarContainer) return;

        // Update selector to match new structure
        const links = sidebarContainer.querySelectorAll('.nav-menu a');
        links.forEach(link => {
            const href = link.getAttribute('href');
            if (href && (href === currentPath || href.endsWith('/' + currentPath))) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }

    function verifyCssApplied() {
        // 白背景のままならCSS未適用の可能性が高い
        const bg = getComputedStyle(document.body).backgroundColor;
        // Check for white or transparent body background which usually means CSS failed
        if (bg === "rgb(255, 255, 255)" || bg === "rgba(0, 0, 0, 0)") {
            console.error("common.css not applied? body background =", bg);
            // 目立つエラー表示（ユーザーが即気づける）
            const bar = document.createElement("div");
            bar.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;background:#b91c1c;color:#fff;padding:8px 12px;font-size:14px;font-weight:bold;";
            bar.textContent = "ERROR: common.css が適用されていません（/css/common.css の読み込みを確認してください）";
            document.body.appendChild(bar);
        }
    }

    window.addEventListener("DOMContentLoaded", async () => {
        try {
            await loadSidebar();
            verifyCssApplied();

            // Allow manual toast container creation if not in sidebar or common path
            if (!document.getElementById('toast-container')) {
                const tc = document.createElement('div');
                tc.id = 'toast-container';
                document.body.appendChild(tc);
            }
        } catch (e) {
            UI.showToast(String(e), "error");
            console.error(e);
        }
    });
})();

