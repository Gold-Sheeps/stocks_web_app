import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
from app.db.database import Database

def check_fx():
    db = Database()
    db.connect()
    try:
        # Check for symbols that look like FX pairs
        print("Checking for USD/JPY symbols...")
        rows = db.execute_query("SELECT symbol, name, market FROM instruments WHERE symbol_key LIKE '%JPY%' OR symbol_key LIKE 'USD%'")
        for row in rows:
            print(row)
            
        # Also check price_daily table for recent data
        print("\nChecking price_daily for recent data...")
        if rows:
            symbol = rows[0][0] # use first found symbol
            prices = db.execute_query("SELECT trading_date, close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1", (symbol,))
            print(f"Latest price for {symbol}: {prices}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_fx()
