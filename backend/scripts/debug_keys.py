
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def debug_keys():
    db = Database()
    db.connect()
    try:
        # Check specific expected keys
        targets = ['US:^DJI', 'US:^GSPC', 'US:^IXIC', 'US:^N225', 'US:USDJPY=X', 'US:XLK', 'US:SPY', 'US:GLD', 'US:SLV', 'FX:USDJPY=X']
        
        print("\n--- Exact Key Match ---")
        placeholders = ','.join(['%s'] * len(targets))
        query = f"SELECT symbol_key, name, market FROM instruments WHERE symbol_key IN ({placeholders})"
        rows = db.execute_query(query, tuple(targets))
        found = set()
        for r in rows:
            print(f"Found: Key={r[0]}, Name={r[1]}, Market={r[2]}")
            found.add(r[0])
            
        # Check for non-standard keys (no colon)
        print("\n--- Non-Standard Keys (No Colon) ---")
        query_bad = "SELECT symbol_key, name, market FROM instruments WHERE symbol_key NOT LIKE '%:%' LIMIT 10"
        rows_bad = db.execute_query(query_bad)
        for r in rows_bad:
            print(f"BAD KEY: {r[0]}")

    finally:
        db.disconnect()

if __name__ == "__main__":
    debug_keys()
