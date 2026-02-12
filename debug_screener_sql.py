import psycopg
import traceback

def test_screener_query():
    conn = psycopg.connect(
        host="localhost",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="test"
    )
    cur = conn.cursor()

    print("Checking columns in indicator_daily table...")
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'indicator_daily'
    """)
    columns = [row[0] for row in cur.fetchall()]
    print(f"Columns: {columns}")

    print("\nTesting Screener SQL Query...")
    # This is the query from screener_service.py
    query = """
        SELECT 
            i.symbol_key,
            i.name,
            p.close as price,
            p.close - pc.close as change,
            CASE 
                WHEN pc.close > 0 THEN ((p.close - pc.close) / pc.close * 100)
                ELSE 0
            END as change_pct,
            p.volume,
            ind.rsi14,
            ind.rs_score,
            ind.sma20,
            ind.sma50,
                ind.sma20,
                ind.sma50,
                ind.sma200,
                ind.dist_to_52w_high_pct
            FROM instruments i
        INNER JOIN LATERAL (
            SELECT close, volume, trading_date
            FROM price_daily
            WHERE symbol_key = i.symbol_key
            ORDER BY trading_date DESC
            LIMIT 1
        ) p ON true
        LEFT JOIN LATERAL (
            SELECT close
            FROM price_daily
            WHERE symbol_key = i.symbol_key
              AND trading_date < p.trading_date
            ORDER BY trading_date DESC
            LIMIT 1
        ) pc ON true
        LEFT JOIN indicator_daily ind ON ind.symbol_key = i.symbol_key
          AND ind.trading_date = p.trading_date
        WHERE i.is_active = true
          AND ind.rs_score >= 80
        ORDER BY ind.rs_score DESC, p.close DESC
        LIMIT 5
        OFFSET 0
    """
    
    try:
        cur.execute(query)
        print("✅ Query executed successfully!")
        rows = cur.fetchall()
        print(f"Returned {len(rows)} rows")
    except Exception as e:
        print("❌ Query failed!")
        print(e)
    
    conn.close()

if __name__ == "__main__":
    test_screener_query()
