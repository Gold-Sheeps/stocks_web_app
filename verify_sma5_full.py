import sys
import os
import requests
import json
import psycopg
# Add backend to path to use config
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from app.core.config import settings

def check_db():
    print("--- Database Check ---")
    conninfo = f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} user={settings.postgres_user} password={settings.postgres_password}"
    try:
        with psycopg.connect(conninfo) as conn:
            cursor = conn.cursor()
            # Check latest 5 rows
            query = """
                SELECT trading_date, sma5, sma20 
                FROM indicator_daily 
                WHERE symbol_key = 'NVDA' 
                ORDER BY trading_date DESC 
                LIMIT 5
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            print(f"Latest 5 rows for NVDA in DB ({settings.postgres_db}):")
            for row in rows:
                print(f"Date: {row[0]}, SMA5: {row[1]}, SMA20: {row[2]}")
    except Exception as e:
        print(f"DB Error: {e}")

def check_api():
    print("\n--- API Check (Port 8000) ---")
    try:
        r = requests.get('http://localhost:8000/api/v1/stock/NVDA', timeout=5)
        if r.status_code == 200:
            data = r.json()
            indicators = data.get('indicators', {})
            print("API Response 'indicators':")
            print(json.dumps(indicators, indent=2))
        else:
            print(f"API Error: Status {r.status_code}")
            print(r.text)
    except Exception as e:
        print(f"API Request Failed: {e}")

if __name__ == "__main__":
    check_db()
    check_api()
