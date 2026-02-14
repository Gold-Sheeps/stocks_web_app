import psycopg
from decimal import Decimal
from datetime import datetime, date
from typing import List, Optional
from app.models.schemas import (
    MonitorResponse, PortfolioSummary, MarketIndexData, WatchlistItem
)
from app.core.config import settings


class MonitorService:
    """Monitor screen business logic using raw SQL (psycopg)"""
    
    def __init__(self):
        # Database connection details from settings or ENV
        self.conn_info = f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} user={settings.postgres_user} password={settings.postgres_password}"

    def get_dashboard_data(self) -> MonitorResponse:
        """
        Main method to gather all dashboard data:
        1. Market Indices
        2. Watchlist Items
        3. Alerts (Surge/Plunge)
        4. Portfolio Summary (Placeholder)
        """
        try:
            indices = self.get_market_indices()
            watchlist_all = self.get_watchlist_with_metrics()
            
            # Filter alerts from watchlist (±3% change)
            alerts = []
            for item in watchlist_all:
                if item.change_pct is not None:
                    if item.change_pct >= 3:
                        item.alert_type = "SURGE"
                        alerts.append(item)
                    elif item.change_pct <= -3:
                        item.alert_type = "PLUNGE"
                        alerts.append(item)
            
            portfolio = self.get_portfolio_summary()
            fx_rates = self.get_fx_rates()
            metals = self.get_metals()
            
            return MonitorResponse(
                indices=indices,
                watchlist=watchlist_all,
                alerts=alerts,
                portfolio=portfolio,
                fx_rates=fx_rates,
                metals=metals
            )
        except Exception as e:
            print(f"[MonitorService] Error in get_dashboard_data: {e}")
            # Robust fallback: return empty lists instead of 500
            return MonitorResponse(indices=[], watchlist=[], alerts=[])

    def get_market_indices(self) -> List[MarketIndexData]:
        """Get market indices data (Indices only)"""
        indices_map = {
            'US:^DJI': 'Dow Jones',
            'US:^GSPC': 'S&P 500',
            'US:^IXIC': 'NASDAQ',
            'US:^RUT': 'Russell 2000',
            'JP:^N225': 'Nikkei 225',
            'US:^VIX': 'VIX'
        }
        return self._get_price_data_bulk(indices_map)

    def get_fx_rates(self) -> List[MarketIndexData]:
        """Get FX rates data"""
        fx_map = {
            'US:USDJPY=X': 'USD/JPY',
            'US:EURUSD=X': 'EUR/USD',
            'US:GBPUSD=X': 'GBP/USD'
        }
        return self._get_price_data_bulk(fx_map)

    def get_metals(self) -> List[MarketIndexData]:
        """Get metal prices data"""
        metal_map = {
            'US:GLD': 'Gold',
            'US:SLV': 'Silver'
        }
        return self._get_price_data_bulk(metal_map)

    def get_watchlist_with_metrics(self) -> List[WatchlistItem]:
        """
        Fetch watchlist items JOINed with latest price and indicators.
        Uses raw SQL for performance and flexible JOINs.
        """
        results = []
        try:
            with psycopg.connect(self.conn_info) as conn:
                with conn.cursor() as cur:
                    query = """
                        WITH latest_date AS (
                            SELECT MAX(trading_date) as max_date FROM price_daily
                        ),
                        prev_date AS (
                            SELECT MAX(trading_date) as prev_date FROM price_daily 
                            WHERE trading_date < (SELECT max_date FROM latest_date)
                        ),
                        metrics AS (
                            SELECT 
                                p.symbol_key,
                                p.close as current_price,
                                p_prev.close as prev_price,
                                p.updated_at,
                                ind.rsi14 as rsi,
                                NULL as volume_ratio,
                                inst.name as instrument_name,
                                inst.market
                            FROM price_daily p
                            JOIN latest_date ld ON p.trading_date = ld.max_date
                            LEFT JOIN price_daily p_prev ON p.symbol_key = p_prev.symbol_key 
                                AND p_prev.trading_date = (SELECT prev_date FROM prev_date)
                            LEFT JOIN indicator_daily ind ON p.symbol_key = ind.symbol_key 
                                AND ind.trading_date = ld.max_date
                            LEFT JOIN instruments inst ON p.symbol_key = inst.symbol_key
                        )
                        SELECT 
                            w.id,
                            w.symbol,
                            COALESCE(m.instrument_name, w.symbol) as name,
                            m.market,
                            m.current_price,
                            m.prev_price,
                            m.rsi,
                            m.volume_ratio,
                            m.updated_at
                        FROM watchlist w
                        LEFT JOIN metrics m ON w.symbol = m.symbol_key
                        ORDER BY w.id ASC
                    """
                    cur.execute(query)
                    rows = cur.fetchall()
                    
                    for row in rows:
                        curr = Decimal(str(row[4])) if row[4] else None
                        prev = Decimal(str(row[5])) if row[5] else None
                        change_pct = None
                        if curr and prev and prev != 0:
                            change_pct = (curr - prev) / prev * 100
                            
                        results.append(WatchlistItem(
                            id=row[0],
                            symbol=row[1],
                            name=row[2],
                            market=row[3],
                            current_price=curr,
                            change_pct=change_pct,
                            rsi=Decimal(str(row[6])) if row[6] else None,
                            volume_ratio=Decimal(str(row[7])) if row[7] else None,
                            last_updated=row[8]
                        ))
        except Exception as e:
            print(f"[MonitorService] Watchlist fetch error: {e}")
            
        return results

    def _get_price_data_bulk(self, symbol_map: dict) -> List[MarketIndexData]:
        """Helper to fetch current and previous price for a set of symbols"""
        results = []
        symbols = list(symbol_map.keys())
        if not symbols: return []
        
        try:
            with psycopg.connect(self.conn_info) as conn:
                with conn.cursor() as cur:
                    symbol_placeholders = ','.join(['%s'] * len(symbols))
                    query = f"""
                        WITH latest_rows AS (
                            SELECT symbol_key, close, trading_date,
                            ROW_NUMBER() OVER (PARTITION BY symbol_key ORDER BY trading_date DESC) as rn
                            FROM price_daily
                            WHERE symbol_key IN ({symbol_placeholders})
                        )
                        SELECT 
                            symbol_key,
                            MAX(CASE WHEN rn = 1 THEN close END) as current_close,
                            MAX(CASE WHEN rn = 2 THEN close END) as prev_close
                        FROM latest_rows
                        WHERE rn <= 2
                        GROUP BY symbol_key
                    """
                    cur.execute(query, symbols)
                    rows = cur.fetchall()
                    
                    for row in rows:
                        s_key = row[0]
                        curr = Decimal(str(row[1])) if row[1] else Decimal('0')
                        prev = Decimal(str(row[2])) if row[2] else None
                        
                        change_pct = Decimal('0')
                        if curr and prev and prev != 0:
                            change_pct = (curr - prev) / prev * 100
                            
                        results.append(MarketIndexData(
                            symbol=s_key,
                            name=symbol_map.get(s_key, s_key),
                            current_price=curr,
                            change_pct=change_pct,
                            last_updated=datetime.now()
                        ))
        except Exception as e:
            print(f"[MonitorService] Bulk price error: {e}")
            
        return results

    def get_portfolio_summary(self) -> PortfolioSummary:
        """Placeholder for portfolio summary"""
        return PortfolioSummary(
            total_value_jpy=Decimal('0'),
            total_cost_jpy=Decimal('0'),
            total_gain_loss_jpy=Decimal('0'),
            total_gain_loss_pct=Decimal('0'),
            ytd_start_date=date(datetime.now().year, 1, 1)
        )

    def get_monitor_data(self) -> MonitorResponse:
        """Legacy compatibility - redirects to get_dashboard_data"""
        return self.get_dashboard_data()
