import sys
sys.path.insert(0, 'backend')

try:
    from app.services.screener_service import ScreenerService
    print("✅ Import successful")
    
    service = ScreenerService()
    print("✅ Service instantiated")
    
    result = service.scan_stocks(limit=1)
    print(f"✅ Query executed: {len(result.get('results', []))} stocks found")
    print(f"Total stocks: {result.get('total_stocks')}")
    
except Exception as e:
    print(f"❌ Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
