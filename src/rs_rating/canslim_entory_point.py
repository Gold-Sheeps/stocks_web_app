import yfinance as yf
import pandas


def check_breakout_candidate(
    price_df: pandas.DataFrame,
    ticker_symbol: str,
    pivot_lookback: int = 20,
    buy_range_pct: float = 0.05,
    vol_avg_window: int = 50,
    vol_ratio_threshold: float = 1.4,
    near_high_threshold: float = 0.85,
    high_52w_window: int = 252,  # だいたい1年の営業日
) -> dict:
    """
    CAN-SLIMの買いポイント周りの数値を返す
    - pivot: 直近pivot_lookback日の高値（ただし直近日は除外）
    - buy_upper: pivot * (1 + buy_range_pct)
    - vol_ratio: 直近出来高 / 50日平均出来高
    - high_52w: 直近252営業日の高値
    """
    if price_df is None or price_df.empty:
        return {"ticker": ticker_symbol, "error": "price_dfが空"}

    required = {"High", "Close", "Volume"}
    if not required.issubset(set(price_df.columns)):
        return {
            "ticker": ticker_symbol,
            "error": f"必要列不足: {required - set(price_df.columns)}",
        }

    df = price_df.dropna(subset=["High", "Close", "Volume"]).copy()
    if len(df) < max(pivot_lookback + 2, vol_avg_window + 1):
        return {"ticker": ticker_symbol, "error": f"データ不足 ({len(df)}日)"}

    current_price = float(df["Close"].iloc[-1])

    # 52週高値（直近252営業日）
    tail_52w = df.tail(high_52w_window)
    high_52w = (
        float(tail_52w["High"].max()) if len(tail_52w) > 0 else float(df["High"].max())
    )
    near_high = current_price >= (high_52w * near_high_threshold)

    # Pivot（直近日を除く直近N日の高値）
    pivot_slice = df["High"].iloc[-(pivot_lookback + 1) : -1]  # 例: -21:-1
    pivot = float(pivot_slice.max())
    buy_upper = pivot * (1.0 + buy_range_pct)

    # 出来高比
    avg_vol = float(df["Volume"].tail(vol_avg_window).mean())
    current_vol = float(df["Volume"].iloc[-1])
    vol_ratio = (current_vol / avg_vol) if avg_vol > 0 else 0.0
    volume_up = vol_ratio >= vol_ratio_threshold

    # ブレイク・買いレンジ
    base_break = current_price > pivot
    within_buy_range = base_break and (current_price <= buy_upper)
    volume_up_on_break = base_break and volume_up

    return {
        "ticker": ticker_symbol,
        "base_break": base_break,
        "within_buy_range": within_buy_range,
        "volume_up": volume_up,
        "volume_up_on_break": volume_up_on_break,
        "current_price": current_price,
        "pivot": pivot,
        "buy_upper": buy_upper,
        "high_52w": high_52w,
        "vol_ratio": vol_ratio,
        "near_high": near_high,
        "avg_vol_50": avg_vol,
        "current_vol": current_vol,
    }


if __name__ == "__main__":
    print(check_breakout_candidate(ticker_symbol="NVDA"))
