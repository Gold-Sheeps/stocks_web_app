import psycopg

try:
    print("Connecting explicitly to postgres...")
    conn = psycopg.connect("host=localhost port=5432 dbname=postgres user=postgres password=test")
    cursor = conn.cursor()
    
    print("Checking NVDA in indicator_daily table...")
    cursor.execute("SELECT trading_date, high_52w FROM indicator_daily WHERE symbol_key = 'NVDA' ORDER BY trading_date DESC LIMIT 5")
    rows = cursor.fetchall()
    print("Top 5 rows (Date, High52w):")
    for row in rows:
        print(f"{row[0]}: {row[1]}")
    
    cursor.execute("SELECT trading_date, close FROM price_daily WHERE symbol_key = 'NVDA' ORDER BY trading_date DESC LIMIT 5")
    result_price = cursor.fetchall()
    print(f"NVDA Prices (Direct): {result_price}")

    conn.close()
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
