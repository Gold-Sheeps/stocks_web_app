"""
yfinanceから株価データを取得してPostgreSQLに登録するスクリプト
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import csv
import yfinance as yf
from datetime import datetime, timedelta
from src.postgresql_connect import PostgreSQLConnect


def read_stock_symbols(csv_path):
    """CSVファイルから銘柄シンボルを読み取る"""
    symbols = []
    with open(csv_path, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0]:  # 空行をスキップ
                symbols.append(row[0])
    return symbols


def register_instrument(db, symbol, name, market='US', currency='USD'):
    """銘柄マスタに登録"""
    try:
        db.command("""
            INSERT INTO instruments (symbol_key, market, name, currency, is_active)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (symbol_key) DO UPDATE
            SET name = EXCLUDED.name,
                updated_at = CURRENT_TIMESTAMP
        """, (symbol, market, name, currency, True))
        return True
    except Exception as e:
        print(f"  ⚠️  銘柄登録エラー ({symbol}): {e}")
        return False


def import_price_data(db, symbol, start_date, end_date):
    """価格データを取得してDBに登録"""
    try:
        print(f"  📊 {symbol} のデータ取得中...")
        
        # yfinanceでデータ取得
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=start_date, end=end_date)
        
        if hist.empty:
            print(f"  ⚠️  データが見つかりません: {symbol}")
            return 0
        
        # データベースに登録
        count = 0
        for date, row in hist.iterrows():
            trading_date = date.date()
            
            db.command("""
                INSERT INTO price_daily 
                (symbol_key, trading_date, open, high, low, close, adj_close, volume, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol_key, trading_date) 
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    adj_close = EXCLUDED.adj_close,
                    volume = EXCLUDED.volume,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                symbol,
                trading_date,
                float(row['Open']),
                float(row['High']),
                float(row['Low']),
                float(row['Close']),
                float(row['Close']),  # adj_close
                int(row['Volume']) if row['Volume'] else None,
                'yfinance'
            ))
            count += 1
        
        print(f"  ✅ {symbol}: {count} 件のデータを登録")
        return count
        
    except Exception as e:
        print(f"  ❌ エラー ({symbol}): {e}")
        return 0


def main():
    print("=" * 60)
    print("株価データインポート")
    print("=" * 60)
    
    # CSVファイルパス
    csv_path = os.path.join(os.path.dirname(__file__), '..', 'stocks_list', 'us_stocks_list.csv')
    
    # データ取得期間（過去5年分）
    end_date = datetime.now()
    start_date = end_date - timedelta(days=5*365)
    
    print(f"\n期間: {start_date.date()} ～ {end_date.date()}")
    print(f"ファイル: {csv_path}\n")
    
    # 銘柄シンボル読み取り
    symbols = read_stock_symbols(csv_path)
    print(f"銘柄数: {len(symbols)}\n")
    
    # データベース接続
    db = PostgreSQLConnect()
    if not db.connect():
        print("❌ データベース接続失敗")
        return
    
    try:
        total_records = 0
        
        for i, symbol in enumerate(symbols, 1):
            print(f"[{i}/{len(symbols)}] {symbol}")
            
            # 銘柄情報取得
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                name = info.get('longName', symbol)
            except:
                name = symbol
            
            # 銘柄マスタ登録
            register_instrument(db, symbol, name)
            
            # 価格データ登録
            count = import_price_data(db, symbol, start_date, end_date)
            total_records += count
            
            print()  # 改行
        
        print("=" * 60)
        print(f"✅ インポート完了")
        print(f"   銘柄数: {len(symbols)}")
        print(f"   データ件数: {total_records}")
        print("=" * 60)
        
    finally:
        db.disconnect()


if __name__ == "__main__":
    main()
