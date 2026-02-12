
import sys
import os
sys.path.append(os.path.abspath("backend"))

import psycopg
from app.core.config import settings

def check_db_direct():
    print("Connecting to DB (Direct)...")
    try:
        conninfo = f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} user={settings.postgres_user} password={settings.postgres_password}"
        print(f"Direct DB Connect: {conninfo}", flush=True)
        conn = psycopg.connect(conninfo)
        cursor = conn.cursor()
        
        print("Checking watchlist table...")
        cursor.execute("SELECT * FROM watchlist")
        rows = cursor.fetchall()
        print(f"Watchlist rows: {len(rows)}")
        for row in rows:
            print(row)
            
        cursor.close()
        conn.close()

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db_direct()
