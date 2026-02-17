"""
インジケーター計算バッチスクリプト
全US株のテクニカル指標を計算してDBに格納
"""

import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
from datetime import datetime
from decimal import Decimal
from tqdm import tqdm
import postgresql_connect


def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    """単純移動平均（SMA）を計算"""
    return prices.rolling(window=period).mean()


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """指数移動平均（EMA）を計算"""
    return prices.ewm(span=period, adjust=False).mean()


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """RSI（相対力指数）を計算"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD（移動平均収束拡散）を計算"""
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    
    return macd, macd_signal, macd_hist


def calculate_bollinger_bands(prices: pd.Series, period: int = 20, std_dev: float = 2.0):
    """ボリンジャーバンドを計算"""
    sma = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper_band = sma + (std * std_dev)
    lower_band = sma - (std * std_dev)
    
    return upper_band, lower_band


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR（平均真の範囲）を計算"""
    high_low = high - low
    high_close = np.abs(high - close.shift())
    low_close = np.abs(low - close.shift())
    
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    
    return atr


def calculate_rs_value(df: pd.DataFrame) -> float:
    """
    RS Valueを計算（パーセンタイルランク前）
    
    Args:
        df: 株価データ（columns: ['close']、日付降順）
    
    Returns:
        RS Value（生の値）
    """
    try:
        C = float(df.iloc[0]['close'])
        C63 = float(df.iloc[63]['close'])
        C126 = float(df.iloc[126]['close'])
        C189 = float(df.iloc[189]['close'])
        C252 = float(df.iloc[252]['close'])
        
        rs_value = (
            ((C - C63) / C63) * 0.4 +
            ((C - C126) / C126) * 0.2 +
            ((C - C189) / C189) * 0.2 +
            ((C - C252) / C252) * 0.2
        ) * 100
        
        return rs_value
    except (IndexError, ZeroDivisionError):
        return None


def calculate_indicators_for_symbol(db, symbol: str):
    """
    特定銘柄のインジケーターを計算
    
    Returns:
        計算成功した日数
    """
    # 価格データ取得（過去300日分）
    price_data = db.execute("""
        SELECT trading_date, open, high, low, close, volume
        FROM price_daily
        WHERE symbol_key = %s
        ORDER BY trading_date DESC
        LIMIT 300
    """, (symbol,))
    
    if not price_data or len(price_data) < 60:
        return 0, "Insufficient data"
    
    # DataFrameに変換（日付昇順にソート）
    df = pd.DataFrame(price_data, columns=['trading_date', 'open', 'high', 'low', 'close', 'volume'])
    df = df.sort_values('trading_date')
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    
    # インジケーター計算
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
    
    # Pivot (Classic): (High + Low + Close) / 3
    df['pivot'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    # ボリンジャーバンド（中心線はSMA20と同じなので上下のみ）
    upper_band, lower_band = calculate_bollinger_bands(df['close'])
    
    # ATR
    df['atr14'] = calculate_atr(df['high'], df['low'], df['close'], 14)
    
    # 52週高値・安値（252日 = 約1年）
    # min_periods=0 to allow calculation even with less than 1 year of data
    df['high_52w'] = df['high'].rolling(window=252, min_periods=1).max()
    df['low_52w'] = df['low'].rolling(window=252, min_periods=1).min()
    df['dist_to_52w_high_pct'] = ((df['close'] - df['high_52w']) / df['high_52w'] * 100)
    
    # 出来高移動平均（50日）
    df['volume_50d_avg'] = df['volume'].rolling(window=50).mean()
    
    # DBに保存（NaNでない行のみ）
    inserted = 0
    for idx, row in df.iterrows():
        # 最低限のデータが揃っているかチェック
        if pd.isna(row['sma20']) or pd.isna(row['rsi14']):
            continue
        
        try:
            db.command("""
                INSERT INTO indicator_daily (
                    symbol_key, trading_date,
                    sma20, sma50, sma200, ema21, ema50,
                    rsi14, macd, macd_signal, macd_hist,
                    pivot,
                    high_52w, low_52w, dist_to_52w_high_pct,
                    atr14,
                    calculated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
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
                    pivot = EXCLUDED.pivot,
                    high_52w = EXCLUDED.high_52w,
                    low_52w = EXCLUDED.low_52w,
                    dist_to_52w_high_pct = EXCLUDED.dist_to_52w_high_pct,
                    atr14 = EXCLUDED.atr14,
                    calculated_at = CURRENT_TIMESTAMP
            """, (
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
                None if pd.isna(row['pivot']) else Decimal(str(row['pivot'])),
                None if pd.isna(row['high_52w']) else Decimal(str(row['high_52w'])),
                None if pd.isna(row['low_52w']) else Decimal(str(row['low_52w'])),
                None if pd.isna(row['dist_to_52w_high_pct']) else Decimal(str(row['dist_to_52w_high_pct'])),
                None if pd.isna(row['atr14']) else Decimal(str(row['atr14']))
            ))
            inserted += 1
        except Exception as e:
            continue
    
    return inserted, None


def calculate_rs_ratings_batch(db):
    """全銘柄のRS Scoreを計算してDBに保存"""
    print("\n" + "=" * 60)
    print("RS Score Calculation")
    print("=" * 60)
    
    # 全US株を取得
    symbols = db.execute("""
        SELECT symbol_key 
        FROM instruments 
        WHERE market = 'US' AND is_active = TRUE
    """)
    
    rs_values = {}
    errors = []
    
    # RS Value計算
    for (symbol,) in tqdm(symbols, desc="Calculating RS Values"):
        try:
            price_data = db.execute("""
                SELECT trading_date, close 
                FROM price_daily 
                WHERE symbol_key = %s
                ORDER BY trading_date DESC
                LIMIT 300
            """, (symbol,))
            
            if len(price_data) < 253:
                errors.append((symbol, "Insufficient data for RS"))
                continue
            
            df = pd.DataFrame(price_data, columns=['trading_date', 'close'])
            rs_value = calculate_rs_value(df)
            
            if rs_value is not None:
                rs_values[symbol] = rs_value
                
        except Exception as e:
            errors.append((symbol, str(e)))
    
    # パーセンタイルランクに変換（1-99の均等分布）
    print(f"\n[INFO] Converting to percentile ranks...")
    sorted_symbols = sorted(rs_values.items(), key=lambda x: x[1], reverse=True)
    total = len(sorted_symbols)
    
    rs_ratings = {}
    for rank, (symbol, value) in enumerate(sorted_symbols, start=1):
        # 正しいパーセンタイルランク計算: 自分より下の銘柄の割合
        # rank=1 → percentile=99, rank=total → percentile=1
        percentile = int(((total - rank) / (total - 1)) * 98) + 1 if total > 1 else 50
        rs_ratings[symbol] = max(1, min(99, percentile))
    
    # DBに保存
    print(f"[INFO] Saving RS Scores to database...")
    saved = 0
    
    for symbol, score in tqdm(rs_ratings.items(), desc="Saving RS Scores"):
        try:
            # 各銘柄の最新取引日を取得
            latest_date_result = db.execute("""
                SELECT MAX(trading_date) 
                FROM indicator_daily 
                WHERE symbol_key = %s
            """, (symbol,))
            
            if not latest_date_result or not latest_date_result[0][0]:
                continue
            
            latest_date = latest_date_result[0][0]
            
            db.command("""
                UPDATE indicator_daily 
                SET rs_rating = %s,
                    calculated_at = CURRENT_TIMESTAMP
                WHERE symbol_key = %s AND trading_date = %s
            """, (Decimal(str(score)), symbol, latest_date))
            saved += 1
        except Exception as e:
            errors.append((symbol, f"RS save error: {e}"))

    
    print(f"\n[OK] RS Scores calculated: {len(rs_ratings)}")
    print(f"[OK] Saved to DB: {saved}")
    print(f"[ERR] Errors: {len(errors)}")
    
    if errors:
        print("\nFirst 10 errors:")
        for symbol, error in errors[:10]:
            print(f"  {symbol}: {error}")


def main():
    """メイン処理"""
    print("=" * 60)
    print("Indicator Calculation Batch - US Stocks")
    print("=" * 60)
    
    db = postgresql_connect.PostgreSQLConnect()
    if not db.connect():
        print("[ERROR] Database connection failed")
        return
    
    # 全US株を取得
    print("\n[INFO] Fetching US stocks...")
    symbols = db.execute("""
        SELECT symbol_key 
        FROM instruments 
        WHERE market = 'US' AND is_active = TRUE
    """)
    
    print(f"[INFO] Found {len(symbols)} US stocks\n")
    
    success = 0
    errors = []
    total_rows = 0
    
    # 各銘柄のインジケーター計算
    for (symbol,) in tqdm(symbols, desc="Calculating Indicators"):
        try:
            rows, error = calculate_indicators_for_symbol(db, symbol)
            
            if error:
                errors.append((symbol, error))
            else:
                success += 1
                total_rows += rows
                
        except Exception as e:
            errors.append((symbol, str(e)))
    
    print("\n" + "=" * 60)
    print("Indicator Calculation Complete")
    print("=" * 60)
    print(f"Total stocks: {len(symbols)}")
    print(f"Success: {success}")
    print(f"Total indicator rows inserted: {total_rows}")
    print(f"Failed: {len(errors)}")
    
    if errors:
        print("\nFirst 10 errors:")
        for symbol, error in errors[:10]:
            print(f"  {symbol}: {error}")
    
    # RS Score計算
    calculate_rs_ratings_batch(db)
    
    db.disconnect()
    print("\n[OK] All done!")


if __name__ == "__main__":
    main()
