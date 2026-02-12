import postgresql_connect


if __name__ == "__main__":
    print("=" * 60)
    print("データベーステーブル一覧確認")
    print("=" * 60)
    
    db = postgresql_connect.PostgreSQLConnect()
    
    if db.connect():
        print("\n【作成されたテーブル一覧】\n")
        
        # テーブル一覧を取得
        tables = db.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        
        if tables:
            for idx, table in enumerate(tables, 1):
                print(f"{idx:2}. {table[0]}")
            
            print(f"\n合計: {len(tables)} テーブル")
            
            # 各テーブルのカラム数も確認
            print("\n" + "=" * 60)
            print("各テーブルの詳細")
            print("=" * 60)
            
            for table in tables:
                table_name = table[0]
                columns = db.execute("""
                    SELECT COUNT(*) 
                    FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                """, (table_name,))
                
                if columns:
                    print(f"{table_name:25} : {columns[0][0]:2} カラム")
        else:
            print("テーブルが見つかりませんでした")
        
        print("\n" + "=" * 60)
        db.disconnect()
