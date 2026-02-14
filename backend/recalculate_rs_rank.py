
import pandas as pd
from app.db.database import Database
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def recalculate_rs_rank():
    """
    RS rank calculation with partial-day handling.
    Scans the last 5 trading days.
    Only normalizes days with significant volume of data (count > 100) to avoid applying 
    normalization to holidays/partial days where only a few tickers (futures/forex) updated.
    """
    db = Database()
    db.connect()
    
    try:
        logger.info("=== RS Rank Normalization Started ===")
        
        # 1. Fetch last 5 trading dates
        query_dates = "SELECT DISTINCT trading_date FROM indicator_daily ORDER BY trading_date DESC LIMIT 5"
        dates = db.execute_query(query_dates)
        
        if not dates:
            logger.warning("No data found in indicator_daily.")
            return

        for (target_date,) in dates:
            logger.info(f"Checking date: {target_date}")
            
            # 2. Check data count for this date
            count_query = "SELECT COUNT(*) FROM indicator_daily WHERE trading_date = %s"
            count = db.execute_query(count_query, (target_date,))[0][0]
            
            if count < 100:
                logger.warning(f"  Skipping {target_date} - Insufficient data count ({count} records). Likely partial day/holiday.")
                continue

            logger.info(f"  Processing {target_date} ({count} records)...")

            # 3. Fetch Raw Scores
            query_data = """
                SELECT symbol_key, rs_score 
                FROM indicator_daily 
                WHERE trading_date = %s
            """
            results = db.execute_query(query_data, (target_date,))
            
            df = pd.DataFrame(results, columns=['symbol', 'raw_score'])
            
            # Convert to numeric, handle None
            df['raw_score'] = pd.to_numeric(df['raw_score'], errors='coerce').fillna(0)
            
            # 4. Calculate Percentile Rank (1-99)
            # pct=True gives 0.0 to 1.0
            df['rank'] = df['raw_score'].rank(pct=True) * 99
            df['rank'] = df['rank'].fillna(1).astype(int)
            
            # Clip to ensure valid range
            df['rank'] = df['rank'].clip(lower=1, upper=99)
            
            # 5. Update DB
            logger.info(f"  Updating {len(df)} records for {target_date}...")
            
            update_query = "UPDATE indicator_daily SET rs_score = %s WHERE symbol_key = %s AND trading_date = %s"
            
            updates = []
            for index, row in df.iterrows():
                updates.append((int(row['rank']), row['symbol'], target_date))
                
            # Batch execution
            try:
                with db.connection.cursor() as cursor:
                    cursor.executemany(update_query, updates)
                db.connection.commit()
                logger.info(f"  Successfully updated {target_date}.")
            except Exception as e:
                db.connection.rollback()
                logger.error(f"  Failed to update {target_date}: {e}")

            # Optional: Check validity after update
            max_rs = df['rank'].max()
            high_rs_count = len(df[df['rank'] >= 90])
            
            if max_rs < 90 or high_rs_count == 0:
                logger.error(f"  CRITICAL: Invalid RS Distribution for {target_date} (Max={max_rs}, HighCount={high_rs_count}).")
                import sys
                sys.exit(1)

        logger.info("=== RS Rank Normalization Completed ===")
        
    except Exception as e:
        logger.error(f"Critical Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.disconnect()

if __name__ == "__main__":
    recalculate_rs_rank()
