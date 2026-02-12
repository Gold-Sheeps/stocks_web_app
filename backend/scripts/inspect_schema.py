
import sys
import os
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.db.database import Database

def inspect_schema():
    db = Database()
    if not db.connect():
        print("Failed to connect to database")
        return

    try:
        # Get all tables
        tables = db.execute_query("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name;
        """)
        
        print("Database Schema:")
        print("================")
        
        for table_row in tables:
            table_name = table_row[0]
            print(f"\nTable: {table_name}")
            print("-" * (len(table_name) + 7))
            
            # Get columns for each table
            columns = db.execute_query(f"""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns 
                WHERE table_schema = 'public' AND table_name = '{table_name}'
                ORDER BY ordinal_position;
            """)
            
            for col in columns:
                col_name, data_type, nullable = col
                null_str = "NULL" if nullable == 'YES' else "NOT NULL"
                print(f"  - {col_name} ({data_type}, {null_str})")

    except Exception as e:
        print(f"Error inspecting schema: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    inspect_schema()
