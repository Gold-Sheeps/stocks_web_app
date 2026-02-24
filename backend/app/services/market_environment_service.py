from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.db.database import Database


@dataclass
class _PxRow:
    trading_date: date
    close: float
    volume: int


class MarketEnvironmentService:
    """
    市場環境ダッシュボードデータを提供する。

    優先順位:
      1. market_environment テーブルに本日データがあればそれを使用
      2. 本日データがなければ price_daily をライブ計算してフォールバック
    """

    def __init__(self) -> None:
        self.db = Database()
        self.db.connect()

    # ------------------------------------------------------------------
    # DB 読み込み
    # ------------------------------------------------------------------

    def _load_from_db(self) -> dict | None:
        """market_environment テーブルから最新レコードを取得。"""
        try:
            rows = self.db.execute_query("""
                SELECT check_date, data, updated_at
                FROM market_environment
                ORDER BY check_date DESC
                LIMIT 1
            """)
            if rows and rows[0] and rows[0][1]:
                rec = rows[0]
                raw = rec[1] if isinstance(rec[1], dict) else __import__("json").loads(rec[1])
                return {
                    "check_date": rec[0].isoformat() if hasattr(rec[0], "isoformat") else str(rec[0]),
                    "data": raw,
                    "updated_at": str(rec[2]),
                }
        except Exception as e:
            print(f"[MarketEnvironmentService] DB load error: {e}")
        return None

    def _db_record_to_dashboard(self, record: dict) -> dict:
        """market_environment テーブルのフラット dict を API レスポンス形式に変換。"""
        d = record["data"]
        check_date = record["check_date"]
        updated_at = record["updated_at"]

        # --- summary ---
        triggers = d.get("active_rules") or []
        if isinstance(triggers, str):
            triggers = [triggers] if triggers else []

        nasdaq_dd_count = d.get("nasdaq_dd_count")
        sp500_dd_count  = d.get("sp500_dd_count")
        nasdaq_dd_dates = d.get("nasdaq_dd_dates") or []
        sp500_dd_dates  = d.get("sp500_dd_dates")  or []

        summary = {
            "market_regime":              d.get("regime",               "不明"),
            "market_gate":                d.get("buy_permission",        "unknown"),
            "new_buy_permission":         d.get("buy_permission",        "unknown"),
            "recommended_position_size_pct": d.get("position_limit_pct", 0),
            "recommended_cash_ratio":     d.get("cash_ratio_recommend",  "不明"),
            "breakout_reliability":       d.get("breakout_reliability",  "不明"),
            "triggers":                   triggers,
            "nasdaq_dd_count_5w":         nasdaq_dd_count,
            "sp500_dd_count_5w":          sp500_dd_count,
            "vix_level":                  d.get("vix"),
            "vix_mode":                   d.get("vix_mode"),
        }

        # --- sections ---
        # HO
        ho_first = d.get("ho_first_date")
        ho_latest = d.get("ho_latest_date")
        sections_ho = {
            "HO点灯有無":  "点灯中" if d.get("ho_triggered") else "非点灯",
            "初回点灯日":  ho_first  if ho_first  else None,
            "最新点灯日":  ho_latest if ho_latest else None,
            "警戒残日数":  d.get("ho_alert_days_remaining", 0),
            "クラスター回数": d.get("ho_cluster_count", 0),
            "NYA上昇トレンド": "Yes" if d.get("nya_uptrend") else ("No" if d.get("nya_uptrend") is False else None),
        }

        # DD
        dd_threshold = (nasdaq_dd_count or 0) >= 5 or (sp500_dd_count or 0) >= 5
        sections_dd = {
            "NASDAQ DD数":  nasdaq_dd_count,
            "SP500 DD数":   sp500_dd_count,
            "DD閾値到達":   "達成" if dd_threshold else "未達",
            "NASDAQ DD日付": ", ".join(nasdaq_dd_dates[-10:]) if nasdaq_dd_dates else None,
            "SP500 DD日付":  ", ".join(sp500_dd_dates[-10:])  if sp500_dd_dates  else None,
            "アクション": "新規買い停止/縮小" if d.get("buy_permission") in ("OFF", "HALF") else "通常運用",
        }

        # Breadth (DB 自前計算データを使用)
        adv = d.get("ad_advancing")
        dec = d.get("ad_declining")
        adr = d.get("ad_ratio")
        ad_dir = d.get("ad_line_direction")
        ad_label = None
        if ad_dir is not None and adv is not None and dec is not None and adr is not None:
            ad_label = f"{ad_dir} ({adv}↑/{dec}↓ = {adr})"
        sections_breadth = {
            "A/Dライン":    ad_label,
            "ダイバージェンス": d.get("divergence"),
            "新高値銘柄数": d.get("new_highs"),
            "新安値銘柄数": d.get("new_lows"),
            "分裂警戒":     d.get("breadth_alert"),
        }

        # Credit / Macro (FRED + yfinance proxy)
        def _fmt_status(val, status):
            if val is None:
                return None
            return f"{val} ({status})" if status else str(val)

        hy_oas_val = d.get("hy_oas")
        nfci_val   = d.get("nfci")
        sp2y10y    = d.get("yield_spread_2y10y")
        yc_status  = d.get("yield_curve_status")
        # 3M-10Y (yfinance proxy) をフォールバックとして残す
        spread_3m  = d.get("yield_spread_3m10y")
        sections_credit = {
            "HY OAS":           _fmt_status(hy_oas_val, d.get("hy_oas_status")),
            "ERP":              f"{d['erp']}%" if d.get("erp") is not None else None,
            "FCI (ANFCI)":      d.get("fci"),
            "NFCI":             _fmt_status(nfci_val, d.get("nfci_status")),
            "2y-10yスプレッド": _fmt_status(sp2y10y, yc_status) if sp2y10y is not None else None,
            "3M-10Yスプレッド(proxy)": spread_3m if sp2y10y is None else None,
            "10年利回り(%)":    d.get("yield_10y_fred") or d.get("yield_10y"),
            "2年利回り(%)":     d.get("yield_2y_fred"),
            "クレジット総合判定": d.get("credit_overall"),
        }

        # Sentiment
        vix  = d.get("vix")
        gate = d.get("buy_permission", "ON")
        skew_val = d.get("skew")
        pcp  = d.get("put_call_proxy")
        pcp_status = d.get("put_call_proxy_status")
        sections_sentiment = {
            "VIX":                    vix,
            "VIX変化率(%)":           d.get("vix_change_pct"),
            "VIXモード":              d.get("vix_mode"),
            "SKEW":                   _fmt_status(skew_val, d.get("skew_status")),
            "Put/Call代替(VIX/VIX3M)": _fmt_status(pcp, pcp_status) if pcp is not None else None,
            "センチメント総合判定":    d.get("sentiment_overall") or (
                "恐怖" if gate == "OFF" else ("警戒" if gate == "HALF" else "安定")
            ),
        }

        # Rules
        sections_rules = {
            "新規買い停止理由":    ", ".join(triggers) if triggers else "なし",
            "ポジションサイズ制限": f"{d.get('position_limit_pct', 0)}%",
            "損切り厳格化モード":  "ON" if d.get("loss_cut_strict") else "OFF",
            "利益確定優先モード":  "ON" if d.get("profit_take_priority") else "OFF",
            "QQQトレンド":        d.get("qqq_trend"),
            "SPYトレンド":        d.get("spy_trend"),
        }

        return {
            "generated_at": updated_at,
            "check_date":   check_date,
            "source":       "db",
            "summary":      summary,
            "sections": {
                "hindenburg_omen":   sections_ho,
                "distribution_days": sections_dd,
                "breadth":           sections_breadth,
                "credit_macro":      sections_credit,
                "sentiment":         sections_sentiment,
                "rules":             sections_rules,
            },
        }

    # ------------------------------------------------------------------
    # Live fallback (price_daily ベース)
    # ------------------------------------------------------------------

    def _get_series(self, symbol_key: str, limit: int = 40) -> list[_PxRow]:
        q = """
            SELECT trading_date, close, COALESCE(volume, 0)
            FROM price_daily
            WHERE symbol_key = %s
            ORDER BY trading_date DESC
            LIMIT %s
        """
        rows = self.db.execute_query(q, (symbol_key, limit)) or []
        out: list[_PxRow] = []
        for r in rows:
            if r[0] is None or r[1] is None:
                continue
            try:
                out.append(_PxRow(trading_date=r[0], close=float(r[1]), volume=int(r[2] or 0)))
            except Exception:
                continue
        return out

    def _latest_vix(self) -> dict[str, Any]:
        for sym in ["US:^VIX", "US:VIX"]:
            rows = self._get_series(sym, limit=2)
            if rows:
                latest = rows[0]
                prev = rows[1] if len(rows) > 1 else None
                change_pct = None
                if prev and prev.close:
                    change_pct = round((latest.close - prev.close) / prev.close * 100, 2)
                return {
                    "symbol": sym,
                    "level": round(latest.close, 2),
                    "change_pct": change_pct,
                    "mode": "RiskOff" if latest.close > 20 else ("Caution" if latest.close >= 18 else "Normal"),
                    "vix_term_structure_flag": 1 if latest.close > 20 else 0,
                    "asof": latest.trading_date.isoformat(),
                }
        return {"symbol": None, "level": None, "change_pct": None, "mode": "Unknown",
                "vix_term_structure_flag": None, "asof": None}

    def _distribution_days(self, symbol_key: str, lookback_days: int = 25) -> dict[str, Any]:
        rows_desc = self._get_series(symbol_key, limit=max(lookback_days + 2, 35))
        if len(rows_desc) < 3:
            return {"count": None, "days": [], "threshold_reached": False}
        rows = list(reversed(rows_desc))
        dd_dates: list[str] = []
        for i in range(1, len(rows)):
            cur = rows[i]; prev = rows[i - 1]
            if cur.close < prev.close and cur.volume > prev.volume and cur.volume > 0 and prev.volume > 0:
                dd_dates.append(cur.trading_date.isoformat())
        recent = dd_dates[-lookback_days:]
        count = len(recent)
        return {"count": count, "days": recent, "threshold_reached": count >= 5}

    def _index_trend_state(self, symbol_key: str) -> str:
        rows_desc = self._get_series(symbol_key, limit=30)
        if len(rows_desc) < 22:
            return "unknown"
        closes = [r.close for r in reversed(rows_desc)]
        latest = closes[-1]
        sma20 = sum(closes[-20:]) / 20.0
        sma5  = sum(closes[-5:])  / 5.0
        if latest > sma20 and sma5 >= sma20:
            return "uptrend"
        if latest < sma20 and sma5 < sma20:
            return "downtrend"
        return "correction"

    def _compute_gate(self, vix_level, vix_term_flag, ndd, sdd, ho_flag) -> tuple[str, str, list[str]]:
        reasons: list[str] = []
        if ho_flag == 1:
            reasons.append("HO")
        if (ndd or 0) >= 5:
            reasons.append("NASDAQ_DD>=5")
        if (sdd or 0) >= 5:
            reasons.append("SP500_DD>=5")
        if vix_level is not None and vix_level > 20 and int(vix_term_flag or 0) == 1:
            reasons.append("VIX>20")
        if reasons:
            return "OFF", "新規買い停止", reasons
        half_reasons: list[str] = []
        if (ndd or 0) in (3, 4) or (sdd or 0) in (3, 4):
            half_reasons.append("DD警戒")
        if vix_level is not None and 18 <= vix_level <= 20:
            half_reasons.append("VIX注意")
        if half_reasons:
            return "HALF", "注意", half_reasons
        return "ON", "買い可", ["安定"]

    def _live_dashboard(self) -> dict[str, Any]:
        """price_daily から直接計算するフォールバック。"""
        vix = self._latest_vix()
        nas_dd = self._distribution_days("US:QQQ")
        spx_dd = self._distribution_days("US:SPY")

        ho_flag = 0
        ho_days_remaining = 0
        ho_cluster_count = 0

        gate, regime_label, trigger_tags = self._compute_gate(
            vix_level=vix.get("level"),
            vix_term_flag=vix.get("vix_term_structure_flag"),
            ndd=nas_dd.get("count"),
            sdd=spx_dd.get("count"),
            ho_flag=ho_flag,
        )

        breakout_reliability = "低" if gate == "OFF" else ("中" if gate == "HALF" else "高")
        cash_ratio  = "高" if gate == "OFF" else ("中" if gate == "HALF" else "低")
        market_regime = "新規買い停止" if gate == "OFF" else ("注意" if gate == "HALF" else "強気")
        position_limit = 0 if gate == "OFF" else (50 if gate == "HALF" else 100)

        nas_days = nas_dd.get("days", [])
        spx_days = spx_dd.get("days", [])
        index_trend = {
            "QQQ": self._index_trend_state("US:QQQ"),
            "SPY": self._index_trend_state("US:SPY"),
        }

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "source": "live",
            "summary": {
                "market_regime": market_regime,
                "market_gate": gate,
                "new_buy_permission": gate,
                "recommended_position_size_pct": position_limit,
                "recommended_cash_ratio": cash_ratio,
                "breakout_reliability": breakout_reliability,
                "triggers": trigger_tags,
                "nasdaq_dd_count_5w": nas_dd.get("count"),
                "sp500_dd_count_5w": spx_dd.get("count"),
                "vix_level": vix.get("level"),
                "vix_mode": vix.get("mode"),
            },
            "sections": {
                "hindenburg_omen": {
                    "HO点灯有無": "非点灯",
                    "初回点灯日": None,
                    "最新点灯日": None,
                    "警戒残日数": ho_days_remaining,
                    "クラスター回数": ho_cluster_count,
                    "data_status": "placeholder_no_breadth_source",
                },
                "distribution_days": {
                    "NASDAQ DD数": nas_dd.get("count"),
                    "SP500 DD数":  spx_dd.get("count"),
                    "DD閾値到達": "達成" if (nas_dd.get("count") or 0) >= 5 or (spx_dd.get("count") or 0) >= 5 else "未達",
                    "NASDAQ DD日付": ", ".join(nas_days[-10:]) if nas_days else None,
                    "SP500 DD日付":  ", ".join(spx_days[-10:]) if spx_days else None,
                    "アクション": "新規買い停止/縮小" if gate in ("OFF", "HALF") else "通常運用",
                },
                "breadth": {
                    "A/Dライン": None, "ダイバージェンス": None,
                    "新高値銘柄数": None, "新安値銘柄数": None, "分裂警戒": None,
                    "data_status": "placeholder_no_breadth_source",
                },
                "credit_macro": {
                    "HY OAS": None, "ERP": None, "FCI": None, "NFCI": None,
                    "2y-10yスプレッド": None, "クレジット総合判定": "未取得",
                    "data_status": "placeholder_no_macro_source",
                },
                "sentiment": {
                    "VIX": vix.get("level"),
                    "VIX変化率(%)": vix.get("change_pct"),
                    "VIXモード": vix.get("mode"),
                    "Put/Callレシオ": None, "SKEW": None,
                    "センチメント総合判定": "恐怖" if gate == "OFF" else ("注意" if gate == "HALF" else "安定"),
                },
                "rules": {
                    "新規買い停止理由": ", ".join(trigger_tags) if trigger_tags else "なし",
                    "ポジションサイズ制限": f"{position_limit}%",
                    "損切り厳格化モード": "ON" if gate in ("OFF", "HALF") else "OFF",
                    "利益確定優先モード": "ON" if gate in ("OFF", "HALF") else "OFF",
                    "QQQトレンド": index_trend["QQQ"],
                    "SPYトレンド": index_trend["SPY"],
                },
            },
            "features": {
                "market_regime_score": 0 if gate == "OFF" else (50 if gate == "HALF" else 100),
                "nasdaq_dd_count_5w": nas_dd.get("count"),
                "sp500_dd_count_5w": spx_dd.get("count"),
                "index_trend_state": index_trend["QQQ"],
                "hindenburg_omen_flag": ho_flag,
                "vix_level": vix.get("level"),
                "vix_term_structure_flag": vix.get("vix_term_structure_flag"),
            },
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_dashboard(self) -> dict[str, Any]:
        try:
            # DB から最新レコードを試みる
            record = self._load_from_db()
            if record:
                return self._db_record_to_dashboard(record)
            # fallback
            return self._live_dashboard()
        finally:
            self.db.disconnect()

    def get_summary(self) -> dict[str, Any]:
        return self.get_dashboard().get("summary", {})
