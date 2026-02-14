
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.abspath('backend'))

from app.db.database import Database

def discover():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    tables = ['price_daily', 'indicator_daily', 'instruments']
    with open('db_columns.txt', 'w', encoding='utf-8') as f:
        for t in tables:
            f.write(f"\n--- {t} ---\n")
            q = f"SELECT column_name FROM information_schema.columns WHERE table_name = '{t}' ORDER BY ordinal_position"
            res = db.execute_query(q)
            if res:
                for row in res:
                    f.write(f"{row[0]}\n")
            else:
                f.write("No columns found\n")
    
    db.disconnect()
    print("Done. Saved to db_columns.txt")

if __name__ == "__main__":
    discover()
