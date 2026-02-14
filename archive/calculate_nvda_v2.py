import sys
import os
import pandas as pd
import numpy as np
from decimal import Decimal
from datetime import datetime

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from app.db.database import Database
# Import calculation functions from original script, but not the DB logic
from src.calculate_indicators_batch import (
    calculate_sma, calculate_ema, calculate_rsi, calculate_macd,
    calculate_bollinger_bands, calculate_atr
)
import traceback

def main():
    symbol = "NVDA"
    print(f"Calculating indicators for {symbol} using backend Database class...")
    
    try:
        db = Database()
        if not db.connect():
            print("Failed to connect to DB")
            return

        # 1. Price Data
        query = """
            SELECT trading_date, open, high, low, close, volume
            FROM price_daily
            WHERE symbol_key = %s
            ORDER BY trading_date DESC
            LIMIT 300
        """
        price_data = db.execute_query(query, (symbol,))
        
        if not price_data or len(price_data) < 60:
            print("Insufficient data")
            return

        # DataFrame setup
        df = pd.DataFrame(price_data, columns=['trading_date', 'open', 'high', 'low', 'close', 'volume'])
        df = df.sort_values('trading_date')
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['volume'] = df['volume'].astype(float)

        # Calculate Indicators
        df['sma20'] = calculate_sma(df['close'], 20)
        df['sma50'] = calculate_sma(df['close'], 50)
        df['sma200'] = calculate_sma(df['close'], 200)
        df['ema21'] = calculate_ema(df['close'], 21)
        df['ema50'] = calculate_ema(df['close'], 50)
        df['rsi14'] = calculate_rsi(df['close'], 14)
        macd, macd_signal, macd_hist = calculate_macd(df['close'])
        df['macd'] = macd
        df['macd_signal'] = macd_signal
        df['macd_hist'] = macd_hist
        
        # 52W High/Low
        df['high_52w'] = df['high'].rolling(window=252, min_periods=1).max()
        df['low_52w'] = df['low'].rolling(window=252, min_periods=1).min()
        df['dist_to_52w_high_pct'] = ((df['close'] - df['high_52w']) / df['high_52w'] * 100)
        df['atr14'] = calculate_atr(df['high'], df['low'], df['close'], 14)

        # Insert
        inserted = 0
        for idx, row in df.iterrows():
            if pd.isna(row['sma20']): continue
            
            insert_query = """
                INSERT INTO indicator_daily (
                    symbol_key, trading_date,
                    sma20, sma50, sma200, ema21, ema50,
                    rsi14, macd, macd_signal, macd_hist,
                    high_52w, low_52w, dist_to_52w_high_pct,
                    atr14,
                    calculated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol_key, trading_date)
                DO UPDATE SET
                    sma20 = EXCLUDED.sma20,
                    sma50 = EXCLUDED.sma50,
                    sma200 = EXCLUDED.sma200,
                    ema21 = EXCLUDED.ema21,
                    ema50 = EXCLUDED.ema50,
                    rsi14 = EXCLUDED.rsi14,
                    macd = EXCLUDED.macd,
                    macd_signal = EXCLUDED.macd_signal,
                    macd_hist = EXCLUDED.macd_hist,
                    high_52w = EXCLUDED.high_52w,
                    low_52w = EXCLUDED.low_52w,
                    dist_to_52w_high_pct = EXCLUDED.dist_to_52w_high_pct,
                    atr14 = EXCLUDED.atr14,
                    calculated_at = CURRENT_TIMESTAMP
            """
            params = (
                symbol,
                row['trading_date'],
                None if pd.isna(row['sma20']) else Decimal(str(row['sma20'])),
                None if pd.isna(row['sma50']) else Decimal(str(row['sma50'])),
                None if pd.isna(row['sma200']) else Decimal(str(row['sma200'])),
                None if pd.isna(row['ema21']) else Decimal(str(row['ema21'])),
                None if pd.isna(row['ema50']) else Decimal(str(row['ema50'])),
                None if pd.isna(row['rsi14']) else Decimal(str(row['rsi14'])),
                None if pd.isna(row['macd']) else Decimal(str(row['macd'])),
                None if pd.isna(row['macd_signal']) else Decimal(str(row['macd_signal'])),
                None if pd.isna(row['macd_hist']) else Decimal(str(row['macd_hist'])),
                None if pd.isna(row['high_52w']) else Decimal(str(row['high_52w'])),
                None if pd.isna(row['low_52w']) else Decimal(str(row['low_52w'])),
                None if pd.isna(row['dist_to_52w_high_pct']) else Decimal(str(row['dist_to_52w_high_pct'])),
                None if pd.isna(row['atr14']) else Decimal(str(row['atr14']))
            )
            
            if db.execute_command(insert_query, params):
                inserted += 1
        
        print(f"Success! Inserted {inserted} rows into postgres DB.")
        db.disconnect()
        
    except Exception as e:
        print(f"Exception: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
