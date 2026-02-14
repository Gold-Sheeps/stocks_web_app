/* common.js - Enhanced Edition */

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
    // Toast Notification System - Enhanced
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
        if (type === 'warning') icon = '⚡';

        toast.innerHTML = `
            <div style="display:flex; align-items:center; gap:12px;">
                <span style="font-size:1.2em;">${icon}</span>
                <span style="color:var(--text-main); font-weight:500;">${message}</span>
            </div>
            <button style="background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:1.5rem; line-height:1;" onclick="this.parentElement.remove()">×</button>
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
        return val.toLocaleString(undefined, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    },

    formatCurrency: (num, currency = 'USD') => {
        if (num === null || num === undefined || num === '') return '-';
        const val = parseFloat(num);
        if (isNaN(val)) return '-';
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: currency
        }).format(val);
    },

    formatPct: (num, addSign = true) => {
        if (num === null || num === undefined || num === '') return '-';
        const val = parseFloat(num);
        if (isNaN(val)) return '-';

        const cls = val > 0 ? 'text-success' : (val < 0 ? 'text-danger' : 'text-muted');
        const sign = (addSign && val > 0) ? '+' : '';
        const arrow = val > 0 ? '▲' : (val < 0 ? '▼' : '');
        return `<span class="${cls}">${arrow} ${sign}${Math.abs(val).toFixed(2)}%</span>`;
    },

    formatDate: (dateStr) => {
        if (!dateStr) return '-';
        try {
            const date = new Date(dateStr);
            return date.toLocaleDateString('ja-JP', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit'
            });
        } catch (e) {
            return dateStr;
        }
    },

    formatDateTime: (dateStr) => {
        if (!dateStr) return '-';
        try {
            const date = new Date(dateStr);
            return date.toLocaleString('ja-JP', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch (e) {
            return dateStr;
        }
    },

    // Loading Overlay
    showLoading: (containerId = 'loading') => {
        const loading = document.getElementById(containerId);
        if (loading) loading.style.display = 'flex';
    },

    hideLoading: (containerId = 'loading') => {
        const loading = document.getElementById(containerId);
        if (loading) loading.style.display = 'none';
    },

    // Modal Utilities
    openModal: (modalId) => {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.add('active');
            document.body.style.overflow = 'hidden';
        }
    },

    closeModal: (modalId) => {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.remove('active');
            document.body.style.overflow = '';
        }
    },

    // Confirm Dialog
    confirm: (message, onConfirm, onCancel) => {
        if (window.confirm(message)) {
            if (onConfirm) onConfirm();
        } else {
            if (onCancel) onCancel();
        }
    },

    // Copy to Clipboard
    copyToClipboard: (text) => {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(text).then(() => {
                UI.showToast('クリップボードにコピーしました', 'success');
            }).catch(err => {
                console.error('Copy failed:', err);
                UI.showToast('コピーに失敗しました', 'error');
            });
        } else {
            // Fallback for older browsers
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            try {
                document.execCommand('copy');
                UI.showToast('クリップボードにコピーしました', 'success');
            } catch (err) {
                UI.showToast('コピーに失敗しました', 'error');
            }
            document.body.removeChild(textarea);
        }
    },

    // Animation Utilities
    fadeIn: (element, duration = 300) => {
        element.style.opacity = '0';
        element.style.display = 'block';

        let start = null;
        const animate = (timestamp) => {
            if (!start) start = timestamp;
            const progress = timestamp - start;
            element.style.opacity = Math.min(progress / duration, 1);

            if (progress < duration) {
                requestAnimationFrame(animate);
            }
        };
        requestAnimationFrame(animate);
    },

    fadeOut: (element, duration = 300) => {
        let start = null;
        const animate = (timestamp) => {
            if (!start) start = timestamp;
            const progress = timestamp - start;
            element.style.opacity = 1 - Math.min(progress / duration, 1);

            if (progress < duration) {
                requestAnimationFrame(animate);
            } else {
                element.style.display = 'none';
            }
        };
        requestAnimationFrame(animate);
    },

    // Debounce Utility
    debounce: (func, wait = 300) => {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    },

    // Throttle Utility
    throttle: (func, limit = 300) => {
        let inThrottle;
        return function (...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }
};

// --- API Utilities ---
const API = {
    // Generic GET request
    get: async (endpoint, params = {}) => {
        const url = new URL(`${CONFIG.API_BASE}${endpoint}`);
        Object.keys(params).forEach(key => url.searchParams.append(key, params[key]));

        try {
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API GET Error:', error);
            UI.showToast(`APIエラー: ${error.message}`, 'error');
            throw error;
        }
    },

    // Generic POST request
    post: async (endpoint, data = {}) => {
        try {
            const response = await fetch(`${CONFIG.API_BASE}${endpoint}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API POST Error:', error);
            UI.showToast(`APIエラー: ${error.message}`, 'error');
            throw error;
        }
    },

    // Generic PUT request
    put: async (endpoint, data = {}) => {
        try {
            const response = await fetch(`${CONFIG.API_BASE}${endpoint}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API PUT Error:', error);
            UI.showToast(`APIエラー: ${error.message}`, 'error');
            throw error;
        }
    },

    // Generic DELETE request
    delete: async (endpoint) => {
        try {
            const response = await fetch(`${CONFIG.API_BASE}${endpoint}`, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json',
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API DELETE Error:', error);
            UI.showToast(`APIエラー: ${error.message}`, 'error');
            throw error;
        }
    }
};

// --- Form Utilities ---
const Form = {
    // Get form data as object
    getData: (formId) => {
        const form = document.getElementById(formId);
        if (!form) return null;

        const formData = new FormData(form);
        const data = {};

        for (let [key, value] of formData.entries()) {
            data[key] = value;
        }

        return data;
    },

    // Set form data from object
    setData: (formId, data) => {
        const form = document.getElementById(formId);
        if (!form) return;

        Object.keys(data).forEach(key => {
            const input = form.elements[key];
            if (input) {
                if (input.type === 'checkbox') {
                    input.checked = data[key];
                } else if (input.type === 'radio') {
                    const radio = form.querySelector(`input[name="${key}"][value="${data[key]}"]`);
                    if (radio) radio.checked = true;
                } else {
                    input.value = data[key];
                }
            }
        });
    },

    // Reset form
    reset: (formId) => {
        const form = document.getElementById(formId);
        if (form) form.reset();
    },

    // Validate form
    validate: (formId) => {
        const form = document.getElementById(formId);
        if (!form) return false;
        return form.checkValidity();
    }
};

// --- Storage Utilities ---
const Storage = {
    // LocalStorage
    set: (key, value) => {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            console.error('LocalStorage set error:', e);
        }
    },

    get: (key, defaultValue = null) => {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : defaultValue;
        } catch (e) {
            console.error('LocalStorage get error:', e);
            return defaultValue;
        }
    },

    remove: (key) => {
        try {
            localStorage.removeItem(key);
        } catch (e) {
            console.error('LocalStorage remove error:', e);
        }
    },

    // SessionStorage
    setSession: (key, value) => {
        try {
            sessionStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            console.error('SessionStorage set error:', e);
        }
    },

    getSession: (key, defaultValue = null) => {
        try {
            const item = sessionStorage.getItem(key);
            return item ? JSON.parse(item) : defaultValue;
        } catch (e) {
            console.error('SessionStorage get error:', e);
            return defaultValue;
        }
    },

    removeSession: (key) => {
        try {
            sessionStorage.removeItem(key);
        } catch (e) {
            console.error('SessionStorage remove error:', e);
        }
    }
};

// --- Tab Management ---
const Tabs = {
    init: (tabButtonsSelector = '.tab', tabPanelsSelector = '.tab-panel') => {
        const tabButtons = document.querySelectorAll(tabButtonsSelector);
        const tabPanels = document.querySelectorAll(tabPanelsSelector);

        tabButtons.forEach(button => {
            button.addEventListener('click', () => {
                const targetTab = button.getAttribute('data-tab');

                // Remove active class from all tabs and panels
                tabButtons.forEach(btn => btn.classList.remove('active', 'is-active'));
                tabPanels.forEach(panel => panel.classList.remove('active', 'is-active'));

                // Add active class to clicked tab and corresponding panel
                button.classList.add('active', 'is-active');
                const targetPanel = document.getElementById(`tab-${targetTab}`);
                if (targetPanel) {
                    targetPanel.classList.add('active', 'is-active');
                }
            });
        });
    }
};

// Expose to window
window.Utils = UI; // Backward compatibility
window.UI = UI;
window.API = API;
window.Form = Form;
window.Storage = Storage;
window.Tabs = Tabs;

// --- Sidebar Loader & Init ---
(function () {
    async function loadSidebar() {
        const container = document.getElementById("sidebar-container");
        if (!container) return;

        const currentVer = "v20260212_enhanced";
        const cacheKey = `sidebar_html_${currentVer}`;

        // Auto-Clear Old Cache
        try {
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                if (key && key.startsWith("sidebar_html_") && key !== cacheKey) {
                    console.log(`[Sidebar] Clearing old cache: ${key}`);
                    sessionStorage.removeItem(key);
                }
            }
        } catch (e) {
            console.warn("SessionStorage access error", e);
        }

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

    function highlightActiveMenu() {
        const currentPath = window.location.pathname.split('/').pop() || 'monitor.html';
        const sidebarContainer = document.getElementById('sidebar-container');
        if (!sidebarContainer) return;

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
        const bg = getComputedStyle(document.body).backgroundColor;
        if (bg === "rgb(255, 255, 255)" || bg === "rgba(0, 0, 0, 0)") {
            console.error("common.css not applied? body background =", bg);
            const bar = document.createElement("div");
            bar.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;background:#b91c1c;color:#fff;padding:8px 12px;font-size:14px;font-weight:bold;";
            bar.textContent = "ERROR: common.css が適用されていません（/css/common.css の読み込みを確認してください）";
            document.body.appendChild(bar);
        }
    }

    // Enhanced initialization with retry logic
    async function init() {
        try {
            await loadSidebar();
            verifyCssApplied();

            // Create toast container if it doesn't exist
            if (!document.getElementById('toast-container')) {
                const tc = document.createElement('div');
                tc.id = 'toast-container';
                document.body.appendChild(tc);
            }

            // Initialize tabs if they exist on the page
            if (document.querySelector('.tab')) {
                Tabs.init();
            }

            // Add escape key handler for modals
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    const activeModal = document.querySelector('.modal.active');
                    if (activeModal) {
                        activeModal.classList.remove('active');
                        document.body.style.overflow = '';
                    }
                }
            });

            // Add click outside handler for modals
            document.addEventListener('click', (e) => {
                if (e.target.classList.contains('modal') && e.target.classList.contains('active')) {
                    e.target.classList.remove('active');
                    document.body.style.overflow = '';
                }
            });

        } catch (e) {
            UI.showToast(String(e), "error");
            console.error(e);
        }
    }

    // Wait for DOM to be ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();