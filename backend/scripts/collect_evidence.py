
import sys
import requests
import json
from pathlib import Path
from decimal import Decimal
import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from app.db.database import Database

def json_serial(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")

def collect_evidence():
    print("=== EVIDENCE COLLECTION START ===")
    db = Database()
    db.connect()
    
    # 1. SQL Evidence
    print("\n--- 1. SQL Evidence (US:NVDA vs NVDA) ---")
    
    sql_price = """
    SELECT symbol_key, COUNT(*) cnt, MIN(trading_date) min_d, MAX(trading_date) max_d
    FROM price_daily
    WHERE symbol_key IN ('US:NVDA','NVDA')
    GROUP BY symbol_key;
    """
    print("\n[Price Daily]")
    rows = db.execute_query(sql_price)
    if rows:
        for r in rows:
            print(f"Key: {r[0]} | Count: {r[1]} | Range: {r[2]} ~ {r[3]}")
    else:
        print("(No results found)")
        
    sql_indicator = """
    SELECT symbol_key, COUNT(*) cnt, MIN(trading_date) min_d, MAX(trading_date) max_d
    FROM indicator_daily
    WHERE symbol_key IN ('US:NVDA','NVDA')
    GROUP BY symbol_key;
    """
    print("\n[Indicator Daily]")
    rows = db.execute_query(sql_indicator)
    if rows:
        for r in rows:
            print(f"Key: {r[0]} | Count: {r[1]} | Range: {r[2]} ~ {r[3]}")
    else:
        print("(No results found)")

    sql_inst = """
    SELECT symbol_key, market, name, currency
    FROM instruments
    WHERE symbol_key IN ('US:NVDA','NVDA');
    """
    print("\n[Instruments]")
    rows = db.execute_query(sql_inst)
    if rows:
        for r in rows:
            print(f"Key: {r[0]} | Market: {r[1]} | Name: {r[2]}")
    else:
        print("(No results found)")

    # Check for any high RS stock to verify Screener
    sql_high_rs = "SELECT symbol_key, rs_score FROM indicator_daily WHERE rs_score >= 85 LIMIT 1"
    print("\n[High RS Stock Check]")
    rows = db.execute_query(sql_high_rs)
    if rows:
        print(f"Found Candidate: {rows[0][0]} with RS={rows[0][1]}")
    else:
        print("(No stocks with RS >= 85 found in DB - Screener will be empty)")

    db.disconnect()
    
    # 2. API Verification
    print("\n--- 2. API Verification ---")
    base_url = "http://localhost:8000/api/v1"
    
    # StockDetail
    target = "US:NVDA"
    url_stock = f"{base_url}/stock/{target}"
    print(f"\n[GET {url_stock}]")
    try:
        res = requests.get(url_stock)
        print(f"Status: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            info = data.get("stock_info", {})
            ind = data.get("indicators", {})
            print(f"Symbol: {info.get('symbol')}")
            print(f"Price: {info.get('current_price')} (Should be valid or null, not 0.00)")
            print(f"RSI: {ind.get('rsi')}")
            print(f"SMA200: {ind.get('sma200')}")
        else:
            print(f"Error: {res.text}")
    except Exception as e:
        print(f"Request failed: {e}")

    # Screener
    url_screener = f"{base_url}/screener/scan[WARN]limit=5&min_rs=60&min_total_score=0" 
    print(f"\n[GET {url_screener}]")
    try:
        res = requests.get(url_screener)
        print(f"Status: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            print(f"Total Count: {data.get('total_count')}")
            results = data.get("results", [])
            print(f"Results returned: {len(results)}")
            for r in results:
                print(f"- {r.get('symbol')}: Price={r.get('price')}, Score={r.get('total_score', 'N/A')}")
        else:
            print(f"Error: {res.text}")
    except Exception as e:
        print(f"Request failed: {e}")

    print("\n=== EVIDENCE COLLECTION END ===")

if __name__ == "__main__":
    collect_evidence()
