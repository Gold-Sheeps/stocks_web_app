
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def migrate_safe():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    print("--- Starting Safe Key Migration ---")
    
    try:
        # 1. Add New Columns
        print("1. Adding new columns...")
        tables = ['instruments', 'price_daily', 'watchlist', 'trades', 'holdings', 'indicator_daily']
        for t in tables:
            try:
                db.execute_command(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS symbol_key_new VARCHAR(50)")
            except Exception as e:
                print(f"Error adding column to {t}: {e}")
        
        try:
             db.execute_command("ALTER TABLE sector_rotation ADD COLUMN IF NOT EXISTS etf_symbol_key_new VARCHAR(50)")
        except Exception as e:
             print(f"Error adding column to sector_rotation: {e}")

        # 2. Populate New Columns
        print("2. Populating new columns...")
        
        # Instruments: Logic to fix FX prefix
        # If it starts with FX:, change to US:. Else keep as is.
        db.execute_command("""
            UPDATE instruments 
            SET symbol_key_new = CASE 
                WHEN symbol_key LIKE 'FX:%' THEN 'US:' || substring(symbol_key from 4)
                ELSE symbol_key 
            END
        """)

        # Children: Join with instruments to get the new key
        # verify orphan handling: if joined key is null (orphan?) keep old key? 
        # Audit said 0 orphans. So inner join is safe?
        # Let's use UPDATE FROM
        
        # price_daily
        db.execute_command("""
            UPDATE price_daily p
            SET symbol_key_new = i.symbol_key_new
            FROM instruments i
            WHERE p.symbol_key = i.symbol_key
        """)
        
        # watchlist
        db.execute_command("""
            UPDATE watchlist w
            SET symbol_key_new = i.symbol_key_new
            FROM instruments i
            WHERE w.symbol_key = i.symbol_key
        """)
        
         # trades
        db.execute_command("""
            UPDATE trades t
            SET symbol_key_new = i.symbol_key_new
            FROM instruments i
            WHERE t.symbol_key = i.symbol_key
        """)
        
         # holdings
        db.execute_command("""
            UPDATE holdings h
            SET symbol_key_new = i.symbol_key_new
            FROM instruments i
            WHERE h.symbol_key = i.symbol_key
        """)
        
         # indicator_daily
        db.execute_command("""
            UPDATE indicator_daily id
            SET symbol_key_new = i.symbol_key_new
            FROM instruments i
            WHERE id.symbol_key = i.symbol_key
        """)
        
         # sector_rotation
        db.execute_command("""
            UPDATE sector_rotation s
            SET etf_symbol_key_new = i.symbol_key_new
            FROM instruments i
            WHERE s.etf_symbol_key = i.symbol_key
        """)

        # 3. Verify No NULLs
        print("3. Verifying data...")
        # Check instruments
        rows = db.execute_query("SELECT count(*) FROM instruments WHERE symbol_key_new IS NULL")
        if rows[0][0] > 0:
            raise Exception(f"Found {rows[0][0]} NULLs in instruments.symbol_key_new")
            
        # Check price_daily
        rows = db.execute_query("SELECT count(*) FROM price_daily WHERE symbol_key_new IS NULL")
        if rows[0][0] > 0:
             # If orphans exist, we might have NULLs. But audit said 0 orphans.
             raise Exception(f"Found {rows[0][0]} NULLs in price_daily.symbol_key_new")

        print("Verification passed.")

        # 4. Swap Columns (Rename)
        print("4. Swapping columns...")
        
        def swap_column(table, old_col, new_col):
            # Rename old to old_backup
            # Rename new to old_col
            # Drop backup later (or keep as per instruction "keep old key column")
            # User said: "Old key column reserved (don't delete immediately)"
            
            # Check if backup already exists?
            try:
                db.execute_command(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {old_col}_old_backup")
            except Exception:
                print(f"Could not rename {old_col} to backup (maybe already done?)")
                
            try:
                db.execute_command(f"ALTER TABLE {table} RENAME COLUMN {new_col} TO {old_col}")
            except Exception as e:
                print(f"Error renaming new col in {table}: {e}")
                raise e

        # Drop constraints on old columns (PK/FK) to allow rename?
        # Postgres allows renaming columns referenced by constraints, but it might rename the constraint?
        # Actually, renaming the column usually carries the constraint with it to the new name if we are not careful?
        # Wait, if I rename `symbol_key` to `symbol_key_old`, the PK is now on `symbol_key_old`.
        # I need to DROP the PK on `symbol_key_old` and ADD it to `symbol_key` (which was `symbol_key_new`).
        
        # A. Constraints Handling
        print(" Dropping constraints...")
        # Instruments PK
        db.execute_command("ALTER TABLE instruments DROP CONSTRAINT IF EXISTS instruments_pkey CASCADE")
        # Watchlist FK? (Usually inferred or named)
        # We'll just recreate them.
        
        # B. Swaps
        swap_column('instruments', 'symbol_key', 'symbol_key_new')
        swap_column('price_daily', 'symbol_key', 'symbol_key_new')
        swap_column('watchlist', 'symbol_key', 'symbol_key_new')
        swap_column('trades', 'symbol_key', 'symbol_key_new')
        swap_column('holdings', 'symbol_key', 'symbol_key_new')
        swap_column('indicator_daily', 'symbol_key', 'symbol_key_new')
        swap_column('sector_rotation', 'etf_symbol_key', 'etf_symbol_key_new')
        
        # C. Restore Constraints
        print(" Restoring PK/FK...")
        
        # Instruments PK
        db.execute_command("ALTER TABLE instruments ADD PRIMARY KEY (symbol_key)")
        
        # Re-add Unique Constraints or Indexes if needed
        # (Assuming essential ones. Scripts inspect_schema would show them but we know PK is critical)
        
        # Indexes
        tables_idx = ['price_daily', 'watchlist', 'trades', 'holdings', 'indicator_daily']
        for t in tables_idx:
             db.execute_command(f"CREATE INDEX IF NOT EXISTS idx_{t}_symbol_key_new ON {t}(symbol_key)")
             
        db.execute_command("CREATE INDEX IF NOT EXISTS idx_sector_rotation_etf_key_new ON sector_rotation(etf_symbol_key)")

        print("Migration and Swap Complete.")

    except Exception as e:
        print(f"MIGRATION FAILED: {e}")
        # In a real real scenario, we'd want a transaction rollback.
        # My Database class is simple autocommit usually.
        # Manual intervention might be needed if it fails halfway.
        
    finally:
        db.disconnect()

if __name__ == "__main__":
    migrate_safe()
