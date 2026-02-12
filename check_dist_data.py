import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from app.db.database import Database
import pandas as pd

def check_data():
    db = Database()
    with db:
        print("Checking indicator_daily for dist_to_52w_high_pct...")
        
        # Check if there are ANY non-null values
        query_any = "SELECT COUNT(*) FROM indicator_daily WHERE dist_to_52w_high_pct IS NOT NULL"
        count = db.execute_query(query_any)[0][0]
        print(f"Total non-null dist_to_52w_high_pct: {count}")
        
        # Check specific symbol if possible
        query_sample = "SELECT symbol_key FROM price_daily LIMIT 1"
        sample = db.execute_query(query_sample)
        
        if sample:
            symbol = sample[0][0]
            print(f"Checking specific symbol: {symbol}")
            
            # Get count of price data
            query_count = "SELECT COUNT(*) FROM price_daily WHERE symbol_key = %s"
            price_count = db.execute_query(query_count, (symbol,))[0][0]
            print(f"Price data count for {symbol}: {price_count}")
            
            # Get indicators
            query_ind = """
                SELECT trading_date, dist_to_52w_high_pct 
                FROM indicator_daily 
                WHERE symbol_key = %s 
                ORDER BY trading_date DESC 
                LIMIT 5
            """
            indicators = db.execute_query(query_ind, (symbol,))
            print("Recent indicators:")
            for row in indicators:
                print(row)

        # Check columns where RSI is present but Dist is missing
        query_mismatch = "SELECT COUNT(*) FROM indicator_daily WHERE rsi14 IS NOT NULL AND dist_to_52w_high_pct IS NULL"
        mismatch_count = db.execute_query(query_mismatch)[0][0]
        print(f"Records with RSI but NO Dist: {mismatch_count}")
        
        if mismatch_count > 0:
            print("Sampling stocks with missing Dist:")
            query_sample_miss = """
                SELECT symbol_key, trading_date 
                FROM indicator_daily 
                WHERE rsi14 IS NOT NULL AND dist_to_52w_high_pct IS NULL 
                LIMIT 5
            """
            miss_samples = db.execute_query(query_sample_miss)
            for row in miss_samples:
                print(row)

if __name__ == "__main__":
    check_data()
