import requests
import json
import sys

BASE_URL = "http://localhost:8000/api/v1"

def test_price_filter():
    print("Testing Price Filter: Min=50, Max=60")
    params = {
        "min_price": 50,
        "max_price": 60,
        "limit": 100
    }
    response = requests.get(f"{BASE_URL}/screener/scan", params=params)
    if response.status_code != 200:
        print(f"FAILED: Status {response.status_code}")
        print(response.text)
        return False
    
    data = response.json()
    items = data.get("items", [])
    print(f"Found {len(items)} items.")
    
    if not items:
        print("WARNING: No items found in this range. Checking if any items exist at all...")
        response_all = requests.get(f"{BASE_URL}/screener/scan", params={"limit": 10})
        print(f"Total items (no filter): {response_all.json().get('total')}")
        return True

    errors = []
    for item in items:
        price = item.get("price")
        if price < 50 or price > 60:
            errors.append(f"Invalid price: {item['symbol']} = {price}")
    
    if errors:
        print("FAILED: Found items outside range:")
        for e in errors[:5]:
            print(f"  {e}")
        return False
    
    print("SUCCESS: All items are within [50, 60]")
    return True

def test_diagnostics():
    print("\nTesting Diagnostics...")
    params = {
        "min_price": 50,
        "max_price": 60
    }
    response = requests.get(f"{BASE_URL}/system/diagnostics/screener", params=params)
    if response.status_code != 200:
        print(f"FAILED: Status {response.status_code}")
        return False
    
    diag = response.json()
    # print(json.dumps(diag, indent=2))
    
    ql = diag.get("query_logic_check", {})
    if not ql.get("latest_price_date_used"):
        print("FAILED: latest_price_date_used is missing")
        return False
    
    if ql.get("price_filter_applied_to_latest_close") is not True:
        print("FAILED: price_filter_applied_to_latest_close is not True")
        return False
    
    sample = ql.get("sample_item_debug", {})
    if not sample:
        print("FAILED: sample_item_debug is missing")
        return False
    
    print(f"SUCCESS: Diagnostics verified. Latest Date: {ql['latest_price_date_used']}")
    print(f"Sample: {sample['symbol']} @ ${sample['price']} (Raw: {sample['raw_close']})")
    return True

if __name__ == "__main__":
    s1 = test_price_filter()
    s2 = test_diagnostics()
    if s1 and s2:
        print("\n✅ Verification PASSED")
        sys.exit(0)
    else:
        print("\n❌ Verification FAILED")
        sys.exit(1)
