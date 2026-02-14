
import sys
import os
from decimal import Decimal

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.db.database import Database
# We don't import service anymore to avoid Pydantic issues, we replicate logic

def audit_screener():
    db = Database()
    db.connect()
    
    print("=== Screener Data Audit (Direct DB) ===")
    
    # 1. Base counts
    try:
        active_count = db.execute_query("SELECT COUNT(*) FROM instruments WHERE is_active = true")[0][0]
        print(f"1. Active Symbols in DB: {active_count}")
        
        indicator_count = db.execute_query("SELECT COUNT(DISTINCT symbol_key) FROM indicator_daily")[0][0]
        print(f"2. Symbols with ANY Indicator Data: {indicator_count}")
        
        # 2. RS Score Distribution
        # Get latest date first
        latest_date = db.execute_query("SELECT MAX(trading_date) FROM indicator_daily")[0][0]
        print(f"   Latest Indicator Date: {latest_date}")
        
        rs_stats = db.execute_query("SELECT MIN(rs_score), AVG(rs_score), MAX(rs_score) FROM indicator_daily WHERE trading_date = %s", (latest_date,))[0]
        print(f"3. RS Score Stats (Latest): Min={rs_stats[0]}, Avg={rs_stats[1]}, Max={rs_stats[2]}")
        
        # 3. Fetch Raw Candidates (Simulate Service Query but get ALL)
        # Using the exact query structure from ScreenerService but without the RS filter (or with rs >= 0)
        query = """
            SELECT 
                i.symbol_key,
                ind.rs_score,
                ind.rsi14,
                ind.sma20,
                ind.sma50,
                ind.sma200,
                ind.dist_to_52w_high_pct,
                p.close as price
            FROM instruments i
            INNER JOIN LATERAL (
                SELECT close, volume, trading_date
                FROM price_daily
                WHERE symbol_key = i.symbol_key
                ORDER BY trading_date DESC
                LIMIT 1
            ) p ON true
            INNER JOIN LATERAL (
                SELECT symbol_key, rsi14, rs_score, sma20, sma50, sma200, dist_to_52w_high_pct
                FROM indicator_daily
                WHERE symbol_key = i.symbol_key
                  AND trading_date <= p.trading_date
                ORDER BY trading_date DESC
                LIMIT 1
            ) ind ON true
            WHERE i.is_active = true
            -- No RS filter here, we want to see everything
        """
        
        results = db.execute_query(query)
        candidates = []
        if results:
            for row in results:
                candidates.append({
                    "symbol": row[0],
                    "rs_score": float(row[1]) if row[1] is not None else 0,
                    "rsi": float(row[2]) if row[2] is not None else None,
                    "sma20": float(row[3]) if row[3] is not None else None,
                    "sma50": float(row[4]) if row[4] is not None else None,
                    "sma200": float(row[5]) if row[5] is not None else None,
                    "dist_52w": float(row[6]) if row[6] is not None else None,
                    "price": float(row[7]) if row[7] is not None else 0,
                })
        
        print(f"4. Total Candidates associated with valid Price+Indicators: {len(candidates)}")
        
        # 4. Simulate Filters Step-by-Step
        
        # Filter A: RS Score >= 85 (SQL level usually)
        c_rs_85 = [c for c in candidates if c['rs_score'] >= 85]
        print(f"   [Step A] RS Score >= 85: {len(c_rs_85)} remaining")
        
        if len(c_rs_85) == 0:
            print("   !!! BLOCKED AT STEP A (RS Score) !!!")
            # Show top RS scores
            candidates.sort(key=lambda x: x['rs_score'], reverse=True)
            print("   Top 5 RS Scores found:")
            for c in candidates[:5]:
                print(f"     {c['symbol']}: {c['rs_score']}")
        
        # Filter B: Total Score >= 70 (App level)
        # Helper to calc score
        def calc_total(c):
            score = 0.0
            # RS (40pts)
            if c['rs_score']: score += (c['rs_score'] / 100) * 40
            # RSI (15pts)
            rsi = c['rsi']
            if rsi:
                if 40 <= rsi <= 70: score += 15
                elif 30 <= rsi < 40 or 70 < rsi <= 80: score += 10
                else: score += 5
            # MA (20pts)
            if c['sma20'] and c['sma50'] and c['sma200']:
                if c['sma20'] > c['sma50'] > c['sma200'] and c['price'] > c['sma20']: score += 20
                elif c['sma20'] > c['sma50'] and c['price'] > c['sma20']: score += 15
                elif c['price'] > c['sma20']: score += 10
                else: score += 5
            # Dist (10pts)
            dist = c['dist_52w']
            if dist is not None:
                if dist >= -5: score += 10
                elif dist >= -10: score += 7
                elif dist >= -20: score += 4
                else: score += 2
            
            return score

        c_total_70 = []
        for c in c_rs_85:
            ts = calc_total(c)
            # Rounding to 1 decimal as in service
            if round(ts, 1) >= 70:
                c_total_70.append(c)
        
        print(f"   [Step B] Total Score >= 70: {len(c_total_70)} remaining")
        
        if len(c_rs_85) > 0 and len(c_total_70) == 0:
             print("   !!! BLOCKED AT STEP B (Total Score) !!!")
             print("   Sample of failed candidates:")
             for c in c_rs_85[:5]:
                 print(f"     {c['symbol']}: Total={calc_total(c):.1f}, RS={c['rs_score']}, RSI={c['rsi']}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.disconnect()

if __name__ == "__main__":
    audit_screener()
