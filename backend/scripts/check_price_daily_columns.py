
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def check_pd():
    db = Database()
    db.connect()
    try:
        # Check columns
        rows = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name='price_daily'")
        cols = [r[0] for r in rows]
        print(f"Columns in price_daily: {cols}")
        
        # Check data sample
        if 'symbol' in cols:
             rows = db.execute_query("SELECT symbol FROM price_daily LIMIT 5")
             print(f"Sample symbol: {rows}")
        if 'symbol_key' in cols:
             rows = db.execute_query("SELECT symbol_key FROM price_daily LIMIT 5")
             print(f"Sample symbol_key: {rows}")

    finally:
        db.disconnect()

if __name__ == "__main__":
    check_pd()
