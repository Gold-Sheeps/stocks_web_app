import psycopg
import sys
import os

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), "backend"))
from app.core.config import settings

def check_trades_schema():
    try:
        conn = psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        cursor = conn.cursor()
        
        print("--- All Columns in public.trades ---")
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'trades' 
              AND table_schema = 'public'
            ORDER BY ordinal_position;
        """)
        columns = cursor.fetchall()
        for col in columns:
            print(f"{col[0]} ({col[1]})")
            
        print("\n--- One row sample ---")
        cursor.execute("SELECT * FROM trades LIMIT 1;")
        row = cursor.fetchone()
        if row:
            for i, val in enumerate(row):
                print(f"{columns[i][0]}: {val}")
        else:
            print("No data in trades")
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_trades_schema()
