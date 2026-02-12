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
                    min_rs: int = 85, min_total_score: int = 70) -> "ScreenerResponse":
        """
        スクリーニング実行
        RS Rating >= min_rs AND Total Score >= min_total_score のフィルター適用
        """
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
            ORDER BY ind.rs_score DESC, p.close DESC
        """
        # Remove LIMIT/OFFSET from SQL to handle filtering in Python
        # query += " LIMIT %s OFFSET %s"
        
        try:
            with self.db:
                # Execute query without limit/offset to get all candidates
                results = self.db.execute_query(query, (min_rs,))
                
                if results is None:
                    print("Query execution returned None")
                    from app.models.schemas import ScreenerResponse
                    return ScreenerResponse(results=[], total_count=0, filter_applied={})

                all_candidates = []
                for row in results:
                    (symbol, name, price, change, change_pct, volume, rsi, rs_score,
                     sma20, sma50, sma200, dist_52w) = row
                    
                    # TODO: volume_ratio_50d is missing from DB, using None for now
                    vol_ratio = None
                    
                    # Total Score v2 calculation
                    total_score = self._calculate_total_score_v2(
                        rs_score, rsi, sma20, sma50, sma200, price, 
                        dist_52w, vol_ratio
                    )
                    
                    # Filter by Total Score
                    if total_score >= min_total_score:
                        all_candidates.append({
                            "rank": 0, # Will be assigned later
                            "symbol": symbol,
                            "name": name or symbol,
                            "price": float(price) if price else 0,
                            "change_pct": float(change_pct) if change_pct else 0,
                            "volume": int(volume) if volume else 0,
                            "rsi": float(rsi) if rsi else None,
                            "rs_score": int(rs_score) if rs_score else 0,
                            "total_score": round(total_score, 1),
                        # Prevent NaN from being sent to frontend
                        "dist_52w_high_pct": (
                            float(dist_52w) 
                            if dist_52w is not None and not (isinstance(dist_52w, float) and math.isnan(dist_52w))
                            else None
                        ),
                        "signals": self._generate_signals(rs_score, rsi, dist_52w, sma20, sma50, sma200, price)
                    })
                
                # Sort by Total Score DESC
                all_candidates.sort(key=lambda x: x["total_score"], reverse=True)
                
                # Apply pagination
                total_stocks = len(all_candidates)
                paginated_results = all_candidates[offset : offset + limit]
                
                # Assign rank based on current page
                for i, stock in enumerate(paginated_results):
                    stock["rank"] = offset + i + 1
                
                from app.models.schemas import ScreenerResponse, ScreenerResult
                
                # Convert to Pydantic models
                screener_results = []
                for stock in paginated_results:
                    screener_results.append(ScreenerResult(
                        symbol=stock["symbol"],
                        name=stock["name"],
                        price=stock["price"],
                        change_pct=stock["change_pct"],
                        volume=stock["volume"],
                        market_cap=None, # Not calculated yet
                        rsi=stock["rsi"],
                        total_score=stock["total_score"],
                        ma_cross=None # Not implemented yet
                    ))

                return ScreenerResponse(
                    results=screener_results,
                    total_count=total_stocks,
                    filter_applied={"mode": mode, "sector": sector}
                )
        except Exception as e:
            import traceback
            error_msg = traceback.format_exc()
            with open("backend_error.log", "w") as f:
                f.write(error_msg)
            print(f"Error in scan_stocks: {e}")
            raise e
    
    def _calculate_total_score_v2(self, rs_score, rsi, sma20, sma50, sma200, 
                                   price, dist_52w, vol_ratio):
        """Total Score v2 calculation"""
        score = 0.0
        
        # RS Rating (40 points)
        if rs_score:
            score += (float(rs_score) / 100) * 40
        
        # RSI (15 points)
        if rsi:
            if 40 <= rsi <= 70:
                score += 15
            elif 30 <= rsi < 40 or 70 < rsi <= 80:
                score += 10
            else:
                score += 5
        
        # Moving Average Trend (20 points)
        if sma20 and sma50 and sma200 and price:
            sma20_f = float(sma20)
            sma50_f = float(sma50)
            sma200_f = float(sma200)
            price_f = float(price)
            
            if sma20_f > sma50_f > sma200_f and price_f > sma20_f:
                score += 20  # Perfect order
            elif sma20_f > sma50_f and price_f > sma20_f:
                score += 15
            elif price_f > sma20_f:
                score += 10
            else:
                score += 5
        
        # Distance to 52W High (10 points)
        if dist_52w is not None:
            dist = float(dist_52w)
            if dist >= -5:
                score += 10
            elif dist >= -10:
                score += 7
            elif dist >= -20:
                score += 4
            else:
                score += 2
                
        return score

    def _generate_signals(self, rs_score, rsi, dist_52w, sma20, sma50, sma200, price):
        """Generate trading signals based on indicators"""
        signals = []
        
        # High RS
        if rs_score and rs_score >= 80:
            signals.append("High RS")
            
        # RSI
        if rsi:
            rsi_val = float(rsi)
            if rsi_val >= 70:
                signals.append("Overbought")
            elif rsi_val <= 30:
                signals.append("Oversold")
                
        # Near 52W High
        if dist_52w is not None and float(dist_52w) >= -5:
            signals.append("Near High")
            
        # Perfect Order
        if sma20 and sma50 and sma200 and price:
            if float(sma20) > float(sma50) > float(sma200) and float(price) > float(sma20):
                signals.append("Perfect Order")
                
        return signals        

