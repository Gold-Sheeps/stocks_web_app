
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def check_col():
    db = Database()
    db.connect()
    try:
        res = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'instruments'")
        cols = [r[0] for r in res]
        print(f"Columns: {cols}")
        if 'asset_type' in cols:
            print("Found asset_type")
        else:
            print("asset_type NOT found")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_col()
