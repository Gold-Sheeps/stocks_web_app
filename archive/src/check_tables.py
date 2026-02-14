import postgresql_connect


if __name__ == "__main__":
    db = postgresql_connect.PostgreSQLConnect()
    
    if db.connect():
        # テーブル一覧を取得
        tables = db.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        
        print("\n作成されたテーブル:")
        if tables:
            for idx, table in enumerate(tables, 1):
                print(f"{idx}. {table[0]}")
            print(f"\n合計: {len(tables)} テーブル")
        
        db.disconnect()
