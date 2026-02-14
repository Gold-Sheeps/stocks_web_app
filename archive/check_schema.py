
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.db.database import Database

def check_schema():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        print("=== Tables ===")
        tables = db.execute_query("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        for t in tables:
            print(f"- {t[0]}")
            
            # Check columns for specific tables of interest
            if t[0] in ['sector_rotation', 'fundamentals', 'instruments', 'price_daily']:
                print(f"  Columns for {t[0]}:")
                cols = db.execute_query(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='{t[0]}'")
                for c in cols:
                     print(f"    - {c[0]} ({c[1]})")

    finally:
        db.disconnect()

if __name__ == "__main__":
    check_schema()
