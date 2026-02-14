
import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from app.db.database import Database

def verify_latest_dist():
    db = Database()
    db.connect()
    
    query = """
    WITH latest AS (
      SELECT trading_date
      FROM indicator_daily
      GROUP BY trading_date
      HAVING COUNT(*) >= 100
      ORDER BY trading_date DESC
      LIMIT 1
    )
    SELECT d.trading_date,
           MAX(d.rs_score) AS max_rs,
           COUNT(CASE WHEN d.rs_score >= 90 THEN 1 END) AS high_rs_count
    FROM indicator_daily d
    JOIN latest l ON d.trading_date = l.trading_date
    GROUP BY d.trading_date;
    """
    
    try:
        results = db.execute_query(query)
        print("=== RS Distribution for Latest Valid Day ===")
        print(f"Date: {results[0][0]}")
        print(f"Max RS: {results[0][1]}")
        print(f"High RS Count (>=90): {results[0][2]}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    verify_latest_dist()
