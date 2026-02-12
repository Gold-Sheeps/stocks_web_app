
import sys
import requests
import json
from pathlib import Path
from decimal import Decimal
import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from app.db.database import Database

def collect_full_evidence():
    print("=== FULL EVIDENCE COLLECTION START ===")
    db = Database()
    db.connect()
    
    # B. Global "100% Unified" Proof
    print("\n--- B. Global Key Unification Proof ---")
    
    sql_legacy_price = """
    SELECT COUNT(*) 
    FROM price_daily
    WHERE symbol_key NOT LIKE 'US:%' AND symbol_key NOT LIKE 'JP:%';
    """
    row_p = db.execute_query(sql_legacy_price)
    count_p = row_p[0][0] if row_p else -1
    print(f"Legacy Keys in price_daily (Expected 0): {count_p}")
    
    sql_legacy_ind = """
    SELECT COUNT(*) 
    FROM indicator_daily
    WHERE symbol_key NOT LIKE 'US:%' AND symbol_key NOT LIKE 'JP:%';
    """
    row_i = db.execute_query(sql_legacy_ind)
    count_i = row_i[0][0] if row_i else -1
    print(f"Legacy Keys in indicator_daily (Expected 0): {count_i}")

    # Find a stock with valid price data for "Positive" proof
    # We saw US:RGC or US:AMPY earlier. Let's find one with recent price.
    sql_valid_stock = """
    SELECT symbol_key, close 
    FROM price_daily 
    WHERE trading_date >= DATE(NOW()) - INTERVAL '5 days' 
    LIMIT 1;
    """
    row_valid = db.execute_query(sql_valid_stock)
    valid_symbol = row_valid[0][0] if row_valid else "US:AAPL" # Fallback, likely won't work if no data
    print(f"\nSelected Valid Stock for Positive Proof: {valid_symbol} (Price: {row_valid[0][1] if row_valid else 'N/A'})")

    # D. Data Freshness / System Logs
    print("\n--- D. Data Freshness & Logs ---")
    
    sql_logs = """
    SELECT status, message, created_at 
    FROM system_logs 
    ORDER BY created_at DESC 
    LIMIT 1;
    """
    row_log = db.execute_query(sql_logs)
    if row_log:
        print(f"Latest System Log: Status={row_log[0][0]}, Msg={row_log[0][1]}, At={row_log[0][2]}")
    else:
        print("Latest System Log: (None)")

    sql_max_date_price = "SELECT MAX(trading_date) FROM price_daily;"
    row_max_p = db.execute_query(sql_max_date_price)
    print(f"Max Date in price_daily: {row_max_p[0][0] if row_max_p else 'None'}")

    sql_max_date_sector = "SELECT MAX(trading_date) FROM sector_rotation;"
    row_max_s = db.execute_query(sql_max_date_sector)
    print(f"Max Date in sector_rotation: {row_max_s[0][0] if row_max_s else 'None'}")
    
    db.disconnect()
    
    # 2. API Verification
    print("\n--- API Verification ---")
    base_url = "http://localhost:8000/api/v1"
    
    # A. StockDetail "Positive" Proof
    print(f"\n[A. Positive StockDetail Proof: GET /stock/{valid_symbol}]")
    try:
        res = requests.get(f"{base_url}/stock/{valid_symbol}")
        if res.status_code == 200:
            data = res.json()
            info = data.get("stock_info", {})
            price = info.get("current_price")
            print(f"Symbol: {info.get('symbol')}")
            print(f"Current Price: {price} (Should be valid number)")
            if price is not None and price > 0:
                print("result: PASS (Price returned)")
            else:
                print("result: FAIL (Price is None or 0)")
        else:
            print(f"Error: {res.status_code} {res.text}")
    except Exception as e:
        print(f"Request failed: {e}")

    # StockDetail "Negative" Proof (US:NVDA)
    print(f"\n[Negative StockDetail Proof: GET /stock/US:NVDA]")
    try:
        res = requests.get(f"{base_url}/stock/US:NVDA")
        if res.status_code == 200:
            data = res.json()
            info = data.get("stock_info", {})
            price = info.get("current_price")
            print(f"Symbol: {info.get('symbol')}")
            print(f"Current Price: {price} (Should be None)")
            if price is None:
                print("result: PASS (Price is None as expected)")
            else:
                print("result: FAIL (Price is not None)")
        else:
            print(f"Error: {res.status_code} {res.text}")
    except Exception as e:
        print(f"Request failed: {e}")

    # C. Screener Multi-hit Proof
    # Relaxed criteria for demonstration
    limit = 10
    # Use min_rs=0 to verify data existence regardless of current performance
    url_screener = f"{base_url}/screener/scan?limit={limit}&min_rs=0&min_total_score=0"
    print(f"\n[C. Screener Multi-hit Proof: GET {url_screener}]")
    try:
        res = requests.get(url_screener)
        if res.status_code == 200:
            data = res.json()
            count = data.get("total_count", 0)
            results = data.get("results", [])
            print(f"Total Count: {count}")
            print(f"Returned: {len(results)}")
            
            if len(results) > 0:
                print("Top 3 Results:")
                for r in results[:3]:
                    print(f"- {r.get('symbol')}: Price={r.get('price')}, Score={r.get('total_score')}")
                if len(results) >= 2: # At least plural
                     print("result: PASS (Multiple results found)")
                else:
                     print("result: WEAK PASS (Only 1 result)")
            else:
                print("result: FAIL (No results)")
        else:
            print(f"Error: {res.status_code} {res.text}")
    except Exception as e:
        print(f"Request failed: {e}")

    print("\n=== FULL EVIDENCE COLLECTION END ===")

if __name__ == "__main__":
    collect_full_evidence()
