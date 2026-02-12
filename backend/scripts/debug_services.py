
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.services.stock_detail_service import StockDetailService
from app.services.screener_service import ScreenerService
from app.db.database import Database

def debug_services():
    print("--- Debugging StockDetailService ---")
    service = StockDetailService()
    
    # Test 1: Raw Symbol 'AAPL' (simulating old frontend behavior)
    print("\nTest 1: get_stock_detail('AAPL')")
    try:
        res = service.get_stock_detail('AAPL')
        print(f"Result Name: {res.stock_info.name}")
        print(f"Result Price: {res.stock_info.current_price}")
        print(f"Price History Len: {len(res.price_history)}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 2: Prefixed Symbol 'US:AAPL' (correct behavior)
    print("\nTest 2: get_stock_detail('US:AAPL')")
    try:
        res = service.get_stock_detail('US:AAPL')
        print(f"Result Name: {res.stock_info.name}")
        print(f"Result Price: {res.stock_info.current_price}")
        print(f"Price History Len: {len(res.price_history)}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- Debugging ScreenerService ---")
    screener = ScreenerService()
    
    # Test 3: Scan Stocks
    print("\nTest 3: scan_stocks(limit=5)")
    try:
        # Relaxing criteria just to see if ANY data returns
        # The service hardcodes "rs_score >= 85" in query.
        # Let's see if that returns anything.
        res = screener.scan_stocks(limit=5)
        print(f"Total Stocks: {res.get('total_stocks')}")
        print(f"Results Count: {len(res.get('results'))}")
        for r in res.get('results', []):
            print(f" - {r['symbol']}: {r['total_score']}")
            
    except Exception as e:
        print(f"Error: {e}")

    # Test 4: Check if indicator data exists
    print("\nTest 4: Check indicator_daily table")
    db = Database()
    db.connect()
    try:
        cnt = db.execute_query("SELECT count(*) FROM indicator_daily")
        print(f"Total Indicator Rows: {cnt[0][0]}")
        
        # Check RS Score distribution
        rs_cnt = db.execute_query("SELECT count(*) FROM indicator_daily WHERE rs_score >= 85")
        print(f"Rows with RS Score >= 85: {rs_cnt[0][0]}")
        
    finally:
        db.disconnect()

if __name__ == "__main__":
    debug_services()
