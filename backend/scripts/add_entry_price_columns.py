import os
import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.db.database import Database

def add_columns():
    db = Database()
    if not db.connect():
        print("Failed to connect to database")
        return

    try:
        # Check if columns exist
        with db.connection.cursor() as cursor:
            # Add entry_price
            cursor.execute("""
                ALTER TABLE watchlist 
                ADD COLUMN IF NOT EXISTS entry_price DECIMAL(10, 2);
            """)
            print("Added entry_price column")

            # Add entry_date
            cursor.execute("""
                ALTER TABLE watchlist 
                ADD COLUMN IF NOT EXISTS entry_date DATE DEFAULT CURRENT_DATE;
            """)
            print("Added entry_date column")
            
            db.connection.commit()
            print("Migration completed successfully")

    except Exception as e:
        print(f"Error during migration: {e}")
        db.connection.rollback()
    finally:
        db.disconnect()

if __name__ == "__main__":
    add_columns()
