
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def audit_keys():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    print("--- Starting Symbol Key Audit ---")
    try:
        # 1. Fetch all instruments
        instruments = db.execute_query("SELECT symbol_key, market, name FROM instruments")
        inst_map = {r[0]: {'market': r[1], 'name': r[2]} for r in instruments}
        print(f"Total Instruments: {len(inst_map)}")

        # 2. Fetch distinct symbol_keys from price_daily
        pd_keys = db.execute_query("SELECT DISTINCT symbol_key FROM price_daily")
        pd_key_set = set(r[0] for r in pd_keys)
        print(f"Distinct Keys in price_daily: {len(pd_key_set)}")

        # 3. Analyze Orphans (In price_daily but not in instruments)
        orphans = pd_key_set - set(inst_map.keys())
        print(f"\nOrphans (in price_daily but missing in instruments): {len(orphans)}")
        for i, key in enumerate(orphans):
            if i < 10: print(f" - {key}")
        if len(orphans) > 10: print(" ...")

        # 4. Analyze Format Violations (Not starting with US: or JP:)
        print("\nFormat Violations (Not US: or JP:):")
        bad_format_inst = [k for k in inst_map.keys() if not (k.startswith('US:') or k.startswith('JP:'))]
        print(f"Instruments with bad format: {len(bad_format_inst)}")
        for i, key in enumerate(bad_format_inst):
            if i < 10: print(f" - {key}")
        
        bad_format_pd = [k for k in pd_key_set if not (k.startswith('US:') or k.startswith('JP:'))]
        print(f"Price_daily with bad format: {len(bad_format_pd)}")
        for i, key in enumerate(bad_format_pd):
            if i < 10: print(f" - {key}")

        # 5. Proposed Mapping Generation
        print("\n--- Proposed Mapping (Preview) ---")
        # Logic: 
        # If key has no prefix:
        #   If 4 digits -> JP:Key
        #   Else -> US:Key
        # If key has FX: prefix -> Change to US: (as per user rule)
        # If key has other prefix -> Check rule
        
        mapping_preview = {}
        all_keys = set(inst_map.keys()) | pd_key_set
        
        for k in all_keys:
            new_key = k
            if ':' not in k:
                # Legacy handling
                if k.isdigit() and len(k) == 4:
                    new_key = f"JP:{k}"
                else:
                    new_key = f"US:{k}"
            elif k.startswith('FX:'):
                # Remap FX to US as per rule: "INDEX/ETF/FX/METAL ... as market=US"
                # FX:USDJPY=X -> US:USDJPY=X
                raw = k.split(':', 1)[1]
                new_key = f"US:{raw}"
            else:
                # Already might be US: or JP:
                # But check if we have any other prefixes
                prefix = k.split(':', 1)[0]
                if prefix not in ['US', 'JP']:
                    # Force US for unknown prefixes?
                    raw = k.split(':', 1)[1]
                    new_key = f"US:{raw}"
            
            if new_key != k:
                mapping_preview[k] = new_key

        print(f"Total Remappings Needed: {len(mapping_preview)}")
        sample_count = 0
        for old, new in mapping_preview.items():
            if sample_count < 15:
                print(f" {old} -> {new}")
                sample_count += 1
        
        # Check for Collision
        # e.g. mapping creates duplicates in the target namespace
        print("\nChecking for Collisions...")
        target_counts = {}
        for old, new in mapping_preview.items():
            target_counts[new] = target_counts.get(new, 0) + 1
        
        # Also check against existing valid keys
        for k in all_keys:
            if k not in mapping_preview:
                # This key is valid and staying. check if any mapping maps to IT
                target_counts[k] = target_counts.get(k, 0) + 1

        collisions = {k: v for k, v in target_counts.items() if v > 1}
        if collisions:
            print(f"CRITICAL: Collisions detected: {len(collisions)}")
            for k, v in collisions.items():
                print(f" - Target {k} generated from {v} sources")
        else:
            print("No collisions detected.")

    finally:
        db.disconnect()

if __name__ == "__main__":
    audit_keys()
