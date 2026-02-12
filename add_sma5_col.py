import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from app.db.database import Database

def main():
    print("Adding sma5 column to indicator_daily...")
    db = Database()
    if db.connect():
        # Check if column exists
        res = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name='indicator_daily' AND column_name='sma5'")
        if not res:
            print("sma5 column missing. Adding...")
            db.execute_command("ALTER TABLE indicator_daily ADD COLUMN sma5 NUMERIC")
            print("Added sma5 column.")
        else:
            print("sma5 column already exists.")
        db.disconnect()

if __name__ == "__main__":
    main()
