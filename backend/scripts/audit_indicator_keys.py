
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def audit_indicator_keys():
    db = Database()
    db.connect()
    try:
        print("--- Auditing indicator_daily Keys ---")
        
        # 1. Get all keys from indicator_daily
        img_keys = db.execute_query("SELECT DISTINCT symbol_key FROM indicator_daily")
        img_keys_set = set([r[0] for r in img_keys])
        print(f"Total unique keys in indicator_daily: {len(img_keys_set)}")
        
        # 2. Get all valid keys from instruments
        inst_keys = db.execute_query("SELECT symbol_key FROM instruments")
        inst_keys_set = set([r[0] for r in inst_keys])
        print(f"Total valid keys in instruments: {len(inst_keys_set)}")
        
        # 3. Find Orphans (Keys in indicator_daily not in instruments)
        orphans = img_keys_set - inst_keys_set
        print(f"Orphans (in indicator_daily but NOT in instruments): {len(orphans)}")
        
        # 4. Check for Format Violations (No Prefix)
        bad_format = []
        for k in img_keys_set:
            if ":" not in k:
                bad_format.append(k)
        
        print(f"Format Violations (No Prefix): {len(bad_format)}")
        if bad_format:
            print("Sample Bad Formats:", bad_format[:10])
            
        # 5. Check for 'Duplicate' concepts (e.g. US:NVDA and NVDA both exist)
        # Scan bad formats and see if prefixed version exists
        duplicates = []
        for k in bad_format:
            # Assume US implied if no prefix (or check both)
            candidate = f"US:{k}"
            if candidate in img_keys_set:
                duplicates.append((k, candidate))
            
            candidate_jp = f"JP:{k}"
            if candidate_jp in img_keys_set:
                duplicates.append((k, candidate_jp))
                
        print(f"Potential Duplicates (Legacy vs New both present): {len(duplicates)}")
        if duplicates:
            print("Sample Duplicates:", duplicates[:10])

    finally:
        db.disconnect()

if __name__ == "__main__":
    audit_indicator_keys()
