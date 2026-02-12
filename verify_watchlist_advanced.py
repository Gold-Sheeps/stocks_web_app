
import requests
import json
import random

BASE_URL = "http://localhost:8000/api/v1"
SYMBOL = "NVDA"

def test_watchlist():
    print("=== Testing Watchlist API ===")
    
    # 1. Add item
    print(f"\n1. Adding {SYMBOL} to watchlist...")
    payload = {
        "symbol": SYMBOL,
        "status": "Setup",
        "memo": "Testing Watchlist Feature",
        "tags": ["AI", "Semiconductor"],
        "fair_value_min": 100,
        "fair_value_max": 200,
        "alert_config": {
            "price_above": 150,
            "rsi_below": 30
        }
    }
    
    try:
        res = requests.post(f"{BASE_URL}/watchlist", json=payload, timeout=5)
        print(f"Status Code: {res.status_code}", flush=True)
        print(f"Response: {res.json()}", flush=True)
        if res.status_code != 200:
            print("Failed to add item.", flush=True)
            return
    except Exception as e:
        print(f"Error: {e}", flush=True)
        return

    # 2. Get items
    print("\n2. Getting watchlist items...", flush=True)
    try:
        res = requests.get(f"{BASE_URL}/watchlist", timeout=5)
        if res.status_code != 200:
             print(f"Get Watchlist Failed: {res.status_code}", flush=True)
             print(f"Response: {res.text}", flush=True)
             return

        # print(f"Data: {json.dumps(res.json(), indent=2)}")
        items = res.json()
        found = False
        for item in items:
            if item['symbol'] == SYMBOL:
                print(f"Found {SYMBOL} in watchlist!", flush=True)
                print(f"Status: {item['status']}", flush=True)
                print(f"Memo: {item['memo']}", flush=True)
                print(f"Tags: {item['tags']}", flush=True)
                print(f"Alert Config: {item['alert_config']}", flush=True)
                found = True
                break
        if not found:
            print(f"{SYMBOL} not found in watchlist.", flush=True)
    except Exception as e:
        print(f"Error: {e}", flush=True)

    # 3. Update item
    print(f"\n3. Updating {SYMBOL}...", flush=True)
    update_payload = {
        "symbol": SYMBOL,
        "status": "Action",
        "memo": "Ready to buy!",
        "tags": ["AI", "Leader"],
        "fair_value_min": 110,
        "fair_value_max": 220,
        "alert_config": {
            "price_above": 160
        }
    }
    try:
        res = requests.put(f"{BASE_URL}/watchlist/{SYMBOL}", json=update_payload, timeout=5)
        print(f"Status Code: {res.status_code}", flush=True)
        print(f"Response: {res.json()}", flush=True)
    except Exception as e:
        print(f"Error: {e}", flush=True)
        
    # 4. Verify update
    print("\n4. Verifying update...", flush=True)
    try:
        res = requests.get(f"{BASE_URL}/watchlist", timeout=5)
        items = res.json()
        for item in items:
            if item['symbol'] == SYMBOL:
                print(f"Updated Status: {item['status']}", flush=True)
                if item['status'] == 'Action':
                     print("Update successful!", flush=True)
                else:
                     print("Update failed.", flush=True)
                break
    except Exception as e:
        print(f"Error: {e}", flush=True)

    # 5. Delete item (Optional - maybe keep it for manual check?)
    # print(f"\n5. Deleting {SYMBOL}...")
    # try:
    #     res = requests.delete(f"{BASE_URL}/watchlist/{SYMBOL}")
    #     print(f"Status Code: {res.status_code}")
    #     print(f"Response: {res.json()}")
    # except Exception as e:
    #     print(f"Error: {e}")

if __name__ == "__main__":
    test_watchlist()
