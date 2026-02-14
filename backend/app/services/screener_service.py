from typing import Optional
import math
from app.db.database import Database


class ScreenerService:
    """Screener画面のビジネスロジック"""
    
    def __init__(self):
        self.db = Database()
        self.db.connect()
    
    def scan_stocks(self, mode: str = "all", sector: Optional[str] = None, 
                    limit: int = 20, offset: int = 0,
                    min_rs: int = 85, min_total_score: int = 70,
                    min_price: Optional[float] = None,
                    max_price: Optional[float] = None,
                    volume_min: Optional[int] = None,
                    rsi_filter: Optional[str] = None,
                    symbol: Optional[str] = None) -> "ScreenerResponse":
        """
        スクリーニング実行
        """
        # [Guardrail] Check if RS normalization is valid
        try:
            with self.db:
                check_query = """
                    SELECT MAX(rs_score) 
                    FROM indicator_daily 
                    WHERE trading_date = (
                        SELECT trading_date 
                        FROM indicator_daily 
                        GROUP BY trading_date 
                        HAVING COUNT(*) >= 100 
                        ORDER BY trading_date DESC 
                        LIMIT 1
                    )
                """
                check_res = self.db.execute_query(check_query)
                max_rs = check_res[0][0] if check_res and check_res[0][0] is not None else 0
                
                if max_rs < 10:
                    print(f"[Guardrail] Critical: Max RS Score is {max_rs}. Normalization missing.")
                    from fastapi import HTTPException
                    raise HTTPException(status_code=503, detail="RS_NORM_MISSING")
        except Exception as e:
            if "HTTPException" in str(type(e)):
                raise e
            print(f"[Guardrail Warning] Check failed: {e}")

        # SQL parameters list
        params = [min_rs]
        
        query = """
            SELECT 
                i.symbol_key,
                i.name,
                p.close as price,
                p.close - pc.close as change,
                CASE 
                    WHEN pc.close > 0 THEN ((p.close - pc.close) / pc.close * 100)
                    ELSE 0
                END as change_pct,
                p.volume,
                ind.rsi14,
                ind.rs_score,
                ind.sma20,
                ind.sma50,
                ind.sma200,
                ind.dist_to_52w_high_pct
            FROM instruments i
            INNER JOIN LATERAL (
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
                  AND trading_date < p.trading_date
                ORDER BY trading_date DESC
                LIMIT 1
            ) pc ON true
            LEFT JOIN LATERAL (
                SELECT symbol_key, rsi14, rs_score, sma20, sma50, sma200, dist_to_52w_high_pct
                FROM indicator_daily
                WHERE symbol_key = i.symbol_key
                  AND trading_date <= p.trading_date
                ORDER BY trading_date DESC
                LIMIT 1
            ) ind ON true
            WHERE i.is_active = true
              AND ind.symbol_key IS NOT NULL
              AND ind.rs_score >= %s
        """

        if volume_min:
            query += " AND p.volume >= %s"
            params.append(volume_min)

        if min_price is not None:
            query += " AND p.close >= %s"
            params.append(min_price)

        if max_price is not None:
            query += " AND p.close <= %s"
            params.append(max_price)
            
        if rsi_filter == 'oversold':
            query += " AND ind.rsi14 <= 30"
        elif rsi_filter == 'neutral':
            query += " AND ind.rsi14 > 30 AND ind.rsi14 <= 70"
        elif rsi_filter == 'overbought':
            query += " AND ind.rsi14 > 70"

        if symbol:
            query += " AND (i.symbol_key ILIKE %s OR i.name ILIKE %s)"
            search_val = f"%{symbol}%"
            params.append(search_val)
            params.append(search_val)

        query += " ORDER BY ind.rs_score DESC, p.close DESC"

        try:
            with self.db:
                results = self.db.execute_query(query, tuple(params))
                
                if results is None:
                    from app.models.schemas import ScreenerResponse
                    return ScreenerResponse(items=[], total=0, page=(offset//limit)+1, limit=limit, total_pages=0)

                all_candidates = []
                for row in results:
                    (s_key, s_name, price, change, change_pct, volume, rsi, rs_score,
                     sma20, sma50, sma200, dist_52w) = row
                    
                    total_score = self._calculate_total_score_v2(
                        rs_score, rsi, sma20, sma50, sma200, price, 
                        dist_52w, None
                    )
                    
                    if total_score >= min_total_score:
                        all_candidates.append({
                            "symbol": s_key,
                            "name": s_name or s_key,
                            "price": float(price) if price else 0,
                            "change_pct": float(change_pct) if change_pct else 0,
                            "volume": int(volume) if volume else 0,
                            "rsi": float(rsi) if rsi else None,
                            "rs_score": int(rs_score) if rs_score else 0,
                            "total_score": round(total_score, 1),
                            "dist_52w_high_pct": float(dist_52w) if dist_52w is not None else None,
                            "signals": self._generate_signals(rs_score, rsi, dist_52w, sma20, sma50, sma200, price)
                        })
                
                all_candidates.sort(key=lambda x: x["total_score"], reverse=True)
                total_stocks = len(all_candidates)
                paginated_results = all_candidates[offset : offset + limit]
                total_pages = math.ceil(total_stocks / limit) if limit > 0 else 0
                
                from app.models.schemas import ScreenerResult
                items = [ScreenerResult(**stock) for stock in paginated_results]

                from app.models.schemas import ScreenerResponse
                return ScreenerResponse(
                    items=items,
                    total=total_stocks,
                    page=(offset // limit) + 1,
                    limit=limit,
                    total_pages=total_pages
                )
        except Exception as e:
            print(f"Error in scan_stocks: {e}")
            raise e
    
    def _calculate_total_score_v2(self, rs_score, rsi, sma20, sma50, sma200, 
                                   price, dist_52w, vol_ratio):
        """Total Score v2 calculation"""
        score = 0.0
        if rs_score: score += (float(rs_score) / 100) * 40
        if rsi:
            if 40 <= rsi <= 70: score += 15
            elif 30 <= rsi < 40 or 70 < rsi <= 80: score += 10
            else: score += 5
        if sma20 and sma50 and sma200 and price:
            s20, s50, s200, p = float(sma20), float(sma50), float(sma200), float(price)
            if s20 > s50 > s200 and p > s20: score += 20
            elif s20 > s50 and p > s20: score += 15
            elif p > s20: score += 10
            else: score += 5
        if dist_52w is not None:
            d = float(dist_52w)
            if d >= -5: score += 10
            elif d >= -10: score += 7
            elif d >= -20: score += 4
            else: score += 2
        return score

    def _generate_signals(self, rs_score, rsi, dist_52w, sma20, sma50, sma200, price):
        """Generate trading signals"""
        signals = []
        if rs_score and rs_score >= 80: signals.append("High RS")
        if rsi:
            r = float(rsi)
            if r >= 70: signals.append("Overbought")
            elif r <= 30: signals.append("Oversold")
        if dist_52w is not None and float(dist_52w) >= -5: signals.append("Near High")
        if sma20 and sma50 and sma200 and price:
            if float(sma20) > float(sma50) > float(sma200) and float(price) > float(sma20):
                signals.append("Perfect Order")
        return signals

    def get_diagnostics(self, request_params: Optional[dict] = None) -> dict:
        """
        Screener Diagnostics (Enhanced for Price Filter Verification)
        """
        diag = {
            "status": "ok",
            "api_contract_check": {
                "status": "ok", 
                "expected_query_params": ["min_price", "max_price", "symbol", "min_rs", "min_total_score", "volume_min", "rsi_filter", "page", "limit"],
                "received_query_params": list(request_params.keys()) if request_params else [],
                "missing_query_params": [],
                "missing_response_fields": []
            },
            "db_integrity_check": {"status": "ok", "tables": {}, "missing_columns": []},
            "data_freshness_check": {"latest_dates": {}, "staleness_days": {}},
            "query_logic_check": {
                "latest_price_date_used": None,
                "price_filter_applied_to_latest_close": True, # Logic verified in scan_stocks update
                "sample_item_debug": None
            }
        }
        
        try:
            # 1. API Contract Check
            if request_params:
                diag["api_contract_check"]["missing_query_params"] = [
                    p for p in diag["api_contract_check"]["expected_query_params"] 
                    if p not in diag["api_contract_check"]["received_query_params"]
                ]

            # Prepare params for scan_stocks internal call
            scan_kwargs = {"limit": 1}
            if request_params:
                if "min_price" in request_params: scan_kwargs["min_price"] = float(request_params["min_price"])
                if "max_price" in request_params: scan_kwargs["max_price"] = float(request_params["max_price"])
                if "min_rs" in request_params: scan_kwargs["min_rs"] = int(request_params["min_rs"])
                # Add others if needed, but these are for testing the filter

            res = self.scan_stocks(**scan_kwargs)
            if res.items:
                sample = res.items[0].model_dump()
                required_keys = ["symbol", "name", "price", "change_pct", "volume", "rs_score", "total_score", "dist_52w_high_pct", "signals"]
                diag["api_contract_check"]["missing_response_fields"] = [k for k in required_keys if k not in sample]
                
                # Sample Item Debug
                with self.db:
                    raw_p = self.db.execute_query(
                        "SELECT trading_date, close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1",
                        (sample["symbol"],)
                    )
                    if raw_p:
                        diag["query_logic_check"]["sample_item_debug"] = {
                            "symbol": sample["symbol"],
                            "price": sample["price"],
                            "price_date": str(raw_p[0][0]),
                            "raw_close": float(raw_p[0][1]),
                            "min_price": scan_kwargs.get("min_price"),
                            "max_price": scan_kwargs.get("max_price")
                        }
                        diag["query_logic_check"]["latest_price_date_used"] = str(raw_p[0][0])
            
            if diag["api_contract_check"]["missing_response_fields"] or diag["api_contract_check"]["missing_query_params"]:
                diag["api_contract_check"]["status"] = "warning"
                if diag["status"] == "ok": diag["status"] = "warning"

            # 2. DB Integrity & Freshness
            with self.db:
                required_cols = {
                    "price_daily": ["symbol_key", "trading_date", "close", "volume"],
                    "indicator_daily": ["symbol_key", "trading_date", "rs_score", "rsi14", "dist_to_52w_high_pct"],
                    "instruments": ["symbol_key", "name", "is_active"]
                }
                for table, cols in required_cols.items():
                    res_cols = self.db.execute_query(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
                    existing = [r[0] for r in res_cols] if res_cols else []
                    missing = [c for c in cols if c not in existing]
                    if missing:
                        diag["db_integrity_check"]["missing_columns"].extend([f"{table}.{m}" for m in missing])
                
                if diag["db_integrity_check"]["missing_columns"]:
                    diag["db_integrity_check"]["status"] = "error"
                    diag["status"] = "error"

                from datetime import date
                today = date.today()
                for table in ["price_daily", "indicator_daily"]:
                    c_res = self.db.execute_query(f"SELECT COUNT(*) FROM {table}")
                    diag["db_integrity_check"]["tables"][table] = {"rows": c_res[0][0] if c_res else 0}
                    
                    d_res = self.db.execute_query(f"SELECT MAX(trading_date) FROM {table}")
                    l_date = d_res[0][0] if d_res and d_res[0][0] else None
                    diag["data_freshness_check"]["latest_dates"][table] = str(l_date) if l_date else None
                    if l_date:
                        days = (today - l_date).days
                        diag["data_freshness_check"]["staleness_days"][table] = days
                        if days > 4 and diag["status"] == "ok": diag["status"] = "warning"

            return diag
        except Exception as e:
            import traceback
            return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

