from decimal import Decimal
from typing import List, Optional, Dict
import psycopg
from app.core.config import settings
from app.models.schemas import PortfolioSummary, Holding, Trade
from datetime import datetime


class PortfolioService:
    def __init__(self):
        self.dsn = f"postgresql://{settings.postgres_user}:{settings.postgres_password}@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"

    def _get_db_connection(self):
        return psycopg.connect(self.dsn)

    def _get_usd_jpy_rate(self) -> Decimal:
        """最新のUSD/JPYレートを取得"""
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT close FROM price_daily 
                        WHERE symbol_key = 'US:USDJPY=X' 
                        ORDER BY trading_date DESC LIMIT 1
                    """
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        return Decimal(str(row[0]))
                    return Decimal("150.0")
        except Exception as e:
            print(f"Error getting USD/JPY rate: {e}")
            return Decimal("150.0")

    def _get_latest_prices(self) -> Dict[str, Decimal]:
        """全銘柄の最新価格を取得して辞書で返す"""
        price_map = {}
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (symbol_key) symbol_key, close 
                        FROM price_daily 
                        ORDER BY symbol_key, trading_date DESC
                    """
                    )
                    rows = cur.fetchall()
                    for r in rows:
                        if r[0] and r[1] is not None:
                            price_map[r[0]] = Decimal(str(r[1]))
        except Exception as e:
            print(f"Error fetching prices: {e}")
        return price_map

    def _find_current_price(
        self, symbol: str, symbol_key: Optional[str], price_map: Dict[str, Decimal]
    ) -> Decimal:
        """
        シンボルマッチングロジック (最重要)
        holdingsのsymbolとprice_dailyのsymbol_keyを柔軟にマッチングさせる
        """
        # 1. symbol_keyで検索 (最も確実)
        if symbol_key and symbol_key in price_map:
            return price_map[symbol_key]

        # 2. US:symbol で検索 (米国株の一般的パターン)
        us_key = f"US:{symbol}"
        if us_key in price_map:
            return price_map[us_key]

        # 3. JP:symbol で検索 (日本株の一般的パターン)
        jp_key = f"JP:{symbol}"
        if jp_key in price_map:
            return price_map[jp_key]

        # 4. symbol そのもので検索 (完全一致)
        if symbol in price_map:
            return price_map[symbol]

        return Decimal("0")

    def get_portfolio_summary(self) -> PortfolioSummary:
        """
        シンプルな積み上げロジックによるポートフォリオサマリー
        """
        try:
            usd_jpy_rate = self._get_usd_jpy_rate()
            price_map = self._get_latest_prices()

            # --- 1. 現金残高 (Cash) の計算 ---
            # tradesテーブルから単純に積み上げ
            cash_balance = Decimal("0")
            total_deposits = Decimal("0")
            total_withdrawals = Decimal("0")

            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT side, total_amount, currency, fx_rate_to_jpy FROM trades"
                    )
                    trades = cur.fetchall()

                    for t in trades:
                        side, amount, currency, fx_rate = t
                        if amount is None:
                            continue

                        val = Decimal(str(amount))

                        # 円換算
                        rate = Decimal("1.0")
                        if currency != "JPY":
                            rate = (
                                Decimal(str(fx_rate))
                                if fx_rate is not None
                                else usd_jpy_rate
                            )

                        val_jpy = val * rate

                        if side == "DEPOSIT":
                            cash_balance += val_jpy
                            total_deposits += val_jpy
                        elif side == "WITHDRAW":
                            cash_balance -= val_jpy
                            total_withdrawals += val_jpy
                        elif side == "BUY":
                            cash_balance -= val_jpy
                        elif side == "SELL":
                            cash_balance += val_jpy

            # --- 2. 保有株評価額 (Market Value) の計算 ---
            total_market_value = Decimal("0")

            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT symbol, symbol_key, quantity, market, currency FROM holdings"
                    )
                    holdings = cur.fetchall()

                    for h in holdings:
                        symbol, symbol_key, quantity, market, currency = h
                        if not quantity:
                            continue

                        qty = Decimal(str(quantity))

                        # 現在価格取得 (シンボルマッチング)
                        current_price = self._find_current_price(
                            symbol, symbol_key, price_map
                        )

                        # 円換算レート
                        rate = Decimal("1.0")
                        if currency == "USD" or market == "US":
                            rate = usd_jpy_rate

                        # 評価額加算
                        total_market_value += qty * current_price * rate

            # --- 3. 総資産 (Total Assets) ---
            total_assets = cash_balance + total_market_value

            # --- 4. 総損益 (Total P&L) ---
            # 総資産 - 純入金額
            net_deposits = total_deposits - total_withdrawals
            total_pl = total_assets - net_deposits

            total_pl_pct = Decimal("0")
            if net_deposits != 0:
                total_pl_pct = (total_pl / abs(net_deposits)) * 100

            return PortfolioSummary(
                total_assets=total_assets,
                cash_balance=cash_balance,
                market_value=total_market_value,
                total_pl=total_pl,
                total_pl_pct=total_pl_pct,
                currency="JPY",
            )

        except Exception as e:
            print(f"Error in get_portfolio_summary: {e}")
            return PortfolioSummary(
                total_assets=Decimal("0"),
                cash_balance=Decimal("0"),
                market_value=Decimal("0"),
                total_pl=Decimal("0"),
                total_pl_pct=Decimal("0"),
                currency="JPY",
            )

    def get_holdings(self) -> List[Holding]:
        """
        保有銘柄一覧 (個別P&L計算)
        """
        holdings_list = []
        try:
            usd_jpy_rate = self._get_usd_jpy_rate()
            price_map = self._get_latest_prices()

            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    # holdingsからデータを取得 (average_costを使用)
                    cur.execute(
                        """
                        SELECT id, symbol, name, quantity, average_cost, market, currency, symbol_key 
                        FROM holdings
                        ORDER BY symbol
                    """
                    )
                    rows = cur.fetchall()

                    for row in rows:
                        (
                            h_id,
                            symbol,
                            name,
                            quantity,
                            average_cost,
                            market,
                            currency,
                            symbol_key,
                        ) = row

                        qty = Decimal(str(quantity)) if quantity else Decimal("0")
                        avg_cost = (
                            Decimal(str(average_cost)) if average_cost else Decimal("0")
                        )

                        # 現在価格 (シンボルマッチング)
                        current_price = self._find_current_price(
                            symbol, symbol_key, price_map
                        )

                        # 円換算レート
                        rate = Decimal("1.0")
                        if currency == "USD" or market == "US":
                            rate = usd_jpy_rate

                        # 日本円換算
                        current_price_jpy = current_price * rate
                        avg_cost_jpy = avg_cost * rate

                        # Value (評価額)
                        value = qty * current_price_jpy

                        # Cost Basis (取得原価総額)
                        cost_basis = qty * avg_cost_jpy

                        # P&L (損益)
                        pnl = value - cost_basis

                        # P&L % (損益率)
                        pnl_pct = Decimal("0")
                        if cost_basis != 0:
                            pnl_pct = (pnl / cost_basis) * 100

                        holdings_list.append(
                            Holding(
                                id=h_id,
                                symbol=symbol,
                                name=name,
                                quantity=qty,
                                average_price=avg_cost_jpy,  # 円換算平均単価
                                current_price=current_price_jpy,  # 円換算現在値
                                value=value,
                                pnl=pnl,
                                pnl_pct=pnl_pct,
                                market=market,
                                currency=currency,
                            )
                        )

            return holdings_list

        except Exception as e:
            print(f"Error in get_holdings: {e}")
            return []

    def get_trades(self) -> List[Trade]:
        """取引履歴取得"""
        trades = []
        try:
            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, trade_date, symbol, side, quantity, price, total_amount, status, currency 
                        FROM trades 
                        ORDER BY trade_date DESC
                    """
                    )
                    rows = cur.fetchall()
                    for row in rows:
                        (
                            t_id,
                            date,
                            symbol,
                            side,
                            quantity,
                            price,
                            amount,
                            status,
                            currency,
                        ) = row
                        trades.append(
                            Trade(
                                id=t_id,
                                date=date,
                                symbol=symbol,
                                side=side,
                                quantity=Decimal(str(quantity)) if quantity else None,
                                price=Decimal(str(price)) if price else None,
                                total_amount=Decimal(str(amount)) if amount else None,
                                status=status,
                                currency=currency,
                            )
                        )
            return trades
        except Exception as e:
            print(f"Error fetching trades: {e}")
            return []
