
import sys
import os
from decimal import Decimal

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.db.database import Database

def check_sector_data():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        print("=== Checking Sector Rotation Data ===")
        rows = db.execute_query("SELECT symbol, trading_date, current_return, momentum, relative_strength, rank FROM sector_rotation ORDER BY rank LIMIT 11")
        
        if not rows:
            print("No data found in sector_rotation table.")
            return

        print(f"Found {len(rows)} records. Top sectors:")
        for r in rows:
            print(f"Rank {r[5]}: {r[0]} (Ret={r[2]}, Mom={r[3]}, RS={r[4]})")
            
        print("=== Verification Complete ===")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_sector_data()
