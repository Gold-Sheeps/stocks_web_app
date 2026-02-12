/* config.js */
/*
 * Centralized Configuration for Stocks Market Checker
 * Loaded BEFORE common.js and page-specific scripts.
 */

window.CONFIG = {
    // API endpoint - MUST point to Port 8000 (Backend)
    API_BASE: 'http://localhost:8000/api/v1',

    // Sidebar settings
    SIDEBAR_PATH: '/components/sidebar.html',
    SIDEBAR_VERSION: 'v20260211',
    UI_VERSION: '20260211-neo-01',

    // Debug flag
    DEBUG: false
};

// Freeze to prevent accidental modification
Object.freeze(window.CONFIG);

console.log(`[Config] Loaded. API_BASE: ${window.CONFIG.API_BASE}`);
