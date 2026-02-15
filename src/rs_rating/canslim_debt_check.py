import yfinance as yf
import pandas as pd


def check_debt(ticker_symbol):
    t = yf.Ticker(ticker_symbol)
    info = t.info

    # 負債資本比率 (Debt to Equity)
    d_e_ratio = info.get("debtToEquity")

    # None / NaN 対策
    if d_e_ratio is None or pd.isnull(d_e_ratio):
        return None, False, "データ取得不可"

    # 型変換（念のため）
    try:
        d_e_ratio = float(d_e_ratio)
    except (TypeError, ValueError):
        return None, False, "数値変換不可"

    # ここはあなたのルール通り：150%以下をOK
    debt_ok = d_e_ratio < 150

    # 補足：極端に大きい値は自己資本が薄い可能性（例：1000%超）
    if d_e_ratio >= 1000:
        reason = f"要注意：自己資本が薄い可能性 ({d_e_ratio:.1f}%)"
        return d_e_ratio, False, reason

    reason = "健全" if debt_ok else f"負債過多 ({d_e_ratio:.1f}%)"
    return d_e_ratio, debt_ok, reason
