import psycopg
import sys
import os

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), "backend"))
from app.core.config import settings

def check_schema():
    try:
        conn = psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        cursor = conn.cursor()
        
        print("--- Tables ---")
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        for row in cursor.fetchall():
            print(row[0])
            
        print("\n--- Columns for price_daily ---")
        cursor.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'price_daily';")
        for row in cursor.fetchall():
            print(f"{row[0]}: {row[1]}")
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_schema()
