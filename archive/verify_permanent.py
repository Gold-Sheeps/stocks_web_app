
import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from app.db.database import Database

def verify_permanent_fix():
    db = Database()
    db.connect()
    
    print("=== Verification of Permanent Fix ===")
    
    # 1. Date Distribution (Confirming what dates exist)
    print("\n1. Date Distribution:")
    q1 = "SELECT trading_date, COUNT(*) FROM indicator_daily GROUP BY trading_date ORDER BY trading_date DESC LIMIT 5"
    rows = db.execute_query(q1)
    for r in rows:
        print(f"  {r[0]}: {r[1]}")

    # 2. RS Stats for latest significant day
    print("\n2. RS Stats for latest significant day (Feb 10):")
    q2 = """
        SELECT MIN(rs_score), AVG(rs_score), MAX(rs_score) 
        FROM indicator_daily 
        WHERE trading_date = '2026-02-10'
    """
    stats = db.execute_query(q2)[0]
    print(f"  Min={stats[0]}, Avg={stats[1]}, Max={stats[2]}")
    
    if stats[2] >= 90:
        print("  [OK] Max RS Score is valid (>= 90)")
    else:
        print("  [FAIL] Max RS Score is too low")

    # 3. RS distribution check
    print("\n3. High RS Count (>=90) for Feb 10:")
    q3 = "SELECT COUNT(*) FROM indicator_daily WHERE trading_date = '2026-02-10' AND rs_score >= 90"
    count = db.execute_query(q3)[0][0]
    print(f"  Count: {count}")

    db.disconnect()

if __name__ == "__main__":
    verify_permanent_fix()
