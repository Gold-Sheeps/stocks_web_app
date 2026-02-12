import unittest
from unittest.mock import MagicMock, patch
from decimal import Decimal
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from app.services.portfolio_service import PortfolioService
from app.models.schemas import Holding

class TestFXConversion(unittest.TestCase):
    def setUp(self):
        self.service = PortfolioService()
        self.service.db = MagicMock()
        self.service.db.connect = MagicMock()
        self.service.db.disconnect = MagicMock()

    @patch('app.services.portfolio_service.yf.Ticker')
    def test_fx_conversion_holdings(self, mock_ticker):
        # Mock FX Rate
        # We can mock _get_usd_jpy_rate or mock yfinance.
        # Let's mock the method directly for simplicity
        self.service._get_usd_jpy_rate = MagicMock(return_value=Decimal("150.0"))
        
        # Mock DB returns for _get_holdings
        # Columns: symbol, market, asset_type, quantity, average_cost, currency
        self.service.db.execute_query.side_effect = [
            # 1. _get_holdings: query holdings
            [('AAPL', 'US', 'EQUITY', Decimal("10"), Decimal("100"), 'USD')],
            # 2. _get_current_price: query price_daily (inside loop)
            [(Decimal("120"),)],
            # 3. instruments check (name)
            [('Apple Inc',)]
        ]
        
        # Run
        holdings = self.service._get_holdings(Decimal("150.0"))
        
        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        
        print(f"Holding: {h}")
        
        # Verify Conversion
        # Qty 10, Price 120, Rate 150 -> Market Value = 10 * 120 * 150 = 180,000
        expected_mv = Decimal("180000.0")
        self.assertEqual(h.market_value, expected_mv)
        self.assertEqual(h.currency, 'JPY')
        
        # Verify Cost Basis
        # Qty 10, Avg Cost 100, Rate 150 -> Cost Basis = 10 * 100 * 150 = 150,000
        expected_cb = Decimal("150000.0")
        self.assertEqual(h.cost_basis, expected_cb)
        
        # Verify Gain Loss
        # 180,000 - 150,000 = 30,000
        self.assertEqual(h.gain_loss, Decimal("30000.0"))
        
        print("Unit Test Passed: FX Conversion Correct")

if __name__ == '__main__':
    unittest.main()
