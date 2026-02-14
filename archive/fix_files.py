import os

# Fix stock_detail_service.py
file1 = r'c:\Users\o_van\Desktop\stocks_market_checker\backend\app\services\stock_detail_service.py'
try:
    with open(file1, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # Replace execute( with execute_query(
    content = content.replace('self.db.execute(', 'self.db.execute_query(')
    
    with open(file1, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'✅ Fixed: {file1}')
except Exception as e:
    print(f'❌ Error fixing {file1}: {e}')

# Fix screener_service.py
file2 = r'c:\Users\o_van\Desktop\stocks_market_checker\backend\app\services\screener_service.py'
try:
    with open(file2, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # Replace execute( with execute_query(
    content = content.replace('self.db.execute(', 'self.db.execute_query(')
    
    with open(file2, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'✅ Fixed: {file2}')
except Exception as e:
    print(f'❌ Error fixing {file2}: {e}')

print('\n✅ All files fixed with UTF-8 encoding')
