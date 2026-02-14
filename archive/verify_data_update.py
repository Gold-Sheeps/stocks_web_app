
import requests
import time
import sys

BASE_URL = "http://localhost:8000/api/v1"

def test_data_update():
    print("=== Testing Data Update API ===", flush=True)
    
    # 1. Trigger Update for Indices (small set)
    print("\n--- 1. Testing Indices Update ---", flush=True)
    payload = {
        "range_days": 5,
        "targets": ["Indices"]
    }
    
    try:
        print(f"Sending request to {BASE_URL}/system/update-data", flush=True)
        start = time.time()
        res = requests.post(f"{BASE_URL}/system/update-data", json=payload)
        elapsed = time.time() - start
        
        print(f"Status Code: {res.status_code}", flush=True)
        if res.status_code == 200:
            data = res.json()
            print(f"Response: {data}", flush=True)
            if data.get("status") == "success":
                print("SUCCESS: Indices Update triggered and completed.", flush=True)
            else:
                print("FAILED: API returned error status.", flush=True)
        else:
            print(f"FAILED: HTTP Error {res.text}", flush=True)
            
        print(f"Time Taken: {elapsed:.2f}s", flush=True)

    except Exception as e:
        print(f"Indices Exception: {e}", flush=True)

    # 2. Check Logs
    print("\n--- 2. Checking System Logs ---", flush=True)
    try:
        res_logs = requests.get(f"{BASE_URL}/system/logs?limit=5")
        if res_logs.status_code == 200:
            logs = res_logs.json()
            for log in logs:
                print(f"[{log['created_at']}] {log['status']} - {log['message']}", flush=True)
        else:
            print("Failed to fetch logs", flush=True)
    except Exception as e:
        print(f"Log Fetch Exception: {e}", flush=True)

    # 3. Trigger Sector Rotation
    print("\n--- 3. Testing Sector Rotation Update ---", flush=True)
    payload_sector = {
        "range_days": 45, # Need enough data for 21d return (30 calendar days is borderline)
        "targets": ["Sector"]
    }
    try:
        print(f"Sending Sector Request...", flush=True)
        # This might take longer due to 11 sectors + benchmark
        res = requests.post(f"{BASE_URL}/system/update-data", json=payload_sector)
        if res.status_code == 200:
            print("Sector Update Triggered", flush=True)
            print(res.json(), flush=True)
        else:
            print(f"Sector Update Failed: {res.text}", flush=True)
            
    except Exception as e:
        print(f"Sector Request Error: {e}", flush=True)

    # 4. Check Frontend Availability
    print("\n--- 4. Checking Frontend Availability ---", flush=True)
    try:
        res = requests.get("http://localhost:8000/frontend/data_update.html")
        print(f"Frontend Status Code: {res.status_code}", flush=True)
        if res.status_code == 200:
            print("SUCCESS: frontend/data_update.html is accessible.", flush=True)
        else:
            print(f"FAILED: accessible status {res.status_code}", flush=True)
    except Exception as e:
        print(f"Frontend Check Error: {e}", flush=True)

if __name__ == "__main__":
    test_data_update()
