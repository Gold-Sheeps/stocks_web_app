import yfinance as yf
import pandas as pd


def check_flat_base(ticker_symbol):
    # 直近30営業日(約6週間)のデータを取得
    data = yf.Ticker(ticker_symbol).history(
        start="2022-01-01", end="2025-12-31", auto_adjust=False
    )

    if data is None or data.empty:
        return False, "データ取得失敗"

    # 必要列のNaNを落とす
    data = data.dropna(subset=["High", "Low", "Close"])
    if len(data) < 35:  # 30営業日+前日比較の余裕
        return False, f"データ不足 ({len(data)}日)"

    recent_base = data.iloc[-30:]

    # 1. 調整幅の計算 (高値と安値の差が15%以内か)
    base_high = recent_base["High"].max()
    base_low = recent_base["Low"].min()
    correction_pct = (base_high - base_low) / base_high

    is_flat = correction_pct <= 0.15  # 15%以内

    # 2. ブレイク判定（「今日ブレイクした」を見たいなら前日も見る）
    current_price = data["Close"].iloc[-1]
    prev_close = data["Close"].iloc[-2]

    # 「今日上抜け」の定義：前日<=ベース高値、今日>ベース高値
    breakout_ok = (prev_close <= base_high) and (current_price > base_high)

    reason = f"ベース内調整幅: {correction_pct*100:.1f}%"
    return (is_flat and breakout_ok), reason
