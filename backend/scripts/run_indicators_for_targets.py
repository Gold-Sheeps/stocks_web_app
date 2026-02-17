
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.services.indicator_service import IndicatorService
from app.db.database import Database

def run_for_targets():
    db = Database()
    db.connect()
    
    targets = set()
    
    print("--- Fetching Portfolio & Watchlist Symbols ---")
    
    # Portfolio
    rows = db.execute_query("SELECT DISTINCT symbol FROM holdings")
    if rows:
        for r in rows:
            targets.add(r[0]) # These might be "NVDA" or "US:NVDA" depending on old data, but should be "US:NVDA" now[WARN]
            
    # Watchlist
    rows = db.execute_query("SELECT DISTINCT symbol FROM watchlist")
    if rows:
        for r in rows:
            targets.add(r[0])
            
    print(f"Found {len(targets)} unique target symbols.")
    
    # Also ensure we have keys. If they are raw symbols, convert them[WARN]
    # Assuming they are valid keys or raw. app services usually convert.
    # IndicatorService expects symbol_key (US:...)
    
    svc = IndicatorService()
    
    # Helper to convert if needed (simple version)
    final_keys = set()
    for t in targets:
        if ":" in t:
            final_keys.add(t)
        else:
            # Assume US if no prefix, unless digit 4
            if t.isdigit() and len(t) == 4:
                final_keys.add(f"JP:{t}")
            else:
                final_keys.add(f"US:{t}")
                
    print(f"Normalized Target Keys: {len(final_keys)}")
    
    for i, key in enumerate(final_keys):
        print(f"Processing {i+1}/{len(final_keys)}: {key}")
        svc.calculate_and_save_indicators(key)
        
    print("Done.")
    db.disconnect()

if __name__ == "__main__":
    run_for_targets()
