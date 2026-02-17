
import sys
import time
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def fix_keys():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    print("Starting Key Standardization (Bulk SQL)...")
    
    try:
        # 0. Drop Constraint to allow FX
        print("Dropping market check constraint...")
        try:
            db.execute_command("ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_market_check")
        except Exception as e:
            print(f"Constraint drop failed (maybe didn't exist[WARN]): {e}")

        # 1. Create New Instruments

        # FX
        print("Creating FX Instruments...")
        db.execute_command("""
            INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
            SELECT 'FX:' || symbol_key, 'FX', name, currency, is_active, created_at, updated_at
            FROM instruments 
            WHERE symbol_key NOT LIKE '%:%' AND symbol_key LIKE '%=X'
            ON CONFLICT (symbol_key) DO NOTHING;
        """)

        # JP (4 digits)
        print("Creating JP Instruments...")
        db.execute_command("""
            INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
            SELECT 'JP:' || symbol_key, 'JP', name, currency, is_active, created_at, updated_at
            FROM instruments 
            WHERE symbol_key NOT LIKE '%:%' AND symbol_key ~ '^[0-9]{4}$'
            ON CONFLICT (symbol_key) DO NOTHING;
        """)

        # US (Everything else containing no colon)
        print("Creating US Instruments...")
        db.execute_command("""
            INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
            SELECT 'US:' || symbol_key, 'US', name, currency, is_active, created_at, updated_at
            FROM instruments 
            WHERE symbol_key NOT LIKE '%:%' AND symbol_key NOT LIKE '%=X' AND symbol_key !~ '^[0-9]{4}$'
            ON CONFLICT (symbol_key) DO NOTHING;
        """)
        
        # 2. Update References
        tables = ['watchlist', 'trades', 'holdings', 'price_daily', 'indicator_daily']
        # sector_rotation uses 'etf_symbol_key'
        
        for table in tables:
            print(f"Updating {table}...")
            # FX
            db.execute_command(f"""
                UPDATE {table} SET symbol_key = 'FX:' || symbol_key
                WHERE symbol_key NOT LIKE '%:%' AND symbol_key LIKE '%=X'
            """)
            # JP
            db.execute_command(f"""
                UPDATE {table} SET symbol_key = 'JP:' || symbol_key
                WHERE symbol_key NOT LIKE '%:%' AND symbol_key ~ '^[0-9]{4}$'
            """)
            # US
            db.execute_command(f"""
                UPDATE {table} SET symbol_key = 'US:' || symbol_key
                WHERE symbol_key NOT LIKE '%:%' AND symbol_key NOT LIKE '%=X' AND symbol_key !~ '^[0-9]{4}$'
            """)

        # sector_rotation
        print("Updating sector_rotation...")
        db.execute_command("""
            UPDATE sector_rotation SET etf_symbol_key = 'US:' || etf_symbol_key
            WHERE etf_symbol_key NOT LIKE '%:%' AND etf_symbol_key NOT LIKE '%=X' AND etf_symbol_key !~ '^[0-9]{4}$'
        """)

        # 3. Clean up Old Instruments
        # Check if any references remain (safety check[WARN])
        # For now, rely on previous updates being comprehensive.
        
        print("Deleting Legcay Instruments...")
        # Since we updated all FKs to new keys, old keys should have no dependents (unless I missed a table).
        # If I missed a table, DELETE will fail due to FK constraint. This is good safety.
        try:
            db.execute_command("DELETE FROM instruments WHERE symbol_key NOT LIKE '%:%'")
            print("Legacy instruments deleted.")
        except Exception as e:
            print(f"Could not delete legacy instruments (FK constraint[WARN]): {e}")

        print("Key Standardization Complete.")

    except Exception as e:
        print(f"Error: {e}")
        # Rollback not explicitly available in my Database helper without access to conn object easily
        # But execute_command usually auto-commits.
        # This is risky but "fix forward" approach.
    finally:
        db.disconnect()

if __name__ == "__main__":
    fix_keys()
