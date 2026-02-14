
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.db.database import Database

def check_markets():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        print("=== Checking Distinct Markets ===")
        rows = db.execute_query("SELECT DISTINCT market FROM instruments")
        if rows:
            for r in rows:
                print(f"- {r[0]}")
        else:
            print("No markets found.")
            
        print("\n=== Checking Instruments Columns ===")
        cols = db.execute_query("SELECT column_name, is_nullable, data_type FROM information_schema.columns WHERE table_name='instruments'")
        for c in cols:
             print(f"- {c[0]} ({c[2]}, Nullable: {c[1]})")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_markets()
