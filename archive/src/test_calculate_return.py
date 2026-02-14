"""
rotation_serviceの_calculate_returnメソッドを単体テスト
"""
import psycopg
from datetime import datetime, timedelta
from decimal import Decimal
import time

def get_connection():
    return psycopg.connect(
        host="localhost",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="test"
    )

def get_price_at_days_ago(conn, symbol_key: str, days_ago: int):
    """N日前の価格を取得"""
    cursor = conn.cursor()
    try:
        target_date = datetime.now().date() - timedelta(days=days_ago)
        
        print(f"  対象日付: {target_date}, 銘柄: {symbol_key}")
        
        cursor.execute("""
            SELECT close FROM prices_daily
            WHERE symbol_key = %s 
              AND trading_date <= %s
            ORDER BY trading_date DESC
            LIMIT 1
        """, (symbol_key, target_date))
        
        result = cursor.fetchone()
        return Decimal(str(result[0])) if result else None
    finally:
        cursor.close()

def calculate_return(conn, symbol_key: str, days: int):
    """指定日数のリターンを計算"""
    print(f"\n[calculate_return] {symbol_key}, {days}日")
    
    start = time.time()
    current_price = get_price_at_days_ago(conn, symbol_key, 0)
    print(f"  現在価格取得: {time.time() - start:.3f}秒")
    
    start = time.time()
    past_price = get_price_at_days_ago(conn, symbol_key, days)
    print(f"  過去価格取得: {time.time() - start:.3f}秒")
    
    if not current_price or not past_price or past_price == 0:
        return Decimal('0')
    
    return ((current_price - past_price) / past_price) * 100

print("=" * 60)
print("rotation_service._calculate_returnメソッドテスト")
print("=" * 60)

conn = get_connection()

# XLKのリターンを計算
try:
    result = calculate_return(conn, 'XLK', 30)
    print(f"\n結果: {result}%")
except Exception as e:
    print(f"\nエラー: {e}")
    import traceback
    traceback.print_exc()

conn.close()

print("\n" + "=" * 60)
print("テスト完了")
print("=" * 60)
