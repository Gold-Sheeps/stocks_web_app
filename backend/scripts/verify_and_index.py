
import sys
import os
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.db.database import Database

def verify_migration():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        tables = [
            ("watchlist", "symbol_key"),
            ("trades", "symbol_key"),
            ("holdings", "symbol_key"),
            ("sector_rotation", "etf_symbol_key")
        ]
        
        all_good = True
        
        print("Verifying NULL counts (Should be 0)...")
        for table, col in tables:
            # Check if table exists (sector_rotation might optionally exist depending on setup)
            # Simple check
            try:
                rows = db.execute_query(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL")
                if rows:
                    count = rows[0][0]
                    if count == 0:
                        print(f"[OK] {table}.{col}: 0 NULLs")
                    else:
                        print(f"[FAIL] {table}.{col}: {count} NULLs remaining!")
                        all_good = False
            except Exception as e:
                print(f"[SKIP] {table}: {e}")
        
        if all_good:
            print("\nVerification Passed. Proceeding to Add Indexes.")
            _add_indexes(db)
        else:
            print("\nVerification FAILED. Fix NULLs before adding indexes.")

    finally:
        db.disconnect()

def _add_indexes(db):
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_watchlist_symbol_key ON watchlist(symbol_key);",
        "CREATE INDEX IF NOT EXISTS idx_trades_symbol_key ON trades(symbol_key);",
        "CREATE INDEX IF NOT EXISTS idx_holdings_symbol_key ON holdings(symbol_key);",
        "CREATE INDEX IF NOT EXISTS idx_sector_rotation_etf_key ON sector_rotation(etf_symbol_key);"
    ]
    
    print("\nAdding Indexes...")
    for idx in indexes:
        try:
            db.execute_command(idx)
            print(f"[OK] {idx}")
        except Exception as e:
            print(f"[ERR] {e}")

if __name__ == "__main__":
    verify_migration()
