"""
セクターETFデータ取得スクリプト
Select Sector SPDR（11セクター）+ ベンチマーク指数（SPY/QQQ）
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


# セクターETF + ベンチマーク指数
SECTOR_ETFS = {
    # 11 Select Sector SPDR ETFs（GICS準拠）
    'XLC': {'name': 'Communication Services Select Sector SPDR', 'sector': 'Communication', 'type': 'SECTOR_ETF'},
    'XLY': {'name': 'Consumer Discretionary Select Sector SPDR', 'sector': 'Consumer Discretionary', 'type': 'SECTOR_ETF'},
    'XLP': {'name': 'Consumer Staples Select Sector SPDR', 'sector': 'Consumer Staples', 'type': 'SECTOR_ETF'},
    'XLE': {'name': 'Energy Select Sector SPDR', 'sector': 'Energy', 'type': 'SECTOR_ETF'},
    'XLF': {'name': 'Financial Select Sector SPDR', 'sector': 'Financials', 'type': 'SECTOR_ETF'},
    'XLV': {'name': 'Health Care Select Sector SPDR', 'sector': 'Healthcare', 'type': 'SECTOR_ETF'},
    'XLI': {'name': 'Industrial Select Sector SPDR', 'sector': 'Industrials', 'type': 'SECTOR_ETF'},
    'XLB': {'name': 'Materials Select Sector SPDR', 'sector': 'Materials', 'type': 'SECTOR_ETF'},
    'XLRE': {'name': 'Real Estate Select Sector SPDR', 'sector': 'Real Estate', 'type': 'SECTOR_ETF'},
    'XLK': {'name': 'Technology Select Sector SPDR', 'sector': 'Technology', 'type': 'SECTOR_ETF'},
    'XLU': {'name': 'Utilities Select Sector SPDR', 'sector': 'Utilities', 'type': 'SECTOR_ETF'},
    
    # ベンチマーク指数
    'SPY': {'name': 'SPDR S&P 500 ETF Trust', 'sector': 'Benchmark', 'type': 'INDEX'},
    'QQQ': {'name': 'Invesco QQQ Trust', 'sector': 'Benchmark', 'type': 'INDEX'},
}


def insert_or_update_instrument(conn, symbol, info):
    """銘柄をinstrumentsテーブルに登録"""
    cursor = conn.cursor()
    
    try:
        # 既存チェック
        cursor.execute("SELECT symbol_key FROM instruments WHERE symbol_key = %s", (symbol,))
        exists = cursor.fetchone()
        
        if exists:
            print(f"  銘柄 {symbol} は既に登録されています")
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
                symbol,
                symbol,
                'US',
                info['name'],
                'USD',
                True,
                datetime.now(),
                datetime.now()
            ))
            conn.commit()
            print(f"  [OK] 銘柄 {symbol} ({info['sector']}) を登録しました")
    
    except Exception as e:
        print(f"  [ERR] {symbol} の登録に失敗 - {e}")
        conn.rollback()
    finally:
        cursor.close()
    
    return symbol


def fetch_and_save_price_data(conn, symbol, days=90):
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
            """, (symbol, trading_date))
            
            exists = cursor.fetchone()[0] > 0
            
            if not exists:
                cursor.execute("""
                    INSERT INTO prices_daily 
                    (symbol_key, trading_date, open, high, low, close, adj_close, volume, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    symbol,
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
    print("セクターETF + ベンチマーク指数データ取得")
    print("=" * 60)
    
    # DB接続
    conn = get_connection()
    if not conn:
        print("[ERR] データベース接続に失敗しました")
        return
    
    print(f"\n取得対象: {len(SECTOR_ETFS)}件（11セクター + 2ベンチマーク）")
    print("-" * 60)
    
    for symbol, info in SECTOR_ETFS.items():
        print(f"\n[{info['type']}] {symbol} - {info['sector']}")
        
        # 1. 銘柄登録
        insert_or_update_instrument(conn, symbol, info)
        
        # 2. 価格データ取得
        fetch_and_save_price_data(conn, symbol, days=90)
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("[OK] 処理完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
