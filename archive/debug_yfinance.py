
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def test_yfinance():
    symbols = ["^GSPC", "XLE"]
    end = datetime.now()
    start = end - timedelta(days=30)
    
    print(f"Downloading {symbols} from {start.date()} to {end.date()}...")
    
    try:
        # Test 1: Batch download
        df = yf.download(symbols, start=start, end=end, group_by='ticker', auto_adjust=False, progress=False, threads=True)
        print("\n=== Batch Download Result ===")
        print(f"Is Empty: {df.empty}")
        print(f"Columns: {df.columns}")
        
        if not df.empty:
            for sym in symbols:
                try:
                    sym_df = df[sym]
                    print(f"\n--- {sym} Data ---")
                    print(sym_df.head())
                    print(f"Rows: {len(sym_df)}")
                except KeyError:
                    print(f"{sym} not found in columns")

        # Test 2: Single Ticker
        print("\n=== Single Ticker (^GSPC) ===")
        t = yf.Ticker("^GSPC")
        hist = t.history(period="1mo")
        print(hist.head())
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_yfinance()
