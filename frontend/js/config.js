/* config.js */
/*
 * Centralized Configuration for Stocks Market Checker
 * Loaded BEFORE common.js and page-specific scripts.
 */

window.CONFIG = {
    // API endpoint
    API_BASE: 'http://127.0.0.1:8010/api/v1',

    // Sidebar settings
    SIDEBAR_PATH: '/components/sidebar.html',
    SIDEBAR_VERSION: 'v20260302',
    UI_VERSION: '20260302-neo-01',

    // Debug flag
    DEBUG: false
};

// Freeze to prevent accidental modification
Object.freeze(window.CONFIG);

console.log(`[Config] Loaded. API_BASE: ${window.CONFIG.API_BASE}`);
