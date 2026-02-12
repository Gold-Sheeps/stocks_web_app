
import sys
from pathlib import Path
from decimal import Decimal
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database

def check_price():
    db = Database()
    db.connect()
    try:
        rows = db.execute_query("SELECT trading_date, close FROM price_daily WHERE symbol_key='US:NVDA' ORDER BY trading_date DESC LIMIT 5")
        if rows:
            for r in rows:
                print(f"{r[0]}: {r[1]}")
        else:
            print("No price data found for US:NVDA")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_price()
