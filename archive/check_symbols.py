import psycopg

conn = psycopg.connect(
    host="localhost",
    port=5432,
    dbname="postgres",
    user="postgres",
    password="test"
)

cur = conn.cursor()

# Check available index symbols
print("=== Available Index Symbols in price_daily ===")
cur.execute("""
    SELECT DISTINCT symbol_key 
    FROM price_daily 
    WHERE symbol_key LIKE '%DJI%' 
       OR symbol_key LIKE '%N225%' 
       OR symbol_key LIKE '%VIX%'
       OR symbol_key LIKE '%GSPC%'
       OR symbol_key LIKE '%IXIC%'
       OR symbol_key LIKE '%DAX%'
       OR symbol_key LIKE '%FTSE%'
       OR symbol_key LIKE '%RUT%'
    ORDER BY symbol_key
""")
print("Index symbols found:", [r[0] for r in cur.fetchall()])

# Check FX symbols
print("\n=== Available FX Symbols ===")
cur.execute("""
    SELECT DISTINCT symbol_key 
    FROM price_daily 
    WHERE symbol_key LIKE '%JPY%'
       OR symbol_key LIKE '%USD%'
       OR symbol_key LIKE '%EUR%'
       OR symbol_key LIKE '%GBP%'
    ORDER BY symbol_key
    LIMIT 20
""")
print("FX symbols found:", [r[0] for r in cur.fetchall()])

# Check Metals symbols  
print("\n=== Available Metal Symbols ===")
cur.execute("""
    SELECT DISTINCT symbol_key 
    FROM price_daily 
    WHERE symbol_key IN ('GLD', 'SLV', 'PALL', 'PPLT', 'GC=F', 'SI=F')
    ORDER BY symbol_key
""")
print("Metal symbols found:", [r[0] for r in cur.fetchall()])

# Check what symbols have data
print("\n=== Checking specific symbols ===")
test_symbols = ['DJI', '^DJI', 'GSPC', '^GSPC', 'N225', '^N225', 'VIX', '^VIX']
for sym in test_symbols:
    cur.execute("SELECT COUNT(*) FROM price_daily WHERE symbol_key = %s", (sym,))
    count = cur.fetchone()[0]
    print(f"  {sym}: {count} records")

conn.close()
