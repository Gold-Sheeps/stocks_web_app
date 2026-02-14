import psycopg
import sys
import os

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), "backend"))
from app.core.config import settings

def prepare_and_update():
    try:
        conn = psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        cursor = conn.cursor()
        
        print("1. Deduplicating price_daily...")
        cursor.execute("""
            DELETE FROM price_daily a USING (
              SELECT MIN(ctid) as min_ctid, symbol_key, trading_date
              FROM price_daily
              GROUP BY symbol_key, trading_date
              HAVING COUNT(*) > 1
            ) b
            WHERE a.symbol_key = b.symbol_key 
              AND a.trading_date = b.trading_date 
              AND a.ctid <> b.min_ctid;
        """)
        print(f"Deleted {cursor.rowcount} duplicate rows.")
        
        print("2. Adding unique constraint to price_daily...")
        # Check if constraint already exists
        cursor.execute("SELECT 1 FROM pg_constraint WHERE conname = 'price_daily_symbol_key_date_unique';")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE price_daily ADD CONSTRAINT price_daily_symbol_key_date_unique UNIQUE (symbol_key, trading_date);")
            print("Constraint added.")
        else:
            print("Constraint already exists.")
            
        conn.commit()
        cursor.close()
        conn.close()
        
        print("3. Running update_monitor_data.py...")
        # Now run the actual update script
        import subprocess
        result = subprocess.run([sys.executable, "backend/scripts/update_monitor_data.py"], capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("Errors from script:")
            print(result.stderr)
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    prepare_and_update()
