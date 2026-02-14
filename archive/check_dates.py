
import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from app.db.database import Database

def check_dates():
    db = Database()
    db.connect()
    print("=== Date Distribution in Indicator Daily ===")
    query = """
        SELECT trading_date, COUNT(*) 
        FROM indicator_daily 
        GROUP BY trading_date 
        ORDER BY trading_date DESC 
        LIMIT 10
    """
    rows = db.execute_query(query)
    for r in rows:
        print(f"Date: {r[0]}, Count: {r[1]}")
    db.disconnect()

if __name__ == "__main__":
    check_dates()
