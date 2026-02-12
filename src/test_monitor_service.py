"""
monitor_serviceのget_market_indicesメソッドを直接テスト
"""
import sys
sys.path.append('c:/Users/o_van/Desktop/stocks_market_checker/backend')

from app.services.monitor_service import MonitorService
from app.database import Database
import time

print("=" * 60)
print("monitor_service.get_market_indicesテスト")
print("=" * 60)

db = Database()
service = MonitorService(db)

print("\n[実行開始]")
start = time.time()

try:
    result = service.get_market_indices()
    elapsed = time.time() - start
    
    print(f"✓ 実行成功")
    print(f"  実行時間: {elapsed:.3f}秒")
    print(f"  取得件数: {len(result)}件")
    
    if result:
        print(f"\n最初の3件:")
        for idx in result[:3]:
            print(f"  - {idx.name}: {idx.current_price}")
    
except Exception as e:
    elapsed = time.time() - start
    print(f"✗ エラー発生")
    print(f"  実行時間: {elapsed:.3f}秒")
    print(f"  エラー: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("テスト完了")
print("=" * 60)
