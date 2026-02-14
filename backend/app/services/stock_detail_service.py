from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
from app.db.database import Database
from app.models.schemas import StockInfo, Indicators, PricePoint, StockDetailResponse

class StockDetailService:
    def __init__(self):
        self.db = Database()
        self.db.connect()

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
            symbol_key = raw_symbol
            if ':' not in raw_symbol:
                if raw_symbol.isdigit() and len(raw_symbol) == 4:
                     symbol_key = f"JP:{raw_symbol}"
                else:
                     symbol_key = f"US:{raw_symbol}"

            # 2. Get Basic Info & Latest Price (Snapshot)
            query_info = """
                SELECT 
                    i.symbol_key, i.name, i.market, 
                    p.close, p.volume, p.trading_date,
                    p.open, p.high, p.low
                FROM instruments i
                LEFT JOIN price_daily p ON i.symbol_key = p.symbol_key
                WHERE i.symbol_key = %s
                ORDER BY p.trading_date DESC
                LIMIT 1
            """
            info_result = self.db.execute_query(query_info, (symbol_key,))

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
                
                # Previous Close for Change Pct
                prev_close = None
                if price_valid and row[5]:
                    query_prev = """
                        SELECT close FROM price_daily 
                        WHERE symbol_key = %s AND trading_date < %s 
                        ORDER BY trading_date DESC LIMIT 1
                    """
                    prev_res = self.db.execute_query(query_prev, (symbol_key, row[5]))
                    if prev_res and prev_res[0][0] and prev_res[0][0] > 0:
                        prev_close = prev_res[0][0]

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

            # 3. Get Latest Indicators
            query_ind = """
                SELECT 
                    sma5, sma20, sma50, sma200, 
                    rsi14, macd, macd_signal, macd_hist, 
                    atr14, high_52w, dist_to_52w_high_pct,
                    ema21, pivot, rs_score, volume_ratio, trading_date
                FROM indicator_daily
                WHERE symbol_key = %s
                ORDER BY trading_date DESC
                LIMIT 1
            """
            ind_res = self.db.execute_query(query_ind, (symbol_key,))
            
            indicators = Indicators()
            latest_ind_date = None
            ind_missing_count = 0
            
            # Helper
            def v(val): return val if val is not None and val != 0 else None

            if ind_res:
                r = ind_res[0]
                latest_ind_date = datetime.combine(r[15], datetime.min.time()) if r[15] else None
                
                indicators = Indicators(
                    ma5=v(r[0]), ma20=v(r[1]), ma50=v(r[2]), sma200=v(r[3]),
                    rsi=v(r[4]), macd=v(r[5]), signal=v(r[6]), macd_hist=v(r[7]),
                    atr14=v(r[8]), high_52w=v(r[9]), dist_52w_high_pct=v(r[10]),
                    ema21=v(r[11]), pivot=v(r[12]), rs_rating=int(r[13]) if r[13] is not None and r[13] > 0 else None,
                    volume_ratio=v(r[14])
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

    def get_price_history_all(self, symbol: str, limit: Optional[int] = None) -> List[PricePoint]:
        """Fetch all historical OHLCV data from price_daily table"""
        self.db.connect()
        try:
            # Normalize Symbol
            raw_symbol = symbol.strip().upper()
            symbol_key = raw_symbol
            if ':' not in raw_symbol:
                if raw_symbol.isdigit() and len(raw_symbol) == 4:
                     symbol_key = f"JP:{raw_symbol}"
                else:
                     symbol_key = f"US:{raw_symbol}"

            query = """
                SELECT trading_date, open, high, low, close, volume
                FROM price_daily
                WHERE symbol_key = %s
                ORDER BY trading_date ASC
            """
            
            if limit:
                query += f" LIMIT {int(limit)}"

            results = self.db.execute_query(query, (symbol_key,))
            
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

