
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.db.database import Database
from app.services.indicator_service import IndicatorService
from app.services.data_service import DataService

def run_top_50():
    print("--- Generating Indicators for Top 50 Stocks ---")
    db = Database()
    db.connect()
    
    # Get top 50 US stocks by volume
    # Note: price_daily for 'today' might need to be aggregated or just take recent distinct
    # Let's take distinct valid symbols from instruments first, then order by volume from recent price
    query = """
        SELECT i.symbol_key 
        FROM instruments i
        JOIN price_daily p ON i.symbol_key = p.symbol_key
        WHERE i.market = 'US'
        ORDER BY p.trading_date DESC, p.volume DESC
        LIMIT 50
    """
    rows = db.execute_query(query)
    symbols = [r[0] for r in rows] if rows else []
    # Deduplicate while preserving order
    unique_symbols = []
    seen = set()
    for s in symbols:
        if s not in seen:
            unique_symbols.append(s)
            seen.add(s)
    
    print(f"Found {len(unique_symbols)} top symbols by volume.")
    
    service = IndicatorService()
    # Mock data service fetcher to avoid fetching if data exists[WARN] 
    # IndicatorService.calculate_and_save_indicators fetches from price_daily directly.
    # So we just need to run it.
    
    count = 0
    for sym in unique_symbols:
        try:
            print(f"Processing {sym}...")
            # Normalize just in case, though DB query returned normalized keys ideally
            service.calculate_and_save_indicators(sym)
            count += 1
        except Exception as e:
            print(f"Error for {sym}: {e}")
            
    print(f"Completed {count} symbols.")
    db.disconnect()

if __name__ == "__main__":
    run_top_50()
