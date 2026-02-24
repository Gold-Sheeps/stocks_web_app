#!/usr/bin/env python
"""
Market Environment データ一括取得スクリプト
yfinance + DB (price_daily) + FRED API から市場環境データを取得し
market_environment テーブルに UPSERT する。

取得項目:
  VIX / SKEW / VIX3M    : yfinance (^VIX, ^SKEW, ^VIX3M)
  VIX はまず DB price_daily から取得
  DD                     : DB price_daily (US:QQQ, US:SPY)
  HO                     : ^NYA トレンドのみ（完全判定は NYSE 銘柄数が必要）
  Breadth (A/D 等)       : DB price_daily から自前計算
  HY OAS / NFCI / FCI    : FRED API（fredapi + FRED_API_KEY が必要）
  2y-10y スプレッド       : FRED API 優先、なければ DB の 3M-10Y で代替
  Trend                  : DB price_daily SMA 判定
  総合レジーム             : 全データから自動計算

FRED API Key の設定:
  backend/scripts/const.py の FRED_API_KEY に設定してください。
  無料取得: https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# パス設定: backend/ と backend/scripts/ を両方追加
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SCRIPT_DIR))

import yfinance as yf

from app.db.database import Database

# FRED API Key
try:
    from const import FRED_API_KEY
except ImportError:
    FRED_API_KEY = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yf_latest(ticker: str, period: str = "5d") -> float | None:
    """yfinance から最新終値を取得。失敗時は None。"""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
    except Exception:
        pass
    return None


def _yf_hist(ticker: str, period: str = "5d"):
    """yfinance の DataFrame を返す。取得失敗時は None。"""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        return hist if not hist.empty else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. Sentiment (VIX / SKEW / VIX3M)
# ---------------------------------------------------------------------------

def fetch_sentiment_enhanced(db: Database) -> dict:
    """
    VIX は DB price_daily から優先取得。
    SKEW と VIX3M は yfinance から取得。
    Put/Call 代替: VIX/VIX3M 比率（バックワーデーション指標）。
    """
    result = {
        "vix": None,
        "vix_change_pct": None,
        "vix_mode": "Unknown",
        "skew": None,
        "skew_status": None,
        "put_call_proxy": None,
        "put_call_proxy_status": None,
        "sentiment_overall": "未取得",
    }

    # VIX: まず DB から
    rows = db.execute_query("""
        SELECT close FROM price_daily
        WHERE symbol_key IN ('US:^VIX', 'US:VIX')
        ORDER BY trading_date DESC LIMIT 2
    """) or []

    if rows:
        level = round(float(rows[0][0]), 2)
        prev  = round(float(rows[1][0]), 2) if len(rows) >= 2 else level
        result["vix"] = level
        result["vix_change_pct"] = round((level - prev) / prev * 100, 2) if prev else None
    else:
        hist = _yf_hist("^VIX")
        if hist is not None:
            result["vix"] = round(float(hist["Close"].iloc[-1]), 2)
            if len(hist) >= 2:
                prev = float(hist["Close"].iloc[-2])
                result["vix_change_pct"] = round((result["vix"] - prev) / prev * 100, 2) if prev else None

    if result["vix"] is not None:
        v = result["vix"]
        result["vix_mode"] = "RiskOff" if v >= 30 else ("Caution" if v >= 20 else "Normal")

    # SKEW
    skew_hist = _yf_hist("^SKEW")
    if skew_hist is not None:
        result["skew"] = round(float(skew_hist["Close"].iloc[-1]), 2)
        s = result["skew"]
        result["skew_status"] = "extreme" if s >= 150 else ("elevated" if s >= 140 else "normal")

    # VIX/VIX3M 比率（Put/Call 代替）
    vix3m_hist = _yf_hist("^VIX3M")
    if vix3m_hist is not None and result["vix"]:
        vix3m = float(vix3m_hist["Close"].iloc[-1])
        if vix3m > 0:
            ratio = round(result["vix"] / vix3m, 3)
            result["put_call_proxy"] = ratio
            if ratio > 1.1:
                result["put_call_proxy_status"] = "panic"
            elif ratio > 1.0:
                result["put_call_proxy_status"] = "fear"
            elif ratio > 0.85:
                result["put_call_proxy_status"] = "normal"
            else:
                result["put_call_proxy_status"] = "complacent"

    # 総合センチメント
    fear_score = 0
    if result["vix"] and result["vix"] >= 20:
        fear_score += 1
    if result["vix"] and result["vix"] >= 30:
        fear_score += 1
    if result["skew_status"] == "extreme":
        fear_score += 1
    if result["put_call_proxy_status"] in ("fear", "panic"):
        fear_score += 1

    if fear_score >= 3:
        result["sentiment_overall"] = "極度の恐怖"
    elif fear_score >= 2:
        result["sentiment_overall"] = "恐怖"
    elif fear_score >= 1:
        result["sentiment_overall"] = "警戒"
    else:
        result["sentiment_overall"] = "中立"

    return result


# ---------------------------------------------------------------------------
# 2. Distribution Days (DD)
# ---------------------------------------------------------------------------

def fetch_distribution_days(db: Database) -> dict:
    """
    DD: 前日比 -0.2% 以上の下落 かつ 出来高が前日以上。
    直近40暦日（≈25営業日）でカウント。
    """
    result: dict = {}
    cutoff = (date.today() - timedelta(days=40)).isoformat()

    for symbol, label in [("US:QQQ", "nasdaq"), ("US:SPY", "sp500")]:
        rows = db.execute_query("""
            SELECT trading_date, close, volume
            FROM price_daily
            WHERE symbol_key = %s
            ORDER BY trading_date DESC LIMIT 35
        """, (symbol,)) or []

        if len(rows) < 2:
            result[f"{label}_dd_count"] = 0
            result[f"{label}_dd_dates"] = []
            continue

        dd_dates: list[str] = []
        for i in range(len(rows) - 1):
            curr_date, curr_close, curr_vol = rows[i]
            _,          prev_close, prev_vol  = rows[i + 1]
            if not (prev_close and prev_close > 0 and curr_vol and prev_vol):
                continue
            chg = (float(curr_close) - float(prev_close)) / float(prev_close) * 100
            d_str = curr_date.isoformat() if hasattr(curr_date, "isoformat") else str(curr_date)
            if chg <= -0.2 and int(curr_vol) >= int(prev_vol) and d_str >= cutoff:
                dd_dates.append(d_str)

        result[f"{label}_dd_count"] = len(dd_dates)
        result[f"{label}_dd_dates"] = sorted(dd_dates)[-10:]

    return result


# ---------------------------------------------------------------------------
# 3. Hindenburg Omen (HO)
# ---------------------------------------------------------------------------

def fetch_hindenburg_omen() -> dict:
    """
    完全判定には NYSE 全銘柄の新高値/新安値データが必要で yfinance では不可。
    ^NYA のトレンドのみ確認する。
    """
    nya_uptrend: bool | None = None
    try:
        hist = yf.Ticker("^NYA").history(period="3mo")
        if not hist.empty and len(hist) >= 50:
            nya_uptrend = float(hist["Close"].iloc[-1]) > float(hist["Close"].iloc[-50])
    except Exception:
        pass

    return {
        "ho_triggered": False,
        "ho_first_date": None,
        "ho_latest_date": None,
        "ho_alert_days_remaining": 0,
        "ho_cluster_count": 0,
        "nya_uptrend": nya_uptrend,
    }


# ---------------------------------------------------------------------------
# 4. Market Breadth (DB 自前計算)
# ---------------------------------------------------------------------------

def fetch_breadth_from_db(db: Database) -> dict:
    """
    DB の price_daily から Advance/Decline と 52週新高値/新安値を計算。
    対象: symbol_key LIKE 'US:%' の全銘柄。
    """
    result: dict = {
        "ad_advancing": None,
        "ad_declining": None,
        "ad_ratio": None,
        "ad_line_direction": None,
        "new_highs": None,
        "new_lows": None,
        "divergence": None,
        "breadth_alert": None,
    }

    try:
        # 直近2日の終値を取得して Advance/Decline を計算
        rows = db.execute_query("""
            WITH latest_dates AS (
                SELECT DISTINCT trading_date
                FROM price_daily
                WHERE symbol_key LIKE 'US:%%'
                ORDER BY trading_date DESC
                LIMIT 2
            ),
            prices AS (
                SELECT p.symbol_key, p.trading_date, p.close
                FROM price_daily p
                WHERE p.trading_date IN (SELECT trading_date FROM latest_dates)
                  AND p.symbol_key LIKE 'US:%%'
            )
            SELECT
                symbol_key,
                MAX(CASE WHEN trading_date = (SELECT MAX(trading_date) FROM latest_dates) THEN close END) AS today_close,
                MAX(CASE WHEN trading_date = (SELECT MIN(trading_date) FROM latest_dates) THEN close END) AS prev_close
            FROM prices
            GROUP BY symbol_key
            HAVING COUNT(DISTINCT trading_date) = 2
        """) or []

        if rows:
            advancing = declining = 0
            for _, today, prev in rows:
                if today and prev and float(prev) > 0:
                    if float(today) > float(prev):
                        advancing += 1
                    elif float(today) < float(prev):
                        declining += 1

            result["ad_advancing"] = advancing
            result["ad_declining"] = declining
            denom = max(declining, 1)
            result["ad_ratio"] = round(advancing / denom, 2)
            result["ad_line_direction"] = (
                "上昇" if result["ad_ratio"] > 1.5 else
                ("下降" if result["ad_ratio"] < 0.67 else "横ばい")
            )

        # 52週 新高値/新安値
        hl_rows = db.execute_query("""
            WITH latest_date AS (
                SELECT MAX(trading_date) AS d
                FROM price_daily
                WHERE symbol_key LIKE 'US:%%'
            ),
            year_range AS (
                SELECT symbol_key,
                    MAX(high) AS high_52w,
                    MIN(low)  AS low_52w
                FROM price_daily
                WHERE symbol_key LIKE 'US:%%'
                  AND trading_date >= (SELECT d FROM latest_date) - INTERVAL '252 days'
                  AND trading_date <  (SELECT d FROM latest_date)
                GROUP BY symbol_key
            ),
            today_px AS (
                SELECT symbol_key, high, low
                FROM price_daily
                WHERE trading_date = (SELECT d FROM latest_date)
                  AND symbol_key LIKE 'US:%%'
            )
            SELECT
                SUM(CASE WHEN t.high >= y.high_52w THEN 1 ELSE 0 END) AS new_highs,
                SUM(CASE WHEN t.low  <= y.low_52w  THEN 1 ELSE 0 END) AS new_lows
            FROM today_px t
            JOIN year_range y ON t.symbol_key = y.symbol_key
        """) or []

        if hl_rows and hl_rows[0] and hl_rows[0][0] is not None:
            result["new_highs"] = int(hl_rows[0][0])
            result["new_lows"]  = int(hl_rows[0][1])

        # ダイバージェンス
        ad = result["ad_ratio"]
        if ad is not None:
            if ad < 1.0:
                result["divergence"] = "弱気乖離の可能性"
            elif ad > 2.0:
                result["divergence"] = "強気（広範参加）"
            else:
                result["divergence"] = "なし"

        # 分裂警戒
        if result["new_highs"] is not None and result["new_lows"] is not None:
            total = (result["ad_advancing"] or 0) + (result["ad_declining"] or 0)
            if total > 0:
                hi_pct = result["new_highs"] / total * 100
                lo_pct = result["new_lows"]  / total * 100
                result["breadth_alert"] = (
                    "分裂警戒（HO条件接近）" if hi_pct > 2.8 and lo_pct > 2.8 else "正常"
                )

    except Exception as e:
        print(f"  Breadth calculation error: {e}")

    return result


# ---------------------------------------------------------------------------
# 5. Credit / Macro (FRED API)
# ---------------------------------------------------------------------------

def fetch_credit_macro() -> dict:
    """
    FRED API からクレジット・マクロ指標を取得。
    FRED_API_KEY が未設定の場合はスキップ。

    FRED Series:
      BAMLH0A0HYM2 : HY OAS (日次)
      T10Y2Y       : 10y-2y Treasury スプレッド (日次)
      DGS10        : 10年利回り (日次)
      DGS2         : 2年利回り (日次)
      NFCI         : National Financial Conditions Index (週次)
      ANFCI        : Adjusted NFCI / FCI (週次)
    """
    result = {
        "hy_oas": None,
        "hy_oas_status": None,
        "erp": None,
        "fci": None,
        "nfci": None,
        "nfci_status": None,
        "yield_spread_2y10y": None,
        "yield_curve_status": None,
        "yield_10y_fred": None,
        "yield_2y_fred": None,
        "credit_overall": "未取得",
    }

    if not FRED_API_KEY:
        print("  FRED_API_KEY が const.py に未設定。FRED データをスキップ。")
        result["credit_overall"] = "API Key未設定"
        return result

    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)
        start = (date.today() - timedelta(days=90)).isoformat()

        def _fred(series: str) -> float | None:
            try:
                s = fred.get_series(series, observation_start=start)
                if s is not None and len(s) > 0:
                    return float(s.dropna().iloc[-1])
            except Exception as e:
                print(f"  FRED {series} error: {e}")
            return None

        # HY OAS
        hy = _fred("BAMLH0A0HYM2")
        if hy is not None:
            result["hy_oas"] = round(hy, 2)
            result["hy_oas_status"] = (
                "crisis" if hy >= 5.0 else ("elevated" if hy >= 3.5 else "normal")
            )

        # 2y-10y スプレッド
        sp = _fred("T10Y2Y")
        if sp is not None:
            result["yield_spread_2y10y"] = round(sp, 2)
            result["yield_curve_status"] = (
                "normal" if sp > 0.5 else ("flat" if sp >= 0 else "inverted")
            )

        # 10年/2年利回り（個別）
        dgs10 = _fred("DGS10")
        if dgs10 is not None:
            result["yield_10y_fred"] = round(dgs10, 2)

        dgs2 = _fred("DGS2")
        if dgs2 is not None:
            result["yield_2y_fred"] = round(dgs2, 2)

        # NFCI
        nfci = _fred("NFCI")
        if nfci is not None:
            result["nfci"] = round(nfci, 4)
            result["nfci_status"] = (
                "tight" if nfci > 0.5 else ("normal" if nfci > 0 else "loose")
            )

        # ANFCI (FCI)
        fci = _fred("ANFCI")
        if fci is not None:
            result["fci"] = round(fci, 4)

        # ERP = S&P500 Earnings Yield - 10Y Treasury
        try:
            ten_y = result["yield_10y_fred"] or dgs10
            if ten_y:
                sp500_info = yf.Ticker("^GSPC").info or {}
                pe = sp500_info.get("trailingPE")
                if pe and pe > 0:
                    ey = (1 / pe) * 100
                    result["erp"] = round(ey - ten_y, 2)
        except Exception as e:
            print(f"  ERP calc error: {e}")

        fetched = sum(
            1 for k in ["hy_oas", "yield_spread_2y10y", "nfci", "fci"]
            if result[k] is not None
        )
        result["credit_overall"] = f"取得済み ({fetched}/4)"

    except ImportError:
        print("  fredapi がインストールされていません。pip install fredapi を実行してください。")
        result["credit_overall"] = "fredapi未インストール"
    except Exception as e:
        print(f"  FRED API エラー: {e}")
        result["credit_overall"] = f"エラー: {str(e)[:60]}"

    return result


# ---------------------------------------------------------------------------
# 6. Index Trend (DB)
# ---------------------------------------------------------------------------

def fetch_index_trend(db: Database) -> dict:
    """QQQ / SPY のトレンド状態を SMA20 / SMA50 で判定。"""
    result: dict = {}
    for symbol, label in [("US:QQQ", "qqq"), ("US:SPY", "spy")]:
        rows = db.execute_query("""
            SELECT close FROM price_daily
            WHERE symbol_key = %s
            ORDER BY trading_date DESC LIMIT 50
        """, (symbol,)) or []

        if len(rows) < 20:
            result[f"{label}_trend"] = "unknown"
            continue

        closes = [float(r[0]) for r in rows]
        current = closes[0]
        sma20 = sum(closes[:20]) / 20
        sma50 = sum(closes) / len(closes)

        if current > sma20 and sma20 >= sma50:
            trend = "uptrend"
        elif current < sma20 and sma20 < sma50:
            trend = "downtrend"
        elif current < sma20:
            trend = "correction"
        else:
            trend = "sideways"

        result[f"{label}_trend"] = trend

    return result


# ---------------------------------------------------------------------------
# Regime computation
# ---------------------------------------------------------------------------

def compute_regime(data: dict) -> dict:
    """全データから総合レジームを計算（FRED データ含む）。"""
    stop_reasons: list[str] = []
    warn_reasons: list[str] = []

    nasdaq_dd = data.get("nasdaq_dd_count") or 0
    sp500_dd  = data.get("sp500_dd_count")  or 0
    vix       = data.get("vix")             or 0
    hy_oas    = data.get("hy_oas")
    nfci      = data.get("nfci")
    spread2y10y = data.get("yield_spread_2y10y")
    skew      = data.get("skew")

    # 停止条件
    if nasdaq_dd >= 5:
        stop_reasons.append(f"NASDAQ_DD={nasdaq_dd}")
    if sp500_dd >= 5:
        stop_reasons.append(f"SP500_DD={sp500_dd}")
    if data.get("ho_triggered"):
        stop_reasons.append("HO点灯中")
    if vix >= 30:
        stop_reasons.append(f"VIX={vix}")
    if hy_oas and hy_oas >= 5.0:
        stop_reasons.append(f"HY_OAS={hy_oas}")
    if nfci and nfci > 0.5:
        stop_reasons.append(f"NFCI={nfci}(引き締め)")
    if data.get("breadth_alert") == "分裂警戒（HO条件接近）":
        stop_reasons.append("ブレッドス分裂警戒")

    # 警戒条件
    if 0 < nasdaq_dd < 5 or 0 < sp500_dd < 5:
        warn_reasons.append("DD警戒")
    if 20 <= vix < 30:
        warn_reasons.append(f"VIX={vix}")
    if spread2y10y is not None and spread2y10y < -0.5:
        warn_reasons.append(f"逆イールド({spread2y10y})")
    if skew and skew >= 150:
        warn_reasons.append(f"SKEW={skew}")
    if hy_oas and 3.5 <= hy_oas < 5.0:
        warn_reasons.append(f"HY_OAS警戒={hy_oas}")

    if stop_reasons:
        regime, gate, cash, reliability, position = "新規買い停止", "OFF", "高", "低", 0
        active = stop_reasons
    elif warn_reasons:
        regime, gate, cash, reliability, position = "注意", "HALF", "中", "中", 50
        active = warn_reasons
    else:
        regime, gate, cash, reliability, position = "強気", "ON", "低", "高", 100
        active = ["安定"]

    return {
        "regime": regime,
        "buy_permission": gate,
        "cash_ratio_recommend": cash,
        "breakout_reliability": reliability,
        "position_limit_pct": position,
        "active_rules": active,
        "loss_cut_strict": gate in ("OFF", "HALF"),
        "profit_take_priority": gate == "OFF",
    }


# ---------------------------------------------------------------------------
# DB save
# ---------------------------------------------------------------------------

def ensure_table(db: Database) -> None:
    db.execute_command("""
        CREATE TABLE IF NOT EXISTS market_environment (
            id SERIAL PRIMARY KEY,
            check_date DATE NOT NULL UNIQUE,
            data JSONB NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)


