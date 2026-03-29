"""
市場指数データ取得スクリプト
yfinanceを使用してダウ、VIX、S&P500などのデータを取得してDBに保存
"""
import os
from pathlib import Path
from datetime import datetime, timedelta
import psycopg

# yfinance timezone cache path fix
_YF_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "yfinance"
_YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YFINANCE_TZ_CACHE_LOCATION", str(_YF_CACHE_DIR))
import yfinance as yf
yf.set_tz_cache_location(str(_YF_CACHE_DIR))


def get_connection():
    """PostgreSQLデータベース接続を取得"""
    return psycopg.connect(
        host="localhost",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="test"
    )


# 市場指数のシンボルマッピング
MARKET_INDICES = {
    # 米国株式指数
    '^DJI': {'name': 'Dow Jones Industrial Average', 'market': 'US', 'currency': 'USD', 'type': 'INDEX'},
    '^GSPC': {'name': 'S&P 500', 'market': 'US', 'currency': 'USD', 'type': 'INDEX'},
    '^IXIC': {'name': 'NASDAQ Composite', 'market': 'US', 'currency': 'USD', 'type': 'INDEX'},
    '^RUT': {'name': 'Russell 2000', 'market': 'US', 'currency': 'USD', 'type': 'INDEX'},
    
    # 海外指数
    '000001.SS': {'name': 'Shanghai Composite', 'market': 'CN', 'currency': 'CNY', 'type': 'INDEX'},
    '^GDAXI': {'name': 'DAX', 'market': 'DE', 'currency': 'EUR', 'type': 'INDEX'},
    '^FTSE': {'name': 'FTSE 100', 'market': 'GB', 'currency': 'GBP', 'type': 'INDEX'},
    '^N225': {'name': 'Nikkei 225', 'market': 'JP', 'currency': 'JPY', 'type': 'INDEX'},
    
    # VIX（恐怖指数）
    '^VIX': {'name': 'CBOE Volatility Index', 'market': 'US', 'currency': 'USD', 'type': 'VIX'},
    '^VVIX': {'name': 'CBOE VVIX Index (Volatility of VIX)', 'market': 'US', 'currency': 'USD', 'type': 'VIX'},
    '^VIX3M': {'name': 'CBOE 3-Month Volatility Index', 'market': 'US', 'currency': 'USD', 'type': 'VIX'},
    
    # 為替
    'USDJPY=X': {'name': 'USD/JPY', 'market': 'FX', 'currency': 'JPY', 'type': 'FX'},
    'EURUSD=X': {'name': 'EUR/USD', 'market': 'FX', 'currency': 'USD', 'type': 'FX'},
    'GBPUSD=X': {'name': 'GBP/USD', 'market': 'FX', 'currency': 'USD', 'type': 'FX'},
    'GBPJPY=X': {'name': 'GBP/JPY', 'market': 'FX', 'currency': 'JPY', 'type': 'FX'},
    
    # 貴金属ETF
    'GLD': {'name': 'SPDR Gold Shares', 'market': 'US', 'currency': 'USD', 'type': 'METAL'},
    'SLV': {'name': 'iShares Silver Trust', 'market': 'US', 'currency': 'USD', 'type': 'METAL'},
    'PALL': {'name': 'Aberdeen Physical Palladium Shares', 'market': 'US', 'currency': 'USD', 'type': 'METAL'},
    'PPLT': {'name': 'Aberdeen Physical Platinum Shares', 'market': 'US', 'currency': 'USD', 'type': 'METAL'},
}


def insert_or_update_instrument(conn, symbol, info):
    """銘柄をinstrumentsテーブルに登録"""
    cursor = conn.cursor()
    
    # symbol_keyを生成（例: ^DJI -> DJI, USDJPY=X -> USDJPY）
    symbol_key = symbol.replace('^', '').replace('=X', '').replace('-', '_').replace('.', '_')
    
    try:
        # 既存チェック
        cursor.execute("SELECT symbol_key FROM instruments WHERE symbol_key = %s", (symbol_key,))
        exists = cursor.fetchone()
        
        if exists:
            print(f"  銘柄 {symbol_key} は既に登録されています")
        else:
            # 新規登録
            cursor.execute("""
                INSERT INTO instruments (
                    symbol_key,
                    symbol_key_old_backup,
                    market,
                    name,
                    currency,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                symbol_key,
                symbol_key,
                info['market'],
                info['name'],
                info['currency'],
                True,
                datetime.now(),
                datetime.now()
            ))
            conn.commit()
            print(f"  [OK] 銘柄 {symbol_key} ({info['name']}) を登録しました")
    
    except Exception as e:
        print(f"  [ERR] {symbol_key} の登録に失敗 - {e}")
        conn.rollback()
    finally:
        cursor.close()
    
    return symbol_key


def fetch_and_save_price_data(conn, symbol, symbol_key, days=90):
    """価格データを取得してDBに保存"""
    cursor = conn.cursor()
    
    try:
        print(f"  データ取得中: {symbol}...")
        
        # yfinanceでデータ取得
        ticker = yf.Ticker(symbol)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        hist = ticker.history(start=start_date, end=end_date)
        
        if hist.empty:
            print(f"  [ERR] データが取得できませんでした: {symbol}")
            return
        
        # データベースに保存
        count = 0
        for date, row in hist.iterrows():
            trading_date = date.date()
            
            # 既存データチェック
            cursor.execute("""
                SELECT COUNT(*) FROM prices_daily 
                WHERE symbol_key = %s AND trading_date = %s
            """, (symbol_key, trading_date))
            
            exists = cursor.fetchone()[0] > 0
            
            if not exists:
                cursor.execute("""
                    INSERT INTO prices_daily 
                    (symbol_key, trading_date, open, high, low, close, adj_close, volume, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    symbol_key,
                    trading_date,
                    float(row['Open']),
                    float(row['High']),
                    float(row['Low']),
                    float(row['Close']),
                    float(row['Close']),  # adj_close
                    int(row['Volume']) if row['Volume'] > 0 else None,
                    'yfinance',
                    datetime.now()
                ))
                count += 1
        
        conn.commit()
        print(f"  [OK] {count}件の価格データを保存しました")
        
    except Exception as e:
        print(f"  [ERR] {symbol} のデータ取得に失敗 - {e}")
        conn.rollback()
    finally:
        cursor.close()


def main():
    """メイン処理"""
    print("=" * 60)
    print("市場指数データ取得スクリプト")
    print("=" * 60)
    
    # DB接続
    conn = get_connection()
    if not conn:
        print("[ERR] データベース接続に失敗しました")
        return
    
    print(f"\n取得対象: {len(MARKET_INDICES)}件の市場指数")
    print("-" * 60)
    
    for symbol, info in MARKET_INDICES.items():
        print(f"\n[{info['type']}] {symbol} - {info['name']}")
        
        # 1. 銘柄登録
        symbol_key = insert_or_update_instrument(conn, symbol, info)
        
        # 2. 価格データ取得
        fetch_and_save_price_data(conn, symbol, symbol_key, days=90)
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("[OK] 処理完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
