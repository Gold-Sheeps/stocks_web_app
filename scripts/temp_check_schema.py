import sys
import os
sys.path.append(os.getcwd())
from app.db.database import Database

def check_schema():
    db = Database()
    db.connect()
    try:
        print("--- indicator_daily ---")
        rows = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'indicator_daily'")
        for r in rows:
            print(r[0])
            
        print("\n--- price_daily ---")
        rows = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'price_daily'")
        for r in rows:
            print(r[0])
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_schema()
