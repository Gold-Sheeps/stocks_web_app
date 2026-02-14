"""
全サービスファイルのクエリ使用状況を分析
"""
import os
import re

services_dir = r"c:\Users\o_van\Desktop\stocks_market_checker\backend\app\services"

print("=" * 60)
print("サービスファイル クエリ分析")
print("=" * 60)

for filename in os.listdir(services_dir):
    if not filename.endswith('.py'):
        continue
    
    filepath = os.path.join(services_dir, filename)
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # クエリパターンを検索
    execute_count = len(re.findall(r'cursor\.execute', content))
    for_loops = len(re.findall(r'^\s*for\s+.+\s+in\s+.+:', content, re.MULTILINE))
    placeholders = len(re.findall(r'symbol_placeholders|%s', content))
    
    print(f"\n{filename}:")
    print(f"  cursor.execute: {execute_count}回")
    print(f"  forループ: {for_loops}回")
    print(f"  プレースホルダー使用: {placeholders}箇所")
    
    # N+1問題の可能性をチェック
    if execute_count > 0 and for_loops > 0:
        # forループ内でexecuteしている可能性
        for_with_execute = re.findall(
            r'(for\s+.+\s+in\s+.+:.*?(?:cursor\.execute|\.execute))', 
            content, 
            re.DOTALL
        )
        if for_with_execute:
            print(f"  ⚠ 潜在的N+1問題あり（ループ内クエリ）")

print("\n" + "=" * 60)
print("分析完了")
print("=" * 60)
