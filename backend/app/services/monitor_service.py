from decimal import Decimal
from datetime import datetime
from app.models.schemas import (
    MonitorResponse, PortfolioSummary, MarketIndexData
)
from app.core.config import settings
import psycopg
import traceback

class MonitorService:
    """Monitor screen business logic"""
    
    def __init__(self):
        pass
    
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
        
        return MonitorResponse(
            portfolio=portfolio,
            indices=market_indices,
            fx_rates=fx_rates,
            metals=metals,
            rates=rates,
            crypto=crypto
        )
    
    def get_portfolio_summary(self) -> PortfolioSummary:
        """Get portfolio summary based on holdings and latest prices"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # 1. Get current USD/JPY rate
            cursor.execute("""
                SELECT close FROM price_daily 
                WHERE symbol_key = 'US:USDJPY=X' 
                ORDER BY trading_date DESC LIMIT 1
            """)
            row = cursor.fetchone()
            usd_jpy = Decimal(str(row[0])) if row and row[0] else Decimal('150.0')

            # 2. Get holdings with latest prices
            # Join with price_daily to get the most recent price for each holding
            # Note: handle US: and JP: prefixes when joining
            cursor.execute("""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (symbol_key) symbol_key, close
                    FROM price_daily
                    ORDER BY symbol_key, trading_date DESC
                )
                SELECT 
                    h.symbol, h.quantity, h.average_cost, h.currency, h.market,
                    lp.close as current_price
                FROM holdings h
                LEFT JOIN latest_prices lp ON (
                    lp.symbol_key = h.symbol OR 
                    lp.symbol_key = 'US:' || h.symbol OR 
                    lp.symbol_key = 'JP:' || h.symbol
                )
            """)
            
            holdings = cursor.fetchall()
            
            total_value_jpy = Decimal('0')
            total_cost_jpy = Decimal('0')
            
            for h in holdings:
                symbol, quantity, avg_cost, currency, market, current_price = h
                
                qty = Decimal(str(quantity)) if quantity else Decimal('0')
                cost = Decimal(str(avg_cost)) if avg_cost else Decimal('0')
                price = Decimal(str(current_price)) if current_price else cost # Fallback to cost if no price
                
                # Conversion to JPY
                if currency == 'USD' or market == 'US':
                    item_value_jpy = qty * price * usd_jpy
                    item_cost_jpy = qty * cost * usd_jpy
                else:
                    item_value_jpy = qty * price
                    item_cost_jpy = qty * cost
                
                total_value_jpy += item_value_jpy
                total_cost_jpy += item_cost_jpy
                
            total_gain_loss_jpy = total_value_jpy - total_cost_jpy
            total_gain_loss_pct = (total_gain_loss_jpy / total_cost_jpy * 100) if total_cost_jpy != 0 else Decimal('0')
            
            cursor.close()
            conn.close()
            
            return PortfolioSummary(
                total_value_jpy=total_value_jpy,
                total_cost_jpy=total_cost_jpy,
                total_gain_loss_jpy=total_gain_loss_jpy,
                total_gain_loss_pct=total_gain_loss_pct,
                ytd_start_date=datetime(datetime.now().year, 1, 1)
            )
            
        except Exception as e:
            print(f"Error in get_portfolio_summary: {e}")
            # Fallback to zeros on error
            return PortfolioSummary(
                total_value_jpy=Decimal('0'),
                total_cost_jpy=Decimal('0'),
                total_gain_loss_jpy=Decimal('0'),
                total_gain_loss_pct=Decimal('0'),
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
        indices_symbols = {
            'US:^DJI': 'Dow Jones',
            'US:^GSPC': 'S&P 500',
            'US:^IXIC': 'NASDAQ',
            'US:^RUT': 'Russell 2000',
            'JP:^N225': 'Nikkei 225',
            'US:^VIX': 'VIX'
        }
        return self._get_market_data_by_symbols(indices_symbols)
    
    def get_rates(self) -> list[MarketIndexData]:
        """Get bond rates"""
        rate_symbols = {
            'US:^TNX': 'US 10Y Yield',
            'US:^FVX': 'US 5Y Yield',
            'US:^IRX': 'US 3Y Yield'
        }
        return self._get_market_data_by_symbols(rate_symbols)
    
    def get_crypto(self) -> list[MarketIndexData]:
        """Get crypto prices"""
        crypto_symbols = {
            'BTC-USD': 'Bitcoin',
            'ETH-USD': 'Ethereum'
        }
        return self._get_market_data_by_symbols(crypto_symbols)

    def get_fx_rates(self) -> list[MarketIndexData]:
        """Get FX rates"""
        fx_symbols = {
            'US:USDJPY=X': 'USD/JPY',
            'US:EURUSD=X': 'EUR/USD',
            'US:GBPUSD=X': 'GBP/USD'
        }
        return self._get_market_data_by_symbols(fx_symbols)
    
    def get_metals(self) -> list[MarketIndexData]:
        """Get metal prices and commodities"""
        metal_symbols = {
            'US:GLD': 'Gold',
            'US:SLV': 'Silver',
            'US:CL=F': 'WTI Crude',
            'US:PL=F': 'Platinum',
            'US:HG=F': 'Copper',
            'US:PA=F': 'Palladium'
        }
        return self._get_market_data_by_symbols(metal_symbols)
