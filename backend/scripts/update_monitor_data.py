import sys
import os
import yfinance as yf
import psycopg
from datetime import datetime, timedelta
from decimal import Decimal

# Add backend directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from app.core.config import settings

# Target Symbols and their Prefixes
TARGETS = {
    "indices": [
        {"yf_sym": "^DJI", "prefix": "US:"},
        {"yf_sym": "^GSPC", "prefix": "US:"},
        {"yf_sym": "^IXIC", "prefix": "US:"},
        {"yf_sym": "^RUT", "prefix": "US:"},
        {"yf_sym": "^N225", "prefix": "JP:"},
        {"yf_sym": "^VIX", "prefix": "US:"},
    ],
    "rates": [
        {"yf_sym": "^TNX", "prefix": "US:"},
        {"yf_sym": "^FVX", "prefix": "US:"},
        {"yf_sym": "^IRX", "prefix": "US:"},
    ],
    "forex": [
        {"yf_sym": "USDJPY=X", "prefix": "US:"},
        {"yf_sym": "EURUSD=X", "prefix": "US:"},
        {"yf_sym": "GBPUSD=X", "prefix": "US:"},
    ],
    "commodities": [
        {"yf_sym": "GLD", "prefix": "US:"},
        {"yf_sym": "SLV", "prefix": "US:"},
        {"yf_sym": "CL=F", "prefix": "US:"},
        {"yf_sym": "PL=F", "prefix": "US:"},
        {"yf_sym": "HG=F", "prefix": "US:"},
        {"yf_sym": "PA=F", "prefix": "US:"},
    ],
    "crypto": [
        {"yf_sym": "BTC-USD", "prefix": ""},
        {"yf_sym": "ETH-USD", "prefix": ""},
    ]
}

def update_monitor_data():
    """Fetch data from yfinance and upsert to price_daily table"""
    print(f"Starting Monitor Data Update: {datetime.now()}")
    
    try:
        conn = psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        cursor = conn.cursor()
        
        # Flatten all tickers for batch download
        all_yf_tickers = []
        ticker_map = {} # yf_sym -> db_sym
        for category, items in TARGETS.items():
            for item in items:
                yf_sym = item["yf_sym"]
                db_sym = f"{item['prefix']}{yf_sym}"
                all_yf_tickers.append(yf_sym)
                ticker_map[yf_sym] = db_sym

        print(f"Fetching data for {len(all_yf_tickers)} tickers...")
        
        # Download last 7 days of daily data
        data = yf.download(all_yf_tickers, period="7d", interval="1d", group_by='ticker', auto_adjust=False)

        upsert_count = 0
        for yf_sym, db_sym in ticker_map.items():
            try:
                # Handle results: yf.download returns a MultiIndex DataFrame if multiple tickers are requested
                if len(all_yf_tickers) > 1:
                    if yf_sym in data.columns.levels[0]:
                        df = data[yf_sym].dropna(subset=['Close'])
                    else:
                        print(f"Warning: No column for {yf_sym} in download results")
                        continue
                else:
                    df = data.dropna(subset=['Close'])
                
                if df.empty:
                    print(f"Warning: No data for {yf_sym}")
                    continue

                for timestamp, row in df.iterrows():
                    trading_date = timestamp.date()
                    # Convert to Decimal for database
                    o = Decimal(str(row['Open']))
                    h = Decimal(str(row['High']))
                    l = Decimal(str(row['Low']))
                    c = Decimal(str(row['Close']))
                    
                    # Handle volume: some tickers (like rates/yields) might have NaN volume
                    try:
                        v = int(row['Volume']) if 'Volume' in row and not hasattr(row['Volume'], '__iter__') and row['Volume'] == row['Volume'] else 0
                    except:
                        v = 0
                    
                    # Upsert query (using symbol_key_old_backup for legacy constraint compliance)
                    cursor.execute("""
                        INSERT INTO price_daily (symbol_key, symbol_key_old_backup, trading_date, open, high, low, close, volume, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol_key, trading_date) DO UPDATE SET
                            symbol_key_old_backup = EXCLUDED.symbol_key_old_backup,
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            updated_at = EXCLUDED.updated_at
                    """, (db_sym, db_sym, trading_date, o, h, l, c, v, datetime.now()))
                    upsert_count += 1
                
                print(f"Updated {db_sym} ({len(df)} rows)")
            except Exception as e:
                print(f"Error processing {yf_sym}: {e}")

        conn.commit()
        print(f"Total rows upserted: {upsert_count}")
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Fatal Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    update_monitor_data()
