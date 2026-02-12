
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.services.indicator_service import IndicatorService
from app.db.database import Database

def debug_indicator_run():
    print("--- Debugging IndicatorService ---")
    svc = IndicatorService()
    target = "US:NVDA"
    
    print(f"Calculating for {target}...")
    svc.calculate_and_save_indicators(target)
    
    print("Checking DB...")
    db = Database()
    db.connect()
    try:
        cnt = db.execute_query("SELECT count(*) FROM indicator_daily WHERE symbol_key=%s", (target,))
        print(f"Rows for {target}: {cnt[0][0]}")
        
        # Check specific values
        if cnt[0][0] > 0:
            row = db.execute_query("SELECT trading_date, rsi14, rs_score FROM indicator_daily WHERE symbol_key=%s ORDER BY trading_date DESC LIMIT 1", (target,))
            print(f"Latest Data: {row[0]}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    debug_indicator_run()
