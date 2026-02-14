"""
APIエンドポイントに直接アクセスしてタイミングを測定
"""
import requests
import time
import sys

API_URL = "http://localhost:8000"

print("=" * 60)
print("APIエンドポイント直接テスト")
print("=" * 60)

# 1. ヘルスチェック（/）
print("\n[1] ヘルスチェック: GET /")
try:
    start = time.time()
    response = requests.get(f"{API_URL}/", timeout=3)
    elapsed = time.time() - start
    
    if response.status_code == 200:
        print(f"  ✓ 成功: {elapsed:.3f}秒")
        print(f"  レスポンス: {response.json()}")
    else:
        print(f"  ✗ 失敗: {response.status_code}")
except requests.Timeout:
    print(f"  ✗ タイムアウト（3秒）")
except Exception as e:
    print(f"  ✗ エラー: {e}")

# 2. Monitor API（タイムアウト30秒に設定）
print("\n[2] Monitor API: GET /api/v1/monitor")
print("  （最大30秒待機...）")
try:
    start = time.time()
    response = requests.get(f"{API_URL}/api/v1/monitor", timeout=30)
    elapsed = time.time() - start
    
    if response.status_code == 200:
        print(f"  ✓ 成功: {elapsed:.3f}秒")
        data = response.json()
        print(f"  キー: {list(data.keys())}")
        if 'market_indices' in data:
            print(f"  市場指数件数: {len(data['market_indices'])}件")
    else:
        print(f"  ✗ 失敗: {response.status_code}")
        print(f"  エラー: {response.text[:200]}")
except requests.Timeout:
    elapsed = time.time() - start
    print(f"  ✗ タイムアウト: {elapsed:.3f}秒")
except Exception as e:
    elapsed = time.time() - start
    print(f"  ✗ エラー({elapsed:.3f}秒): {e}")

print("\n" + "=" * 60)
print("テスト完了")
print("=" * 60)

# 明示的に終了
sys.exit(0)
