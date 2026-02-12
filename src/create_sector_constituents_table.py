"""
sector_constituentsテーブル作成スクリプト
セクターETFの構成銘柄を管理
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
    print("sector_constituentsテーブルを作成")
    print("=" * 60)
    
    # テーブル作成
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sector_constituents (
            id SERIAL PRIMARY KEY,
            sector_etf_symbol VARCHAR(10) NOT NULL,
            constituent_symbol VARCHAR(20) NOT NULL,
            weight DECIMAL(5, 2),
            rank INT,
            data_source VARCHAR(50) DEFAULT 'yfinance',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sector_etf_symbol, constituent_symbol)
        )
    """)
    
    # インデックス作成
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sector_constituents_etf 
        ON sector_constituents(sector_etf_symbol, rank)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sector_constituents_symbol 
        ON sector_constituents(constituent_symbol)
    """)
    
    conn.commit()
    
    print("✓ sector_constituentsテーブルを作成しました")
    print("✓ インデックスを作成しました")
    
    # テーブル構造確認
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'sector_constituents'
        ORDER BY ordinal_position
    """)
    
    print("\nテーブル構造:")
    for row in cursor.fetchall():
        print(f"  {row[0]:25s} {row[1]}")
    
    cursor.close()
    conn.close()
    
    print("\n" + "=" * 60)
    print("✓ 完了")
    print("=" * 60)
    
except Exception as e:
    print(f"✗ エラー: {e}")
    import traceback
    traceback.print_exc()
