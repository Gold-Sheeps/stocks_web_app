
import sys
from pathlib import Path
from decimal import Decimal
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.services.stock_detail_service import StockDetailService
from app.services.screener_service import ScreenerService
from app.db.database import Database

def verify_fixes():
    print("--- Verifying Fixes per Acceptance Criteria ---")
    
    # 1. StockDetail for US:NVDA
    print("\n1. StockDetail Check (US:NVDA)")
    detail_svc = StockDetailService()
    # Check both inputs
    try:
        resp = detail_svc.get_stock_detail("US:NVDA")
        info = resp.stock_info
        
        print(f"Symbol: {info.symbol}")
        print(f"Current Price: {info.current_price}")
        print(f"Indicators: {resp.indicators}")
        
        # Criteria: current_price != 0.00
        if info.current_price == Decimal("0"):
            print("FAIL: Current Price is 0.00")
        elif info.current_price is None:
             print("INFO: Current Price is None (Data missing?) - Better than 0.00")
        else:
            print("PASS: Current Price is valid")

        # Criteria: SMA/EMA/RSI/ATR are valid or NULL
        ind = resp.indicators
        print(f"RSI: {ind.rsi}, SMA200: {ind.sma200}")
        
        # Criteria: 52W Dist != -100%
        print(f"52W Dist: {ind.dist_52w_high_pct}")
        if ind.dist_52w_high_pct == Decimal("-100"):
             print("FAIL: 52W Dist is -100%")
        else:
             print("PASS: 52W Dist seems valid")
             
    except Exception as e:
        print(f"FAIL: StockDetail Exception: {e}")

    # 2. Screener Check
    print("\n2. Screener Check")
    screener_svc = ScreenerService()
    try:
        # We don't expect many results yet since we only ran indicators for targets
        results = screener_svc.scan_stocks(limit=10)
        print(f"Screener Results Count: {results.total_count}")
        for r in results.results:
            print(f"- {r.symbol}: RS={r.rs_score}, Price={r.price}")
            
        print("PASS: Screener ran without error")
    except Exception as e:
        print(f"FAIL: Screener Exception: {e}")
        
    # 3. DB Check (Keys)
    print("\n3. DB Key Consistency Check")
    db = Database()
    db.connect()
    try:
        # check for 'NVDA' in indicator_daily
        bad = db.execute_query("SELECT count(*) FROM indicator_daily WHERE symbol_key = 'NVDA'")
        if bad and bad[0][0] > 0:
            print(f"FAIL: Found {bad[0][0]} rows with 'NVDA' in indicator_daily")
        else:
             print("PASS: No legacy 'NVDA' keys in indicator_daily")
    finally:
        db.disconnect()

if __name__ == "__main__":
    verify_fixes()
