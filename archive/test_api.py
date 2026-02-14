import requests

print("=== API Tests ===")
print()

# Test 1: Screener
print("1. Screener API Test")
try:
    r = requests.get('http://localhost:8000/api/v1/screener/scan?limit=3')
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   Total Stocks: {data.get('total_stocks', 'N/A')}")
        print(f"   Count: {data.get('count', 'N/A')}")
        print(f"   ✅ SUCCESS")
    else:
        print(f"   ❌ ERROR: {r.text[:200]}")
except Exception as e:
    print(f"   ❌ EXCEPTION: {e}")

print()

# Test 2: Stock Detail
print("2. Stock Detail API Test (NVDA)")
try:
    r = requests.get('http://localhost:8000/api/v1/stock/NVDA')
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   Symbol: {data['stock_info']['symbol']}")
        print(f"   Current Price: ${data['stock_info']['current_price']}")
        print(f"   YTD Change: {data['stock_info'].get('ytd_change_pct', 'N/A')}%")
        print(f"   Price History: {len(data['price_history'])} days")
        print(f"   ✅ SUCCESS")
    else:
        print(f"   ❌ ERROR: {r.text[:200]}")
except Exception as e:
    print(f"   ❌ EXCEPTION: {e}")

print()

# Test 3: Signals
print("3. Signals API Test (NVDA)")
try:
    r = requests.get('http://localhost:8000/api/v1/stock/NVDA/signals')
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        signals = data.get('signals', {})
        print(f"   MACD+ Signals: {len(signals.get('MACD+', []))}")
        print(f"   BREAKOUT Signals: {len(signals.get('BREAKOUT', []))}")
        print(f"   HIGH_RS Signals: {len(signals.get('HIGH_RS', []))}")
        print(f"   ✅ SUCCESS")
    else:
        print(f"   ❌ ERROR: {r.text[:200]}")
except Exception as e:
    print(f"   ❌ EXCEPTION: {e}")

print()
print("=== End of Tests ===")
