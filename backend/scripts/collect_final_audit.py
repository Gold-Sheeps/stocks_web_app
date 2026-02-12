
import sys
from pathlib import Path
import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))
from app.db.database import Database

def collect_final_audit():
    print("=== FINAL AUDIT COLLECTION START ===")
    db = Database()
    db.connect()
    
    # 1. Freshness Evidence
    print("\n[Freshness Evidence]")
    
    # Global Max Date
    sql_global = "SELECT MAX(trading_date) FROM price_daily;"
    row_g = db.execute_query(sql_global)
    print(f"Global Latest Date: {row_g[0][0]}")
    
    # Monitor Symbols
    # Note: Using symbols that are likely in DB. The user suggested ^GSPC, USDJPY=X, GC=F, XLK.
    # I should check if they exist first or just run the query.
    # If they are not in DB, I might need to check what IS in DB to provide a good sample.
    # But let's try the user's list first.
    targets = ['^GSPC', 'USDJPY=X', 'GC=F', 'XLK', 'US:XLK', 'JP:^N225'] 
    # Use unified keys if possible. 
    # The user input "USDJPY=X", but my system uses "US:USDJPY=X"? Or just "USDJPY=X"?
    # I should check instruments for these.
    
    # Let's clean up the query to handle both raw and prefixed just in case, 
    # or just query a broader set and filter in python verify.
    # Actually, let's just run the user's query logic with broad IN clause.
    target_keys = [
        'US:^GSPC', '^GSPC', 
        'US:USDJPY=X', 'USDJPY=X', 
        'US:GC=F', 'GC=F', 
        'US:XLK', 'XLK',
        'US:^VIX', '^VIX'
    ]
    placeholders = ','.join(['%s'] * len(target_keys))
    sql_monitor = f"""
        SELECT symbol_key, MAX(trading_date) 
        FROM price_daily 
        WHERE symbol_key IN ({placeholders}) 
        GROUP BY symbol_key 
        ORDER BY symbol_key;
    """
    rows_m = db.execute_query(sql_monitor, tuple(target_keys))
    if rows_m:
        for r in rows_m:
            print(f"Symbol: {r[0]} | Latest: {r[1]}")
    else:
        print("(No Monitor Symbols found from list)")

    # 2. System Logs
    print("\n[System Logs Evidence]")
    # check columns first to avoid error
    # job_name / status / started_at / finished_at / success_count / fail_count / error_summary
    # We'll just select * and map manually to be safe
    sql_logs = """
    SELECT *
    FROM system_logs 
    ORDER BY created_at DESC 
    LIMIT 1;
    """
    # I don't know the exact column order, so I should probably use a dictionary cursor or inspect columns.
    # Database.execute_query returns tuples.
    # Let's get column names from cursor description if possible, but Database class hides it.
    # I will try to select specific columns I expect.
    try:
        sql_log_specific = """
            SELECT job_id, status, started_at, finished_at, message
            FROM system_logs
            ORDER BY created_at DESC
            LIMIT 1
        """
        # Note: 'success_count' / 'fail_count' might be in 'metadata' or 'message' or separate columns depending on implementation.
        # I'll try to guess based on previous interactions or just dump the message.
        # implementation_plan says "system_logs table".
        rows_l = db.execute_query(sql_log_specific)
        if rows_l:
            r = rows_l[0]
            print(f"Job: {r[0]}") # job_id
            print(f"Status: {r[1]}")
            print(f"Started: {r[2]}")
            print(f"Finished: {r[3]}")
            print(f"Message: {r[4]}")
    except Exception as e:
        print(f"Error querying logs: {e}")
        # Fallback to just pulling everything
        rows_curr = db.execute_query("SELECT * FROM system_logs ORDER BY created_at DESC LIMIT 1")
        print(f"Raw Log Row: {rows_curr[0]}")


    # 3. NVDA Price Check
    print("\n[NVDA Price Check]")
    sql_nvda = "SELECT trading_date, close FROM price_daily WHERE symbol_key = 'US:NVDA' ORDER BY trading_date DESC LIMIT 1;"
    row_n = db.execute_query(sql_nvda)
    if row_n:
        print(f"US:NVDA Latest: {row_n[0][0]} Price: {row_n[0][1]}")
    else:
        print("US:NVDA Latest: (No Data)")
        
    db.disconnect()
    print("=== FINAL AUDIT COLLECTION END ===")

if __name__ == "__main__":
    collect_final_audit()
