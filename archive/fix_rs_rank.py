
import sys
import os
import pandas as pd
from decimal import Decimal

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.db.database import Database

def recalculate_rs_rank_v2():
    db = Database()
    db.connect()
    
    try:
        print("=== RS Rank Normalization (Last 5 Days) ===")
        
        # 1. Get last 5 trading dates
        query_dates = "SELECT DISTINCT trading_date FROM indicator_daily ORDER BY trading_date DESC LIMIT 5"
        dates = db.execute_query(query_dates)
        
        if not dates:
            print("No dates found.")
            return

        for (target_date,) in dates:
            print(f"\nProcessing date: {target_date}")
            
            # 2. Fetch data for this specific date
            query_data = """
                SELECT symbol_key, rs_score 
                FROM indicator_daily 
                WHERE trading_date = %s
            """
            results = db.execute_query(query_data, (target_date,))
            
            if not results:
                print(f"No data for {target_date}")
                continue
                
            df = pd.DataFrame(results, columns=['symbol', 'raw_score'])
            
            # Convert to numeric
            df['raw_score'] = pd.to_numeric(df['raw_score'], errors='coerce').fillna(0)
            
            count = len(df)
            print(f"  Records found: {count}")
            if count < 10:
                print("  Skipping normalization (too few records).")
                continue
                
            # 3. Calculate Percentile Rank (1-99)
            df['rank'] = df['raw_score'].rank(pct=True) * 99
            df['rank'] = df['rank'].fillna(1).astype(int)
            df['rank'] = df['rank'].clip(lower=1, upper=99)
            
            # 4. Update DB
            update_query = "UPDATE indicator_daily SET rs_score = %s WHERE symbol_key = %s AND trading_date = %s"
            
            updates = []
            for index, row in df.iterrows():
                updates.append((int(row['rank']), row['symbol'], target_date))
                
            print(f"  Updating {len(updates)} records...")
            
            # Batch execution
            with db.connection.cursor() as cursor:
                cursor.executemany(update_query, updates)
            db.connection.commit()
            print("  Done.")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.disconnect()

if __name__ == "__main__":
    recalculate_rs_rank_v2()
