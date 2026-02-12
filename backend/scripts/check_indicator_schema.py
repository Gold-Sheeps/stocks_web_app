
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def check_schema():
    db = Database()
    db.connect()
    try:
        query = "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='indicator_daily'"
        rows = db.execute_query(query)
        print("Column | Is Nullable")
        print("-------|------------")
        for r in rows:
            print(f"{r[0]} | {r[1]}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_schema()
