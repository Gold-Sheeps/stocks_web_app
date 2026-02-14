import requests
import json
from decimal import Decimal
from datetime import datetime

BASE_URL = "http://localhost:8000/api/v1/portfolio"

def test_portfolio():
    print("=== Portfolio Verification Start ===")
    
    current_year = datetime.now().year
    date_1 = f"{current_year}-01-01"
    date_2 = f"{current_year}-01-02"
    
    # 1. Clean up (Delete all trades if possible, or just ignore previous)
    # Since we don't have 'delete all', we assume we start fresh or append.
    # Let's add a unique symbol for testing to avoid conflict.
    symbol = "TEST_STOCK"
    
    # Prerequisite: Ensure symbol exists in instruments? 
    # Our code checks foreign key? 
    # schemas.py TradeCreate says symbol_key.
    # DB trades table has symbol TEXT. NO FOREIGN KEY constraint in create_portfolio_tables.py!
    # So we can use any symbol.
    
    # 1. Add BUY Trade (Long 10 @ 100)
    print("\n1. Adding BUY Trade (10 @ 100)...")
    trade_data = {
        "symbol_key": symbol,
        "market": "US",
        "asset_type": "EQUITY",
        "side": "BUY",
        "trade_date": date_1,
        "shares": 10,
        "price": 100,
        "currency": "USD"
    }
    res = requests.post(f"{BASE_URL}/trade", json=trade_data)
    if res.status_code != 200:
        print(f"FAILED: {res.text}")
        return
    print("OK")
    
    # Verify Holding
    res = requests.get(f"{BASE_URL}/")
    data = res.json()
    holding = next((h for h in data['holdings'] if h['symbol'] == symbol), None)
    if holding and Decimal(str(holding['shares'])) == 10:
        print(f"Verified Holding: {holding['shares']} shares. OK.")
    else:
        print(f"FAILED Verification: {holding}")

    # 2. Add SELL Trade (Sell 5 @ 120) -> Realized Gain 100 ((120-100)*5)
    print("\n2. Adding SELL Trade (5 @ 120)...")
    trade_data['side'] = "SELL"
    trade_data['trade_date'] = date_2
    trade_data['shares'] = 5
    trade_data['price'] = 120
    res = requests.post(f"{BASE_URL}/trade", json=trade_data)
    print("OK" if res.status_code == 200 else f"FAILED: {res.text}")

    # Verify Holding (Should be 5) and Best Performer (Should be TEST_STOCK if highest)
    res = requests.get(f"{BASE_URL}/")
    data = res.json()
    holding = next((h for h in data['holdings'] if h['symbol'] == symbol), None)
    if holding and Decimal(str(holding['shares'])) == 5:
        print(f"Verified Holding: {holding['shares']} shares. OK.")
    else:
        print(f"FAILED Verification. Holding: {holding}")
        
    print(f"Performance: {data['performance']}")
    print(f"Best Performer: {data['performance']['best_performer']}")
    # Note: If other trades exist, it might not be TEST_STOCK, but we assume clean DB or high gain.
    
    # 3. Add SHORT Trade (Sell 10 @ 120) -> Net Short 5
    # Current: Long 5 @ 100.
    # Sell 10 @ 120.
    # - Close 5 @ 120 (Gain (120-100)*5 = 100). Total Gain = 200.
    # - Open Short 5 @ 120.
    print("\n3. Adding Short SELL Trade (10 @ 120)...")
    trade_data['shares'] = 10
    trade_data['price'] = 120
    res = requests.post(f"{BASE_URL}/trade", json=trade_data)
    print("OK" if res.status_code == 200 else f"FAILED: {res.text}")

    # Verify Holding (Should be -5)
    res = requests.get(f"{BASE_URL}/")
    data = res.json()
    holding = next((h for h in data['holdings'] if h['symbol'] == symbol), None)
    if holding and Decimal(str(holding['shares'])) == -5:
        print(f"Verified Holding: {holding['shares']} shares (Short). OK.")
    else:
        print(f"FAILED Verification: {holding}")

    # 4. Cleanup (Delete the trades)
    print("\n4. Cleaning up...")
    # Get trades for symbol (not easy via API list, but we can verify logic manually or via DB)
    # For now, end test.
    
    print("\n=== Verification Complete ===")

if __name__ == "__main__":
    test_portfolio()
