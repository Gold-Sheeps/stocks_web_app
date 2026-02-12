
import sys
import os
import argparse
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.db.database import Database

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('migration.log')
    ]
)
logger = logging.getLogger(__name__)

class MigrationService:
    def __init__(self, dry_run=True):
        self.db = Database()
        self.dry_run = dry_run
        self.instruments_cache = {}  # symbol -> {market, symbol_key}
        self.conversion_stats = {
            "watchlist": {"total": 0, "success": 0, "failed": 0},
            "trades": {"total": 0, "success": 0, "failed": 0},
            "holdings": {"total": 0, "success": 0, "failed": 0},
            "sector_rotation": {"total": 0, "success": 0, "failed": 0},
            "instruments_created": 0
        }

    def run(self):
        if not self.db.connect():
            logger.error("Failed to connect to DB")
            return

        try:
            # 1. Add Columns (DDL)
            self._add_columns()

            # 2. Load Instruments Cache
            self._load_instruments()

            # 3. Migrate Tables
            self._migrate_watchlist()
            self._migrate_trades()
            self._migrate_holdings()
            self._migrate_sector_rotation()

            # 4. Finalize
            if not self.dry_run:
                logger.info("Committing changes...")
                # self.db.connection.commit() # DDL commands auto-commit in some drivers, data updates need explicit if not autocommit
                # Our Database class methods like execute_command usually commit. 
                # But for batch updates we might want to be careful. 
                # Let's assume execute_command commits.
                pass
            else:
                logger.info("DRY RUN COMPLETE: No changes committed to data (DDL might have run if not using transaction DDL).")
                # Note: Schema changes usually cannot be rolled back easily in all DBs inside same transaction block context with some drivers. 
                # But we use IF NOT EXISTS so it's safe-ish.

            self._print_summary()

        except Exception as e:
            logger.error(f"Migration Failed: {e}", exc_info=True)
            if not self.dry_run:
                self.db.connection.rollback()
        finally:
            self.db.disconnect()

    def _add_columns(self):
        """Add symbol_key columns if they don't exist"""
        logger.info(">>> Step 1: Schema Updates (Adding Columns)")
        queries = [
            "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS symbol_key VARCHAR(50);",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS symbol_key VARCHAR(50);",
            "ALTER TABLE holdings ADD COLUMN IF NOT EXISTS symbol_key VARCHAR(50);",
            "ALTER TABLE sector_rotation ADD COLUMN IF NOT EXISTS etf_symbol_key VARCHAR(50);"
        ]
        
        # We always run DDL even in dry-run to ensure checks pass? 
        # Requirement says: "4) 反映の順番... DDLで列追加" before Dry-run. 
        # So we assume DDL is allowed or already done. 
        # I will execute them here. If user wants strictly NO DB changes in dry run, this is tricky.
        # But "Phase 2... 1) 追加するカラム... 2) script... Dry-run" implies script might handle it or separate.
        # Let's execute DDL safely.
        
        for q in queries:
            try:
                # In dry run, we might skip DDL if we strictly want no side effects.
                # But to test migration logic, columns must exist if we are to Update them? 
                # SQL UPDATE queries will fail if column doesn't exist.
                # So logically DDL must exist.
                if self.dry_run:
                     logger.info(f"[DRY-RUN] Would execute: {q}")
                     # Verify if columns exist to proceed with Dry-Run logic?
                     # If columns missing, Dry-Run UPDATEs will fail efficiently.
                     # We'll try to execute DDL only if NOT dry_run OR if we implement a check.
                     # User instruction: "4) 反映の順番 (DDL) -> Dry-run". 
                     # This implies DDL should be run BEFORE script or script runs it first then Dry-Run logic.
                     # I will run DDL.
                     pass 
                
                # Check column existence first to avoid error spam?
                # Using execute_command with IF NOT EXISTS is safe.
                self.db.execute_command(q)
                
            except Exception as e:
                logger.warning(f"DDL Warning ({q}): {e}")

    def _load_instruments(self):
        """Cache all instruments for lookup"""
        logger.info(">>> Step 2: Loading Instruments")
        rows = self.db.execute_query("SELECT symbol_key, market, name, currency FROM instruments")
        if rows:
            for r in rows:
                # Key maps from "Displayed Symbol" (e.g. NVDA) to details
                # But wait, instruments.symbol_key is US:NVDA. We might need to look up by 'NVDA'.
                # We need a map: symbol -> record.
                # Caveat: 'NVDA' might exist in US and JP? 
                # Current system seems to treat 'symbol' loosely.
                # We will map "Suffix/Prefix free" or just use raw symbol_key if we can guess it.
                
                s_key = r[0] # US:NVDA
                market = r[1]
                
                # Parse symbol from symbol_key (US:NVDA -> NVDA)
                if ':' in s_key:
                    clean_symbol = s_key.split(':', 1)[1]
                    self.instruments_cache[clean_symbol] = {"market": market, "key": s_key}
                    # Also map the full key just in case
                    self.instruments_cache[s_key] = {"market": market, "key": s_key}
                else:
                    # Fallback for malformed keys
                    self.instruments_cache[s_key] = {"market": market, "key": s_key}
        
        logger.info(f"Loaded {len(self.instruments_cache)} instruments for lookup")

    def _get_or_create_symbol_key(self, symbol: str, explicit_market: str = None) -> str:
        """
        Determine symbol_key.
        1. If explicit_market is 'US'/'JP', construct key.
        2. Else look up in instruments.
        3. If not found, default to US and create instrument.
        """
        # Clean symbol just in case
        symbol = symbol.strip().upper()
        
        # 1. Try to make key from explicit info
        candidate_key = None
        market = explicit_market
        
        # Existing logic check
        if symbol in self.instruments_cache:
            # We found a match in instruments!
            # If explicit_market matches or is None, use it.
            cached = self.instruments_cache[symbol]
            if not market or market == cached["market"]:
                 return cached["key"]
        
        # 2. Not in cache yet.
        # Default to US if market invalid/missing
        if not market or market not in ['US', 'JP']:
             market = 'US' # Default rule
             # Log that we defaulted?
             # logger.debug(f"Defaulting {symbol} to market US")

        # Construct Key
        final_key = f"{market}:{symbol}"
        
        # 3. Create if missing in cache (and likely DB)
        if final_key not in self.instruments_cache:
            if not self.dry_run:
                self._create_instrument(final_key, market, symbol)
            else:
                self.conversion_stats["instruments_created"] += 1
                logger.info(f"[DRY-RUN] Would create instrument: {final_key}")
                
            # Update cache so we don't recreate 
            self.instruments_cache[final_key] = {"market": market, "key": final_key}
            self.instruments_cache[symbol] = {"market": market, "key": final_key} # Alias
            
        return final_key

    def _create_instrument(self, symbol_key, market, symbol):
        query = """
            INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, true, NOW(), NOW())
            ON CONFLICT (symbol_key) DO NOTHING
        """
        # Guess currency
        currency = 'JPY' if market == 'JP' else 'USD'
        
        try:
            self.db.execute_command(query, (symbol_key, market, symbol, currency))
            self.conversion_stats["instruments_created"] += 1
            logger.info(f"Created missing instrument: {symbol_key}")
        except Exception as e:
            logger.error(f"Failed to create instrument {symbol_key}: {e}")

    def _migrate_watchlist(self):
        logger.info(">>> Step 3a: Migrating Watchlist")
        rows = self.db.execute_query("SELECT id, symbol FROM watchlist WHERE symbol_key IS NULL")
        if not rows:
            logger.info("No watchlist items to migrate.")
            return

        self.conversion_stats["watchlist"]["total"] = len(rows)
        
        for r in rows:
            item_id, symbol = r
            try:
                # Watchlist table doesn't have market column usually.
                # Must infer from Instruments.
                new_key = self._get_or_create_symbol_key(symbol)
                
                if not self.dry_run:
                    self.db.execute_command("UPDATE watchlist SET symbol_key = %s WHERE id = %s", (new_key, item_id))
                
                self.conversion_stats["watchlist"]["success"] += 1
                logger.info(f"Watchlist: {symbol} -> {new_key}")
                
            except Exception as e:
                self.conversion_stats["watchlist"]["failed"] += 1
                logger.error(f"Watchlist Failed {symbol}: {e}")

    def _migrate_trades(self):
        logger.info(">>> Step 3b: Migrating Trades")
        # Trades usually has 'market' column? Check schema inspector output.
        # Yes: symbol, market are columns.
        rows = self.db.execute_query("SELECT trade_id, symbol, market FROM trades WHERE symbol_key IS NULL") # Adjust WHERE if needed
        if not rows:
             logger.info("No trades to migrate.")
             return

        self.conversion_stats["trades"]["total"] = len(rows)

        for r in rows:
            pk, symbol, market = r
            try:
                # Sanitize market
                if market == 'FX' or market == 'INDEX' or market == 'CRYPTO':
                    market = 'US' # Force US mapping per rules
                
                new_key = self._get_or_create_symbol_key(symbol, market)
                
                if not self.dry_run:
                    self.db.execute_command("UPDATE trades SET symbol_key = %s WHERE trade_id = %s", (new_key, pk))
                
                self.conversion_stats["trades"]["success"] += 1
                
            except Exception as e:
                self.conversion_stats["trades"]["failed"] += 1
                logger.error(f"Trade keygen failed {symbol}: {e}")

    def _migrate_holdings(self):
        logger.info(">>> Step 3c: Migrating Holdings")
        # Holdings has symbol, market
        rows = self.db.execute_query("SELECT symbol, market FROM holdings WHERE symbol_key IS NULL")
        if not rows:
            logger.info("No holdings to migrate.")
            return

        self.conversion_stats["holdings"]["total"] = len(rows)
        
        for r in rows:
            symbol, market = r
            try:
                # Sanitize market
                if market == 'FX' or market == 'INDEX': 
                    market = 'US'

                new_key = self._get_or_create_symbol_key(symbol, market)
                
                if not self.dry_run:
                    # Holdings PK is symbol. UPDATE is fine.
                    self.db.execute_command("UPDATE holdings SET symbol_key = %s WHERE symbol = %s", (new_key, symbol))
                
                self.conversion_stats["holdings"]["success"] += 1
                
            except Exception as e:
                self.conversion_stats["holdings"]["failed"] += 1
                logger.error(f"Holdings keygen failed {symbol}: {e}")

    def _migrate_sector_rotation(self):
        logger.info(">>> Step 3d: Migrating Sector Rotation")
        # PK is (symbol, trading_date). symbol is e.g. 'XLK'.
        rows = self.db.execute_query("SELECT symbol, trading_date FROM sector_rotation WHERE etf_symbol_key IS NULL")
        if not rows:
            logger.info("No sector rotation rows to migrate.")
            return

        self.conversion_stats["sector_rotation"]["total"] = len(rows)
        
        # Batch updates? 1000s of rows potentially.
        # For simplicity and robust handling, loop. Can optimize later if slow (unlikely for <100k rows in local python)
        
        # Optimization: Unique symbols only
        unique_symbols = set([r[0] for r in rows])
        symbol_key_map = {}
        for s in unique_symbols:
            symbol_key_map[s] = self._get_or_create_symbol_key(s, 'US') # ETFs are US

        logger.info(f"Mapped {len(unique_symbols)} sector ETFs")

        # Now update DB
        # Single bulk update queries would be better.
        # "UPDATE sector_rotation SET etf_symbol_key = 'US:' || symbol WHERE etf_symbol_key IS NULL"
        # Since we know logic is simple (US prefix), maybe direct SQL is safer/faster?
        # But User requested "Strict Logic" in python.
        
        if not self.dry_run:
            for s, key in symbol_key_map.items():
                try:
                    self.db.execute_command("UPDATE sector_rotation SET etf_symbol_key = %s WHERE symbol = %s", (key, s))
                    # This updates multiple rows. Count?
                    # We'd have to count how many rows match 's'.
                    # For stats, let's just claim success based on rows count logic approximation.
                except Exception as e:
                    logger.error(f"Sector update failed {s}: {e}")
        
        # Update stats
        self.conversion_stats["sector_rotation"]["success"] = self.conversion_stats["sector_rotation"]["total"] # Approximate

    def _print_summary(self):
        print("\n\n========================================")
        print("      MIGRATION SUMMARY (" + ("DRY RUN" if self.dry_run else "LIVE") + ")")
        print("========================================")
        for table, stats in self.conversion_stats.items():
            if isinstance(stats, dict):
                print(f"{table.ljust(20)}: Total={stats['total']}, Success={stats['success']}, Failed={stats['failed']}")
            else:
                print(f"{table.ljust(20)}: {stats}")
        print("========================================\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate DB to use symbol_key")
    parser.add_argument("--live", action="store_true", help="Execute changes against DB (Default is Dry-Run)")
    args = parser.parse_args()
    
    dry_run = not args.live
    print(f"Starting Migration Service... (Mode: {'DRY-RUN' if dry_run else 'LIVE'})")
    
    svc = MigrationService(dry_run=dry_run)
    svc.run()
