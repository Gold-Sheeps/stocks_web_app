"""
最適化したコードが実際にロードされているか確認
monitor_serviceの実際のコードをチェック
"""
import inspect
import sys
sys.path.append('c:/Users/o_van/Desktop/stocks_market_checker/backend')

try:
    from app.services.monitor_service import MonitorService
    
    print("=" * 60)
    print("monitor_service.get_market_indicesのソースコード確認")
    print("=" * 60)
    
    # get_market_indicesメソッドのソースコードを取得
    source = inspect.getsource(MonitorService.get_market_indices)
    
    # 最適化されているかチェック
    if "symbol_placeholders" in source:
        print("✓ 最適化版がロードされています（一括クエリ）")
        print("\nキーワード確認:")
        print(f"  - 'symbol_placeholders' 存在: はい")
        print(f"  - 'WITH latest_prices' 存在: {'WITH latest_prices' in source}")
    elif "for symbol_key, name in indices_symbols.items():" in source:
        print("✗ 旧版がロードされています（ループクエリ）")
        print("\n原因: uvicornが新しいコードをリロードしていません")
        print("対策: backend側のターミナルでuvicornを再起動してください")
    else:
        print("? 不明なバージョン")
    
    print("\n" + "=" * 60)
    
except Exception as e:
    print(f"エラー: {e}")
    import traceback
    traceback.print_exc()
