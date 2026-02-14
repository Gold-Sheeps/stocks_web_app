import psycopg

def check_schema():
    conn = psycopg.connect(
        host="localhost",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="test"
    )
    cur = conn.cursor()

    print("=== indicator_daily columns ===")
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'indicator_daily'
        ORDER BY ordinal_position
    """)
    for row in cur.fetchall():
        print(row[0])
        
    print("\n=== price_daily columns ===")
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'price_daily'
        ORDER BY ordinal_position
    """)
    for row in cur.fetchall():
        print(row[0])

    conn.close()

if __name__ == "__main__":
    check_schema()
