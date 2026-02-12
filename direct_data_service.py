
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.services.data_service import DataService

def run_direct():
    with open("debug_output.txt", "w", encoding="utf-8") as f:
        sys.stdout = f
        print("=== Running DataService Directly ===")
        service = DataService()
        
        # Test Indices Update
        print("\n--- Updating Indices ---")
        result = service.update_all_data(range_days=5, targets=["Indices"])
        print(f"Result: {result}")
        
        # Test Sector Update
        print("\n--- Updating Sector ---")
        result_sector = service.update_all_data(range_days=30, targets=["Sector"])
        print(f"Result Sector: {result_sector}")
        
    sys.stdout = sys.__stdout__
    print("Done. Check debug_output.txt")

if __name__ == "__main__":
    run_direct()
