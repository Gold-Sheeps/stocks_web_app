import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from app.services.stock_detail_service import StockDetailService

try:
    print("Initializing StockDetailService...")
    service = StockDetailService()
    
    symbol = "NVDA"
    print(f"Calling get_stock_detail({symbol})...")
    
    # Check what DB it is connecting to
    # We can't access service.db.connection directly easily if it's protected, but let's try
    # Adding debug print inside service would be better, but let's see result first
    
    result = service.get_stock_detail(symbol)
    
    print(f"Result Name: {result.stock_info.name}")
    print(f"Result Indicators: {result.indicators}")
    
    if result.indicators.high_52w is None:
        print("WARNING: high_52w is None!")
        # Let's verify DB directly here using same Database class
        print("Verifying DB content with same Database class...")
        db = service.db
        chk = db.execute_query("SELECT count(*) FROM indicator_daily WHERE symbol_key = 'NVDA'")
        print(f"Count in DB: {chk}")
    
except Exception as e:
    print(f"EXCEPTION: {e}")
    import traceback
    traceback.print_exc()
