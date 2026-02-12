import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
from app.db.database import Database

def debug_db():
    db = Database()
    db.connect()
    
    print("\n--- Trades (id, symbol, side, qty, price, realized_pnl, date) ---")
    trades = db.execute_query("SELECT trade_id, symbol, side, quantity, price, realized_pnl, trade_date FROM trades ORDER BY trade_id")
    for t in trades:
        print(t)
        
    print("\n--- Holdings (symbol, shares, avg_cost, market_value, gain_loss) ---")
    holdings = db.execute_query("SELECT symbol, quantity, average_cost, market_value, gain_loss FROM holdings")
    for h in holdings:
        print(h)
    
    db.disconnect()

if __name__ == "__main__":
    debug_db()
