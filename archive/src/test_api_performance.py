"""
全APIエンドポイントのパフォーマンステスト
"""
import requests
import time

API_BASE = "http://localhost:8000/api/v1"

endpoints = [
    "/monitor",
    "/rotation/sectors",
    "/sector/XLK",
]

print("=" * 60)
print("APIパフォーマンステスト")
print("=" * 60)

for endpoint in endpoints:
    url = f"{API_BASE}{endpoint}"
    print(f"\n[テスト] {endpoint}")
    
    try:
        start = time.time()
        response = requests.get(url, timeout=10)
        elapsed = time.time() - start
        
        print(f"  ステータス: {response.status_code}")
        print(f"  実行時間: {elapsed:.2f}秒")
        print(f"  レスポンスサイズ: {len(response.content)} bytes")
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                print(f"  キー数: {len(data.keys())}")
        else:
            print(f"  エラー: {response.text[:100]}")
            
    except requests.exceptions.Timeout:
        print(f"  ✗ タイムアウト（10秒超過）")
    except Exception as e:
        print(f"  ✗ エラー: {e}")

print("\n" + "=" * 60)
print("テスト完了")
print("=" * 60)
