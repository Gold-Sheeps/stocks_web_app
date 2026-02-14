import psycopg
import sys
import os

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), "backend"))
from app.core.config import settings

def check_prices_daily():
    try:
        conn = psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        cursor = conn.cursor()
        
        print("--- Columns for prices_daily ---")
        cursor.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'prices_daily';")
        for row in cursor.fetchall():
            print(f"{row[0]}: {row[1]}")
            
        print("\n--- Constraints for prices_daily ---")
        cursor.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'prices_daily'::regclass;")
        for row in cursor.fetchall():
            print(f"{row[0]}: {row[1]}")
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error checking prices_daily: {e}")

if __name__ == "__main__":
    check_prices_daily()
