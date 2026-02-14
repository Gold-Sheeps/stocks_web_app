import requests
import sys
import json

try:
    symbol = "NVDA"
    url = f'http://localhost:8001/api/v1/stock/{symbol}'
    
    response = requests.get(url)
    
    if response.status_code == 200:
        data = response.json()
        print("Success!")
        import json
        print("Stock Info:")
        print(json.dumps(data.get('stock_info', {}), indent=2, default=str))
        print("Indicators:")
        print(json.dumps(data.get('indicators', {}), indent=2, default=str))
    else:
        print(f"ERROR_STATUS: {response.text}")

except Exception as e:
    print(f"EXCEPTION: {e}")
