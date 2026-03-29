from typing import Optional
import math
from app.db.database import Database
from app.services.freshness_service import FreshnessService
from app.services.market_environment_service import MarketEnvironmentService
from app.services.ai_prediction_service import _compute_action_label


class ScreenerService:
    """Business logic for screener endpoints."""

    def __init__(self):
        self.db = Database()
        self.db.connect()
        self.freshness_service = FreshnessService()

    def _get_screener_freshness(self, market: str = "US") -> "DataFreshness":
        mkt = str(market or "US").strip().upper()
        if mkt == "JP":
            # JPX trading hours: 9:00-15:30 JST. Use 16:00 as safe cutoff.
            return self.freshness_service.price_freshness_for_active_instruments(
                scope="screener.active_universe.jp",
                tz_name="Asia/Tokyo",
                close_cutoff_hour_local=16,
                note="JP screener uses latest daily bar per active JP instrument.",
                market_prefix="JP",
            )
        return self.freshness_service.price_freshness_for_active_instruments(
            scope="screener.active_universe",
            tz_name="America/New_York",
            close_cutoff_hour_local=18,
            note="Screener uses latest daily bar per active instrument.",
        )

    def _ensure_rs_table_exists(self) -> None:
        self.db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS rs_ratings (
                symbol_key TEXT PRIMARY KEY,
                rating INTEGER,
                raw_score DOUBLE PRECISION,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def _ensure_ai_predictions_table_exists(self) -> None:
        self.db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS ai_predictions (
                symbol_key TEXT NOT NULL,
                asof DATE NOT NULL,
                p_up5 DOUBLE PRECISION,
                threshold_buy DOUBLE PRECISION,
                decision TEXT,
                cal_method TEXT NOT NULL DEFAULT 'none',
                artifact_path TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol_key, asof, cal_method)
            )
            """
        )

    def _get_market_summary_safe(self) -> dict:
        try:
            return MarketEnvironmentService().get_summary()
        except Exception:
            return {}

    def _get_latest_ai_predictions(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        self._ensure_ai_predictions_table_exists()
        q = """
            SELECT DISTINCT ON (symbol_key)
                symbol_key, asof, p_up5, threshold_buy, decision
            FROM ai_predictions
            WHERE symbol_key = ANY(%s)
              AND COALESCE(cal_method, 'none') != 'class15'
            ORDER BY symbol_key, asof DESC,
                CASE LOWER(COALESCE(cal_method, ''))
                    WHEN 'none' THEN 0
                    WHEN 'platt' THEN 1
                    WHEN 'artifact' THEN 2
                    WHEN 'isotonic' THEN 3
                    ELSE 9
                END,
                updated_at DESC
        """
        rows = self.db.execute_query(q, (symbols,)) or []
        out: dict[str, dict] = {}
        for r in rows:
            try:
                out[str(r[0]).upper()] = {
                    "asof": str(r[1]) if r[1] is not None else None,
                    "p_up5": float(r[2]) if r[2] is not None else None,
                    "threshold_buy": float(r[3]) if r[3] is not None else None,
                    "decision": str(r[4]) if r[4] is not None else None,
                }
            except Exception:
                continue
        return out

    def _get_latest_class15_predictions(self, symbols: list[str]) -> dict[str, dict]:
        """15営業日3クラス予測(class15)をバルク取得する。なければ空 dict。"""
        if not symbols:
            return {}
        try:
            self._ensure_ai_predictions_table_exists()
            q = """
                SELECT DISTINCT ON (symbol_key)
                    symbol_key, asof, prob_up, prob_flat, prob_down, predicted_class
                FROM ai_predictions
                WHERE symbol_key = ANY(%s)
                  AND cal_method = 'class15'
                ORDER BY symbol_key, asof DESC, updated_at DESC
            """
            rows = self.db.execute_query(q, (symbols,)) or []
            out: dict[str, dict] = {}
            for r in rows:
                try:
                    predicted_class = int(r[5]) if r[5] is not None else None
                    _labels = {2: "Up", 1: "Flat", 0: "Down"}
                    out[str(r[0]).upper()] = {
                        "asof": str(r[1]) if r[1] is not None else None,
                        "prob_up": float(r[2]) if r[2] is not None else None,
                        "prob_flat": float(r[3]) if r[3] is not None else None,
                        "prob_down": float(r[4]) if r[4] is not None else None,
                        "predicted_class": predicted_class,
                        "direction": _labels.get(predicted_class) if predicted_class is not None else None,
                    }
                except Exception:
                    continue
            return out
        except Exception:
            return {}

    def _derive_ai_3class(self, p_up5: Optional[float]) -> dict[str, int]:
        if p_up5 is None:
            return {"up": 0, "flat": 0, "down": 0}
        p = max(0.0, min(1.0, float(p_up5)))
        up = int(round(p * 100))
        down = int(round((1.0 - p) * 0.55 * 100))
        flat = 100 - up - down
        if flat < 0:
            flat = 0
            down = 100 - up
        s = up + flat + down
        if s != 100:
            up += 100 - s
        return {"up": up, "flat": flat, "down": down}

    def _compute_screener_ai_labels(
        self,
        gate: str,
        p_up5: Optional[float],
        threshold_buy: Optional[float],
        decision: Optional[str],
    ) -> tuple[str, list[str], Optional[int]]:
        """
        スクリーナー各行の action_label / gate_reasons / predicted_class を返す。
        DB には保存しない。gate = market_gate (OFF/HALF/ON)。
        """
        gate_u = (gate or "").upper()
        dec = (decision or "").upper()
        p = float(p_up5) if p_up5 is not None else 0.0
        thr = float(threshold_buy) if threshold_buy is not None else 0.5
        gate_reason: str | None = None
        gate_reasons: list[str] = []

        # ゲート適用（ai_prediction_service の論理と統一）
        if gate_u == "OFF" and dec == "BUY":
            dec = "GATE_BLOCKED"
            gate_reason = "新規買い停止中（市場環境）"
            gate_reasons.append("market_regime_blocked")
        elif gate_u == "HALF" and p < 0.6 and dec == "BUY":
            dec = "CAUTION"
            gate_reason = "市場環境「注意」のため閾値引き上げ（p<0.6）"
            gate_reasons.append("market_regime_caution")
            gate_reasons.append("prob_below_caution_threshold")

        action_label = _compute_action_label(dec, gate_reason)

        # predicted_class: up5 閾値と下落閾値(0.3)から導出
        predicted_class: Optional[int] = None
        if p_up5 is not None:
            if p >= thr:
                predicted_class = 2  # 上昇
            elif p < 0.3:
                predicted_class = 0  # 下落
            else:
                predicted_class = 1  # フラット

        return action_label, gate_reasons, predicted_class

    # class15 の重み (Phase 3-4 で追加)
    # report_class15_observe.py の推奨値: Up=81.5%, Flat+Down=18.5% → w=0.07 (標準)
    _W_C15: float = 0.07

    def _compute_final_rank_score(
        self,
        total_score: Optional[float],
        p_up5: Optional[float],
        canslim_count: Optional[int],
        action_label: Optional[str],
        predicted_class: Optional[int],
        prob_up: Optional[float] = None,
        prob_down: Optional[float] = None,
    ) -> float:
        """
        投資判断に使える総合ランクスコア (0〜100)。
        構成 (Phase 3-4):
          - total_score (技術的スコア):  50%
          - AI p_up5 (上昇確率):        30% - w_c15  (class15 分を削る)
          - CANSLIM通過率:              20%
          - class15_score (Up-Down確率): w_c15 (デフォルト 7%)
        ゲート調整:
          - BLOCKED → 0点 (市場停止で新規エントリー不可)
          - WATCH (ゲート起因) → 0.5倍 (注意フラグ)
          - AI未予測 (predicted_class=None) → AI成分を 0 として計算 (低め)
        class15:
          - prob_up/prob_down が与えられた場合: class15_score = prob_up - prob_down を [0,100] に正規化
          - None の場合: c15_component=50 (中立) → ai 重みへの影響ゼロ
        """
        if action_label == "BLOCKED":
            return 0.0

        tech = float(total_score or 0)                              # 0-100
        ai_component = float(p_up5 or 0) * 100                     # 0-100
        canslim_component = (float(canslim_count or 0) / 7) * 100  # 0-100

        # class15 成分: prob_up - prob_down を [0, 100] に正規化
        if prob_up is not None and prob_down is not None:
            c15_component = (float(prob_up) - float(prob_down) + 1.0) / 2.0 * 100
        else:
            c15_component = 50.0  # 中立: class15 なし → ai 重みへの影響ゼロ

        w_c15 = self._W_C15
        w_ai  = 0.30 - w_c15
        raw = (
            tech              * 0.50
            + ai_component    * w_ai
            + canslim_component * 0.20
            + c15_component   * w_c15
        )

        # WATCH かつゲート起因 → スコアを半減（WATCH だが AI未予測は半減しない）
        if action_label == "WATCH" and predicted_class is not None:
            raw *= 0.5

        return round(max(0.0, min(100.0, raw)), 1)

    def _m_badge(self, market_gate: str) -> str:
        gate = (market_gate or "").upper()
        if gate == "OFF":
            return "新規買い停止"
        if gate == "HALF":
            return "注意"
        return "買い可"

    def _breakout_effectiveness(self, market_gate: str, row: dict) -> str:
        gate = (market_gate or "").upper()
        if gate == "OFF":
            return "だまし警戒"
        if gate == "HALF":
            return "注意"
        pivot = row.get("pivot")
        price = row.get("price", 0)
        if pivot and price and float(price) >= float(pivot):
            return "有効"
        return "注意"

    def _estimate_canslim_pass_count(
        self,
        row: dict,
        market_gate: str,
        eps_yoy_pct: Optional[float] = None,
        revenue_yoy_pct: Optional[float] = None,
        llm_tone_score: Optional[float] = None,
    ) -> tuple[int, dict]:
        """Returns (count, criteria_dict) where criteria_dict maps letter -> bool."""
        criteria: dict[str, bool] = {}

        # C: Current quarterly EPS growth >= 25% YoY
        if eps_yoy_pct is not None:
            criteria["C"] = eps_yoy_pct >= 25.0
        else:
            # Fallback: proxy via Perfect Order signal
            criteria["C"] = any(s in (row.get("signals") or []) for s in ["Perfect Order"])

        # A: Annual earnings growth (proxy: revenue YoY >= 20% or EPS YoY >= 20%)
        if eps_yoy_pct is not None:
            criteria["A"] = eps_yoy_pct >= 20.0
        elif revenue_yoy_pct is not None:
            criteria["A"] = revenue_yoy_pct >= 20.0
        else:
            criteria["A"] = False

        # N: New products/catalysts — use LLM tone if available, else price breakout proxy
        if llm_tone_score is not None:
            criteria["N"] = llm_tone_score > 0.3
        else:
            n_breakout = row.get("pivot") and (row.get("price") or 0) >= (row.get("pivot") or 0)
            n_near_high = (row.get("dist_52w_high_pct") or -999) >= -10
            criteria["N"] = bool(n_breakout or n_near_high)

        # S: Supply/demand — high RS indicates institutional accumulation
        criteria["S"] = (row.get("rs_rating") or 0) >= 70

        # L: Leader — RS Rating >= 80
        criteria["L"] = (row.get("rs_rating") or 0) >= 80

        # I: Institutional sponsorship — proxy via volume surge on breakout
        # We don't have direct institutional data; use volume + RS as proxy
        rsi_val = row.get("rsi")
        criteria["I"] = (
            (row.get("rs_rating") or 0) >= 80
            and rsi_val is not None
            and 45 <= float(rsi_val) <= 80
        )

        # M: Market direction
        criteria["M"] = (market_gate or "").upper() == "ON"

        count = sum(1 for v in criteria.values() if v)
        return min(count, 7), criteria

    def _get_canslim_fundamentals(self, symbol_keys: list[str]) -> dict[str, dict]:
        """Bulk-fetch EPS/revenue YoY growth from fundamentals_comparison_latest."""
        if not symbol_keys:
            return {}
        # fundamentals_comparison_latest stores raw ticker (e.g. "AAPL"), build mapping
        ticker_to_sk: dict[str, str] = {}
        for sk in symbol_keys:
            if ":" in sk:
                _, ticker = sk.split(":", 1)
            else:
                ticker = sk
            ticker_to_sk[ticker.upper()] = sk.upper()
        tickers = list(ticker_to_sk.keys())
        rows = self.db.execute_query(
            """
            SELECT symbol, eps_yoy_pct, revenue_yoy_pct
            FROM fundamentals_comparison_latest
            WHERE UPPER(symbol) = ANY(%s)
            """,
            (tickers,),
        ) or []
        out: dict[str, dict] = {}
        for r in rows:
            ticker = (r[0] or "").upper()
            sk = ticker_to_sk.get(ticker)
            if sk:
                out[sk] = {
                    "eps_yoy_pct": float(r[1]) if r[1] is not None else None,
                    "revenue_yoy_pct": float(r[2]) if r[2] is not None else None,
                }
        return out

    def _get_canslim_llm_signals(self, symbol_keys: list[str]) -> dict[str, dict]:
        """Bulk-fetch LLM tone scores from llm_signal_feature_sets for N criterion."""
        if not symbol_keys:
            return {}
        import json
        import datetime
        cutoff = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
        rows = self.db.execute_query(
            """
            SELECT DISTINCT ON (symbol_key) symbol_key, llm_features_json
            FROM llm_signal_feature_sets
            WHERE symbol_key = ANY(%s)
              AND asof >= %s
            ORDER BY symbol_key, asof DESC
            """,
            (symbol_keys, cutoff),
        ) or []
        out: dict[str, dict] = {}
        for r in rows:
            sk = (r[0] or "").upper()
            try:
                features = json.loads(r[1]) if r[1] else {}
            except Exception:
                features = {}
            tone = features.get("llm_earnings_tone_score")
            out[sk] = {"llm_tone_score": float(tone) if tone is not None else None}
        return out

    def _overall_grade(self, market_gate: str, p_up5: Optional[float], total_score: float) -> str:
        gate = (market_gate or "").upper()
        p = p_up5 if p_up5 is not None else 0.0
        if gate == "OFF":
            return "見送り"
        if gate == "HALF":
            if p >= 0.70 and total_score >= 75:
                return "A"
            if p >= 0.55:
                return "B"
            return "C"
        if p >= 0.70 and total_score >= 80:
            return "A"
        if p >= 0.60 and total_score >= 70:
            return "B"
        if p >= 0.45:
            return "C"
        return "見送り"

    def _build_filter_clause(
        self,
        min_rs: Optional[int],
        min_price: Optional[float],
        max_price: Optional[float],
        volume_min: Optional[int],
        rsi_filter: Optional[str],
        symbol: Optional[str],
        market: Optional[str],
    ) -> tuple[str, list]:
        conditions: list[str] = []
        params: list = []

        if min_rs is not None:
            conditions.append("COALESCE(rr.rating, ind_rs.rs_rating, 0) >= %s")
            params.append(min_rs)
        if volume_min:
            conditions.append("p.volume >= %s")
            params.append(volume_min)
        if min_price is not None:
            conditions.append("p.close >= %s")
            params.append(min_price)
        if max_price is not None:
            conditions.append("p.close <= %s")
            params.append(max_price)

        if rsi_filter == "oversold":
            conditions.append("ind.rsi14 <= 30")
        elif rsi_filter == "neutral":
            conditions.append("ind.rsi14 > 30 AND ind.rsi14 <= 70")
        elif rsi_filter == "overbought":
            conditions.append("ind.rsi14 > 70")

        if symbol:
            conditions.append("(s.symbol_key ILIKE %s OR COALESCE(i.name, '') ILIKE %s)")
            search_val = f"%{symbol}%"
            params.extend([search_val, search_val])

        market_val = str(market or "US").strip().upper()
        if market_val in ("US", "JP"):
            conditions.append("s.symbol_key LIKE %s")
            params.append(f"{market_val}:%")

        where_sql = ""
        if conditions:
            where_sql = " AND " + " AND ".join(conditions)

        return where_sql, params

    def _resolve_ai_mode(self, ai_mode: Optional[str]) -> tuple[str, Optional[float], Optional[str]]:
        mode = str(ai_mode or "off").strip().lower()
        aliases = {
            "off": "off",
            "all": "off",
            "balanced": "balanced",
            "balance": "balanced",
            "high_precision": "high_precision",
            "high-precision": "high_precision",
            "high": "high_precision",
            "strict": "strict",
        }
        m = aliases.get(mode, "off")
        if m == "balanced":
            return m, 0.6, None
        if m == "high_precision":
            return m, 0.7, None
        if m == "strict":
            return m, 0.6, "isotonic"
        return "off", None, None

    def _generate_signals(self, rs_rating, rsi, dist_52w, sma20, sma50, sma200, price):
        signals = []
        if rs_rating and rs_rating >= 80:
            signals.append("High RS")
        if rsi is not None:
            r = float(rsi)
            if r >= 70:
                signals.append("Overbought")
            elif r <= 30:
                signals.append("Oversold")
        if dist_52w is not None and float(dist_52w) >= -5:
            signals.append("Near High")
        if sma20 and sma50 and sma200 and price:
            if float(sma20) > float(sma50) > float(sma200) and float(price) > float(sma20):
                signals.append("Perfect Order")
        return signals

    _ETF_SECTOR_MAP = {
        'XLK': 'Technology',
        'XLV': 'Health Care',
        'XLE': 'Energy',
        'XLF': 'Financials',
        'XLY': 'Consumer Discretionary',
        'XLP': 'Consumer Staples',
        'XLU': 'Utilities',
        'XLB': 'Materials',
        'XLRE': 'Real Estate',
        'XLC': 'Communication Services',
        'XLI': 'Industrials',
    }

    def _get_sector_map(self, symbols: list[str]) -> dict[str, str]:
        """constituent_symbol -> sector name via sector_constituents."""
        if not symbols:
            return {}
        # strip market prefix: US:AAPL -> AAPL
        tickers = [s.split(':', 1)[1] if ':' in s else s for s in symbols]
        rows = self.db.execute_query(
            """
            SELECT constituent_symbol, sector_etf_symbol
            FROM sector_constituents
            WHERE constituent_symbol = ANY(%s)
            """,
            (tickers,),
        ) or []
        out: dict[str, str] = {}
        for r in rows:
            etf = r[1]
            sector_name = self._ETF_SECTOR_MAP.get(etf, etf)
            # map back to symbol_key
            out[f'US:{r[0]}'] = sector_name
        return out

    def scan_stocks(
        self,
        mode: str = "all",
        ai_mode: str = "off",
        sector: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        min_rs: Optional[int] = None,
        min_total_score: int = 70,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        volume_min: Optional[int] = None,
        rsi_filter: Optional[str] = None,
        symbol: Optional[str] = None,
        market: Optional[str] = "US",
    ) -> "ScreenerResponse":
        # When user searches a specific symbol, do not hide it behind score cutoffs.
        effective_min_total_score = 0 if symbol else int(min_total_score)
        where_sql, where_params = self._build_filter_clause(
            min_rs=min_rs,
            min_price=min_price,
            max_price=max_price,
            volume_min=volume_min,
            rsi_filter=rsi_filter,
            symbol=symbol,
            market=market,
        )
        ai_mode_used, ai_min_p, ai_cal_method = self._resolve_ai_mode(ai_mode)
        ai_lateral_filter_sql = ""
        ai_lateral_filter_params: list = []
        if ai_mode_used in ("balanced", "high_precision"):
            ai_lateral_filter_sql = " AND LOWER(COALESCE(cal_method, 'none')) = %s"
            ai_lateral_filter_params.append("none")
        elif ai_mode_used == "strict":
            ai_lateral_filter_sql = " AND LOWER(COALESCE(cal_method, '')) = %s"
            ai_lateral_filter_params.append("isotonic")
        ai_filter_sql = ""
        ai_filter_params: list = []
        if ai_mode_used != "off":
            ai_filter_sql = " AND ai_latest.p_up5 IS NOT NULL AND ai_latest.p_up5 >= %s"
            ai_filter_params.append(float(ai_min_p or 0.0))
            if ai_cal_method and not ai_lateral_filter_sql:
                ai_filter_sql += " AND LOWER(COALESCE(ai_latest.cal_method, '')) = %s"
                ai_filter_params.append(str(ai_cal_method).lower())

        cte = f"""
            WITH base AS (
                SELECT
                    s.symbol_key,
                    i.name,
                    p.close AS price,
                    CASE
                        WHEN pc.close > 0 THEN ((p.close - pc.close) / pc.close * 100)
                        ELSE 0
                    END AS change_pct,
                    p.volume,
                    ind.rsi14,
                    COALESCE(rr.rating, ind_rs.rs_rating, 0) AS rs_rating,
                    ind.sma20,
                    ind.sma50,
                    ind.sma200,
                    ind.dist_to_52w_high_pct AS dist_52w_high_pct,
                    COALESCE(ind.pivot, ind_pivot.pivot) AS pivot
                FROM (
                    SELECT symbol_key
                    FROM instruments
                    WHERE is_active = true
                    UNION
                    SELECT DISTINCT symbol_key
                    FROM indicator_daily
                    WHERE symbol_key IS NOT NULL
                ) s
                LEFT JOIN instruments i
                  ON i.symbol_key = s.symbol_key
                INNER JOIN LATERAL (
                    SELECT close, volume, trading_date
                    FROM price_daily
                    WHERE symbol_key = s.symbol_key
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) p ON true
                LEFT JOIN LATERAL (
                    SELECT close
                    FROM price_daily
                    WHERE symbol_key = s.symbol_key
                      AND trading_date < p.trading_date
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) pc ON true
                LEFT JOIN LATERAL (
                    SELECT symbol_key, rsi14, sma20, sma50, sma200, dist_to_52w_high_pct, pivot
                    FROM indicator_daily
                    WHERE symbol_key = s.symbol_key
                      AND trading_date <= p.trading_date
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind ON true
                LEFT JOIN LATERAL (
                    SELECT pivot
                    FROM indicator_daily
                    WHERE symbol_key = s.symbol_key
                      AND trading_date <= p.trading_date
                      AND pivot IS NOT NULL
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind_pivot ON true
                LEFT JOIN LATERAL (
                    SELECT rs_rating
                    FROM indicator_daily
                    WHERE symbol_key = s.symbol_key
                      AND trading_date <= p.trading_date
                      AND rs_rating IS NOT NULL
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind_rs ON true
                LEFT JOIN rs_ratings rr
                  ON rr.symbol_key = s.symbol_key
                LEFT JOIN LATERAL (
                    SELECT p_up5, threshold_buy, decision, cal_method, asof
                    FROM ai_predictions
                    WHERE symbol_key = s.symbol_key
                      {ai_lateral_filter_sql}
                    ORDER BY asof DESC, updated_at DESC
                    LIMIT 1
                ) ai_latest ON true
                WHERE ind.symbol_key IS NOT NULL
                  {where_sql}
                  {ai_filter_sql}
            ),
            scored AS (
                SELECT
                    *,
                    (
                        (COALESCE(rs_rating, 0) / 100.0) * 40
                        + CASE
                            WHEN rsi14 BETWEEN 40 AND 70 THEN 15
                            WHEN (rsi14 BETWEEN 30 AND 39.999999) OR (rsi14 > 70 AND rsi14 <= 80) THEN 10
                            WHEN rsi14 IS NOT NULL THEN 5
                            ELSE 0
                          END
                        + CASE
                            WHEN sma20 IS NOT NULL AND sma50 IS NOT NULL AND sma200 IS NOT NULL AND price IS NOT NULL THEN
                                CASE
                                    WHEN sma20 > sma50 AND sma50 > sma200 AND price > sma20 THEN 20
                                    WHEN sma20 > sma50 AND price > sma20 THEN 15
                                    WHEN price > sma20 THEN 10
                                    ELSE 5
                                END
                            ELSE 0
                          END
                        + CASE
                            WHEN dist_52w_high_pct IS NOT NULL THEN
                                CASE
                                    WHEN dist_52w_high_pct >= -5 THEN 10
                                    WHEN dist_52w_high_pct >= -10 THEN 7
                                    WHEN dist_52w_high_pct >= -20 THEN 4
                                    ELSE 2
                                END
                            ELSE 0
                          END
                    )::numeric(8,2) AS total_score
                FROM base
            )
        """

        count_query = cte + " SELECT COUNT(*) FROM scored WHERE total_score >= %s "
        page_query = cte + """
            SELECT
                symbol_key,
                name,
                price,
                change_pct,
                volume,
                rsi14,
                rs_rating,
                sma20,
                sma50,
                sma200,
                dist_52w_high_pct,
                pivot,
                total_score
            FROM scored
            WHERE total_score >= %s
            ORDER BY total_score DESC, rs_rating DESC, price DESC, symbol_key ASC
            LIMIT %s OFFSET %s
        """

        try:
            with self.db:
                self._ensure_rs_table_exists()
                self._ensure_ai_predictions_table_exists()
                count_res = self.db.execute_query(
                    count_query,
                    tuple(where_params + ai_lateral_filter_params + ai_filter_params + [effective_min_total_score]),
                )
                total_stocks = int(count_res[0][0]) if count_res and count_res[0] else 0

                results = self.db.execute_query(
                    page_query,
                    tuple(where_params + ai_lateral_filter_params + ai_filter_params + [effective_min_total_score, limit, offset]),
                )

                if not results:
                    from app.models.schemas import ScreenerResponse

                    total_pages = math.ceil(total_stocks / limit) if limit > 0 else 0
                    return ScreenerResponse(
                        items=[],
                        total=total_stocks,
                        page=(offset // limit) + 1 if limit > 0 else 1,
                        limit=limit,
                        total_pages=total_pages,
                        freshness=self._get_screener_freshness(market=str(market or "US")),
                        usd_jpy_rate=self._get_usd_jpy_rate(),
                    )

                from app.models.schemas import ScreenerResult, ScreenerResponse
                market_summary = self._get_market_summary_safe()

                items = []
                for row in results:
                    (
                        s_key,
                        s_name,
                        price,
                        change_pct,
                        volume,
                        rsi,
                        rs_rating,
                        sma20,
                        sma50,
                        sma200,
                        dist_52w,
                        pivot,
                        total_score,
                    ) = row

                    items.append(
                        ScreenerResult(
                            symbol=s_key,
                            name=s_name or s_key,
                            price=float(price) if price else 0,
                            change_pct=float(change_pct) if change_pct else 0,
                            volume=int(volume) if volume else 0,
                            rsi=float(rsi) if rsi is not None else None,
                            rs_rating=int(rs_rating) if rs_rating else 0,
                            total_score=round(float(total_score), 1) if total_score is not None else 0.0,
                            dist_52w_high_pct=float(dist_52w) if dist_52w is not None else None,
                            pivot=float(pivot) if pivot is not None else None,
                            signals=self._generate_signals(
                                rs_rating, rsi, dist_52w, sma20, sma50, sma200, price
                            ),
                            ma_cross=None,
                        )
                    )

                symbol_key_list = [item.symbol for item in items]
                ai_map = self._get_latest_ai_predictions(symbol_key_list)
                class15_map = self._get_latest_class15_predictions(symbol_key_list)
                fund_map = self._get_canslim_fundamentals(symbol_key_list)
                llm_map = self._get_canslim_llm_signals(symbol_key_list)
                sector_map = self._get_sector_map(symbol_key_list)
                price_hist_map = self._get_price_history_bulk(symbol_key_list)
                obv_hist_map = self._get_obv_history_bulk(symbol_key_list)
                gate = (market_summary.get("market_gate") or "ON").upper()
                for item in items:
                    sk = (item.symbol or "").upper()
                    ai = ai_map.get(sk, {})
                    p_up5 = ai.get("p_up5")
                    probs3 = self._derive_ai_3class(p_up5)
                    item.m_judgment = self._m_badge(gate)
                    item.market_gate = gate
                    item.ho_alert = market_summary.get("ho_alert", "非点灯")
                    item.ho_days_remaining = market_summary.get("ho_days_remaining", 0)
                    ndd = market_summary.get("nasdaq_dd_count_5w")
                    sdd = market_summary.get("sp500_dd_count_5w")
                    item.dd_count = {"nasdaq": ndd, "sp500": sdd}
                    item.dd_count_display = f"{ndd if ndd is not None else '-'} / {sdd if sdd is not None else '-'}"
                    item.vix_mode = market_summary.get("vix_mode", "Unknown")
                    item.breakout_effectiveness = self._breakout_effectiveness(
                        gate, item.model_dump()
                    )
                    fund_data = fund_map.get(sk, {})
                    llm_data = llm_map.get(sk, {})
                    count, criteria = self._estimate_canslim_pass_count(
                        item.model_dump(),
                        gate,
                        eps_yoy_pct=fund_data.get("eps_yoy_pct"),
                        revenue_yoy_pct=fund_data.get("revenue_yoy_pct"),
                        llm_tone_score=llm_data.get("llm_tone_score"),
                    )
                    item.canslim_pass_count = count
                    item.canslim_pass_count_display = f"{count}/7"
                    item.canslim_criteria = criteria
                    item.ai_p_up5_2w = round(p_up5 * 100, 1) if p_up5 is not None else None
                    item.ai_3class = probs3
                    item.ai_3class_summary = f"上{probs3['up']}/横{probs3['flat']}/下{probs3['down']}"
                    item.overall_grade = self._overall_grade(gate, p_up5, float(item.total_score or 0))
                    # Phase 2: action_label / gate_reasons / predicted_class
                    al, gr, pc = self._compute_screener_ai_labels(
                        gate=gate,
                        p_up5=p_up5,
                        threshold_buy=ai.get("threshold_buy"),
                        decision=ai.get("decision"),
                    )
                    item.action_label = al
                    item.gate_reasons = gr
                    item.predicted_class = pc
                    c15 = class15_map.get(sk) or {}
                    item.final_rank_score = self._compute_final_rank_score(
                        total_score=item.total_score,
                        p_up5=p_up5,
                        canslim_count=item.canslim_pass_count,
                        action_label=al,
                        predicted_class=pc,
                        prob_up=c15.get("prob_up"),
                        prob_down=c15.get("prob_down"),
                    )
                    item.class15 = class15_map.get(sk)
                    item.sector = sector_map.get(sk)
                    item.market_summary = market_summary
                    # Institutional footprint (Layer 1)
                    prices = price_hist_map.get(sk, [])
                    obv_data = obv_hist_map.get(sk, [])
                    fp_score, fp_flags = self._compute_institutional_footprint_score(prices, obv_data)
                    inst_score, inst_source = self._resolve_institutional_score(fp_score, None)
                    item.institutional_footprint_score = fp_score
                    item.institutional_evidence_score = None
                    item.institutional_score = inst_score
                    item.institutional_score_source = inst_source
                    item.institutional_flags = fp_flags

                total_pages = math.ceil(total_stocks / limit) if limit > 0 else 0
                return ScreenerResponse(
                    items=items,
                    total=total_stocks,
                    page=(offset // limit) + 1 if limit > 0 else 1,
                    limit=limit,
                    total_pages=total_pages,
                    freshness=self._get_screener_freshness(market=str(market or "US")),
                    usd_jpy_rate=self._get_usd_jpy_rate(),
                )
        except Exception as e:
            print(f"Error in scan_stocks: {e}")
            raise e

    # ─────────────────────────────────────────────────────────────
    # Institutional footprint (I factor) — 2-layer architecture
    # Layer 1: footprint (price/volume proxy) ← implemented here
    # Layer 2: evidence (13F / 5%-rule)       ← schema placeholder only
    # ─────────────────────────────────────────────────────────────

    def _get_price_history_bulk(self, symbols: list[str]) -> dict[str, list]:
        """
        Bulk-fetch last 50 trading days of OHLCV per symbol.
        Returns: symbol_key -> list of (close, high, low, volume), newest-first.
        """
        if not symbols:
            return {}
        rows = self.db.execute_query(
            """
            SELECT symbol_key, close, high, low, volume
            FROM (
                SELECT symbol_key,
                       close::float,
                       high::float,
                       low::float,
                       volume::bigint,
                       ROW_NUMBER() OVER (PARTITION BY symbol_key ORDER BY trading_date DESC) AS rn
                FROM price_daily
                WHERE symbol_key = ANY(%s)
            ) t
            WHERE rn <= 50
            ORDER BY symbol_key, rn ASC
            """,
            (symbols,),
        ) or []
        out: dict[str, list] = {}
        for r in rows:
            sk = str(r[0]).upper()
            if sk not in out:
                out[sk] = []
            out[sk].append((
                float(r[1]) if r[1] is not None else None,
                float(r[2]) if r[2] is not None else None,
                float(r[3]) if r[3] is not None else None,
                int(r[4]) if r[4] is not None else None,
            ))
        return out

    def _get_obv_history_bulk(self, symbols: list[str]) -> dict[str, list]:
        """
        Bulk-fetch last 20 OBV values per symbol, oldest-first.
        Returns: symbol_key -> list of int (OBV).
        """
        if not symbols:
            return {}
        rows = self.db.execute_query(
            """
            SELECT symbol_key, obv
            FROM (
                SELECT symbol_key,
                       obv::bigint,
                       ROW_NUMBER() OVER (PARTITION BY symbol_key ORDER BY trading_date DESC) AS rn
                FROM indicator_daily
                WHERE symbol_key = ANY(%s)
                  AND obv IS NOT NULL
            ) t
            WHERE rn <= 20
            ORDER BY symbol_key, rn DESC
            """,
            (symbols,),
        ) or []
        out: dict[str, list] = {}
        for r in rows:
            sk = str(r[0]).upper()
            if sk not in out:
                out[sk] = []
            out[sk].append(int(r[1]) if r[1] is not None else None)
        return out

    @staticmethod
    def _breakout_volume_score(prices_50d: list) -> float:
        """
        A: ブレイクアウト + 出来高急増 (重み 30%)
        prices_50d: (close, high, low, volume) タプルのリスト, newest-first.
        """
        if len(prices_50d) < 20:
            return 0.0
        highs = [r[1] for r in prices_50d[:50] if r[1] is not None]
        if not highs:
            return 0.0
        high_50d = max(highs)
        if high_50d <= 0:
            return 0.0
        latest_close = prices_50d[0][0]
        if latest_close is None:
            return 0.0
        vols_20d = [r[3] for r in prices_50d[:20] if r[3] is not None and r[3] > 0]
        if not vols_20d:
            return 0.0
        vol_avg = sum(vols_20d) / len(vols_20d)
        latest_vol = prices_50d[0][3] or 0
        volume_ratio = latest_vol / vol_avg if vol_avg > 0 else 0.0
        # 50日高値へのnearness: 90%以上で0→1に線形
        nearness = min(1.0, max(0.0, (float(latest_close) / high_50d - 0.90) / 0.10))
        # 出来高倍率: 1.0倍→0, 2.5倍→1.0
        vol_factor = min(1.0, max(0.0, (volume_ratio - 1.0) / 1.5))
        return min(100.0, max(0.0, nearness * 60 + vol_factor * 40))

    @staticmethod
    def _accumulation_score(prices_20d: list) -> float:
        """
        B: 上昇日 vs 下落日の出来高比較 (重み 25%)
        prices_20d: (close, high, low, volume) タプルのリスト, newest-first.
        """
        if len(prices_20d) < 5:
            return 0.0
        up_vols: list[int] = []
        down_vols: list[int] = []
        n = min(20, len(prices_20d))
        for i in range(n - 1):
            curr_close = prices_20d[i][0]
            prev_close = prices_20d[i + 1][0]
            vol = prices_20d[i][3]
            if curr_close is None or prev_close is None or vol is None:
                continue
            if curr_close > prev_close:
                up_vols.append(vol)
            elif curr_close < prev_close:
                down_vols.append(vol)
        if not up_vols and not down_vols:
            return 50.0
        up_avg = sum(up_vols) / len(up_vols) if up_vols else 0.0
        down_avg = sum(down_vols) / len(down_vols) if down_vols else 0.0
        if down_avg == 0:
            return 80.0 if up_avg > 0 else 50.0
        ratio = up_avg / down_avg
        # ratio: 0.5→0, 1.0→33, 2.0→100
        return min(100.0, max(0.0, (ratio - 0.5) / 1.5 * 100))

    @staticmethod
    def _obv_trend_score(obv_list: list) -> float:
        """
        C: OBV 20日トレンド (重み 20%)
        obv_list: OBV値のリスト, oldest-first.
        """
        vals = [v for v in obv_list if v is not None]
        if len(vals) < 5:
            return 50.0
        first = vals[0]
        last = vals[-1]
        if first == 0:
            return 50.0
        change_pct = (last - first) / abs(first)
        # +5%→75, 0%→50, -5%→25
        return min(100.0, max(0.0, 50 + change_pct * 500))

    @staticmethod
    def _support_rebound_score(prices_20d: list) -> float:
        """
        D: 安値圏反発 + 出来高急増 (重み 15%)
        prices_20d: (close, high, low, volume) タプルのリスト, newest-first.
        """
        if len(prices_20d) < 5:
            return 0.0
        lows = [r[2] for r in prices_20d if r[2] is not None and r[2] > 0]
        if not lows:
            return 0.0
        low_20d = min(lows)
        vols = [r[3] for r in prices_20d if r[3] is not None and r[3] > 0]
        if not vols:
            return 0.0
        vol_avg = sum(vols) / len(vols)
        best = 0.0
        for r in prices_20d[:10]:
            close_, _, low_, vol_ = r[0], r[1], r[2], r[3]
            if close_ is None or low_ is None or vol_ is None:
                continue
            near_support = low_ <= low_20d * 1.03
            bullish = close_ > low_
            vol_ratio = vol_ / vol_avg if vol_avg > 0 else 0.0
            if near_support and bullish and vol_ratio >= 1.3:
                best = max(best, min(100.0, vol_ratio * 40))
        return best

    @staticmethod
    def _volume_profile_proxy_score(prices_20d: list) -> float:
        """
        E: 高値圏への出来高集中 (重み 10%)
        prices_20d: (close, high, low, volume) タプルのリスト, newest-first.
        """
        pairs = [(r[0], r[3]) for r in prices_20d if r[0] is not None and r[3] is not None]
        if len(pairs) < 5:
            return 50.0
        price_min = min(c for c, _ in pairs)
        price_max = max(c for c, _ in pairs)
        if price_max <= price_min:
            return 50.0
        mid = (price_min + price_max) / 2
        high_vol = sum(v for c, v in pairs if c >= mid)
        total_vol = sum(v for _, v in pairs)
        if total_vol == 0:
            return 50.0
        ratio = high_vol / total_vol
        # 0.30→0, 0.70→100
        return min(100.0, max(0.0, (ratio - 0.30) / 0.40 * 100))

    @staticmethod
    def _compute_institutional_footprint_score(prices_50d: list, obv_list: list) -> tuple:
        """
        Layer 1: 機関投資家フットプリントスコア (price/volume ベース)
        Returns (score: float | None, flags: list[str]).
        データ不足時は (None, ["insufficient_data"]).
        """
        if len(prices_50d) < 10:
            return None, ["insufficient_data"]
        prices_20d = prices_50d[:20]
        a = ScreenerService._breakout_volume_score(prices_50d)
        b = ScreenerService._accumulation_score(prices_20d)
        c = ScreenerService._obv_trend_score(obv_list) if obv_list else 50.0
        d = ScreenerService._support_rebound_score(prices_20d)
        e = ScreenerService._volume_profile_proxy_score(prices_20d)
        flags: list[str] = []
        if a >= 60:
            flags.append("breakout_volume_confirmed")
        if b >= 60:
            flags.append("accumulation_positive")
        if c >= 60:
            flags.append("obv_rising")
        if d >= 50:
            flags.append("support_rebound_volume")
        if e >= 60:
            flags.append("volume_concentration_high")
        score = a * 0.30 + b * 0.25 + c * 0.20 + d * 0.15 + e * 0.10
        return round(score, 1), flags

    @staticmethod
    def _resolve_institutional_score(
        footprint: Optional[float],
        evidence: Optional[float],
    ) -> tuple:
        """
        Layer 2: evidence があれば優先、なければ footprint を使用。
        Returns (score: float | None, source: str).
        source: "evidence" | "footprint" | "none"
        """
        if evidence is not None:
            return evidence, "evidence"
        if footprint is not None:
            return footprint, "footprint"
        return None, "none"

    def _get_usd_jpy_rate(self) -> Optional[float]:
        q = """
            SELECT close
            FROM price_daily
            WHERE symbol_key IN ('US:USDJPY=X', 'USDJPY=X')
            ORDER BY trading_date DESC
            LIMIT 1
        """
        rows = self.db.execute_query(q) or []
        if not rows or rows[0][0] is None:
            return None
        try:
            return float(rows[0][0])
        except Exception:
            return None

    def get_diagnostics(self, request_params: Optional[dict] = None) -> dict:
        diag = {
            "status": "ok",
            "api_contract_check": {
                "status": "ok",
                "expected_query_params": [
                    "min_price",
                    "max_price",
                    "symbol",
                    "market",
                    "min_rs",
                    "min_total_score",
                    "volume_min",
                    "rsi_filter",
                    "ai_mode",
                    "offset",
                    "limit",
                ],
                "received_query_params": list(request_params.keys()) if request_params else [],
                "missing_query_params": [],
                "missing_response_fields": [],
            },
            "db_integrity_check": {"status": "ok", "tables": {}, "missing_columns": []},
            "data_freshness_check": {"latest_dates": {}, "staleness_days": {}},
            "query_logic_check": {
                "latest_price_date_used": None,
                "price_filter_applied_to_latest_close": True,
                "sample_item_debug": None,
            },
        }

        try:
            if request_params:
                diag["api_contract_check"]["missing_query_params"] = [
                    p
                    for p in diag["api_contract_check"]["expected_query_params"]
                    if p not in diag["api_contract_check"]["received_query_params"]
                ]

            scan_kwargs = {"limit": 1, "offset": 0, "min_total_score": 0}
            if request_params:
                if "min_price" in request_params:
                    scan_kwargs["min_price"] = float(request_params["min_price"])
                if "max_price" in request_params:
                    scan_kwargs["max_price"] = float(request_params["max_price"])
                if "min_rs" in request_params:
                    scan_kwargs["min_rs"] = int(request_params["min_rs"])
                if "market" in request_params:
                    scan_kwargs["market"] = str(request_params["market"])

            res = self.scan_stocks(**scan_kwargs)
            if res.items:
                sample = res.items[0].model_dump()
                required_keys = [
                    "symbol",
                    "name",
                    "price",
                    "change_pct",
                    "volume",
                    "rs_rating",
                    "total_score",
                    "dist_52w_high_pct",
                    "pivot",
                    "signals",
                ]
                diag["api_contract_check"]["missing_response_fields"] = [
                    k for k in required_keys if k not in sample
                ]

                with self.db:
                    raw_p = self.db.execute_query(
                        "SELECT trading_date, close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1",
                        (sample["symbol"],),
                    )
                    if raw_p:
                        diag["query_logic_check"]["sample_item_debug"] = {
                            "symbol": sample["symbol"],
                            "price": sample["price"],
                            "price_date": str(raw_p[0][0]),
                            "raw_close": float(raw_p[0][1]),
                            "min_price": scan_kwargs.get("min_price"),
                            "max_price": scan_kwargs.get("max_price"),
                        }
                        diag["query_logic_check"]["latest_price_date_used"] = str(raw_p[0][0])

            if (
                diag["api_contract_check"]["missing_response_fields"]
                or diag["api_contract_check"]["missing_query_params"]
            ):
                diag["api_contract_check"]["status"] = "warning"
                if diag["status"] == "ok":
                    diag["status"] = "warning"

            with self.db:
                required_cols = {
                    "price_daily": ["symbol_key", "trading_date", "close", "volume"],
                    "indicator_daily": [
                        "symbol_key",
                        "trading_date",
                        "rs_rating",
                        "rsi14",
                        "dist_to_52w_high_pct",
                        "pivot",
                    ],
                    "instruments": ["symbol_key", "name", "is_active"],
                }
                for table, cols in required_cols.items():
                    res_cols = self.db.execute_query(
                        f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'"
                    )
                    existing = [r[0] for r in res_cols] if res_cols else []
                    missing = [c for c in cols if c not in existing]
                    if missing:
                        diag["db_integrity_check"]["missing_columns"].extend(
                            [f"{table}.{m}" for m in missing]
                        )

                if diag["db_integrity_check"]["missing_columns"]:
                    diag["db_integrity_check"]["status"] = "error"
                    diag["status"] = "error"

                from datetime import date

                today = date.today()
                for table in ["price_daily", "indicator_daily"]:
                    c_res = self.db.execute_query(f"SELECT COUNT(*) FROM {table}")
                    diag["db_integrity_check"]["tables"][table] = {
                        "rows": c_res[0][0] if c_res else 0
                    }

                    d_res = self.db.execute_query(f"SELECT MAX(trading_date) FROM {table}")
                    l_date = d_res[0][0] if d_res and d_res[0][0] else None
                    diag["data_freshness_check"]["latest_dates"][table] = (
                        str(l_date) if l_date else None
                    )
                    if l_date:
                        days = (today - l_date).days
                        diag["data_freshness_check"]["staleness_days"][table] = days
                        if days > 4 and diag["status"] == "ok":
                            diag["status"] = "warning"

            return diag
        except Exception as e:
            import traceback

            return {
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc(),
            }
