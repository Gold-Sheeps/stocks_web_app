import psycopg

def check_watchlist_table():
    try:
        conn = psycopg.connect(
            host="localhost",
            port="5432",
            dbname="postgres",
            user="postgres",
            password="test"
        )
        cur = conn.cursor()
        
        # Check if table exists
        cur.execute("SELECT to_regclass('public.watchlist');")
        result = cur.fetchone()
        
        if result and result[0]:
            print("Watchlist table exists.")
            cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'watchlist';")
            columns = cur.fetchall()
            for col in columns:
                print(f"  - {col[0]}: {col[1]}")
        else:
            print("Watchlist table does NOT exist.")
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_watchlist_table()
