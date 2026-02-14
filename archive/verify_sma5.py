import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from app.db.database import Database

def main():
    print("Verifying sma5 for NVDA...")
    db = Database()
    if db.connect():
        # Get latest date and sma5
        query = "SELECT trading_date, sma5, sma20, high_52w FROM indicator_daily WHERE symbol_key = 'NVDA' ORDER BY trading_date DESC LIMIT 3"
        rows = db.execute_query(query)
        print("Top 3 rows (Date, SMA5, SMA20, High52):")
        if rows:
            for row in rows:
                print(f"Date: {row[0]}, SMA5: {row[1]}, SMA20: {row[2]}, High52: {row[3]}")
        else:
            print("No rows found.")
        db.disconnect()

if __name__ == "__main__":
    main()
