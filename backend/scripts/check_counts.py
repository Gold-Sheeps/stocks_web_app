
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def check_counts():
    db = Database()
    db.connect()
    try:
        res = db.execute_query("SELECT COUNT(DISTINCT symbol_key) FROM indicator_daily")
        print(f"Unique Symbols in indicator_daily: {res[0][0]}")
        
        res = db.execute_query("SELECT symbol_key, COUNT(*) FROM indicator_daily GROUP BY symbol_key ORDER BY count(*) DESC LIMIT 10")
        print("Top 10 symbols by row count:")
        for r in res:
            print(f"{r[0]}: {r[1]}")
            
    except Exception as e:
        print(e)
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_counts()
