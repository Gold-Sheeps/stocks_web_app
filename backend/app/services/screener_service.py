from typing import Optional
import math
from app.db.database import Database
from app.services.freshness_service import FreshnessService


class ScreenerService:
    """Business logic for screener endpoints."""

    def __init__(self):
        self.db = Database()
        self.db.connect()
        self.freshness_service = FreshnessService()

    def _get_screener_freshness(self):
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

    def _build_filter_clause(
        self,
        min_rs: Optional[int],
        min_price: Optional[float],
        max_price: Optional[float],
        volume_min: Optional[int],
        rsi_filter: Optional[str],
        symbol: Optional[str],
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
            conditions.append("(i.symbol_key ILIKE %s OR i.name ILIKE %s)")
            search_val = f"%{symbol}%"
            params.extend([search_val, search_val])

        where_sql = ""
        if conditions:
            where_sql = " AND " + " AND ".join(conditions)

        return where_sql, params

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

    def scan_stocks(
        self,
        mode: str = "all",
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
        )

        cte = f"""
            WITH base AS (
                SELECT
                    i.symbol_key,
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
                    SELECT symbol_key, rsi14, sma20, sma50, sma200, dist_to_52w_high_pct, pivot
                    FROM indicator_daily
                    WHERE symbol_key = i.symbol_key
                      AND trading_date <= p.trading_date
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind ON true
                LEFT JOIN LATERAL (
                    SELECT pivot
                    FROM indicator_daily
                    WHERE symbol_key = i.symbol_key
                      AND trading_date <= p.trading_date
                      AND pivot IS NOT NULL
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind_pivot ON true
                LEFT JOIN LATERAL (
                    SELECT rs_rating
                    FROM indicator_daily
                    WHERE symbol_key = i.symbol_key
                      AND trading_date <= p.trading_date
                      AND rs_rating IS NOT NULL
                    ORDER BY trading_date DESC
                    LIMIT 1
                ) ind_rs ON true
                LEFT JOIN rs_ratings rr
                  ON rr.symbol_key = i.symbol_key
                WHERE i.is_active = true
                  AND ind.symbol_key IS NOT NULL
                  {where_sql}
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
                count_res = self.db.execute_query(
                    count_query,
                    tuple(where_params + [effective_min_total_score]),
                )
                total_stocks = int(count_res[0][0]) if count_res and count_res[0] else 0

                results = self.db.execute_query(
                    page_query,
                    tuple(where_params + [effective_min_total_score, limit, offset]),
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
                        freshness=self._get_screener_freshness(),
                    )

                from app.models.schemas import ScreenerResult, ScreenerResponse

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

                total_pages = math.ceil(total_stocks / limit) if limit > 0 else 0
                return ScreenerResponse(
                    items=items,
                    total=total_stocks,
                    page=(offset // limit) + 1 if limit > 0 else 1,
                    limit=limit,
                    total_pages=total_pages,
                    freshness=self._get_screener_freshness(),
                )
        except Exception as e:
            print(f"Error in scan_stocks: {e}")
            raise e

    def get_diagnostics(self, request_params: Optional[dict] = None) -> dict:
        diag = {
            "status": "ok",
            "api_contract_check": {
                "status": "ok",
                "expected_query_params": [
                    "min_price",
                    "max_price",
                    "symbol",
                    "min_rs",
                    "min_total_score",
                    "volume_min",
                    "rsi_filter",
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
