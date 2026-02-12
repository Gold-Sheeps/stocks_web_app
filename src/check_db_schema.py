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
    print("テーブル構造の確認")
    print("=" * 60)
    
    # すべてのテーブルを表示
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    
    tables = cursor.fetchall()
    print(f"\n登録されているテーブル:")
    for table in tables:
        print(f"  - {table[0]}")
    
    # instrumentsテーブルのデータ
    print(f"\n【instruments テーブルのデータ】")
    cursor.execute("SELECT COUNT(*) FROM instruments")
    count = cursor.fetchone()[0]
    print(f"総件数: {count}件")
    
    if count > 0:
        cursor.execute("""
            SELECT symbol_key, name, market 
            FROM instruments 
            ORDER BY symbol_key 
            LIMIT 20
        """)
        print("\n最初の20件:")
        for row in cursor.fetchall():
            print(f"  {row[0]:15s} | {row[1]:40s} | {row[2]}")
    
    cursor.close()
    conn.close()
    
    print("\n" + "=" * 60)
    print("✓ 確認完了")
    print("=" * 60)
    
except Exception as e:
    print(f"✗ エラー: {e}")
    import traceback
    traceback.print_exc()
