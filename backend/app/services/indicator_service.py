
import pandas as pd
import numpy as np
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from app.db.database import Database

class IndicatorService:
    """テクニカル指標計算・保存サービス"""

    def __init__(self):
        self.db = Database()

    def calculate_and_save_indicators(self, symbol_key: str, days_back: int = 365):
        """指定された銘柄のテクニカル指標を計算して保存"""
        
        # 1. Fetch Price History
        query = """
            SELECT trading_date, open, high, low, close, volume 
            FROM price_daily 
            WHERE symbol_key = %s 
            ORDER BY trading_date ASC
        """
        self.db.connect()
        try:
            rows = self.db.execute_query(query, (symbol_key,))
            if not rows or len(rows) < 20: # Need at least some data
                # print(f"Not enough data for {symbol_key}")
                return

            df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
            df['close'] = pd.to_numeric(df['close'])
            df['high'] = pd.to_numeric(df['high'])
            df['low'] = pd.to_numeric(df['low'])
            df.set_index('date', inplace=True)
            
            # 2. Calculate Indicators using Pandas
            close = df['close']
            high = df['high']
            low = df['low']

            # SMA
            df['sma5'] = close.rolling(window=5).mean()
            df['sma20'] = close.rolling(window=20).mean()
            df['sma50'] = close.rolling(window=50).mean()
            df['sma200'] = close.rolling(window=200).mean()

            # EMA
            df['ema21'] = close.ewm(span=21, adjust=False).mean()

            # RSI (14)
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi14'] = 100 - (100 / (1 + rs))
            # Fix RSI calculation (Wilder's Smoothing is better but simple rolling is okay for MVP)
            # Standard Wilder's RSI:
            # gain = delta.where(delta > 0, 0)
            # loss = -delta.where(delta < 0, 0)
            # avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            # avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            # rs = avg_gain / avg_loss
            # df['rsi14'] = 100 - (100 / (1 + rs))
            
            # MACD (12, 26, 9)
            exp1 = close.ewm(span=12, adjust=False).mean()
            exp2 = close.ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['macd_hist'] = df['macd'] - df['macd_signal']

            # Pivot Points (Classic) = (High + Low + Close) / 3
            # Calculated for the NEXT day usually, but often visualized on current day as support/resistance
            # We'll store it as (H+L+C)/3 for the current day
            df['pivot'] = (high + low + close) / 3

            # ATR (14)
            # TR = max(H-L, |H-Cp|, |L-Cp|)
            df['prev_close'] = close.shift(1)
            df['tr1'] = high - low
            df['tr2'] = (high - df['prev_close']).abs()
            df['tr3'] = (low - df['prev_close']).abs()
            df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            df['atr14'] = df['tr'].rolling(window=14).mean()

            # 52W High
            df['high_52w'] = high.rolling(window=252, min_periods=1).max()
            # Handle 0 or NaN high_52w to avoid division by zero or invalid dist
            # Also handle 0 close
            df['dist_to_52w_high_pct'] = np.where(
                (df['high_52w'] > 0) & (close > 0),
                ((close - df['high_52w']) / df['high_52w']) * 100,
                None
            )

            # RS Raw Score (for Ranking)
            # Weighted: 40% 3m + 20% 6m + 20% 9m + 20% 12m (Simplified IBD style)
            # Or just 1-year change.
            # Using simple 1-year change * 2 + 3-month change * 1
            # Let's use 12-month change (252 days)
            # Requires 252 days data
            df['rs_rating'] = 0.0
            
            # Change % over N days
            # Fillna with 0
            ret_252 = close.pct_change(periods=252).fillna(0)
            ret_126 = close.pct_change(periods=126).fillna(0)
            ret_63 = close.pct_change(periods=63).fillna(0)

            # Weighting: 2 parts 3-mo, 1 part 6-mo, 1 part 12-mo
            df['rs_rating'] = (ret_63 * 0.4) + (ret_126 * 0.3) + (ret_252 * 0.3)
            # Normalize[WARN] No, recalculate_rs_rank normalizes it.

            # 3. Save to DB (Loop/Batch)
            # We only need to save records that changed or are new.
            # For simplicity, let's just save the last `days_back` records (or just today/yesterday if running daily)
            # If `days_back` is large, this is heavy.
            # Assuming we run this for ALL history initially, passing days_back=9999
            
            # Filter to save range
            # Limit to last N days to save time on upserts[WARN]
            # User might want full history for charts.
            
            # Let's verify existing data[WARN] No, simpler to upsert.
            
            # Prepare Query
            upsert_query = """
                INSERT INTO indicator_daily (
                    symbol_key, trading_date, 
                    sma5, sma20, sma50, sma200, 
                    rsi14, macd, macd_signal, macd_hist, 
                    atr14, high_52w, dist_to_52w_high_pct, 
                    ema21, pivot, rs_rating, updated_at
                ) VALUES (
                    %s, %s, 
                    %s, %s, %s, %s, 
                    %s, %s, %s, %s, 
                    %s, %s, %s, 
                    %s, %s, %s, CURRENT_TIMESTAMP
                )
                ON CONFLICT (symbol_key, trading_date) DO UPDATE SET
                    sma5 = EXCLUDED.sma5,
                    sma20 = EXCLUDED.sma20,
                    sma50 = EXCLUDED.sma50,
                    sma200 = EXCLUDED.sma200,
                    rsi14 = EXCLUDED.rsi14,
                    macd = EXCLUDED.macd,
                    macd_signal = EXCLUDED.macd_signal,
                    macd_hist = EXCLUDED.macd_hist,
                    atr14 = EXCLUDED.atr14,
                    high_52w = EXCLUDED.high_52w,
                    dist_to_52w_high_pct = EXCLUDED.dist_to_52w_high_pct,
                    ema21 = EXCLUDED.ema21,
                    pivot = EXCLUDED.pivot,
                    rs_rating = EXCLUDED.rs_rating,
                    updated_at = CURRENT_TIMESTAMP
            """

            # Iterate and Upsert
            # Use only rows with valid data[WARN]
            # Rolling window starts with NaNs.
            # slice dataframe
            
            # Batch Execution via raw cursor
            # Optimization: Use executemany[WARN] Database class doesn't expose it directly but cursor does.
            # FIX: Handle Inf values and possible pipeline errors by falling back to loop or cleaning data strictly.
            
            # Clean data
            cleaned_data = []
            for date_idx, row in df.iterrows():
                if pd.isna(row['sma5']) and pd.isna(row['rsi14']):
                    continue
                
                def clean_val(k):
                    v = row[k]
                    if pd.isna(v) or np.isinf(v):
                        return None
                    return Decimal(str(v))

                cleaned_data.append((
                   symbol_key, date_idx,
                   clean_val('sma5'), clean_val('sma20'), clean_val('sma50'), clean_val('sma200'),
                   clean_val('rsi14'), clean_val('macd'), clean_val('macd_signal'), clean_val('macd_hist'),
                   clean_val('atr14'), clean_val('high_52w'), clean_val('dist_to_52w_high_pct'),
                   clean_val('ema21'), clean_val('pivot'), clean_val('rs_rating')
                ))

            if cleaned_data:
                # Use a new cursor from connection for batch operation
                try:
                    with self.db.connection.cursor() as cursor:
                         cursor.executemany(upsert_query, cleaned_data)
                    self.db.connection.commit()
                except Exception as e:
                    print(f"Batch insert failed for {symbol_key}: {e}. Retrying row by row...")
                    self.db.connection.rollback()
                    # Retry row by row to save partial data or isolate error
                    for item in cleaned_data:
                        try:
                            with self.db.connection.cursor() as cursor:
                                cursor.execute(upsert_query, item)
                            self.db.connection.commit()
                        except Exception as row_e:
                            print(f"Row insert failed: {row_e}")
                            self.db.connection.rollback()
                
        except Exception as e:
            print(f"Error calculating indicators for {symbol_key}: {e}")
            self.db.connection.rollback()
        finally:
            self.db.disconnect()
            
    def calculate_all(self):
        """全銘柄計算（重い処理）"""
        self.db.connect()
        try:
            # Get all active symbol_keys
            rows = self.db.execute_query("SELECT symbol_key FROM instruments WHERE is_active = true")
            symbols = [r[0] for r in rows]
            self.db.disconnect() # Reconnect inside loop
            
            print(f"Calculating indicators for {len(symbols)} symbols...")
            for i, sym in enumerate(symbols):
                self.calculate_and_save_indicators(sym)
                if i % 10 == 0:
                    print(f"Processed {i}/{len(symbols)}")
                    
        except Exception as e:
             print(f"Error in calculate_all: {e}")
             
if __name__ == "__main__":
    svc = IndicatorService()
    svc.calculate_all()
