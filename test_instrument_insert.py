
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.db.database import Database

def test_insert():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        print("=== Testing Instrument Insert ===")
        # Try inserting a dummy instrument with market='US'
        query = """
            INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol_key) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        """
        if db.execute_command(query, ("TEST_US_INSERT", "US", "Test US", "USD")):
            print("SUCCESS: Inserted/Updated with market='US'")
        else:
            print("FAILED: Insert with market='US'")
            
        # Try inserting with market='FX'
        if db.execute_command(query, ("TEST_FX_INSERT", "FX", "Test FX", "USD")):
             print("SUCCESS: Inserted/Updated with market='FX'")
        else:
             print("FAILED: Insert with market='FX'")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    test_insert()
