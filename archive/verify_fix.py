
import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from app.db.database import Database
import datetime

def verify_fix():
    db = Database()
    db.connect()
    
    # Check Feb 10 (Main data)
    target_date = datetime.date(2026, 2, 10)
    print(f"=== Verification for {target_date} ===")
    
    count_total = db.execute_query("SELECT COUNT(*) FROM indicator_daily WHERE trading_date = %s", (target_date,))[0][0]
    count_high_rs = db.execute_query("SELECT COUNT(*) FROM indicator_daily WHERE trading_date = %s AND rs_score >= 80", (target_date,))[0][0]
    
    print(f"Total Records: {count_total}")
    print(f"High RS (>=80): {count_high_rs}")
    
    if count_high_rs > 0:
        print("SUCCESS: High RS scores exist.")
        # Show sample
        rows = db.execute_query("SELECT symbol_key, rs_score FROM indicator_daily WHERE trading_date = %s AND rs_score >= 98 LIMIT 5", (target_date,))
        for r in rows:
            print(f"  {r[0]}: {r[1]}")
    else:
        print("FAILURE: No high RS scores.")

    db.disconnect()

if __name__ == "__main__":
    verify_fix()