def save_to_db(db: Database, all_data: dict) -> None:
    ensure_table(db)
    today = date.today()
    json_data = json.dumps(all_data, ensure_ascii=False, default=str)
    db.execute_command("""
        INSERT INTO market_environment (check_date, data, updated_at)
        VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (check_date)
        DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
    """, (today, json_data))
    print(f"Saved market_environment data for {today}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Market Environment data")
    parser.add_argument("--dry-run", action="store_true", help="Output data without saving to DB")
    args = parser.parse_args()

    db = Database()
    db.connect()

    try:
        print("=== Fetching Market Environment Data ===")

        print("[1/6] Sentiment (VIX / SKEW / VIX3M)...")
        sentiment = fetch_sentiment_enhanced(db)
        print(f"  VIX={sentiment['vix']}  SKEW={sentiment['skew']}  put_call_proxy={sentiment['put_call_proxy']}  overall={sentiment['sentiment_overall']}")

        print("[2/6] Distribution Days (DD)...")
        dd = fetch_distribution_days(db)
        print(f"  NASDAQ DD={dd['nasdaq_dd_count']}  SP500 DD={dd['sp500_dd_count']}")

        print("[3/6] Hindenburg Omen...")
        ho = fetch_hindenburg_omen()
        print(f"  triggered={ho['ho_triggered']}  NYA uptrend={ho['nya_uptrend']}")

        print("[4/6] Market Breadth (from DB)...")
        breadth = fetch_breadth_from_db(db)
        print(f"  A/D={breadth['ad_advancing']}↑/{breadth['ad_declining']}↓  ratio={breadth['ad_ratio']}  new_highs={breadth['new_highs']}  new_lows={breadth['new_lows']}")

        print("[5/6] Credit / Macro (FRED API)...")
        credit = fetch_credit_macro()
        print(f"  HY_OAS={credit['hy_oas']}  NFCI={credit['nfci']}  FCI={credit['fci']}  spread_2y10y={credit['yield_spread_2y10y']}")

        print("[6/6] Index Trend...")
        trend = fetch_index_trend(db)
        print(f"  QQQ={trend['qqq_trend']}  SPY={trend['spy_trend']}")

        # 全フィールドをマージ
        all_data: dict = {}
        for d in (sentiment, dd, ho, breadth, credit, trend):
            all_data.update(d)
        all_data["check_timestamp"] = datetime.now().isoformat()

        # 総合レジーム
        regime = compute_regime(all_data)
        all_data.update(regime)

        print(f"\n=== Regime={regime['regime']}  Gate={regime['buy_permission']} ===")
        print(f"  Active rules: {regime['active_rules']}")

        if args.dry_run:
            print("\n[DRY RUN] Data NOT saved to DB.")
            print(json.dumps(all_data, indent=2, ensure_ascii=False, default=str))
        else:
            save_to_db(db, all_data)

    finally:
        db.disconnect()


if __name__ == "__main__":
    main()
