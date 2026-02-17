"""
Simple US Stock Bulk Import Script (Simplified Version)
"""

import sys
import csv
import time
from datetime import datetime, timedelta
from decimal import Decimal
import yfinance as yf
from tqdm import tqdm
import postgresql_connect


def read_tickers_simple(csv_path: str, limit: int = None):
    """Read tickers from CSV - simplified version"""
    tickers = []
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            
            for row in reader:
                if not row or len(row) < 1:
                    continue
                
                symbol = row[0].strip()
                
                # Skip empty or header rows
                if not symbol or len(symbol) > 10 or not symbol[0].isalpha():
                    continue
                
                tickers.append({
                    'symbol': symbol,
                    'name': row[1] if len(row) > 1 else symbol,
                    'market': 'US',
                    'currency': 'USD'
                })
                
                if limit and len(tickers) >= limit:
                    break
        
        print(f"[OK] Loaded {len(tickers)} tickers from {csv_path}")
        return tickers
    
    except Exception as e:
        print(f"[ERR] Failed to read CSV: {e}")
        import traceback
        traceback.print_exc()
        return []


def insert_instrument(db, symbol: str, name: str, market: str, currency: str):
    """Insert instrument into DB"""
    try:
        db.command("""
            INSERT INTO instruments (symbol_key, market, name, currency, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
            ON CONFLICT (symbol_key) 
            DO UPDATE SET 
                name = EXCLUDED.name,
                updated_at = CURRENT_TIMESTAMP
        """, (symbol, market, name, currency))
        return True
    except Exception as e:
        print(f"  [ERR] Failed to insert {symbol}: {e}")
        return False


def fetch_and_insert_prices(db, symbol: str, years: int = 10):
    """Fetch and insert price data"""
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years * 365)
        
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


def bulk_import(csv_path: str, limit: int = None, delay: float = 2.0):
    """Main import function"""
    print("=" * 60)
    print("US Stock Bulk Import - 10 Year Historical Data")
    print("=" * 60)
    
    db = postgresql_connect.PostgreSQLConnect()
    
    if not db.connect():
        print("[ERROR] Failed to connect to database")
        return
    
    tickers = read_tickers_simple(csv_path, limit=limit)
    if not tickers:
        print("[ERROR] No tickers to import")
        return
    
    print(f"\n[INFO] Starting import of {len(tickers)} stocks...")
    print(f"[INFO] Delay: {delay}s between requests\n")
    
    success = 0
    errors = []
    
    for ticker_info in tqdm(tickers, desc="Importing", unit="stock"):
        symbol = ticker_info['symbol']
        name = ticker_info['name']
        
        try:
            if not insert_instrument(db, symbol, name, 'US', 'USD'):
                errors.append({'symbol': symbol, 'error': 'DB insert failed'})
                continue
            
            rows, error = fetch_and_insert_prices(db, symbol, years=10)
            
            if error:
                errors.append({'symbol': symbol, 'error': error})
            else:
                success += 1
                tqdm.write(f"  [OK] {symbol}: {rows} rows")
            
            time.sleep(delay)
        
        except Exception as e:
            errors.append({'symbol': symbol, 'error': str(e)})
            tqdm.write(f"  [ERR] {symbol}: {e}")
    
    print("\n" + "=" * 60)
    print("Import Complete")
    print("=" * 60)
    print(f"Total: {len(tickers)}")
    print(f"Success: {success}")
    print(f"Failed: {len(errors)}")
    
    if errors:
        print(f"\nFirst 10 errors:")
        for err in errors[:10]:
            print(f"  {err['symbol']}: {err['error']}")
    
    db.disconnect()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true', help='Test with 5 stocks')
    parser.add_argument('--limit', type=int, help='Limit number of stocks')
    parser.add_argument('--delay', type=float, default=2.0, help='Delay (seconds)')
    
    args = parser.parse_args()
    
    csv_path = 'stocks_list/Monex_US_LIST.csv'
    
    if args.test:
        print("[TEST MODE] First 5 stocks\n")
        bulk_import(csv_path, limit=5, delay=args.delay)
    elif args.limit:
        bulk_import(csv_path, limit=args.limit, delay=args.delay)
    else:
        print("[FULL MODE]\n")
        response = input("This will take ~3 hours. Continue[WARN] (yes/no): ")
        if response.lower() == 'yes':
            bulk_import(csv_path, delay=args.delay)
        else:
            print("Cancelled.")

