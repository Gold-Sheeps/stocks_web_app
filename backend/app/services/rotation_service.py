from decimal import Decimal
from datetime import datetime

import psycopg

from app.models.schemas import RotationResponse, SectorPerformance
from app.services.freshness_service import FreshnessService


class RotationService:
    """Rotation screen business logic."""

    SECTOR_SYMBOLS = {
        'US:XLK': 'Technology',
        'US:XLV': 'Healthcare',
        'US:XLF': 'Financials',
        'US:XLE': 'Energy',
        'US:XLY': 'Consumer Discretionary',
        'US:XLP': 'Consumer Staples',
        'US:XLI': 'Industrials',
        'US:XLB': 'Materials',
        'US:XLU': 'Utilities',
        'US:XLRE': 'Real Estate',
        'US:XLC': 'Communication Services',
    }

    def __init__(self):
        self.freshness_service = FreshnessService()

    def _get_db_connection(self):
        return psycopg.connect(
            host="localhost",
            port=5432,
            dbname="postgres",
            user="postgres",
            password="test"
        )

    def get_sector_rotation(self) -> RotationResponse:
        sectors = self._get_sector_performance()
        freshness = self.freshness_service.price_freshness_for_symbols(
            scope="rotation.sectors",
            symbol_keys=list(self.SECTOR_SYMBOLS.keys()) + ["US:SPY"],
            tz_name="America/New_York",
            close_cutoff_hour_local=18,
            note="Sector rotation uses US sector ETFs and SPY daily bars.",
        )
        return RotationResponse(
            sectors=sectors,
            last_updated=datetime.now(),
            freshness=freshness,
        )

    def _get_sector_performance(self) -> list[SectorPerformance]:
        results = []

        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            all_symbols = list(self.SECTOR_SYMBOLS.keys()) + ['US:SPY']
            symbol_placeholders = ','.join(['%s'] * len(all_symbols))

            cursor.execute(f"""
                WITH target_dates AS (
                    SELECT
                        CURRENT_DATE as date_0,
                        CURRENT_DATE - INTERVAL '10 days' as date_10,
                        CURRENT_DATE - INTERVAL '20 days' as date_20,
                        CURRENT_DATE - INTERVAL '30 days' as date_30
                ),
                price_0 AS (
                    SELECT DISTINCT ON (p.symbol_key)
                        p.symbol_key,
                        p.close as price_0
                    FROM price_daily p, target_dates t
                    WHERE p.symbol_key IN ({symbol_placeholders})
                      AND p.trading_date <= t.date_0
                    ORDER BY p.symbol_key, p.trading_date DESC
                ),
                price_10 AS (
                    SELECT DISTINCT ON (p.symbol_key)
                        p.symbol_key,
                        p.close as price_10
                    FROM price_daily p, target_dates t
                    WHERE p.symbol_key IN ({symbol_placeholders})
                      AND p.trading_date <= t.date_10
                    ORDER BY p.symbol_key, p.trading_date DESC
                ),
                price_20 AS (
                    SELECT DISTINCT ON (p.symbol_key)
                        p.symbol_key,
                        p.close as price_20
                    FROM price_daily p, target_dates t
                    WHERE p.symbol_key IN ({symbol_placeholders})
                      AND p.trading_date <= t.date_20
                    ORDER BY p.symbol_key, p.trading_date DESC
                ),
                price_30 AS (
                    SELECT DISTINCT ON (p.symbol_key)
                        p.symbol_key,
                        p.close as price_30
                    FROM price_daily p, target_dates t
                    WHERE p.symbol_key IN ({symbol_placeholders})
                      AND p.trading_date <= t.date_30
                    ORDER BY p.symbol_key, p.trading_date DESC
                )
                SELECT
                    p0.symbol_key,
                    p0.price_0,
                    p10.price_10,
                    p20.price_20,
                    p30.price_30
                FROM price_0 p0
                LEFT JOIN price_10 p10 ON p0.symbol_key = p10.symbol_key
                LEFT JOIN price_20 p20 ON p0.symbol_key = p20.symbol_key
                LEFT JOIN price_30 p30 ON p0.symbol_key = p30.symbol_key
            """, all_symbols * 4)

            price_data = {}
            for row in cursor.fetchall():
                symbol = row[0]
                price_data[symbol] = {
                    'price_0': Decimal(str(row[1])) if row[1] else None,
                    'price_10': Decimal(str(row[2])) if row[2] else None,
                    'price_20': Decimal(str(row[3])) if row[3] else None,
                    'price_30': Decimal(str(row[4])) if row[4] else None,
                }

            cursor.close()

            spy_data = price_data.get('US:SPY', {})
            spy_30 = spy_data.get('price_30')
            spy_0 = spy_data.get('price_0')
            if spy_30 and spy_0 and spy_30 != 0:
                spy_return = float(((spy_0 - spy_30) / spy_30) * 100)
            else:
                spy_return = 0

            for symbol, name in self.SECTOR_SYMBOLS.items():
                try:
                    data = price_data.get(symbol, {})
                    p0 = data.get('price_0')
                    p10 = data.get('price_10')
                    p20 = data.get('price_20')
                    p30 = data.get('price_30')

                    if p0 and p30 and p30 != 0:
                        current_return = float(((p0 - p30) / p30) * 100)
                    else:
                        current_return = 0

                    if p0 and p10 and p10 != 0:
                        return_10d = float(((p0 - p10) / p10) * 100)
                    else:
                        return_10d = 0

                    if p0 and p20 and p20 != 0:
                        return_20d = float(((p0 - p20) / p20) * 100)
                    else:
                        return_20d = 0

                    momentum = return_10d - return_20d
                    if spy_return != 0:
                        relative_strength = (current_return / spy_return) * 100
                    else:
                        relative_strength = 100

                    results.append({
                        'sector': name,
                        'symbol': symbol,
                        'current_return': Decimal(str(current_return)),
                        'momentum': Decimal(str(momentum)),
                        'relative_strength': Decimal(str(relative_strength)),
                    })
                except Exception as e:
                    print(f"Error calculating {symbol}: {e}")
                    continue

            conn.close()

            results.sort(key=lambda x: float(x['current_return']), reverse=True)

            performance_list = []
            for rank, sector_data in enumerate(results, 1):
                performance_list.append(SectorPerformance(
                    sector=sector_data['sector'],
                    current_return=sector_data['current_return'],
                    momentum=sector_data['momentum'],
                    relative_strength=sector_data['relative_strength'],
                    rank=rank,
                ))
            return performance_list

        except Exception as e:
            print(f"DB connection error: {e}")
            import traceback
            traceback.print_exc()
            return self._get_dummy_performance()

    def _get_dummy_performance(self) -> list[SectorPerformance]:
        return [
            SectorPerformance(
                sector="Technology",
                current_return=Decimal("8.5"),
                momentum=Decimal("1.2"),
                relative_strength=Decimal("115.3"),
                rank=1,
            ),
            SectorPerformance(
                sector="Healthcare",
                current_return=Decimal("4.2"),
                momentum=Decimal("0.5"),
                relative_strength=Decimal("102.1"),
                rank=2,
            ),
            SectorPerformance(
                sector="Financials",
                current_return=Decimal("2.8"),
                momentum=Decimal("-0.3"),
                relative_strength=Decimal("98.5"),
                rank=3,
            ),
        ]
