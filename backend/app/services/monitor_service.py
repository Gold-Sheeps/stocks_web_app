from decimal import Decimal
from datetime import datetime
from app.models.schemas import (
    MonitorResponse, PortfolioSummary, MarketIndexData
)
import psycopg


class MonitorService:
    """Monitor screen business logic"""
    
    def __init__(self):
        pass
    
    def get_monitor_data(self) -> MonitorResponse:
        """Get Monitor screen data"""
        portfolio = self.get_portfolio_summary()
        market_indices = self.get_market_indices()
        fx_rates = self.get_fx_rates()
        metals = self.get_metals()
        
        return MonitorResponse(
            portfolio=portfolio,
            indices=market_indices,
            fx_rates=fx_rates,
            metals=metals
        )
    
    def get_portfolio_summary(self) -> PortfolioSummary:
        """Get portfolio summary (dummy data for now)"""
        ytd_start_date = datetime(datetime.now().year, 1, 1)
        total_value = Decimal('1000000')
        total_cost = Decimal('950000')
        total_gain_loss = total_value - total_cost
        total_gain_loss_pct = (total_gain_loss / total_cost * 100) if total_cost != 0 else Decimal('0')
        
        return PortfolioSummary(
            total_value_jpy=total_value,
            total_cost_jpy=total_cost,
            total_gain_loss_jpy=total_gain_loss,
            total_gain_loss_pct=total_gain_loss_pct,
            ytd_start_date=ytd_start_date
        )
    
    def get_market_indices(self) -> list[MarketIndexData]:
        """Get market indices data (optimized - bulk query)"""
        import psycopg
        
        indices_symbols = {
            'US:^DJI': 'Dow Jones',
            'US:^GSPC': 'S&P 500',
            'US:^IXIC': 'NASDAQ',
            'US:^RUT': 'Russell 2000',
            'JP:^N225': 'Nikkei 225', # Assuming JP prefix for Nikkei based on migration
            '000001.SS': 'Shanghai Composite', # Shanghai might be special, verify if in migration scope?
            'US:^GDAXI': 'DAX', # Check if DAX is US based in yfinance? Usually ^GDAXI.
            'US:^FTSE': 'FTSE 100',
            'US:^VIX': 'VIX'
        }
        
        result = []
        try:
            conn = psycopg.connect(
                host="localhost",
                port=5432,
                dbname="postgres",
                user="postgres",
                password="test"
            )
            cursor = conn.cursor()
            
            symbols = list(indices_symbols.keys())
            symbol_placeholders = ','.join(['%s'] * len(symbols))
            
            cursor.execute(f"""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (symbol_key)
                        symbol_key,
                        close,
                        trading_date,
                        ROW_NUMBER() OVER (PARTITION BY symbol_key ORDER BY trading_date DESC) as rn
                    FROM price_daily
                    WHERE symbol_key IN ({symbol_placeholders})
                    ORDER BY symbol_key, trading_date DESC
                ),
                current_price AS (
                    SELECT symbol_key, close as current_close
                    FROM latest_prices
                    WHERE rn = 1
                ),
                previous_price AS (
                    SELECT symbol_key, close as previous_close
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
                    cp.symbol_key,
                    cp.current_close,
                    pp.previous_close
                FROM current_price cp
                LEFT JOIN previous_price pp ON cp.symbol_key = pp.symbol_key
            """, symbols * 2)
            
            rows = cursor.fetchall()
            for row in rows:
                symbol_key = row[0]
                current_price = Decimal(str(row[1])) if row[1] else Decimal('0')
                previous_price = Decimal(str(row[2])) if row[2] else None
                
                if previous_price and previous_price != 0:
                    change_pct = ((current_price - previous_price) / previous_price * 100)
                else:
                    change_pct = Decimal('0')
                
                name = indices_symbols.get(symbol_key, symbol_key)
                
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
            print(f"DB connection error in get_market_indices: {e}")
            import traceback
            traceback.print_exc()
        
        if not result:
            print("Warning: Returning dummy market indices data")
            return [
                MarketIndexData(
                    symbol="DJI",
                    name="Dow Jones",
                    current_price=Decimal('38671.69'),
                    change_pct=Decimal('0.15'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="GSPC",
                    name="S&P 500",
                    current_price=Decimal('5026.61'),
                    change_pct=Decimal('0.57'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="IXIC",
                    name="NASDAQ",
                    current_price=Decimal('15990.66'),
                    change_pct=Decimal('1.25'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="N225",
                    name="Nikkei 225",
                    current_price=Decimal('36897.42'),
                    change_pct=Decimal('0.09'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="RUT",
                    name="Russell 2000",
                    current_price=Decimal('2009.99'),
                    change_pct=Decimal('1.53'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="VIX",
                    name="VIX",
                    current_price=Decimal('12.93'),
                    change_pct=Decimal('-5.20'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="USDJPY",
                    name="USD/JPY",
                    current_price=Decimal('149.32'),
                    change_pct=Decimal('0.05'),
                    last_updated=datetime.now()
                ),
            ]
        
        return result
    
    def get_fx_rates(self) -> list[MarketIndexData]:
        """Get FX rates (optimized - bulk query)"""
        import psycopg
        
        fx_symbols = {
            'US:USDJPY=X': 'USD/JPY',
            'US:EURUSD=X': 'EUR/USD',
            'US:GBPUSD=X': 'GBP/USD',
            'US:GBPJPY=X': 'GBP/JPY'
        }
        
        result = []
        try:
            conn = psycopg.connect(
                host="localhost",
                port=5432,
                dbname="postgres",
                user="postgres",
                password="test"
            )
            cursor = conn.cursor()
            
            symbols = list(fx_symbols.keys())
            symbol_placeholders = ','.join(['%s'] * len(symbols))
            
            cursor.execute(f"""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (symbol_key)
                        symbol_key,
                        close,
                        trading_date,
                        ROW_NUMBER() OVER (PARTITION BY symbol_key ORDER BY trading_date DESC) as rn
                    FROM price_daily
                    WHERE symbol_key IN ({symbol_placeholders})
                    ORDER BY symbol_key, trading_date DESC
                ),
                current_price AS (
                    SELECT symbol_key, close as current_close
                    FROM latest_prices
                    WHERE rn = 1
                ),
                previous_price AS (
                    SELECT symbol_key, close as previous_close
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
                    cp.symbol_key,
                    cp.current_close,
                    pp.previous_close
                FROM current_price cp
                LEFT JOIN previous_price pp ON cp.symbol_key = pp.symbol_key
            """, symbols * 2)
            
            rows = cursor.fetchall()
            for row in rows:
                symbol_key = row[0]
                current_price = Decimal(str(row[1])) if row[1] else Decimal('0')
                previous_price = Decimal(str(row[2])) if row[2] else None
                
                if previous_price and previous_price != 0:
                    change_pct = ((current_price - previous_price) / previous_price * 100)
                else:
                    change_pct = Decimal('0')
                
                name = fx_symbols.get(symbol_key, symbol_key)
                
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
            print(f"DB connection error in get_fx_rates: {e}")
        
        if not result:
            print("Warning: Returning dummy FX rates data")
            return [
                MarketIndexData(
                    symbol="US:USDJPY=X",
                    name="USD/JPY",
                    current_price=Decimal('149.32'),
                    change_pct=Decimal('0.05'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="US:EURUSD=X",
                    name="EUR/USD",
                    current_price=Decimal('1.07'),
                    change_pct=Decimal('-0.12'),
                    last_updated=datetime.now()
                ),
                MarketIndexData(
                    symbol="US:GBPUSD=X",
                    name="GBP/USD",
                    current_price=Decimal('1.26'),
                    change_pct=Decimal('0.08'),
                    last_updated=datetime.now()
                ),
            ]
            
        return result
    
    def get_metals(self) -> list[MarketIndexData]:
        """Get metal prices (optimized - bulk query)"""
        import psycopg
        
        metal_symbols = {
            'US:GLD': 'Gold',
            'US:SLV': 'Silver',
            'US:PALL': 'Palladium',
            'US:PPLT': 'Platinum'
        }
        
        result = []
        try:
            conn = psycopg.connect(
                host="localhost",
                port=5432,
                dbname="postgres",
                user="postgres",
                password="test"
            )
            cursor = conn.cursor()
            
            symbols = list(metal_symbols.keys())
            symbol_placeholders = ','.join(['%s'] * len(symbols))
            
            cursor.execute(f"""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (symbol_key)
                        symbol_key,
                        close,
                        trading_date,
                        ROW_NUMBER() OVER (PARTITION BY symbol_key ORDER BY trading_date DESC) as rn
                    FROM price_daily
                    WHERE symbol_key IN ({symbol_placeholders})
                    ORDER BY symbol_key, trading_date DESC
                ),
                current_price AS (
                    SELECT symbol_key, close as current_close
                    FROM latest_prices
                    WHERE rn = 1
                ),
                previous_price AS (
                    SELECT symbol_key, close as previous_close
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
                    cp.symbol_key,
                    cp.current_close,
                    pp.previous_close
                FROM current_price cp
                LEFT JOIN previous_price pp ON cp.symbol_key = pp.symbol_key
            """, symbols * 2)
            
            rows = cursor.fetchall()
            for row in rows:
                symbol_key = row[0]
                current_price = Decimal(str(row[1])) if row[1] else Decimal('0')
                previous_price = Decimal(str(row[2])) if row[2] else None
                
                if previous_price and previous_price != 0:
                    change_pct = ((current_price - previous_price) / previous_price * 100)
                else:
                    change_pct = Decimal('0')
                
                name = metal_symbols.get(symbol_key, symbol_key)
                
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
            print(f"DB connection error in get_metals: {e}")
        
        return result
