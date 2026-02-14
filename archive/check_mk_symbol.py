import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from app.db.database import Database

try:
    db = Database()
    db.connect()
    
    print("Checking MKSI in instruments table...")
    query = "SELECT symbol_key, name FROM instruments WHERE symbol_key = %s"
    result = db.execute_query(query, ('MKSI',))
    print(f"Exact match result: {result}")
    
    print("Checking similar symbols...")
    query_like = "SELECT symbol_key, name FROM instruments WHERE symbol_key LIKE %s"
    result_like = db.execute_query(query_like, ('%MKSI%',))
    print(f"Like match result: {result_like}")
    
    db.disconnect()
    
except Exception as e:
    print(f"Error: {e}")
