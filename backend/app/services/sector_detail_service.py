from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional
from app.models.schemas import SectorDetailResponse, SectorConstituent, SectorPerformance
import psycopg


class SectorDetailService:
    """Sector Detail画面のビジネスロジック"""
    
    def __init__(self):
        pass
    
    def _get_db_connection(self):
        """DB接続を取得"""
        return psycopg.connect(
            host="localhost",
            port=5432,
            dbname="postgres",
            user="postgres",
            password="test"
        )
    
    def get_sector_detail(self, sector_symbol: str) -> dict:
        """セクター詳細データを取得"""
        try:
            conn = self._get_db_connection()
            
            # 1. セクター概要情報
            sector_info = self._get_sector_info(conn, sector_symbol)
            
            # 2. セクターパフォーマンス
            performance = self._get_sector_performance(conn, sector_symbol)
            
            # 3. 構成銘柄ランキング
            constituents = self._get_sector_constituents(conn, sector_symbol)
            
            # 4. トップ3企業
            top_3 = [c['symbol'] for c in constituents[:3]] if constituents else []
            
            # 5. チャートデータ
            chart_data = self._get_sector_chart_data(conn, sector_symbol)
            
            conn.close()
            
            return {
                'sector_name': sector_info['name'],
                'etf_symbol': sector_symbol,
                'performance': performance,
                'top_3': top_3,
                'chart_data': chart_data,
                'constituents': constituents,
                'last_updated': datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"Error getting sector detail: {e}")
            return self._get_dummy_sector_detail(sector_symbol)
    
    def _get_sector_info(self, conn, sector_symbol: str) -> dict:
        """セクター基本情報を取得"""
        sector_names = {
            'XLK': 'Technology',
            'XLV': 'Healthcare',
            'XLF': 'Financials',
            'XLE': 'Energy',
            'XLY': 'Consumer Discretionary',
            'XLP': 'Consumer Staples',
            'XLI': 'Industrials',
            'XLB': 'Materials',
            'XLU': 'Utilities',
            'XLRE': 'Real Estate',
            'XLC': 'Communication Services',
        }
        
        return {
            'name': sector_names.get(sector_symbol, sector_symbol),
            'etf_symbol': sector_symbol
        }
    
    def _get_price_at_days_ago(self, conn, symbol_key: str, days_ago: int) -> Decimal | None:
        """N日前の価格を取得"""
        cursor = conn.cursor()
        try:
            target_date = datetime.now().date() - timedelta(days=days_ago)
            
            cursor.execute("""
                SELECT close FROM prices_daily
                WHERE symbol_key = %s 
                  AND trading_date <= %s
                ORDER BY trading_date DESC
                LIMIT 1
            """, (symbol_key, target_date))
            
            result = cursor.fetchone()
            return Decimal(str(result[0])) if result else None
        finally:
            cursor.close()
    
    def _calculate_return(self, conn, symbol_key: str, days: int) -> float:
        """指定日数のリターンを計算"""
        current_price = self._get_price_at_days_ago(conn, symbol_key, 0)
        past_price = self._get_price_at_days_ago(conn, symbol_key, days)
        
        if not current_price or not past_price or past_price == 0:
            return 0.0
        
        return float(((current_price - past_price) / past_price) * 100)
    
    def _get_sector_performance(self, conn, sector_symbol: str) -> dict:
        """セクターパフォーマンスを計算"""
        try:
            # セクターETFのリターン計算
            return_5d = self._calculate_return(conn, sector_symbol, 5)
            return_21d = self._calculate_return(conn, sector_symbol, 21)
            return_63d = self._calculate_return(conn, sector_symbol, 63)
            
            # SPYとの比較
            spy_return_21d = self._calculate_return(conn, 'SPY', 21)
            vs_benchmark = return_21d - spy_return_21d
            
            return {
                'return_5d': round(return_5d, 2),
                'return_21d': round(return_21d, 2),
                'return_63d': round(return_63d, 2),
                'vs_benchmark': round(vs_benchmark, 2)
            }
        except Exception as e:
            print(f"Error calculating performance: {e}")
            return {
                'return_5d': 0.0,
                'return_21d': 0.0,
                'return_63d': 0.0,
                'vs_benchmark': 0.0
            }
    
    def _get_sector_constituents(self, conn, sector_symbol: str, top_n: int = 20) -> list:
        """セクター構成銘柄ランキングTop Nを取得（最適化版）"""
        cursor = conn.cursor()
        try:
            # 1. 構成銘柄の基本情報を取得
            cursor.execute("""
                SELECT 
                    sc.constituent_symbol,
                    sc.weight,
                    sc.rank,
                    i.name
                FROM sector_constituents sc
                LEFT JOIN instruments i ON sc.constituent_symbol = i.symbol_key
                WHERE sc.sector_etf_symbol = %s
                ORDER BY sc.rank
                LIMIT %s
            """, (sector_symbol, top_n))
            
            rows = cursor.fetchall()
            if not rows:
                return []
            
            # 2. シンボルリストを作成
            symbols = [row[0] for row in rows]
            
            # 3. 直近の出来高データを一括取得
            symbol_placeholders = ','.join(['%s'] * len(symbols))
            cursor.execute(f"""
                WITH latest_volume AS (
                    SELECT DISTINCT ON (symbol_key) 
                        symbol_key, 
                        volume,
                        trading_date
                    FROM price_daily
                    WHERE symbol_key IN ({symbol_placeholders})
                    ORDER BY symbol_key, trading_date DESC
                ),
                avg_volume AS (
                    SELECT 
                        symbol_key,
                        AVG(volume) as avg_vol
                    FROM price_daily
                    WHERE symbol_key IN ({symbol_placeholders})
                      AND trading_date >= CURRENT_DATE - INTERVAL '20 days'
                    GROUP BY symbol_key
                )
                SELECT 
                    lv.symbol_key,
                    lv.volume,
                    av.avg_vol
                FROM latest_volume lv
                LEFT JOIN avg_volume av ON lv.symbol_key = av.symbol_key
            """, symbols + symbols)
            
            volume_data = {row[0]: {
                'current': float(row[1]) if row[1] else 0,
                'avg': float(row[2]) if row[2] else 0
            } for row in cursor.fetchall()}
            
            # 4. SPYの21日リターンを1回だけ取得
            spy_return = self._calculate_return(conn, 'SPY', 21)
            
            # 5. 各銘柄の21日リターンを一括取得（最適化版）
            stock_returns = {}
            if symbols:
                # 全銘柄の現在価格と21日前価格を一括取得
                cursor.execute(f"""
                    WITH current_prices AS (
                        SELECT DISTINCT ON (symbol_key)
                            symbol_key,
                            close as price_current
                        FROM price_daily
                        WHERE symbol_key IN ({symbol_placeholders})
                        ORDER BY symbol_key, trading_date DESC
                    ),
                    past_prices AS (
                        SELECT DISTINCT ON (symbol_key)
                            symbol_key,
                            close as price_past
                        FROM price_daily
                        WHERE symbol_key IN ({symbol_placeholders})
                          AND trading_date <= CURRENT_DATE - INTERVAL '21 days'
                        ORDER BY symbol_key, trading_date DESC
                    )
                    SELECT 
                        cp.symbol_key,
                        cp.price_current,
                        pp.price_past
                    FROM current_prices cp
                    LEFT JOIN past_prices pp ON cp.symbol_key = pp.symbol_key
                """, symbols * 2)
                
                for row in cursor.fetchall():
                    symbol = row[0]
                    current = Decimal(str(row[1])) if row[1] else None
                    past = Decimal(str(row[2])) if row[2] else None
                    
                    if current and past and past != 0:
                        ret = float(((current - past) / past) * 100)
                        stock_returns[symbol] = ret
                    else:
                        stock_returns[symbol] = 0
            
            # 6. 結果を構築
            constituents = []
            for row in rows:
                symbol = row[0]
                weight = float(row[1]) if row[1] else 0.0
                rank = row[2]
                name = row[3] or symbol
                
                # 出来高比を計算
                vol_data = volume_data.get(symbol, {'current': 0, 'avg': 0})
                if vol_data['avg'] > 0:
                    volume_ratio = round(vol_data['current'] / vol_data['avg'], 1)
                else:
                    volume_ratio = 1.0
                
                # RSスコアを計算
                stock_return = stock_returns.get(symbol, 0)
                if spy_return != 0:
                    rs_raw = (stock_return / spy_return) * 50 + 50
                    rs_score = max(0, min(100, int(rs_raw)))
                else:
                    rs_score = 50
                
                constituents.append({
                    'rank': rank,
                    'symbol': symbol,
                    'name': name,
                    'weight': weight,
                    'market_cap': 0,  # 将来実装
                    'rs_score': rs_score,
                    'volume_ratio': volume_ratio,
                    'institution_flag': False
                })
            
            return constituents
            
        finally:
            cursor.close()
    
    def _get_sector_chart_data(self, conn, sector_symbol: str, days: int = 90) -> list:
        """セクターチャートデータを取得"""
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT trading_date, open, high, low, close
                FROM price_daily
                WHERE symbol_key = %s
                  AND trading_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY trading_date
            """, (sector_symbol, days))
            
            rows = cursor.fetchall()
            return [
                {
                    'date': row[0].isoformat(),
                    'open': float(row[1]),
                    'high': float(row[2]),
                    'low': float(row[3]),
                    'close': float(row[4])
                }
                for row in rows
            ]
        finally:
            cursor.close()
    
    def _get_dummy_sector_detail(self, sector_symbol: str) -> dict:
        """ダミーデータ（フォールバック）"""
        return {
            'sector_name': 'Technology',
            'etf_symbol': sector_symbol,
            'performance': {
                'return_5d': 2.3,
                'return_21d': 8.5,
                'return_63d': 15.2,
                'vs_benchmark': 3.2
            },
            'top_3': ['AAPL', 'MSFT', 'NVDA'],
            'chart_data': [],
            'constituents': [],
            'last_updated': datetime.now().isoformat()
        }
