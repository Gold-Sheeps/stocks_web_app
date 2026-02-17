from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
from app.db.database import Database
from app.models.schemas import StockInfo, Indicators, PricePoint, StockDetailResponse

class StockDetailService:
    def __init__(self):
        self.db = Database()
        self.db.connect()

    def _candidate_symbol_keys(self, symbol: str) -> List[str]:
        raw = (symbol or "").strip().upper()
        if not raw:
            return []

        market = "US"
        ticker = raw
        if ":" in raw:
            p_market, p_ticker = raw.split(":", 1)
            market = p_market.strip().upper() or "US"
            ticker = p_ticker.strip().upper()
        else:
            if raw.isdigit() and len(raw) == 4:
                market = "JP"
                ticker = raw

        candidates: List[str] = []
        if market == "US":
            # Some US symbols are stored as BRK-B, while users may input BRK.B.
            t_dash = ticker.replace(".", "-")
            t_dot = ticker.replace("-", ".")
            candidates = [f"US:{t_dash}", f"US:{t_dot}"] if t_dash != t_dot else [f"US:{t_dash}"]
        else:
            candidates = [f"{market}:{ticker}"]

        # de-dup preserving order
        seen = set()
        uniq: List[str] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq

    def get_stock_summary(self, symbol: str) -> StockDetailResponse:
        """
        Phase 5-1: Lightweight Summary for Initial Load
        Includes: Price, Change, Indicator Snapshot, Signal Summary, Data Quality
        """
        self.db.connect()
        try:
            from app.models.schemas import SignalStatus, SignalReason, StockSignalSummary, DataQuality
            
            # 1. Normalize Symbol
            raw_symbol = symbol.strip().upper()
            candidates = self._candidate_symbol_keys(raw_symbol)
            symbol_key = candidates[0] if candidates else raw_symbol

            # 2. Get Snapshot aligned with Screener joins/priority
            query_info = """
                SELECT
                    i.symbol_key,
                    i.name,
                    i.market,
                    p.close,
                    p.volume,
                    p.trading_date,
                    pc.close AS prev_close,
                    ind.sma5,
                    ind.sma20,
                    ind.sma50,
                    ind.sma200,
                    ind.rsi14,
                    ind.macd,
                    ind.macd_signal,
                    ind.macd_hist,
                    ind.atr14,
                    ind.high_52w,
                    ind.dist_to_52w_high_pct,
                    ind.ema21,
                    ind.pivot,
                    COALESCE(rr.rating, ind_rs.rs_rating) AS rs_rating,
                    ind.trading_date AS ind_trading_date
                FROM instruments i
                LEFT JOIN LATERAL (
                    SELECT close, volume, trading_date
                    FROM price_daily
                    WHERE symbol_key = i.symbol_key
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) p ON true
                LEFT JOIN LATERAL (
                    SELECT close
                    FROM price_daily
                    WHERE symbol_key = i.symbol_key
                      AND p.trading_date IS NOT NULL
                      AND trading_date < p.trading_date
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) pc ON true
                LEFT JOIN LATERAL (
                    SELECT
                        sma5, sma20, sma50, sma200,
                        rsi14, macd, macd_signal, macd_hist,
                        atr14, high_52w, dist_to_52w_high_pct,
                        ema21, pivot, rs_rating, trading_date
                    FROM indicator_daily
                    WHERE symbol_key = i.symbol_key
                      AND (p.trading_date IS NULL OR trading_date <= p.trading_date)
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind ON true
                LEFT JOIN LATERAL (
                    SELECT rs_rating
                    FROM indicator_daily
                    WHERE symbol_key = i.symbol_key
                      AND rs_rating IS NOT NULL
                      AND (p.trading_date IS NULL OR trading_date <= p.trading_date)
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind_rs ON true
                LEFT JOIN rs_ratings rr
                  ON rr.symbol_key = i.symbol_key
                WHERE i.symbol_key = %s
                LIMIT 1
            """
            info_result = None
            for cand in candidates or [symbol_key]:
                rows = self.db.execute_query(query_info, (cand,))
                if rows:
                    info_result = rows
                    symbol_key = cand
                    break

            stock_info = None
            price_valid = False
            latest_price_date = None
            
            if not info_result:
                # Completely missing
                stock_info = StockInfo(
                    symbol=symbol_key, name=raw_symbol, market="Unknown", currency="USD",
                    current_price=None, volume=0, change_pct=None, last_updated=datetime.now()
                )
            else:
                row = info_result[0]
                close_price = row[3]
                latest_price_date = datetime.combine(row[5], datetime.min.time()) if isinstance(row[5], date) else None
                
                # Validation: Close must be > 0 and not None
                if close_price is not None and close_price > 0:
                    price_valid = True
                
                # Previous Close for Change Pct (from same snapshot query)
                prev_close = row[6] if (row[6] is not None and row[6] > 0) else None

                change_pct = None
                if price_valid and prev_close:
                    change_pct = ((close_price - prev_close) / prev_close) * 100

                # YTD Logic (Simplified for summary)
                ytd_change = None
                current_year = date.today().year
                start_of_year = date(current_year, 1, 1)
                if price_valid:
                    q_ytd = "SELECT close FROM price_daily WHERE symbol_key=%s AND trading_date >= %s ORDER BY trading_date ASC LIMIT 1"
                    ytd_res = self.db.execute_query(q_ytd, (symbol_key, start_of_year))
                    if ytd_res and ytd_res[0][0] and ytd_res[0][0] > 0:
                        ytd_start = ytd_res[0][0]
                        ytd_change = ((close_price - ytd_start) / ytd_start) * 100

                stock_info = StockInfo(
                    symbol=row[0], name=row[1], market=row[2] or "US", currency="USD",
                    current_price=close_price if price_valid else None, # STRICT rule
                    volume=row[4] if row[4] else 0,
                    change_pct=change_pct,
                    ytd_change_pct=ytd_change,
                    last_updated=latest_price_date or datetime.now()
                )

            # 3. Indicators from the same aligned snapshot row
            indicators = Indicators()
            latest_ind_date = None
            ind_missing_count = 0
            
            # Helper
            def v(val): return val if val is not None and val != 0 else None

            if info_result:
                r = info_result[0]
                latest_ind_date = datetime.combine(r[21], datetime.min.time()) if r[21] else None
                pivot_val = v(r[19])

                # Fallback: latest row's pivot can be NULL depending on update order.
                # In that case, use the most recent non-null pivot.
                if pivot_val is None:
                    q_pivot = """
                        SELECT pivot
                        FROM indicator_daily
                        WHERE symbol_key = %s AND pivot IS NOT NULL
                        ORDER BY trading_date DESC
                        LIMIT 1
                    """
                    pivot_res = self.db.execute_query(q_pivot, (symbol_key,))
                    if pivot_res and pivot_res[0][0] is not None:
                        pivot_val = pivot_res[0][0]
                
                indicators = Indicators(
                    ma5=v(r[7]), ma20=v(r[8]), ma50=v(r[9]), sma200=v(r[10]),
                    rsi=v(r[11]), macd=v(r[12]), signal=v(r[13]), macd_hist=v(r[14]),
                    atr14=v(r[15]), high_52w=v(r[16]), dist_52w_high_pct=v(r[17]),
                    ema21=v(r[18]), pivot=pivot_val, rs_rating=int(r[20]) if r[20] is not None and r[20] > 0 else None,
                    volume_ratio=None
                )
                # Count missing critical indicators
                for f in [indicators.rsi, indicators.macd, indicators.sma200, indicators.rs_rating]:
                    if f is None: ind_missing_count += 1
            else:
                ind_missing_count = 99

            # 4. Signal Logic
            reasons = []
            overall_signal = SignalStatus.NEUTRAL
            confidence = 0.0

            if not price_valid:
                overall_signal = SignalStatus.NO_DATA
                reasons.append(SignalReason(key="price", status=SignalStatus.NO_DATA, message="Price Data Missing"))
                confidence = 0.0
            else:
                # 4-1. Trend (SMA200)
                cp = stock_info.current_price
                if indicators.sma200:
                    if cp > indicators.sma200:
                        reasons.append(SignalReason(key="trend_long", status=SignalStatus.BUY, message=f"Price > SMA200 (${indicators.sma200:.2f})"))
                        confidence += 0.3
                    else:
                        reasons.append(SignalReason(key="trend_long", status=SignalStatus.SELL, message="Price < SMA200"))
                        confidence -= 0.3
                else:
                    reasons.append(SignalReason(key="trend_long", status=SignalStatus.NO_DATA, message="SMA200 Missing"))

                # 4-2. Momentum (RSI)
                if indicators.rsi:
                    if indicators.rsi < 30:
                        reasons.append(SignalReason(key="rsi", status=SignalStatus.BUY, message=f"RSI Oversold ({indicators.rsi:.1f})"))
                        confidence += 0.2
                    elif indicators.rsi > 70:
                        reasons.append(SignalReason(key="rsi", status=SignalStatus.CAUTION, message=f"RSI Overbought ({indicators.rsi:.1f})"))
                        confidence -= 0.2
                    else:
                        reasons.append(SignalReason(key="rsi", status=SignalStatus.NEUTRAL, message=f"RSI Neutral ({indicators.rsi:.1f})"))
                
                # 4-3. MACD
                if indicators.macd is not None and indicators.signal is not None:
                    if indicators.macd > indicators.signal:
                        reasons.append(SignalReason(key="macd", status=SignalStatus.BUY, message="MACD > Signal"))
                        confidence += 0.2
                    else:
                        reasons.append(SignalReason(key="macd", status=SignalStatus.SELL, message="MACD < Signal"))
                        confidence -= 0.2

                # 4-4. RS Rating
                if indicators.rs_rating:
                    if indicators.rs_rating > 80:
                         reasons.append(SignalReason(key="rs", status=SignalStatus.BUY, message=f"Strong RS ({indicators.rs_rating})"))
                         confidence += 0.2
                    elif indicators.rs_rating < 40:
                         reasons.append(SignalReason(key="rs", status=SignalStatus.SELL, message=f"Weak RS ({indicators.rs_rating})"))
                         confidence -= 0.2

                # Determine Overall
                if confidence >= 0.4: overall_signal = SignalStatus.BUY
                elif confidence <= -0.4: overall_signal = SignalStatus.SELL
                elif confidence > 0.2: overall_signal = SignalStatus.BUY # Weak Buy
                elif confidence < -0.2: overall_signal = SignalStatus.SELL # Weak Sell
                else: overall_signal = SignalStatus.NEUTRAL

            signal_summary = StockSignalSummary(
                overall=overall_signal,
                confidence=round(confidence, 2),
                reasons=reasons
            )

            # 5. Data Quality
            data_quality = DataQuality(
                price_valid=price_valid,
                price_reason="ok" if price_valid else ("missing" if info_result else "symbol_not_found"),
                indicator_missing_count=ind_missing_count,
                latest_price_date=latest_price_date,
                latest_indicator_date=latest_ind_date
            )

            return StockDetailResponse(
                stock_info=stock_info,
                indicators=indicators,
                signal_summary=signal_summary,
                data_quality=data_quality,
                price_history=[] # Empty for summary
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            # Return strict error state
            return self._get_error_response(symbol, str(e))
        finally:
            self.db.disconnect()

    def _get_error_response(self, symbol: str, error_msg: str):
         from app.models.schemas import StockInfo, Indicators, SignalStatus, StockSignalSummary, DataQuality, StockDetailResponse
         return StockDetailResponse(
            stock_info=StockInfo(
                symbol=symbol, name="Error", market="", currency="USD", 
                current_price=None, volume=0, change_pct=None, last_updated=datetime.now()
            ),
            indicators=Indicators(),
            signal_summary=StockSignalSummary(overall=SignalStatus.NO_DATA, confidence=0, reasons=[]),
            data_quality=DataQuality(price_valid=False, price_reason=f"error: {error_msg}", indicator_missing_count=99),
            price_history=[]
        )

    # Legacy Compatibility (calls summary + history)
    def get_stock_detail(self, symbol: str) -> StockDetailResponse:
        summary = self.get_stock_summary(symbol)
        # Fetch history separately and merge (for now)
        # Ideally this endpoint is deprecated.
        return summary

    def get_signals(self, symbol: str) -> Dict[str, Any]:
        """(Deprecated) Return Signals for specific endpoint"""
        return {"signals": {}}

    def get_ratios(self, symbol: str) -> Dict[str, Any]:
        self.db.connect()
        try:
            candidates = self._candidate_fundamental_symbols(symbol)
            if not candidates:
                return {"symbol": (symbol or "").strip().upper(), "ratios": {}}

            q_rat = """
                SELECT
                    as_of_date, pe_ratio, forward_pe, roe_pct, roa_pct, net_margin_pct,
                    debt_equity, current_ratio, quick_ratio, asset_turnover
                FROM fundamentals_ratios_latest
                WHERE UPPER(symbol) = ANY(%s)
                LIMIT 1
            """
            rat_res = self.db.execute_query(q_rat, (candidates,)) or []

            if rat_res:
                rr = rat_res[0]
                as_of = rr[0].isoformat() if isinstance(rr[0], (date, datetime)) else str(rr[0])
                return {
                    "symbol": (symbol or "").strip().upper(),
                    "ratios": {
                        "pe_ratio": float(rr[1]) if rr[1] is not None else None,
                        "forward_pe": float(rr[2]) if rr[2] is not None else None,
                        "roe": float(rr[3]) if rr[3] is not None else None,
                        "roa": float(rr[4]) if rr[4] is not None else None,
                        "net_margin": float(rr[5]) if rr[5] is not None else None,
                        "debt_equity": float(rr[6]) if rr[6] is not None else None,
                        "current_ratio": float(rr[7]) if rr[7] is not None else None,
                        "quick_ratio": float(rr[8]) if rr[8] is not None else None,
                        "asset_turnover": float(rr[9]) if rr[9] is not None else None,
                    },
                    "meta": {"as_of": as_of, "eps_period": as_of, "margin_period": as_of},
                }

            # Fallback for environments without fundamentals_ratios_latest.
            q_price = """
                SELECT close
                FROM price_daily
                WHERE symbol_key = ANY(%s)
                ORDER BY trading_date DESC
                LIMIT 1
            """
            price_res = self.db.execute_query(q_price, (candidates,)) or []
            current_price = float(price_res[0][0]) if price_res and price_res[0][0] is not None else None

            q_fund = """
                SELECT period_end_date, eps, revenue, net_income
                FROM fundamentals
                WHERE UPPER(symbol) = ANY(%s)
                ORDER BY period_end_date DESC
            """
            fund_rows = self.db.execute_query(q_fund, (candidates,)) or []

            latest_eps = None
            latest_eps_period = None
            latest_margin = None
            latest_margin_period = None
            for r in fund_rows:
                if latest_eps is None and r[1] is not None:
                    latest_eps = float(r[1])
                    latest_eps_period = r[0].isoformat() if isinstance(r[0], (date, datetime)) else str(r[0])
                rev = float(r[2]) if r[2] is not None else None
                ni = float(r[3]) if r[3] is not None else None
                if latest_margin is None and rev is not None and rev != 0 and ni is not None:
                    latest_margin = (ni / rev) * 100.0
                    latest_margin_period = r[0].isoformat() if isinstance(r[0], (date, datetime)) else str(r[0])
                if latest_eps is not None and latest_margin is not None:
                    break

            pe = None if (current_price is None or latest_eps in (None, 0)) else (current_price / latest_eps)

            return {
                "symbol": (symbol or "").strip().upper(),
                "ratios": {
                    "pe_ratio": pe,
                    "forward_pe": None,
                    "roe": None,
                    "roa": None,
                    "net_margin": latest_margin,
                    "debt_equity": None,
                    "current_ratio": None,
                    "quick_ratio": None,
                    "asset_turnover": None,
                },
                "meta": {
                    "eps_period": latest_eps_period,
                    "margin_period": latest_margin_period,
                },
            }
        except Exception as e:
            print(f"[StockDetailService] get_ratios error: {e}")
            return {"symbol": (symbol or "").strip().upper(), "ratios": {}, "error": str(e)}
        finally:
            self.db.disconnect()

    def _candidate_fundamental_symbols(self, symbol: str) -> List[str]:
        raw = (symbol or "").strip().upper()
        if not raw:
            return []

        candidates: List[str] = []
        keys = self._candidate_symbol_keys(raw)
        candidates.extend(keys)

        if ":" in raw:
            market, ticker = raw.split(":", 1)
            market = (market or "").strip().upper()
            ticker = (ticker or "").strip().upper()
        else:
            market = "JP" if raw.isdigit() and len(raw) == 4 else "US"
            ticker = raw

        # Fundamentals updater primarily stores raw symbol (e.g., AMD).
        candidates.append(ticker)
        candidates.append(ticker.replace("-", "."))
        candidates.append(ticker.replace(".", "-"))
        if market != "US":
            candidates.append(f"{market}:{ticker}")

        seen = set()
        uniq: List[str] = []
        for c in candidates:
            cc = (c or "").strip().upper()
            if cc and cc not in seen:
                seen.add(cc)
                uniq.append(cc)
        return uniq

    def get_fundamentals(self, symbol: str, limit: Optional[int] = None) -> Dict[str, Any]:
        self.db.connect()
        try:
            safe_limit = None
            if limit is not None:
                safe_limit = max(1, min(int(limit), 200))
            candidates = self._candidate_fundamental_symbols(symbol)
            if not candidates:
                return {
                    "symbol": (symbol or "").strip().upper(),
                    "rows": [],
                    "message": "No fundamentals data in DB.",
                }

            query = """
                SELECT
                    symbol,
                    period_end_date,
                    eps,
                    revenue,
                    net_income,
                    operating_cash_flow,
                    free_cash_flow,
                    updated_at
                FROM fundamentals
                WHERE UPPER(symbol) = ANY(%s)
                ORDER BY period_end_date DESC
            """
            params: tuple = (candidates,)
            if safe_limit is not None:
                query += " LIMIT %s"
                params = (candidates, safe_limit)

            results = self.db.execute_query(query, params) or []

            rows: List[Dict[str, Any]] = []
            for row in results:
                period_end = row[1]
                updated_at = row[7]
                rows.append(
                    {
                        "symbol": row[0],
                        "period_end_date": period_end.isoformat() if isinstance(period_end, (date, datetime)) else str(period_end),
                        "eps": float(row[2]) if row[2] is not None else None,
                        "revenue": float(row[3]) if row[3] is not None else None,
                        "net_income": float(row[4]) if row[4] is not None else None,
                        "operating_cash_flow": float(row[5]) if row[5] is not None else None,
                        "free_cash_flow": float(row[6]) if row[6] is not None else None,
                        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else (str(updated_at) if updated_at else None),
                    }
                )

            # Preferred source: precomputed DB comparison table.
            comp_query = """
                SELECT
                    latest_period,
                    prev_period,
                    yoy_period,
                    revenue_qoq_diff, revenue_qoq_pct, revenue_yoy_diff, revenue_yoy_pct,
                    net_income_qoq_diff, net_income_qoq_pct, net_income_yoy_diff, net_income_yoy_pct,
                    eps_qoq_diff, eps_qoq_pct, eps_yoy_diff, eps_yoy_pct,
                    operating_cash_flow_qoq_diff, operating_cash_flow_qoq_pct, operating_cash_flow_yoy_diff, operating_cash_flow_yoy_pct,
                    free_cash_flow_qoq_diff, free_cash_flow_qoq_pct, free_cash_flow_yoy_diff, free_cash_flow_yoy_pct
                FROM fundamentals_comparison_latest
                WHERE UPPER(symbol) = ANY(%s)
                LIMIT 1
            """
            comp_res = self.db.execute_query(comp_query, (candidates,)) or []

            def _iso(v):
                return v.isoformat() if isinstance(v, (date, datetime)) else (str(v) if v is not None else None)

            def _num(v):
                return float(v) if v is not None else None

            if comp_res:
                c = comp_res[0]
                growth_qoq = {
                    "revenue": {"diff": _num(c[3]), "pct": _num(c[4])},
                    "net_income": {"diff": _num(c[7]), "pct": _num(c[8])},
                    "eps": {"diff": _num(c[11]), "pct": _num(c[12])},
                    "operating_cash_flow": {"diff": _num(c[15]), "pct": _num(c[16])},
                    "free_cash_flow": {"diff": _num(c[19]), "pct": _num(c[20])},
                }
                growth_yoy = {
                    "revenue": {"diff": _num(c[5]), "pct": _num(c[6])},
                    "net_income": {"diff": _num(c[9]), "pct": _num(c[10])},
                    "eps": {"diff": _num(c[13]), "pct": _num(c[14])},
                    "operating_cash_flow": {"diff": _num(c[17]), "pct": _num(c[18])},
                    "free_cash_flow": {"diff": _num(c[21]), "pct": _num(c[22])},
                }
                latest_period = _iso(c[0])
                prev_period = _iso(c[1])
                yoy_period = _iso(c[2])
            else:
                # Fallback runtime calculation if comparison table has no rows.
                def calc_growth(metric: str, base_idx: Optional[int], prev_idx: Optional[int]) -> Dict[str, Any]:
                    if base_idx is None or prev_idx is None:
                        return {"diff": None, "pct": None}
                    if len(rows) <= base_idx or len(rows) <= prev_idx:
                        return {"diff": None, "pct": None}
                    latest_v = rows[base_idx].get(metric)
                    prev_v = rows[prev_idx].get(metric)
                    if latest_v is None or prev_v is None:
                        return {"diff": None, "pct": None}
                    diff = latest_v - prev_v
                    pct = None
                    if prev_v != 0:
                        pct = (diff / prev_v) * 100.0
                    return {"diff": diff, "pct": pct}

                base_idx = 0 if rows else None
                prev_idx = 1 if len(rows) > 1 else None
                yoy_idx: Optional[int] = None
                yoy_period = None
                if rows and base_idx is not None:
                    latest_dt = datetime.fromisoformat(rows[base_idx]["period_end_date"]).date()
                    for idx, r in enumerate(rows[1:], start=1):
                        d = datetime.fromisoformat(r["period_end_date"]).date()
                        if d.year == (latest_dt.year - 1) and d.month == latest_dt.month:
                            yoy_idx = idx
                            yoy_period = rows[idx]["period_end_date"]
                            break
                    if yoy_idx is None and len(rows) > 4:
                        yoy_idx = 4
                        yoy_period = rows[yoy_idx]["period_end_date"]

                metrics = ["revenue", "net_income", "eps", "operating_cash_flow", "free_cash_flow"]
                growth_qoq = {m: calc_growth(m, base_idx, prev_idx) for m in metrics}
                growth_yoy = {m: calc_growth(m, base_idx, yoy_idx) for m in metrics}
                latest_period = rows[0]["period_end_date"] if rows else None
                prev_period = rows[1]["period_end_date"] if len(rows) > 1 else None

            has_data = len(rows) > 0
            return {
                "symbol": (symbol or "").strip().upper(),
                "rows": rows,
                "message": None if has_data else "No fundamentals data in DB.",
                "comparison": {
                    "latest_period": latest_period,
                    "prev_period": prev_period,
                    "yoy_period": yoy_period,
                    "latest_vs_prev_quarter": growth_qoq,
                    "latest_vs_same_quarter_prev_year": growth_yoy,
                },
                "available_fields": [
                    "eps",
                    "revenue",
                    "net_income",
                    "operating_cash_flow",
                    "free_cash_flow",
                ],
            }
        except Exception as e:
            print(f"[StockDetailService] get_fundamentals error: {e}")
            return {
                "symbol": (symbol or "").strip().upper(),
                "rows": [],
                "message": f"Failed to load fundamentals: {e}",
                "comparison": {},
                "available_fields": [
                    "eps",
                    "revenue",
                    "net_income",
                    "operating_cash_flow",
                    "free_cash_flow",
                ],
            }
        finally:
            self.db.disconnect()

    def _ensure_price_weekly_table(self) -> None:
        self.db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS price_weekly (
                symbol_key TEXT NOT NULL,
                trading_date DATE NOT NULL,
                open NUMERIC(20, 6),
                high NUMERIC(20, 6),
                low NUMERIC(20, 6),
                close NUMERIC(20, 6) NOT NULL,
                volume BIGINT,
                source TEXT DEFAULT 'agg_weekly',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol_key, trading_date)
            )
            """
        )
        self.db.execute_command(
            """
            CREATE INDEX IF NOT EXISTS idx_price_weekly_symbol_date
            ON price_weekly(symbol_key, trading_date DESC)
            """
        )

    def _refresh_price_weekly_for_symbol(self, symbol_key: str) -> None:
        self.db.execute_command(
            """
            WITH d AS (
                SELECT
                    symbol_key,
                    trading_date,
                    open,
                    high,
                    low,
                    close,
                    COALESCE(volume, 0) AS volume,
                    date_trunc('week', trading_date)::date AS week_date
                FROM price_daily
                WHERE symbol_key = %s
            ),
            o AS (
                SELECT DISTINCT ON (symbol_key, week_date)
                    symbol_key,
                    week_date,
                    open
                FROM d
                ORDER BY symbol_key, week_date, trading_date ASC
            ),
            c AS (
                SELECT DISTINCT ON (symbol_key, week_date)
                    symbol_key,
                    week_date,
                    close
                FROM d
                ORDER BY symbol_key, week_date, trading_date DESC
            ),
            a AS (
                SELECT
                    d.symbol_key,
                    d.week_date AS trading_date,
                    o.open,
                    MAX(d.high) AS high,
                    MIN(d.low) AS low,
                    c.close,
                    SUM(d.volume)::bigint AS volume
                FROM d
                JOIN o ON o.symbol_key = d.symbol_key AND o.week_date = d.week_date
                JOIN c ON c.symbol_key = d.symbol_key AND c.week_date = d.week_date
                GROUP BY d.symbol_key, d.week_date, o.open, c.close
            )
            INSERT INTO price_weekly (
                symbol_key, trading_date, open, high, low, close, volume, source, updated_at
            )
            SELECT
                symbol_key, trading_date, open, high, low, close, volume, 'agg_weekly', CURRENT_TIMESTAMP
            FROM a
            ON CONFLICT (symbol_key, trading_date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                updated_at = CURRENT_TIMESTAMP
            """,
            (symbol_key,),
        )

    def get_price_history_all(
        self,
        symbol: str,
        limit: Optional[int] = None,
        timeframe: Optional[str] = "1d",
    ) -> List[PricePoint]:
        """Fetch all historical OHLCV data from price_daily table"""
        self.db.connect()
        try:
            candidates = self._candidate_symbol_keys(symbol)
            symbol_key = candidates[0] if candidates else symbol.strip().upper()
            tf = (timeframe or "1d").strip().lower()
            use_weekly = tf in ("1w", "w", "week", "weekly")

            if use_weekly:
                self._ensure_price_weekly_table()

            query_daily = """
                SELECT trading_date, open, high, low, close, volume
                FROM price_daily
                WHERE symbol_key = %s
                ORDER BY trading_date ASC
            """
            query_weekly = """
                SELECT trading_date, open, high, low, close, volume
                FROM price_weekly
                WHERE symbol_key = %s
                ORDER BY trading_date ASC
            """
            query = query_weekly if use_weekly else query_daily

            if limit:
                query += f" LIMIT {int(limit)}"

            if use_weekly:
                # Build/refresh weekly aggregate for primary candidate before reading.
                self._refresh_price_weekly_for_symbol(symbol_key)

            results = self.db.execute_query(query, (symbol_key,))
            if (not results) and len(candidates) > 1:
                for cand in candidates[1:]:
                    if use_weekly:
                        self._refresh_price_weekly_for_symbol(cand)
                    rows = self.db.execute_query(query, (cand,))
                    if rows:
                        results = rows
                        symbol_key = cand
                        break
            
            history = []
            for row in results:
                trading_date, open_price, high, low, close, volume = row
                history.append(PricePoint(
                    date=trading_date.isoformat() if isinstance(trading_date, (date, datetime)) else str(trading_date),
                    open=Decimal(str(open_price)) if open_price is not None else Decimal("0"),
                    high=Decimal(str(high)) if high is not None else Decimal("0"),
                    low=Decimal(str(low)) if low is not None else Decimal("0"),
                    close=Decimal(str(close)) if close is not None else Decimal("0"),
                    volume=int(volume) if volume is not None else 0
                ))
            return history
        except Exception as e:
            print(f"[StockDetailService] get_price_history_all error: {e}")
            return []
        finally:
            self.db.disconnect()

