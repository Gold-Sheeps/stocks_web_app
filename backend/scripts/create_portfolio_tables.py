import os
import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.db.database import Database

def create_tables():
    db = Database()
    if not db.connect():
        print("Failed to connect to database")
        return

    try:
        with db.connection.cursor() as cursor:
            # Create trades table
            # Drop existing tables to ensure schema update
            cursor.execute("DROP TABLE IF EXISTS trades CASCADE;")
            cursor.execute("DROP TABLE IF EXISTS holdings CASCADE;")
            print("Dropped existing tables")

            # Create trades table
            cursor.execute("""
                CREATE TABLE trades (
                    trade_id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    trade_date DATE NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    quantity DECIMAL NOT NULL,
                    price DECIMAL NOT NULL,
                    total_amount DECIMAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    fx_rate_to_jpy DECIMAL DEFAULT 1.0,
                    asset_type TEXT NOT NULL,
                    realized_pnl DECIMAL,
                    memo TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            print("Created trades table")

            # Create holdings table
            cursor.execute("""
                CREATE TABLE holdings (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    market TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    quantity DECIMAL NOT NULL DEFAULT 0,
                    average_cost DECIMAL NOT NULL DEFAULT 0,
                    total_cost DECIMAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    current_price DECIMAL,
                    market_value DECIMAL,
                    gain_loss DECIMAL,
                    gain_loss_pct DECIMAL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            print("Created holdings table")
            
            db.connection.commit()
            print("Portfolio tables created successfully")

    except Exception as e:
        print(f"Error creating tables: {e}")
        db.connection.rollback()
    finally:
        db.disconnect()

if __name__ == "__main__":
    create_tables()
