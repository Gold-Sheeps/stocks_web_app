import requests
import json
from decimal import Decimal
from datetime import datetime

BASE_URL = "http://localhost:8000/api/v1/portfolio"

def test_fx_conversion():
    print("=== FX Conversion Verification Start ===")
    
    current_year = datetime.now().year
    date_1 = f"{current_year}-01-01"
    symbol = "FX_TEST_STOCK"
    
    # 1. Inject Dummy Price Data for FX_TEST_STOCK
    # Since we can't easily use API to inject price, we might need a helper endpoint or use direct DB access (but script is outside app).
    # Alternative: Update PortfolioService to return trade price if current price is 0? No, that's bad logic.
    # We should use a symbol that HAS price data, e.g., NVDA if available.
    # But we want to test "USD" market logic explicitly without relying on external data state.
    # Let's direct insert into DB using app's Database class since we can import it (we fixed path in check_fx.py, so we can do it here too).
    
    import sys
    import os
    sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))
    from app.db.database import Database
    
    db = Database()
    db.connect()
    try:
        # Insert Price
        print("Injecting dummy price for FX_TEST_STOCK...")
        db.execute_command("""
            INSERT INTO price_daily (symbol_key, trading_date, open, high, low, close, volume, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol_key, trading_date) DO UPDATE SET close = EXCLUDED.close
        """, (symbol, date_1, 100, 105, 95, 120, 10000, 'test')) # Close 120
        
        # Verify Injection
        rows = db.execute_query("SELECT close FROM price_daily WHERE symbol_key = %s", (symbol,))
        print(f"DB Verification: Price for {symbol} is {rows}", flush=True)
    finally:
        db.disconnect()
        
    # 2. Add US BUY Trade (Long 10 @ 100 USD)
    print(f"\n2. Adding US BUY Trade (10 @ 100 USD) for {symbol}...")
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
        print(f"FAILED to add trade: {res.text}")
        return

    # 3. Get Portfolio Detail
    print("Getting Portfolio Detail...")
    res = requests.get(f"{BASE_URL}/")
    data = res.json()
    
    # 3. Verify Holding
    holding = next((h for h in data['holdings'] if h['symbol'] == symbol), None)
    if not holding:
        print(f"FAILED: Holding not found for {symbol}")
        return

    print(f"Holding: {holding}", flush=True)
    
    # Check Currency
    if holding['currency'] != 'JPY':
         print(f"FAILED: Currency should be JPY, got {holding['currency']}", flush=True)
    else:
         print("OK: Currency is JPY", flush=True)

    # Check Market Value
    mkt_val = Decimal(str(holding['market_value']))
    shares = Decimal(str(holding['shares']))
    price = Decimal(str(holding['current_price']))
    
    print(f"Details: Shares={shares}, Price={price}, MktVal={mkt_val}", flush=True)
    
    usd_val = shares * price
    if usd_val == 0:
        print("Warning: USD Value is 0 (Price might be 0)")
        implied_rate = 0
    else:
        implied_rate = mkt_val / usd_val
        
    print(f"Implied FX Rate: {implied_rate}", flush=True)
    
    if implied_rate > 100:
        print(f"OK: Market Value reflects conversion (Rate {implied_rate})", flush=True)
    else:
        print(f"FAILED: Market Value seems unconverted: {mkt_val}", flush=True)

    # Check Total Value (Summary)
    total_val = Decimal(str(data['performance']['total_value']))
    
    success = "TRUE" if implied_rate > 100 else "FALSE"
    
    print(f"\nFINAL_RESULT: TotalVal={total_val} ImpliedRate={implied_rate} Success={success}", flush=True)
    
    # 4. Cleanup
    # We can delete the trade if we want, but for now just verify logic.
    
    print("\n=== Verification Complete ===", flush=True)

if __name__ == "__main__":
    test_fx_conversion()
