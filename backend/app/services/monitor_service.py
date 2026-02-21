from decimal import Decimal
from datetime import datetime
from app.models.schemas import (
    MonitorResponse, PortfolioSummary, MarketIndexData
)
from app.services.freshness_service import FreshnessService
from app.services.portfolio_service import PortfolioService
from app.core.config import settings
import psycopg
import traceback

class MonitorService:
    """Monitor screen business logic"""

    INDICES_SYMBOLS = {
        'US:^DJI': 'Dow Jones',
        'US:^GSPC': 'S&P 500',
        'US:^IXIC': 'NASDAQ',
        'US:^RUT': 'Russell 2000',
        'JP:^N225': 'Nikkei 225',
        'US:^VIX': 'VIX'
    }
    RATE_SYMBOLS = {
        'US:^TNX': 'US 10Y Yield',
        'US:^FVX': 'US 5Y Yield',
        'US:^IRX': 'US 3Y Yield'
    }
    CRYPTO_SYMBOLS = {
        'BTC-USD': 'Bitcoin',
        'ETH-USD': 'Ethereum'
    }
    FX_SYMBOLS = {
        'US:USDJPY=X': 'USD/JPY',
        'US:EURUSD=X': 'EUR/USD',
        'US:GBPUSD=X': 'GBP/USD'
    }
    METAL_SYMBOLS = {
        'US:GLD': 'Gold',
        'US:SLV': 'Silver',
        'US:CL=F': 'WTI Crude',
        'US:PL=F': 'Platinum',
        'US:HG=F': 'Copper',
        'US:PA=F': 'Palladium'
    }
    
    def __init__(self):
        self.freshness_service = FreshnessService()
    
    def _get_db_connection(self):
        """Get DB connection using settings"""
        return psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password
        )

    def get_monitor_data(self) -> MonitorResponse:
        """Get Monitor screen data"""
        portfolio = self.get_portfolio_summary()
        market_indices = self.get_market_indices()
        fx_rates = self.get_fx_rates()
        metals = self.get_metals()
        rates = self.get_rates()
        crypto = self.get_crypto()
        freshness = self._get_monitor_freshness()
        
        return MonitorResponse(
            portfolio=portfolio,
            indices=market_indices,
            fx_rates=fx_rates,
            metals=metals,
            rates=rates,
            crypto=crypto,
            freshness=freshness
        )
    
    def get_portfolio_summary(self) -> PortfolioSummary:
        """Get portfolio summary using PortfolioService (single source of truth)."""
        try:
            return PortfolioService().get_portfolio_summary()
        except Exception as e:
            print(f"Error in get_portfolio_summary: {e}")
            # Fallback to zeros on error
            return PortfolioSummary(
                total_value_jpy=Decimal('0'),
                total_cost_jpy=Decimal('0'),
                total_gain_loss_jpy=Decimal('0'),
                total_gain_loss_pct=Decimal('0'),
                cash_balance_jpy=Decimal('0'),
                equity_value_jpy=Decimal('0'),
                ytd_start_date=datetime(datetime.now().year, 1, 1)
            )

    def _get_market_data_by_symbols(self, symbol_map: dict) -> list[MarketIndexData]:
        """Generic method to fetch market data from DB based on a symbol mapping"""
        result = []
        symbols = list(symbol_map.keys())
        if not symbols:
            return []

        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            symbol_placeholders = ','.join(['%s'] * len(symbols))
            
            cursor.execute(f"""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (symbol_key)
                        symbol_key,
                        close,
                        trading_date
                    FROM price_daily
                    WHERE symbol_key IN ({symbol_placeholders})
                    ORDER BY symbol_key, trading_date DESC
                ),
                previous_prices AS (
                   SELECT 
                        symbol_key,
                        close
                    FROM (
                        SELECT 
                            symbol_key,
                            close,
                            ROW_NUMBER() OVER (PARTITION BY symbol_key ORDER BY trading_date DESC) as rn
                        FROM price_daily
                        WHERE symbol_key IN ({symbol_placeholders})
                    ) ranked
                    WHERE rn = 2
                )
                SELECT 
                    lp.symbol_key,
                    lp.close as current_close,
                    pp.close as previous_close
                FROM latest_prices lp
                LEFT JOIN previous_prices pp ON lp.symbol_key = pp.symbol_key
            """, symbols * 2)
            
            rows = cursor.fetchall()
            for row in rows:
                symbol_key = row[0]
                current_price = Decimal(str(row[1])) if row[1] is not None else Decimal('0')
                previous_price = Decimal(str(row[2])) if row[2] is not None else None
                
                if previous_price and previous_price != 0:
                    change_pct = ((current_price - previous_price) / previous_price * 100)
                else:
                    change_pct = Decimal('0')
                
                name = symbol_map.get(symbol_key, symbol_key)
                
                result.append(MarketIndexData(
                    symbol=symbol_key,
                    name=name,
                    current_price=current_price,
                    change_pct=change_pct,
                    last_updated=datetime.now()
                ))
            
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"DB Error in _get_market_data_by_symbols: {e}")
        
        return result

    def get_market_indices(self) -> list[MarketIndexData]:
        """Get market indices data"""
        return self._get_market_data_by_symbols(self.INDICES_SYMBOLS)
    
    def get_rates(self) -> list[MarketIndexData]:
        """Get bond rates"""
        return self._get_market_data_by_symbols(self.RATE_SYMBOLS)
    
    def get_crypto(self) -> list[MarketIndexData]:
        """Get crypto prices"""
        return self._get_market_data_by_symbols(self.CRYPTO_SYMBOLS)

    def get_fx_rates(self) -> list[MarketIndexData]:
        """Get FX rates"""
        return self._get_market_data_by_symbols(self.FX_SYMBOLS)
    
    def get_metals(self) -> list[MarketIndexData]:
        """Get metal prices and commodities"""
        return self._get_market_data_by_symbols(self.METAL_SYMBOLS)

    def _get_monitor_freshness(self) -> dict:
        return {
            "indices": self.freshness_service.price_freshness_for_symbols(
                scope="monitor.indices",
                symbol_keys=list(self.INDICES_SYMBOLS.keys()),
                tz_name="America/New_York",
                close_cutoff_hour_local=18,
                note="US market close 기준. US market daily snapshot may be previous US business day.",
            ),
            "rates": self.freshness_service.price_freshness_for_symbols(
                scope="monitor.rates",
                symbol_keys=list(self.RATE_SYMBOLS.keys()),
                tz_name="America/New_York",
                close_cutoff_hour_local=18,
                note="US market close 기준.",
            ),
            "fx_rates": self.freshness_service.price_freshness_for_symbols(
                scope="monitor.fx_rates",
                symbol_keys=list(self.FX_SYMBOLS.keys()),
                tz_name="America/New_York",
                close_cutoff_hour_local=18,
                note="FX daily snapshot is judged on US/Eastern business-day close.",
            ),
            "metals": self.freshness_service.price_freshness_for_symbols(
                scope="monitor.metals",
                symbol_keys=list(self.METAL_SYMBOLS.keys()),
                tz_name="America/New_York",
                close_cutoff_hour_local=18,
                note="Commodity ETFs/futures proxies judged on US/Eastern close.",
            ),
            "crypto": self.freshness_service.price_freshness_for_symbols(
                scope="monitor.crypto",
                symbol_keys=list(self.CRYPTO_SYMBOLS.keys()),
                tz_name="America/New_York",
                close_cutoff_hour_local=18,
                note="Crypto is 24/7, but this system stores daily bars; freshness is checked by daily date.",
            ),
        }
