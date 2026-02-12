
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def debug_data():
    db = Database()
    db.connect()
    
    targets = ['US:AAPL', 'US:NVDA', 'US:RGC']
    
    print("\n--- Debugging Data for Targets ---")
    for t in targets:
        print(f"\nChecking {t}:")
        # Check Instrument
        inst = db.execute_query("SELECT is_active FROM instruments WHERE symbol_key = %s", (t,))
        print(f"  is_active: {inst[0][0] if inst else 'NOT FOUND'}")
        
        # Check Price
        price = db.execute_query("SELECT trading_date, close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1", (t,))
        p_date = price[0][0] if price else None
        print(f"  Latest Price: {price[0][1] if price else 'None'} at {p_date}")
        
        # Check Indicator
        ind = db.execute_query("SELECT trading_date, rs_score FROM indicator_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1", (t,))
        i_date = ind[0][0] if ind else None
        print(f"  Latest Ind: RS={ind[0][1] if ind else 'None'} at {i_date}")
        
        if p_date and i_date:
            print(f"  Date Match: {p_date == i_date}")
            if p_date != i_date:
                # Check if indicator exists for price date
                chk = db.execute_query("SELECT 1 FROM indicator_daily WHERE symbol_key = %s AND trading_date = %s", (t, p_date))
                print(f"  Ind exists for Price Date? {bool(chk)}")

    print("\n--- Global RS Score Stats ---")
    stats = db.execute_query("""
        SELECT 
            COUNT(*) as total, 
            COUNT(rs_score) as has_rs, 
            MIN(rs_score), MAX(rs_score), AVG(rs_score)
        FROM indicator_daily
        WHERE trading_date = (SELECT MAX(trading_date) FROM indicator_daily)
    """)
    print(f"Latest Date Stats: Total={stats[0][0]}, HasRS={stats[0][1]}, Min={stats[0][2]}, Max={stats[0][3]}")

    db.disconnect()

if __name__ == "__main__":
    debug_data()
