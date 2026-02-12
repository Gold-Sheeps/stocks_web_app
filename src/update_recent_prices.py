"""
価格データ更新スクリプト
過去2週間（または指定期間）のUS株価データを更新
"""

import sys
sys.path.insert(0, 'src')

import argparse
from datetime import datetime, timedelta
from decimal import Decimal
import time
import yfinance as yf
from tqdm import tqdm
import postgresql_connect


def update_prices(db, symbol: str, start_date: datetime, end_date: datetime):
    """
    指定期間の価格データを取得してDBに保存
    
    Args:
        db: データベース接続
        symbol: ティッカーシンボル
        start_date: 開始日
        end_date: 終了日
    
    Returns:
        (挿入件数, エラーメッセージ)
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=start_date, end=end_date, auto_adjust=False)
        
        if hist.empty:
            return (0, "No data")
        
        inserted = 0
        for date_index, row in hist.iterrows():
            try:
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
                    date_index.date(),
                    Decimal(str(row['Open'])),
                    Decimal(str(row['High'])),
                    Decimal(str(row['Low'])),
                    Decimal(str(row['Close'])),
                    Decimal(str(row['Adj Close'])) if 'Adj Close' in row else None,
                    int(row['Volume']),
                    'yfinance'
                ))
                inserted += 1
            except:
                continue
        
        return (inserted, None)
    
    except Exception as e:
        return (0, str(e))


def daily_update(start_date: datetime = None, end_date: datetime = None, delay: float = 0.5):
    """
    日次価格データ更新
    
    Args:
        start_date: 開始日（Noneの場合は2週間前）
        end_date: 終了日（Noneの場合は本日）
        delay: API呼び出しの遅延秒数
    """
    # デフォルト日付設定
    if end_date is None:
        end_date = datetime.now()
    
    if start_date is None:
        start_date = end_date - timedelta(days=14)
    
    print("=" * 60)
    print("US Stock Price Update")
    print("=" * 60)
    print(f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    print(f"Delay: {delay}s between requests\n")
    
    db = postgresql_connect.PostgreSQLConnect()
    if not db.connect():
        print("[ERROR] Database connection failed")
        return
    
    # 全US株を取得
    symbols = db.execute("""
        SELECT symbol_key 
        FROM instruments 
        WHERE market = 'US' AND is_active = TRUE
    """)
    
    print(f"[INFO] Found {len(symbols)} US stocks\n")
    
    success = 0
    errors = []
    total_rows = 0
    
    for (symbol,) in tqdm(symbols, desc="Updating prices"):
        try:
            rows, error = update_prices(db, symbol, start_date, end_date)
            
            if error:
                errors.append((symbol, error))
            else:
                success += 1
                total_rows += rows
            
            time.sleep(delay)
            
        except Exception as e:
            errors.append((symbol, str(e)))
            tqdm.write(f"  ✗ {symbol}: {e}")
    
    print("\n" + "=" * 60)
    print("Update Complete")
    print("=" * 60)
    print(f"Total stocks: {len(symbols)}")
    print(f"Success: {success}")
    print(f"Total rows inserted/updated: {total_rows}")
    print(f"Failed: {len(errors)}")
    
    if errors:
        print(f"\nFirst 10 errors:")
        for symbol, error in errors[:10]:
            print(f"  {symbol}: {error}")
    
    db.disconnect()
    print("\n✓ Done!")


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description='Update US stock prices for a specified date range',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update past 2 weeks (default)
  python update_recent_prices.py

  # Update specific date range
  python update_recent_prices.py --start 2026-02-01 --end 2026-02-09

  # Update with custom delay
  python update_recent_prices.py --delay 1.0
        """
    )
    
    parser.add_argument(
        '--start',
        type=str,
        help='Start date (YYYY-MM-DD). Default: 2 weeks ago'
    )
    
    parser.add_argument(
        '--end',
        type=str,
        help='End date (YYYY-MM-DD). Default: today'
    )
    
    parser.add_argument(
        '--delay',
        type=float,
        default=0.5,
        help='Delay in seconds between API calls (default: 0.5)'
    )
    
    args = parser.parse_args()
    
    # 日付パース
    start_date = None
    end_date = None
    
    if args.start:
        try:
            start_date = datetime.strptime(args.start, '%Y-%m-%d')
        except ValueError:
            print(f"[ERROR] Invalid start date format: {args.start}")
            print("Please use YYYY-MM-DD format")
            return
    
    if args.end:
        try:
            end_date = datetime.strptime(args.end, '%Y-%m-%d')
        except ValueError:
            print(f"[ERROR] Invalid end date format: {args.end}")
            print("Please use YYYY-MM-DD format")
            return
    
    # 実行
    daily_update(start_date, end_date, args.delay)


if __name__ == "__main__":
    main()
