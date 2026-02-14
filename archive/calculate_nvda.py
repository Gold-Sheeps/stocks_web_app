import sys
import os

# Add src to path to import postgresql_connect
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from src.postgresql_connect import PostgreSQLConnect
from src.calculate_indicators_batch import calculate_indicators_for_symbol
from app.core.config import settings
import traceback

def main():
    symbol = "NVDA"
    print(f"Calculating indicators for {symbol}...")
    
    try:
        # Use settings from config
        db = PostgreSQLConnect(
            host=settings.postgres_host,
            port=int(settings.postgres_port),
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )
        if not db.connect():
            print("Failed to connect to DB")
            return

        rows, error = calculate_indicators_for_symbol(db, symbol)
        
        if error:
            print(f"Error: {error}")
        else:
            print(f"Success! Inserted {rows} rows.")
            
        db.disconnect()
        
    except Exception as e:
        print(f"Exception: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
