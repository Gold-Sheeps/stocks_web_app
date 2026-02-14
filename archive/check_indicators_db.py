import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

import psycopg
from app.core.config import settings

# Connect to DB
conninfo = f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} user={settings.postgres_user} password={settings.postgres_password}"
try:
    with psycopg.connect(conninfo) as conn:
        cursor = conn.cursor()
        
        # Check columns of indicator_daily
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='indicator_daily'")
        cols = [row[0] for row in cursor.fetchall()]
        print(f"Columns in indicator_daily: {cols}")
        
        # Check data for NVDA
        # Specifically check sma5, sma20, rsi14, etc.
        # Construct query based on available columns
        target_cols = ['trading_date', 'sma20', 'rsi14', 'macd', 'high_52w']
        if 'sma5' in cols:
            target_cols.append('sma5')
        else:
            print("WARNING: 'sma5' column not found in indicator_daily table!")
            
        if 'ma5' in cols:
            target_cols.append('ma5')
            
        query = f"SELECT {', '.join(target_cols)} FROM indicator_daily WHERE symbol_key = 'NVDA' ORDER BY trading_date DESC LIMIT 5"
        print(f"Executing: {query}")
        
        cursor.execute(query)
        rows = cursor.fetchall()
        print(f"Data for NVDA (Top 5):")
        for row in rows:
            print(row)
            
except Exception as e:
    print(f"Error: {e}")
