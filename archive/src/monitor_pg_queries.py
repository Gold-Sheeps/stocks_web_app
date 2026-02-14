"""
PostgreSQLで実際に実行されているクエリをリアルタイム監視
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

print("=" * 60)
print("PostgreSQL アクティブクエリ監視")
print("=" * 60)

conn = get_connection()
cursor = conn.cursor()

print("\n現在実行中のクエリを確認...")

try:
    cursor.execute("""
        SELECT 
            pid,
            state,
            query_start,
            state_change,
            wait_event_type,
            wait_event,
            substring(query, 1, 100) as query_snippet
        FROM pg_stat_activity
        WHERE datname = 'postgres'
          AND state != 'idle'
          AND pid != pg_backend_pid()
        ORDER BY query_start
    """)
    
    rows = cursor.fetchall()
    
    if rows:
        print(f"\n実行中のクエリ: {len(rows)}件")
        for row in rows:
            print(f"\nPID: {row[0]}")
            print(f"  状態: {row[1]}")
            print(f"  開始: {row[2]}")
            print(f"  待機イベント: {row[4]} - {row[5]}")
            print(f"  クエリ: {row[6]}")
    else:
        print("\n実行中のクエリなし")
    
    # 統計情報
    cursor.execute("""
        SELECT 
            count(*) as total_connections,
            count(*) FILTER (WHERE state = 'active') as active,
            count(*) FILTER (WHERE state = 'idle') as idle,
            count(*) FILTER (WHERE state = 'idle in transaction') as idle_in_trans
        FROM pg_stat_activity
        WHERE datname = 'postgres'
    """)
    
    stats = cursor.fetchone()
    print(f"\n接続統計:")
    print(f"  総接続数: {stats[0]}")
    print(f"  アクティブ: {stats[1]}")
    print(f"  アイドル: {stats[2]}")
    print(f"  トランザクション中アイドル: {stats[3]}")
    
except Exception as e:
    print(f"エラー: {e}")
finally:
    cursor.close()
    conn.close()

print("\n" + "=" * 60)
print("監視完了")
print("=" * 60)
