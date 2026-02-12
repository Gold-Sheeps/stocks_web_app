import os
import re

# Fix files by replacing full-width parentheses
files = [
    r'c:\Users\o_van\Desktop\stocks_market_checker\backend\app\services\stock_detail_service.py',
    r'c:\Users\o_van\Desktop\stocks_market_checker\backend\app\services\screener_service.py'
]

for file_path in files:
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Replace full-width parentheses with half-width
        content = content.replace('（', '(')
        content = content.replace('）', ')')
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'✅ Fixed full-width parentheses in: {os.path.basename(file_path)}')
    except Exception as e:
        print(f'❌ Error: {e}')

print('\n✅ All files fixed')
