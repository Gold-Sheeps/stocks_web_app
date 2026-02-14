import sys
import os

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), 'backend')))

try:
    print("Importing ScreenerService...")
    from app.services.screener_service import ScreenerService
    print("✅ Import successful")

    print("Instantiating ScreenerService...")
    service = ScreenerService()
    print("✅ Instantiation successful")

    print("Calling scan_stocks...")
    result = service.scan_stocks(limit=1)
    print(f"✅ scan_stocks successful. Returned {result.get('count')} items.")
except Exception as e:
    print("❌ Error occurred!")
    import traceback
    traceback.print_exc()
