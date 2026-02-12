
import requests
import sys

BASE_URL = "http://localhost:8000/api/v1"

def check_server():
    print("Checking Monitor API...", flush=True)
    try:
        # Timeout is vital here to catch hangs
        res = requests.get(f"{BASE_URL}/monitor", timeout=3)
        print(f"Monitor API Status: {res.status_code}")
        if res.status_code == 200:
            print("Monitor API is OK.")
        else:
            print("Monitor API returned error.")
    except Exception as e:
        print(f"Monitor API Failed: {e}")

    print("\nChecking Watchlist API...", flush=True)
    try:
        res = requests.get(f"{BASE_URL}/watchlist", timeout=3)
        print(f"Watchlist API Status: {res.status_code}")
        print(f"Watchlist Response: {res.text[:100]}...")
    except Exception as e:
        print(f"Watchlist API Failed: {e}")

if __name__ == "__main__":
    check_server()
