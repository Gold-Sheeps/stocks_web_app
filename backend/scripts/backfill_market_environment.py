#!/usr/bin/env python
"""
market_environment テーブルへの過去データ一括バックフィルスクリプト。

price_daily テーブルのデータから各営業日の市場環境を計算し、
market_environment テーブルに UPSERT する。

計算項目:
  VIX / vix_change_pct  : price_daily (US:^VIX)
  Distribution Days      : price_daily (US:QQQ, US:SPY) - 各日の直近25営業日窓
  Index Trend (QQQ/SPY) : price_daily SMA20/SMA50
  FRED データ            : fredapi (NFCI, HY OAS, T10Y2Y, DGS10, DGS2) - 全期間一括取得
  レジーム計算           : fetch_market_environment.compute_regime() を流用

Usage:
    python backend/scripts/backfill_market_environment.py --start 2024-01-01 --end 2026-02-23
    python backend/scripts/backfill_market_environment.py --start 2024-01-01 --end 2026-02-23 --dry-run
    python backend/scripts/backfill_market_environment.py --start 2024-01-01 --end 2026-02-23 --skip-existing
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SCRIPT_DIR))

import numpy as np
import pandas as pd

from app.db.database import Database

# fetch_market_environment の compute_regime を流用
try:
    from fetch_market_environment import compute_regime, FRED_API_KEY
except ImportError:
    def compute_regime(data: dict) -> dict:  # type: ignore[misc]
        return {
            "regime": "不明", "buy_permission": "unknown",
            "cash_ratio_recommend": "不明", "breakout_reliability": "不明",
            "position_limit_pct": 0, "active_rules": [],
            "loss_cut_strict": False, "profit_take_priority": False,
        }
    FRED_API_KEY = ""


# ---------------------------------------------------------------------------
# FRED 全期間一括取得
# ---------------------------------------------------------------------------

def _fetch_fred_series_all(start: str, end: str) -> Dict[str, pd.Series]:
    """
    FRED から主要系列を start〜end の期間で一括取得して dict[series_id → Series] を返す。
    失敗した系列はスキップ（空 Series）。
    """
    if not FRED_API_KEY:
        print("  FRED_API_KEY 未設定 - FRED データをスキップ（NaN で補完）")
        return {}

    try:
        from fredapi import Fred
        fred = Fred(api_key=FRED_API_KEY)
    except ImportError:
        print("  fredapi 未インストール - FRED データをスキップ")
        return {}

    series_ids = ["BAMLH0A0HYM2", "T10Y2Y", "DGS10", "DGS2", "NFCI", "ANFCI"]
    result: Dict[str, pd.Series] = {}
    for sid in series_ids:
        try:
            s = fred.get_series(sid, observation_start=start, observation_end=end)
            if s is not None and len(s) > 0:
                s = s.dropna()
                s.index = pd.to_datetime(s.index)
                result[sid] = s
                print(f"  FRED {sid}: {len(s)} rows ({s.index[0].date()} - {s.index[-1].date()})")
        except Exception as e:
            print(f"  FRED {sid} 取得エラー: {e}")

    return result


def _fred_value_asof(series: pd.Series, target_date: pd.Timestamp) -> Optional[float]:
    """series から target_date 以前の最新値を返す。"""
    if series is None or len(series) == 0:
        return None
    idx = series.index[series.index <= target_date]
    if len(idx) == 0:
        return None
    return float(series.loc[idx[-1]])


# ---------------------------------------------------------------------------
# Price data preload
# ---------------------------------------------------------------------------

def _load_price_history(db: Database, symbol_key: str, start: str, end: str) -> pd.DataFrame:
    """price_daily から指定銘柄の OHLCV を DataFrame で返す。"""
    rows = db.execute_query("""
        SELECT trading_date, open, high, low, close, volume
        FROM price_daily
        WHERE symbol_key = %s
          AND trading_date BETWEEN %s AND %s
        ORDER BY trading_date
    """, (symbol_key, start, end)) or []
    if not rows:
        return pd.DataFrame(columns=["trading_date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["trading_date", "open", "high", "low", "close", "volume"])
    df["trading_date"] = pd.to_datetime(df["trading_date"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("trading_date").sort_index()


# ---------------------------------------------------------------------------
# DD calculation for a specific date
# ---------------------------------------------------------------------------

def _calc_dd_for_date(price_df: pd.DataFrame, target_date: pd.Timestamp, window_days: int = 40) -> int:
    """
    target_date までの直近 window_days 暦日における Distribution Day 数を計算。
    DD 条件: 前日比 -0.2% 以上の下落 かつ 出来高が前日以上
    """
    cutoff = target_date - timedelta(days=window_days)
    subset = price_df[price_df.index <= target_date].tail(35)
    if len(subset) < 2:
        return 0

    count = 0
    for i in range(len(subset) - 1):
        curr = subset.iloc[i]
        prev = subset.iloc[i + 1]
        if not (prev["close"] and prev["close"] > 0 and curr["volume"] and prev["volume"]):
            continue
        chg = (float(curr["close"]) - float(prev["close"])) / float(prev["close"]) * 100
        if chg <= -0.2 and int(curr["volume"]) >= int(prev["volume"]):
            if subset.index[i] >= cutoff:
                count += 1
    return count


def _calc_dd_dates_for_date(price_df: pd.DataFrame, target_date: pd.Timestamp, window_days: int = 40) -> List[str]:
    cutoff = target_date - timedelta(days=window_days)
    subset = price_df[price_df.index <= target_date].tail(35)
    if len(subset) < 2:
        return []

    dates: List[str] = []
    for i in range(len(subset) - 1):
        curr = subset.iloc[i]
        prev = subset.iloc[i + 1]
        if not (prev["close"] and prev["close"] > 0 and curr["volume"] and prev["volume"]):
            continue
        chg = (float(curr["close"]) - float(prev["close"])) / float(prev["close"]) * 100
        if chg <= -0.2 and int(curr["volume"]) >= int(prev["volume"]):
            if subset.index[i] >= cutoff:
                dates.append(subset.index[i].strftime("%Y-%m-%d"))
    return sorted(dates)[-10:]


# ---------------------------------------------------------------------------
# Trend calculation for a specific date
# ---------------------------------------------------------------------------

def _calc_trend_for_date(price_df: pd.DataFrame, target_date: pd.Timestamp) -> str:
    """SMA20/SMA50 でトレンドを判定。"""
    subset = price_df[price_df.index <= target_date]["close"].dropna()
    if len(subset) < 20:
        return "unknown"

    closes = list(subset.values)[::-1]  # 新しい順
    current = closes[0]
    sma20 = sum(closes[:20]) / 20
    sma50 = sum(closes[:50]) / len(closes[:50]) if len(closes) >= 50 else sma20

    if current > sma20 and sma20 >= sma50:
        return "uptrend"
    elif current < sma20 and sma20 < sma50:
        return "downtrend"
    elif current < sma20:
        return "correction"
    else:
        return "sideways"


# ---------------------------------------------------------------------------
# Main backfill function
# ---------------------------------------------------------------------------

def backfill_market_environment(
    db: Database,
    start_date: str,
    end_date: str,
    dry_run: bool = False,
    skip_existing: bool = False,
) -> int:
    """
    start_date から end_date まで、US:QQQ の営業日に合わせて
    market_environment テーブルにデータを UPSERT する。

    Returns: upserted row count
    """
    print(f"[Backfill] start={start_date}  end={end_date}  dry_run={dry_run}  skip_existing={skip_existing}")

    # テーブル作成（存在しない場合）
    db.execute_command("""
        CREATE TABLE IF NOT EXISTS market_environment (
            id SERIAL PRIMARY KEY,
            check_date DATE NOT NULL UNIQUE,
            data JSONB NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # 既存レコードを取得（skip_existing 用）
    existing_dates: set = set()
    if skip_existing:
        rows = db.execute_query("SELECT check_date FROM market_environment")
        existing_dates = {str(r[0]) for r in (rows or [])}
        print(f"[Backfill] 既存レコード数: {len(existing_dates)}")

    # 対象日付（US:QQQ の営業日）
    date_rows = db.execute_query("""
        SELECT DISTINCT trading_date FROM price_daily
        WHERE symbol_key = 'US:QQQ'
          AND trading_date BETWEEN %s AND %s
        ORDER BY trading_date
    """, (start_date, end_date)) or []

    all_dates = [r[0] for r in date_rows]
    if not all_dates:
        print("[Backfill] 対象日付なし（price_daily に US:QQQ データがない）")
        return 0
    print(f"[Backfill] 対象営業日数: {len(all_dates)} ({all_dates[0]} - {all_dates[-1]})")

    # 価格データ一括ロード
    extended_start = (pd.Timestamp(start_date) - timedelta(days=90)).strftime("%Y-%m-%d")
    qqq_df = _load_price_history(db, "US:QQQ", extended_start, end_date)
    spy_df = _load_price_history(db, "US:SPY", extended_start, end_date)
    vix_df = _load_price_history(db, "US:^VIX", extended_start, end_date)
    print(f"[Backfill] 価格ロード完了: QQQ={len(qqq_df)}, SPY={len(spy_df)}, VIX={len(vix_df)}")

    # FRED 全期間一括取得
    print("[Backfill] FRED データ取得中...")
    fred_data = _fetch_fred_series_all(extended_start, end_date)

    upserted = 0

    for target_date_raw in all_dates:
        target_date = pd.Timestamp(target_date_raw)
        date_str = target_date.strftime("%Y-%m-%d")

        if skip_existing and date_str in existing_dates:
            continue

        rec: Dict[str, Any] = {}

        # VIX
        vix_subset = vix_df[vix_df.index <= target_date]["close"].dropna()
        if len(vix_subset) >= 2:
            vix_val = round(float(vix_subset.iloc[-1]), 2)
            vix_prev = round(float(vix_subset.iloc[-2]), 2)
            rec["vix"] = vix_val
            rec["vix_change_pct"] = round((vix_val - vix_prev) / vix_prev * 100, 2) if vix_prev else None
            rec["vix_mode"] = "RiskOff" if vix_val >= 30 else ("Caution" if vix_val >= 20 else "Normal")
        elif len(vix_subset) == 1:
            vix_val = round(float(vix_subset.iloc[-1]), 2)
            rec["vix"] = vix_val
            rec["vix_change_pct"] = None
            rec["vix_mode"] = "RiskOff" if vix_val >= 30 else ("Caution" if vix_val >= 20 else "Normal")
        else:
            rec["vix"] = None
            rec["vix_change_pct"] = None
            rec["vix_mode"] = "Unknown"

        # SKEW, put_call_proxy - バックフィルでは省略（NaN）
        rec["skew"] = None
        rec["skew_status"] = None
        rec["put_call_proxy"] = None
        rec["put_call_proxy_status"] = None
        rec["sentiment_overall"] = "不明"

        # Distribution Days
        nasdaq_dd_dates = _calc_dd_dates_for_date(qqq_df, target_date)
        sp500_dd_dates = _calc_dd_dates_for_date(spy_df, target_date)
        rec["nasdaq_dd_count"] = len(nasdaq_dd_dates)
        rec["nasdaq_dd_dates"] = nasdaq_dd_dates
        rec["sp500_dd_count"] = len(sp500_dd_dates)
        rec["sp500_dd_dates"] = sp500_dd_dates

        # Hindenburg Omen - バックフィルでは省略
        rec["ho_triggered"] = False
        rec["ho_first_date"] = None
        rec["ho_latest_date"] = None
        rec["ho_alert_days_remaining"] = 0
        rec["ho_cluster_count"] = 0
        rec["nya_uptrend"] = None

        # Breadth - バックフィルでは省略（NaN、DB計算が重いため）
        rec["ad_advancing"] = None
        rec["ad_declining"] = None
        rec["ad_ratio"] = None
        rec["ad_line_direction"] = None
        rec["new_highs"] = None
        rec["new_lows"] = None
        rec["divergence"] = None
        rec["breadth_alert"] = None

        # FRED
        def _fv(series_id: str) -> Optional[float]:
            s = fred_data.get(series_id)
            if s is None:
                return None
            v = _fred_value_asof(s, target_date)
            return round(float(v), 4) if v is not None else None

        hy_oas = _fv("BAMLH0A0HYM2")
        rec["hy_oas"] = round(hy_oas, 2) if hy_oas else None
        rec["hy_oas_status"] = (
            "crisis" if hy_oas and hy_oas >= 5.0 else
            ("elevated" if hy_oas and hy_oas >= 3.5 else "normal")
        ) if hy_oas else None

        spread = _fv("T10Y2Y")
        rec["yield_spread_2y10y"] = round(spread, 2) if spread is not None else None
        rec["yield_curve_status"] = (
            "normal" if spread is not None and spread > 0.5 else
            ("flat" if spread is not None and spread >= 0 else "inverted")
        ) if spread is not None else None

        dgs10 = _fv("DGS10")
        rec["yield_10y_fred"] = round(dgs10, 2) if dgs10 else None
        dgs2 = _fv("DGS2")
        rec["yield_2y_fred"] = round(dgs2, 2) if dgs2 else None

        nfci = _fv("NFCI")
        rec["nfci"] = round(nfci, 4) if nfci is not None else None
        rec["nfci_status"] = (
            "tight" if nfci is not None and nfci > 0.5 else
            ("normal" if nfci is not None and nfci > 0 else "loose")
        ) if nfci is not None else None

        fci = _fv("ANFCI")
        rec["fci"] = round(fci, 4) if fci is not None else None
        rec["erp"] = None  # バックフィルでは省略

        rec["credit_overall"] = f"取得済み" if any([hy_oas, spread, nfci, fci]) else "未取得"

        # Trend
        rec["qqq_trend"] = _calc_trend_for_date(qqq_df, target_date)
        rec["spy_trend"] = _calc_trend_for_date(spy_df, target_date)

        rec["check_timestamp"] = target_date.isoformat()

        # レジーム計算
        regime = compute_regime(rec)
        rec.update(regime)

        if dry_run:
            if upserted < 3:
                print(f"  [DRY] {date_str}: regime={rec.get('regime')}  nasdaq_dd={rec['nasdaq_dd_count']}  vix={rec.get('vix')}")
            elif upserted == 3:
                print("  [DRY] ... (以下省略)")
        else:
            json_data = json.dumps(rec, ensure_ascii=False, default=str)
            db.execute_command("""
                INSERT INTO market_environment (check_date, data, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (check_date)
                DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
            """, (date_str, json_data))

        upserted += 1
        if upserted % 100 == 0:
            print(f"  進捗: {upserted}/{len(all_dates)} ({date_str})")

    print(f"[Backfill] 完了: {upserted} 件{'（dry run）' if dry_run else 'を UPSERT'}")
    return upserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill market_environment table from historical data")
    parser.add_argument("--start", required=True, help="開始日 (YYYY-MM-DD)")
    parser.add_argument("--end", default=date.today().isoformat(), help="終了日 (YYYY-MM-DD, default: today)")
    parser.add_argument("--dry-run", action="store_true", help="DB に保存せず内容を確認")
    parser.add_argument("--skip-existing", action="store_true", help="既存レコードをスキップ（差分のみ追加）")
    args = parser.parse_args()

    db = Database()
    db.connect()
    try:
        backfill_market_environment(
            db=db,
            start_date=args.start,
            end_date=args.end,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
        )
    finally:
        db.disconnect()


if __name__ == "__main__":
    main()
