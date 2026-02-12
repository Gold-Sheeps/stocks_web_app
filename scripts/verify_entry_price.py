import requests
import json

BASE_URL = "http://localhost:8000/api/v1"

def test_entry_price():
    # 1. Add a stock to watchlist (e.g., AAPL)
    # Ensure AAPL exists in price_daily for this to work perfectly, 
    # otherwise entry_price might be None if no data.
    # We'll use 'AAPL' as it's likely seeded.
    
    print("Adding AAPL to watchlist...")
    payload = {
        "symbol": "AAPL",
        "status": "Research",
        "memo": "Testing Entry Price",
        "tags": ["test"],
        "fair_value_min": 100,
        "fair_value_max": 200,
        "alert_config": {"price_target": 150}
    }
    
    # Clean up first
    try:
        requests.delete(f"{BASE_URL}/watchlist/AAPL")
    except:
        pass
        
    response = requests.post(f"{BASE_URL}/watchlist", json=payload)
    if response.status_code == 200:
        print("Successfully added AAPL.")
    else:
        print(f"Failed to add AAPL: {response.text}")
        return

    # 2. Fetch watchlist and check entry_price
    print("Fetching watchlist...")
    response = requests.get(f"{BASE_URL}/watchlist")
    if response.status_code == 200:
        items = response.json()
        aapl_item = next((item for item in items if item['symbol'] == 'AAPL'), None)
        
        if aapl_item:
            print(f"Parsed Item: {json.dumps(aapl_item, indent=2)}")
            if aapl_item.get('entry_price') is not None:
                print(f"SUCCESS: Entry Price is set: {aapl_item['entry_price']}")
            else:
                print("FAILURE: Entry Price is None. (Maybe no price data in DB?)")
                
            if aapl_item.get('entry_date') is not None:
                print(f"SUCCESS: Entry Date is set: {aapl_item['entry_date']}")
            else:
                print("FAILURE: Entry Date is None.")
        else:
            print("FAILURE: AAPL not found in watchlist.")
    else:
        print(f"Failed to fetch watchlist: {response.text}")

if __name__ == "__main__":
    test_entry_price()
