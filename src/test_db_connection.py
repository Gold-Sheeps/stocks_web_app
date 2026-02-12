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
    print("DBに登録されているデータを確認")
    print("=" * 60)
    
    # 1. instruments テーブルを確認
    print("\n【instruments テーブル】")
    cursor.execute("""
        SELECT symbol_key, name, market, currency
        FROM instruments
        ORDER BY symbol_key
    """)
    
    instruments = cursor.fetchall()
    print(f"登録されている銘柄数: {len(instruments)}")
    for row in instruments[:20]:  # 最初の20件だけ表示
        print(f"  {row[0]:15s} | {row[1]:30s} | {row[2]:10s} | {row[3]}")
    
    # 2. 市場指数データの件数
    print("\n【市場指数の価格データ件数】")
    indices = ['DJI', 'GSPC', 'IXIC', 'VIX', 'N225', '000001_SS', 'GDAXI', 'FTSE']
    for symbol in indices:
        cursor.execute("""
            SELECT COUNT(*) 
            FROM prices_daily
            WHERE symbol_key = %s
        """, (symbol,))
        count = cursor.fetchone()[0]
        if count > 0:
            # 最新データも表示
            cursor.execute("""
                SELECT close, trading_date
                FROM prices_daily
                WHERE symbol_key = %s
                ORDER BY trading_date DESC
                LIMIT 1
            """, (symbol,))
            latest = cursor.fetchone()
            print(f"  {symbol:15s}: {count:3d}件 | 最新 ${latest[0]:10.2f} ({latest[1]})")
        else:
            print(f"  {symbol:15s}: データなし")
    
    # 3. 為替データの件数
    print("\n【為替の価格データ件数】")
    fx_symbols = ['USDJPY', 'EURUSD', 'GBPUSD', 'GBPJPY']
    for symbol in fx_symbols:
        cursor.execute("""
            SELECT COUNT(*) 
            FROM prices_daily
            WHERE symbol_key = %s
        """, (symbol,))
        count = cursor.fetchone()[0]
        if count > 0:
            cursor.execute("""
                SELECT close, trading_date
                FROM prices_daily
                WHERE symbol_key = %s
                ORDER BY trading_date DESC
                LIMIT 1
            """, (symbol,))
            latest = cursor.fetchone()
            print(f"  {symbol:15s}: {count:3d}件 | 最新 {latest[0]:10.4f} ({latest[1]})")
        else:
            print(f"  {symbol:15s}: データなし")
    
    # 4. 貴金属データの件数
    print("\n【貴金属の価格データ件数】")
    metal_symbols = ['GC_F', 'SI_F']
    for symbol in metal_symbols:
        cursor.execute("""
            SELECT COUNT(*) 
            FROM prices_daily
            WHERE symbol_key = %s
        """, (symbol,))
        count = cursor.fetchone()[0]
        if count > 0:
            cursor.execute("""
                SELECT close, trading_date
                FROM prices_daily
                WHERE symbol_key = %s
                ORDER BY trading_date DESC
                LIMIT 1
            """, (symbol,))
            latest = cursor.fetchone()
            print(f"  {symbol:15s}: {count:3d}件 | 最新 ${latest[0]:10.2f} ({latest[1]})")
        else:
            print(f"  {symbol:15s}: データなし")
    
    cursor.close()
    conn.close()
    
    print("\n" + "=" * 60)
    print("✓ 確認完了")
    print("=" * 60)
    
except Exception as e:
    print(f"✗ エラー: {e}")
    import traceback
    traceback.print_exc()
