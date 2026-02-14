
import requests
import json
import sys

try:
    r = requests.get('http://localhost:8002/api/v1/stock/NVDA')
    if r.status_code == 200:
        data = r.json()
        ind = data.get('indicators', {})
        stock_info = data.get('stock_info', {})
        print(f"Stock Name: {stock_info.get('name')}")
        
        print("--- API INDICATOR CHECK ---")
        print(f"EMA21: {ind.get('ema21')}")
        print(f"Pivot: {ind.get('pivot')}")
        print(f"RS Rating: {ind.get('rs_rating')}")
        print(f"Volume Ratio: {ind.get('volume_ratio')}")
        print("---------------------------")
        
        # Check if they are None/null
        if ind.get('ema21') is None:
             print("FAIL: EMA21 is null")
        else:
             print("PASS: EMA21 is present")
             
    else:
        print(f"Error: Status {r.status_code}")
except Exception as e:
    print(f"Error: {e}")
