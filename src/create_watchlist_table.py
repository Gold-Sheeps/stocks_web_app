
import psycopg
from datetime import datetime

# Connection details
DB_PARAMS = {
    "host": "localhost",
    "port": "5432",
    "dbname": "postgres",
    "user": "postgres",
    "password": "test"
}

def create_watchlist_table():
    try:
        conn = psycopg.connect(**DB_PARAMS)
        cur = conn.cursor()

        # Drop existing table if exists (for clean setup)
        cur.execute("DROP TABLE IF EXISTS watchlist;")

        create_table_query = """
        CREATE TABLE watchlist (
            id SERIAL PRIMARY KEY,
            symbol TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'Research',
            memo TEXT,
            tags TEXT[],
            fair_value_min DECIMAL,
            fair_value_max DECIMAL,
            alert_config JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        
        cur.execute(create_table_query)
        conn.commit()
        print("Watchlist table created successfully.")
        
        # Verify columns
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'watchlist';")
        columns = cur.fetchall()
        print("Columns created:")
        for col in columns:
            print(f"  - {col[0]}: {col[1]}")

        cur.close()
        conn.close()

    except Exception as e:
        print(f"Error creating watchlist table: {e}")

if __name__ == "__main__":
    create_watchlist_table()
