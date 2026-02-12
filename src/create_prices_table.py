"""
prices_dailyテーブル作成スクリプト
"""
import psycopg

try:
    conn = psycopg.connect(
        host="localhost",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="test"
    )
    cursor = conn.cursor()
    
    print("=" * 60)
    print("prices_dailyテーブルを作成")
    print("=" * 60)
    
    # テーブル作成
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices_daily (
            id SERIAL PRIMARY KEY,
            symbol_key VARCHAR(50) NOT NULL,
            trading_date DATE NOT NULL,
            open NUMERIC(20, 6),
            high NUMERIC(20, 6),
            low NUMERIC(20, 6),
            close NUMERIC(20, 6) NOT NULL,
            adj_close NUMERIC(20, 6),
            volume BIGINT,
            source VARCHAR(50) DEFAULT 'manual',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol_key, trading_date)
        )
    """)
    
    # インデックス作成
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_prices_daily_symbol_date 
        ON prices_daily(symbol_key, trading_date DESC)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_prices_daily_trading_date 
        ON prices_daily(trading_date DESC)
    """)
    
    conn.commit()
    
    print("✓ prices_dailyテーブルを作成しました")
    print("✓ インデックスを作成しました")
    
    # テーブル確認
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'prices_daily'
        ORDER BY ordinal_position
    """)
    
    print("\nテーブル構造:")
    for row in cursor.fetchall():
        print(f"  {row[0]:20s} {row[1]}")
    
    cursor.close()
    conn.close()
    
    print("\n" + "=" * 60)
    print("✓ 完了")
    print("=" * 60)
    
except Exception as e:
    print(f"✗ エラー: {e}")
    import traceback
    traceback.print_exc()
