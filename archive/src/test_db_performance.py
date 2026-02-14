"""
データベースクエリパフォーマンステスト
"""
import psycopg
import time

def get_connection():
    return psycopg.connect(
        host="localhost",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="test"
    )

queries = {
    "instruments count": "SELECT COUNT(*) FROM instruments",
    "prices_daily count": "SELECT COUNT(*) FROM prices_daily",
    "sector_constituents count": "SELECT COUNT(*) FROM sector_constituents",
    "latest prices": """
        SELECT symbol_key, MAX(trading_date) 
        FROM prices_daily 
        GROUP BY symbol_key 
        LIMIT 10
    """,
    "sector XLK constituents": """
        SELECT sc.constituent_symbol, sc.weight, i.name
        FROM sector_constituents sc
        LEFT JOIN instruments i ON sc.constituent_symbol = i.symbol_key
        WHERE sc.sector_etf_symbol = 'XLK'
        ORDER BY sc.rank
        LIMIT 20
    """,
}

print("=" * 60)
print("データベースクエリパフォーマンステスト")
print("=" * 60)

conn = get_connection()

for name, query in queries.items():
    print(f"\n[クエリ] {name}")
    cursor = conn.cursor()
    
    try:
        start = time.time()
        cursor.execute(query)
        rows = cursor.fetchall()
        elapsed = time.time() - start
        
        print(f"  実行時間: {elapsed:.3f}秒")
        print(f"  結果行数: {len(rows)}")
        
    except Exception as e:
        print(f"  ✗ エラー: {e}")
    finally:
        cursor.close()

conn.close()

print("\n" + "=" * 60)
print("テスト完了")
print("=" * 60)
