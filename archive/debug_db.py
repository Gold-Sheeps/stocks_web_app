import sys
import os
sys.path.append(os.path.abspath("backend"))

from app.db.database import Database

def check_db():
    print("Connecting to DB...")
    db = Database()
    if not db.connect():
        print("Failed to connect.")
        return

    print("Checking watchlist table...")
    try:
        rows = db.execute_query("SELECT * FROM watchlist")
        print(f"Watchlist rows: {len(rows) if rows else 0}")
        if rows:
            for row in rows:
                print(row)
        else:
            print("Table is empty.")
            
        # Debug: Check if table exists
        check_table = db.execute_query("SELECT to_regclass('public.watchlist')")
        print(f"Table exists check: {check_table}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_db()
