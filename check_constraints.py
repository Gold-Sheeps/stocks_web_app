
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.db.database import Database

def check_constraints():
    db = Database()
    if not db.connect():
        print("Failed to connect")
        return

    try:
        print("=== Checking Constraints on price_daily ===")
        query = """
            SELECT conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE conrelid = 'instruments'::regclass
        """
        rows = db.execute_query(query)
        for r in rows:
            print(f"- {r[0]}: {r[1]}")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    check_constraints()
