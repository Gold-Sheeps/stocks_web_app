
import sys
import os

# Add backend to path
# Add backend to path (parent of scripts is backend)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.database import Database

def create_tables():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        print("Creating tables...")
        
        # 1. Sector Rotation Table
        # Store daily calculated metrics
        # Composite PK: symbol + trading_date
        db.execute_command("""
            CREATE TABLE IF NOT EXISTS sector_rotation (
                symbol TEXT NOT NULL,
                trading_date DATE NOT NULL,
                current_return DECIMAL, -- e.g. 21-day return
                momentum DECIMAL,       -- e.g. 5-day return
                relative_strength DECIMAL, -- vs Benchmark
                rank INTEGER,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, trading_date)
            );
        """)
        print("- sector_rotation created")

        # 2. Fundamentals Table
        # Store basic data for now
        db.execute_command("""
            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol TEXT NOT NULL,
                period_end_date DATE NOT NULL,
                eps DECIMAL,
                revenue DECIMAL,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, period_end_date)
            );
        """)
        print("- fundamentals created")

        # 3. System Logs Table
        # For Data Update screen logs
        db.execute_command("""
            CREATE TABLE IF NOT EXISTS system_logs (
                log_id SERIAL PRIMARY KEY,
                job_name TEXT NOT NULL,
                status TEXT NOT NULL, -- RUNNING, SUCCESS, FAILED
                message TEXT,
                details JSONB, -- Store list of failed symbols etc.
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("- system_logs created")
        
        # 4. Ensure foreign keys or indexes if needed?
        # Instruments symbol is key, but we might not enforce strict FK to avoid update order issues.
        # Indexing for speed
        db.execute_command("CREATE INDEX IF NOT EXISTS idx_sector_rotation_date ON sector_rotation(trading_date);")
        db.execute_command("CREATE INDEX IF NOT EXISTS idx_system_logs_created ON system_logs(created_at DESC);")
        
        print("Done.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    create_tables()
