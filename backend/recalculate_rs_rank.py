
import pandas as pd
from app.db.database import Database
from decimal import Decimal

def recalculate_rs_rank():
    db = Database()
    db.connect()
    
    try:
        print("Fetching current RS raw scores...")
        # Fetch all latest indicators with their raw rs_score
        query = """
            SELECT symbol_key, rs_score, trading_date 
            FROM indicator_daily 
            WHERE trading_date = (SELECT MAX(trading_date) FROM indicator_daily)
        """
        results = db.execute_query(query)
        
        if not results:
            print("No data found")
            return

        df = pd.DataFrame(results, columns=['symbol', 'raw_score', 'date'])
        
        # Convert to numeric, handle None
        df['raw_score'] = pd.to_numeric(df['raw_score'], errors='coerce').fillna(0)
        
        # Calculate Percentile Rank (1-99)
        # pct=True gives 0.0 to 1.0. Multiply by 99 and add 1 to get 1-99 range (approx)
        # or multiply by 100 to get 0-100. IBD uses 1-99.
        df['rank'] = df['raw_score'].rank(pct=True) * 99
        df['rank'] = df['rank'].fillna(1).astype(int)
        
        # Handle edge case where rank might be 0 (if desired min is 1)
        df['rank'] = df['rank'].clip(lower=1, upper=99)
        
        print("Updating database with RS Rating (1-99)...")
        update_query = "UPDATE indicator_daily SET rs_score = %s WHERE symbol_key = %s AND trading_date = %s"
        
        updates = []
        for index, row in df.iterrows():
            updates.append((int(row['rank']), row['symbol'], row['date']))
            
        # Batch update (simple loop for safety/simplicity here, or executemany if implemented)
        count = 0
        for params in updates:
            db.execute_command(update_query, params)
            count += 1
            if count % 100 == 0:
                print(f"Updated {count} records...")
                
        print(f"Finished updating {count} records.")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.disconnect()

if __name__ == "__main__":
    recalculate_rs_rank()
