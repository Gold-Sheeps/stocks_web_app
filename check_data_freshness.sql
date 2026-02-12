-- Check latest date in price_daily
SELECT 'Latest Price Date' as metric, MAX(trading_date) as value FROM price_daily;

-- Check latest date in sector_rotation
SELECT 'Latest Sector Date' as metric, MAX(trading_date) as value FROM sector_rotation;

-- Check count of records for the latest date in price_daily
WITH latest_date AS (SELECT MAX(trading_date) as d FROM price_daily)
SELECT 'Price Records count for Latest Date', COUNT(*) 
FROM price_daily 
WHERE trading_date = (SELECT d FROM latest_date);

-- Check specific symbol update (e.g. NVDA)
SELECT symbol_key, MAX(trading_date) as last_update 
FROM price_daily 
WHERE symbol_key = 'US:NVDA'
GROUP BY symbol_key;
