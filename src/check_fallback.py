"""
Monitor service の get_metals 関数のフォールバック部分のみを修正するスクリプト
"""

code = '''
        # データが取得できなかった場合はダミーデータを返す
        if not result:
            print("Warning: Returning dummy metals data")
            return [
                MarketIndexData(
                    symbol="GLD",
                    name="金 (GLD)",
                    current_price=Decimal('185.50'),
                    change_pct=Decimal('0.5'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="SLV",
                    name="銀 (SLV)",
                    current_price=Decimal('23.45'),
                    change_pct=Decimal('-0.3'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="PALL",
                    name="パラジウム (PALL)",
                    current_price=Decimal('92.30'),
                    change_pct=Decimal('1.2'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="PPLT",
                    name="プラチナ (PPLT)",
                    current_price=Decimal('85.70'),
                    change_pct=Decimal('0.8'),
                    last_updated=datetime.now()
                ),
            ]
        
        return result
'''

print("修正コード:")
print(code)
print("\n上記のコードを monitor_service.py の get_metals() 関数の最後に追加してください。")
print("現在の 'return result' の前に挿入する形になります。")
