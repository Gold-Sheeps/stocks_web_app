
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def debug_dates():
    db = Database()
    db.connect()
    
    target = 'US:AAPL'
    print(f"--- Debugging {target} ---")
    
    # 1. Price Max Date
    res1 = db.execute_query("SELECT MAX(trading_date) FROM price_daily WHERE symbol_key=%s", (target,))
    print(f"Price Max Date: {res1[0][0]}")

    # 2. Indicator Max Date
    res2 = db.execute_query("SELECT MAX(trading_date) FROM indicator_daily WHERE symbol_key=%s", (target,))
    print(f"Indicator Max Date: {res2[0][0]}")
    
    # 3. RS Score on Max Indicator Date
    if res2[0][0]:
        res3 = db.execute_query("SELECT rs_score FROM indicator_daily WHERE symbol_key=%s AND trading_date=%s", (target, res2[0][0]))
        print(f"RS Score on {res2[0][0]}: {res3[0][0]}")
        
    db.disconnect()

if __name__ == "__main__":
    debug_dates()
