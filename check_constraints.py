import psycopg
import sys
import os

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), "backend"))
from app.core.config import settings

def check_constraints(table_name):
    try:
        conn = psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        cursor = conn.cursor()
        
        print(f"--- Constraints for {table_name} ---")
        cursor.execute(f"SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = '{table_name}'::regclass;")
        for row in cursor.fetchall():
            print(f"{row[0]}: {row[1]}")
            
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error checking {table_name}: {e}")

if __name__ == "__main__":
    check_constraints("price_daily")
    print()
    check_constraints("prices_daily")
