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


def calculate_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    """日足ベースの近似VWAP（Typical PriceのローリングVWAP）"""
    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume.fillna(0)
    vol_sum = volume.fillna(0).rolling(window=period).sum()
    pv_sum = pv.rolling(window=period).sum()
    return np.where(vol_sum > 0, pv_sum / vol_sum, np.nan)


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """OBV（On-Balance Volume）"""
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume.fillna(0)).cumsum()


def calculate_mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
    """MFI（Money Flow Index）"""
    tp = (high + low + close) / 3.0
    money_flow = tp * volume.fillna(0)
    prev_tp = tp.shift(1)
    pos_flow = money_flow.where(tp > prev_tp, 0.0)
    neg_flow = money_flow.where(tp < prev_tp, 0.0).abs()
    pos_sum = pos_flow.rolling(window=period).sum()
    neg_sum = neg_flow.rolling(window=period).sum()
    ratio = pos_sum / neg_sum.replace(0, np.nan)
    mfi = 100 - (100 / (1 + ratio))
    # When negative flow is 0 and positive exists, treat as 100.
    mfi = np.where((neg_sum == 0) & (pos_sum > 0), 100.0, mfi)
    return pd.Series(mfi, index=close.index)


def calculate_dmi_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    """DMI (+DI/-DI) と ADX（簡易ローリング版）"""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    tr_sum = tr.rolling(window=period).sum()
    plus_dm_sum = plus_dm.rolling(window=period).sum()
    minus_dm_sum = minus_dm.rolling(window=period).sum()

    plus_di = 100 * (plus_dm_sum / tr_sum.replace(0, np.nan))
    minus_di = 100 * (minus_dm_sum / tr_sum.replace(0, np.nan))
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.rolling(window=period).mean()
    return plus_di, minus_di, adx


def calculate_ichimoku(high: pd.Series, low: pd.Series, close: pd.Series):
    """一目均衡表（保存は日付整列値。描画時の先行/遅行シフトはUIで再適用可能）"""
    tenkan = (high.rolling(window=9).max() + low.rolling(window=9).min()) / 2.0
    kijun = (high.rolling(window=26).max() + low.rolling(window=26).min()) / 2.0
    senkou_a = (tenkan + kijun) / 2.0
    senkou_b = (high.rolling(window=52).max() + low.rolling(window=52).min()) / 2.0
    chikou = close.shift(-26)
    return tenkan, kijun, senkou_a, senkou_b, chikou


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
    df['bb_upper20'] = upper_band
    df['bb_lower20'] = lower_band
    bb_width = upper_band - lower_band
    df['bb_width20'] = bb_width
    df['bb_percent_b'] = np.where(bb_width > 0, (df['close'] - lower_band) / bb_width, np.nan)

    # ATR
    df['atr14'] = calculate_atr(df['high'], df['low'], df['close'], 14)

    # VWAP / OBV / MFI / ADX-DMI
    df['vwap20'] = calculate_vwap(df['high'], df['low'], df['close'], df['volume'], 20)
    df['obv'] = calculate_obv(df['close'], df['volume'])
    df['mfi14'] = calculate_mfi(df['high'], df['low'], df['close'], df['volume'], 14)
    plus_di14, minus_di14, adx14 = calculate_dmi_adx(df['high'], df['low'], df['close'], 14)
    df['plus_di14'] = plus_di14
    df['minus_di14'] = minus_di14
    df['adx14'] = adx14

    # Ichimoku
    tenkan, kijun, senkou_a, senkou_b, chikou = calculate_ichimoku(df['high'], df['low'], df['close'])
    df['ichimoku_tenkan9'] = tenkan
    df['ichimoku_kijun26'] = kijun
    df['ichimoku_senkou_a'] = senkou_a
    df['ichimoku_senkou_b'] = senkou_b
    df['ichimoku_chikou'] = chikou
    
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
                    vwap20, obv, mfi14,
                    plus_di14, minus_di14, adx14,
                    bb_upper20, bb_lower20, bb_width20, bb_percent_b,
                    ichimoku_tenkan9, ichimoku_kijun26, ichimoku_senkou_a, ichimoku_senkou_b, ichimoku_chikou,
                    calculated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    CURRENT_TIMESTAMP
                )
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
                    vwap20 = EXCLUDED.vwap20,
                    obv = EXCLUDED.obv,
                    mfi14 = EXCLUDED.mfi14,
                    plus_di14 = EXCLUDED.plus_di14,
                    minus_di14 = EXCLUDED.minus_di14,
                    adx14 = EXCLUDED.adx14,
                    bb_upper20 = EXCLUDED.bb_upper20,
                    bb_lower20 = EXCLUDED.bb_lower20,
                    bb_width20 = EXCLUDED.bb_width20,
                    bb_percent_b = EXCLUDED.bb_percent_b,
                    ichimoku_tenkan9 = EXCLUDED.ichimoku_tenkan9,
                    ichimoku_kijun26 = EXCLUDED.ichimoku_kijun26,
                    ichimoku_senkou_a = EXCLUDED.ichimoku_senkou_a,
                    ichimoku_senkou_b = EXCLUDED.ichimoku_senkou_b,
                    ichimoku_chikou = EXCLUDED.ichimoku_chikou,
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
                None if pd.isna(row['atr14']) else Decimal(str(row['atr14'])),
                None if pd.isna(row['vwap20']) else Decimal(str(row['vwap20'])),
                None if pd.isna(row['obv']) else int(row['obv']),
                None if pd.isna(row['mfi14']) else Decimal(str(row['mfi14'])),
                None if pd.isna(row['plus_di14']) else Decimal(str(row['plus_di14'])),
                None if pd.isna(row['minus_di14']) else Decimal(str(row['minus_di14'])),
                None if pd.isna(row['adx14']) else Decimal(str(row['adx14'])),
                None if pd.isna(row['bb_upper20']) else Decimal(str(row['bb_upper20'])),
                None if pd.isna(row['bb_lower20']) else Decimal(str(row['bb_lower20'])),
                None if pd.isna(row['bb_width20']) else Decimal(str(row['bb_width20'])),
                None if pd.isna(row['bb_percent_b']) else Decimal(str(row['bb_percent_b'])),
                None if pd.isna(row['ichimoku_tenkan9']) else Decimal(str(row['ichimoku_tenkan9'])),
                None if pd.isna(row['ichimoku_kijun26']) else Decimal(str(row['ichimoku_kijun26'])),
                None if pd.isna(row['ichimoku_senkou_a']) else Decimal(str(row['ichimoku_senkou_a'])),
                None if pd.isna(row['ichimoku_senkou_b']) else Decimal(str(row['ichimoku_senkou_b'])),
                None if pd.isna(row['ichimoku_chikou']) else Decimal(str(row['ichimoku_chikou']))
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
