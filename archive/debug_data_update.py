
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.db.database import Database

def debug_data():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        print("=== System Logs (Last 10) ===")
        logs = db.execute_query("SELECT created_at, job_name, status, message, details FROM system_logs ORDER BY created_at DESC LIMIT 10")
        for log in logs:
            print(f"[{log[0]}] {log[1]} ({log[2]}): {log[3]}")
            if log[4]:
                print(f"   Details: {log[4]}")
        
        print("\n=== Price Data Count ===")
        # Check XLE and ^GSPC
        for sym in ["XLE", "^GSPC"]:
            cnt = db.execute_query("SELECT COUNT(*) FROM price_daily WHERE symbol_key = %s", (sym,))
            print(f"{sym}: {cnt[0][0]} records")
            
            # Check range
            dates = db.execute_query("SELECT MIN(trading_date), MAX(trading_date) FROM price_daily WHERE symbol_key = %s", (sym,))
            print(f"   Range: {dates[0][0]} to {dates[0][1]}")

    finally:
        db.disconnect()

if __name__ == "__main__":
    debug_data()
