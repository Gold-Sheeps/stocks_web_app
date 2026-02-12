import requests
import json
import time

url = "http://localhost:8001/api/v1/stock/NVDA"
try:
    print(f"Requesting {url}...")
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        print("Success!")
        print("Stock Info:")
        print(json.dumps(data.get('stock_info', {}), indent=2))
        print("Indicators:")
        print(json.dumps(data.get('indicators', {}), indent=2))
    else:
        print(f"Error: {response.status_code} {response.text}")
except Exception as e:
    print(f"Exception: {e}")
