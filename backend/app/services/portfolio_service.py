from decimal import Decimal
from datetime import datetime
from typing import List, Optional
import yfinance as yf
from app.db.database import Database
from app.models.schemas import (
    Holding,
    TradeHistory,
    PortfolioPerformance,
    PortfolioDetailResponse,
    TradeCreate,
    PortfolioSummary,
)


class PortfolioService:
    """Portfolio画面のビジネスロジック"""

    def __init__(self):
        self.db = Database()

    def get_portfolio_summary(self) -> PortfolioSummary:
        """ポートフォリオサマリー（総資産、現金、評価額、損益）を取得"""
        self.db.connect()
        try:
            usd_jpy_rate = self._get_usd_jpy_rate()
            holdings = self._get_holdings(usd_jpy_rate)
            total_market_value_jpy = sum((h.market_value for h in holdings), Decimal("0"))
            total_deposits_jpy, trade_cash_flow_jpy = self._get_trade_cash_components(usd_jpy_rate)

            # Ledger-based cash:
            # Cash = Deposits - Withdrawals - Buys + Sells
            cash_balance = total_deposits_jpy + trade_cash_flow_jpy
            total_assets = cash_balance + total_market_value_jpy
            total_pl = total_assets - total_deposits_jpy
            total_pl_pct = (total_pl / abs(total_deposits_jpy) * 100) if total_deposits_jpy != 0 else Decimal("0")

            return PortfolioSummary(
                total_value_jpy=total_assets,
                total_cost_jpy=total_deposits_jpy,
                total_gain_loss_jpy=total_pl,
                total_gain_loss_pct=total_pl_pct,
                cash_balance_jpy=cash_balance,
                equity_value_jpy=total_market_value_jpy,
                ytd_start_date=datetime(datetime.now().year, 1, 1),
            )

        finally:
            self.db.disconnect()

    def get_portfolio_detail(self) -> PortfolioDetailResponse:
        """ポートフォリオ詳細データを取得"""
        self.db.connect()
        try:
            # 1. Get Latest USD/JPY Rate
            usd_jpy_rate = self._get_usd_jpy_rate()

            # 2. Get Holdings with FX conversion
            holdings = self._get_holdings(usd_jpy_rate)
            recent_trades = self._get_recent_trades()

            # Ledger-based portfolio formula in JPY.
            holdings_market_value = sum((h.market_value for h in holdings), Decimal("0"))
            total_deposits_jpy, trade_cash_flow_jpy = self._get_trade_cash_components(usd_jpy_rate)
            cash_balance = total_deposits_jpy + trade_cash_flow_jpy
            total_value = holdings_market_value + cash_balance
            total_cost = total_deposits_jpy
            total_gain_loss = total_value - total_cost
            total_gain_loss_pct = (total_gain_loss / abs(total_cost) * 100) if total_cost != 0 else Decimal("0")

            # Calculate Best Performer
            best_performer = self._get_best_performer(usd_jpy_rate)

            performance = PortfolioPerformance(
                total_value=total_value,
                total_cost=total_cost,
                total_gain_loss=total_gain_loss,
                total_gain_loss_pct=total_gain_loss_pct,
                cash_balance=cash_balance,
                best_performer=best_performer,
                worst_performer=None,
            )

            return PortfolioDetailResponse(
                performance=performance,
                holdings=holdings,
                recent_trades=recent_trades,
                last_updated=datetime.now(),
            )
        finally:
            self.db.disconnect()

    def add_trade(self, trade: TradeCreate):
        """取引を追加し、保有状況を更新"""
        self.db.connect()
        try:
            self._ensure_trades_side_constraint()

            # Extract display symbol from symbol_key for legacy column
            # Assumes format "MARKET:SYMBOL" (e.g. US:NVDA)
            display_symbol = trade.symbol_key
            if ":" in display_symbol:
                display_symbol = display_symbol.split(":", 1)[1]

            # 1. Insert Trade
            # symbol_key column is added in Phase 2
            query_insert = """
                INSERT INTO trades 
                (symbol, symbol_key, market, trade_date, side, quantity, price, total_amount, currency, fx_rate_to_jpy, asset_type, memo, realized_pnl)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING trade_id
            """

            total_amount = trade.shares * trade.price
            realized_pnl = Decimal("0")
            if trade.side in ("DEPOSIT", "WITHDRAW"):
                realized_pnl = None

            # Get current holding to calculate PnL and new average cost
            # Use symbol_key for lookup if possible, or fallback
            current_holding = self._get_holding_db(display_symbol)

            new_quantity = Decimal("0")
            new_avg_cost = Decimal("0")

            if current_holding:
                current_qty = current_holding["quantity"]
                current_avg = current_holding["average_cost"]

                if trade.side == "BUY":
                    if current_qty >= 0:
                        # Long + Buy = Add to position (Weighted Avg)
                        new_quantity = current_qty + trade.shares
                        new_total_cost = (current_qty * current_avg) + total_amount
                        new_avg_cost = (
                            new_total_cost / new_quantity if new_quantity != 0 else 0
                        )
                    else:
                        # Short + Buy = Cover (Realize PnL)
                        remaining_qty = current_qty + trade.shares

                        if remaining_qty <= 0:
                            # Still Short or Flat
                            covered_qty = trade.shares
                            realized_pnl = (current_avg - trade.price) * covered_qty
                            new_quantity = remaining_qty
                            new_avg_cost = current_avg
                        else:
                            # Flip to Long
                            covered_qty = abs(current_qty)
                            realized_pnl = (current_avg - trade.price) * covered_qty
                            new_quantity = remaining_qty
                            new_avg_cost = trade.price

                elif trade.side == "SELL":
                    if current_qty <= 0:
                        # Short + Sell = Add to short position
                        new_quantity = current_qty - trade.shares
                        current_proceeds = abs(current_qty) * current_avg
                        new_proceeds = current_proceeds + total_amount
                        new_avg_cost = (
                            new_proceeds / abs(new_quantity) if new_quantity != 0 else 0
                        )
                    else:
                        # Long + Sell = Close (Realize PnL)
                        remaining_qty = current_qty - trade.shares

                        if remaining_qty >= 0:
                            # Still Long or Flat
                            sold_qty = trade.shares
                            realized_pnl = (trade.price - current_avg) * sold_qty
                            new_quantity = remaining_qty
                            new_avg_cost = current_avg
                        else:
                            # Flip to Short
                            sold_qty = current_qty
                            realized_pnl = (trade.price - current_avg) * sold_qty
                            new_quantity = remaining_qty
                            new_avg_cost = trade.price
            else:
                # No existing holding
                if trade.side == "BUY":
                    new_quantity = trade.shares
                    new_avg_cost = trade.price
                else:
                    new_quantity = -trade.shares
                    new_avg_cost = trade.price

            # Execute Insert
            inserted = self.db.execute_command(
                query_insert,
                (
                    display_symbol,
                    trade.symbol_key,
                    trade.market,
                    trade.trade_date,
                    trade.side,
                    trade.shares,
                    trade.price,
                    total_amount,
                    trade.currency,
                    trade.fx_rate_to_jpy,
                    trade.asset_type,
                    trade.memo,
                    realized_pnl,
                ),
            )
            if not inserted:
                raise Exception("Failed to insert trade")

            # Cash events should not create/update holdings.
            if trade.side in ("DEPOSIT", "WITHDRAW"):
                return True

            # Insert/Update Holding
            self._upsert_holding(
                display_symbol,
                trade.symbol_key,
                trade.market,
                trade.asset_type,
                new_quantity,
                new_avg_cost,
                trade.currency,
            )

            return True

        except Exception as e:
            print(f"Error adding trade: {e}")
            raise e
        finally:
            self.db.disconnect()

    def _ensure_trades_side_constraint(self):
        """Allow DEPOSIT/WITHDRAW in trades.side check constraint."""
        rows = self.db.execute_query(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = 'trades'
              AND c.conname = 'trades_side_check'
            """
        )
        if not rows:
            return
        definition = rows[0][0] if rows and rows[0] else ""
        if "DEPOSIT" in definition and "WITHDRAW" in definition:
            return
        self.db.execute_command("ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_side_check")
        self.db.execute_command(
            """
            ALTER TABLE trades
            ADD CONSTRAINT trades_side_check
            CHECK (side IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAW'))
            """
        )

    def delete_trade(self, trade_id: int):
        """取引を削除"""
        self.db.connect()
        try:
            query_get = "SELECT symbol, symbol_key FROM trades WHERE trade_id = %s"
            result = self.db.execute_query(query_get, (trade_id,))
            if not result:
                return False
            symbol, symbol_key = result[0]

            self.db.execute_command(
                "DELETE FROM trades WHERE trade_id = %s", (trade_id,)
            )

            # Rebuild holding for this symbol
            self._rebuild_holding(symbol, symbol_key)
            return True
        finally:
            self.db.disconnect()

    def _get_holding_db(self, symbol):
        # symbol PK is used for holdings lookups
        query = "SELECT quantity, average_cost FROM holdings WHERE symbol = %s"
        result = self.db.execute_query(query, (symbol,))
        if result:
            return {"quantity": result[0][0], "average_cost": result[0][1]}
        return None

    def _upsert_holding(
        self, symbol, symbol_key, market, asset_type, quantity, avg_cost, currency
    ):
        if quantity == 0:
            query = "DELETE FROM holdings WHERE symbol = %s"
            self.db.execute_command(query, (symbol,))
        else:
            # Update symbol_key as well
            query = """
                INSERT INTO holdings (symbol, symbol_key, market, asset_type, quantity, average_cost, currency, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (symbol) DO UPDATE SET
                    quantity = EXCLUDED.quantity,
                    average_cost = EXCLUDED.average_cost,
                    symbol_key = EXCLUDED.symbol_key,
                    updated_at = NOW()
            """
            self.db.execute_command(
                query,
                (symbol, symbol_key, market, asset_type, quantity, avg_cost, currency),
            )

    def _rebuild_holding(self, symbol, symbol_key=None):
        # Fetch all trades ASC
        query = "SELECT side, quantity, price, asset_type, market, currency, symbol_key FROM trades WHERE symbol = %s ORDER BY trade_date ASC, created_at ASC"
        trades = self.db.execute_query(query, (symbol,))

        quantity = Decimal("0")
        avg_cost = Decimal("0")
        market = ""
        asset_type = ""
        currency = "USD"

        last_symbol_key = symbol_key

        for t in trades:
            side, shares, price, at, mkt, cur, skey = t
            market = mkt
            asset_type = at
            currency = cur
            if skey:
                last_symbol_key = skey

            total_amt = shares * price

            if side == "BUY":
                if quantity >= 0:
                    new_qty = quantity + shares
                    new_total = (quantity * avg_cost) + total_amt
                    avg_cost = new_total / new_qty if new_qty != 0 else 0
                    quantity = new_qty
                else:
                    remaining = quantity + shares
                    if remaining <= 0:
                        quantity = remaining
                    else:
                        quantity = remaining
                        avg_cost = price
            elif side == "SELL":
                if quantity <= 0:
                    new_qty = quantity - shares
                    new_total = (abs(quantity) * avg_cost) + total_amt
                    avg_cost = new_total / abs(new_qty) if new_qty != 0 else 0
                    quantity = new_qty
                else:
                    remaining = quantity - shares
                    if remaining >= 0:
                        quantity = remaining
                    else:
                        quantity = remaining
                        avg_cost = price

        # If we didn't get symbol_key from trades, try to derive/lookup[WARN]
        # Assuming last_symbol_key is valid if trades exist
        if not last_symbol_key:
            # Fallback
            last_symbol_key = f"{market}:{symbol}" if market else f"US:{symbol}"

        self._upsert_holding(
            symbol, last_symbol_key, market, asset_type, quantity, avg_cost, currency
        )

    def _get_holdings(self, fx_rate: Decimal) -> list[Holding]:
        # Get holdings from DB, including symbol_key
        query = "SELECT symbol, market, asset_type, quantity, average_cost, currency, symbol_key FROM holdings"
        rows = self.db.execute_query(query)

        holdings = []
        if not rows:
            return []

        for r in rows:
            symbol, market, asset_type, qty, avg_cost, curr, symbol_key = r

            # Identify Key to use for Price Lookup
            lookup_key = symbol_key if symbol_key else symbol

            # Get current price with key mismatch fallback handling.
            current_price = self._get_current_price(symbol=symbol, symbol_key=lookup_key, market=market)

            # Calculate Values in Local Currency
            market_value_local = qty * current_price
            cost_basis_local = qty * avg_cost
            gain_loss_local = market_value_local - cost_basis_local

            # Convert to JPY
            rate_to_apply = Decimal("1.0")
            if market == "US" or curr == "USD":
                rate_to_apply = fx_rate

            market_value_jpy = market_value_local * rate_to_apply
            cost_basis_jpy = cost_basis_local * rate_to_apply
            gain_loss_jpy = gain_loss_local * rate_to_apply

            avg_cost_jpy = avg_cost * rate_to_apply
            current_price_jpy = current_price * rate_to_apply

            gain_loss_pct = Decimal("0")
            if cost_basis_jpy != 0:
                gain_loss_pct = (gain_loss_jpy / abs(cost_basis_jpy)) * 100

            # Get Name from Instruments using symbol_key
            name = symbol
            if lookup_key:
                name_res = self.db.execute_query(
                    "SELECT name FROM instruments WHERE symbol_key = %s", (lookup_key,)
                )
                if name_res:
                    name = name_res[0][0]
                else:
                    # Fallback name lookup for key mismatch cases (e.g. AMD vs US:AMD)
                    for k in self._candidate_symbol_keys(symbol, lookup_key, market):
                        res2 = self.db.execute_query("SELECT name FROM instruments WHERE symbol_key = %s", (k,))
                        if res2:
                            name = res2[0][0]
                            break

            holdings.append(
                Holding(
                    symbol=symbol,
                    name=name,
                    market=market,
                    asset_type=asset_type,
                    shares=qty,
                    avg_cost=avg_cost_jpy,
                    current_price=current_price_jpy,
                    market_value=market_value_jpy,
                    cost_basis=cost_basis_jpy,
                    gain_loss=gain_loss_jpy,
                    gain_loss_pct=gain_loss_pct,
                    currency="JPY",
                )
            )

        return holdings

    def _get_current_price(self, symbol: str, symbol_key: Optional[str], market: Optional[str]):
        # Latest Close from price_daily with key normalization fallback.
        query = "SELECT close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1"
        for key in self._candidate_symbol_keys(symbol, symbol_key, market):
            res = self.db.execute_query(query, (key,))
            if res and res[0][0] is not None:
                return res[0][0]
        return Decimal("0")

    def _get_recent_trades(self) -> list[TradeHistory]:
        query = "SELECT trade_id, symbol, side, trade_date, quantity, price, total_amount, currency FROM trades ORDER BY trade_date DESC, created_at DESC LIMIT 50"
        rows = self.db.execute_query(query)
        trades = []
        if rows:
            for r in rows:
                trades.append(
                    TradeHistory(
                        trade_id=str(r[0]),
                        symbol=r[1],
                        side=r[2],
                        trade_date=str(r[3]),
                        shares=r[4],
                        price=r[5],
                        amount=r[6],
                        currency=r[7],
                    )
                )
        return trades

    def _get_usd_jpy_rate(self) -> Decimal:
        """最新のUSD/JPYレートを取得"""
        try:
            # Try DB first (project symbol_key standard: US:USDJPY=X)
            query = "SELECT close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1"
            res = self.db.execute_query(query, ("US:USDJPY=X",))
            if res:
                return res[0][0]

            # Fallback to yfinance if not in DB
            ticker = yf.Ticker("USDJPY=X")
            hist = ticker.history(period="1d")
            if not hist.empty:
                return Decimal(str(hist["Close"].iloc[-1]))
        except Exception as e:
            print(f"Failed to fetch USDJPY: {e}")

        return Decimal("150.0")

    def _get_trade_cash_components(self, current_usd_jpy_rate: Decimal) -> tuple[Decimal, Decimal]:
        """
        Returns:
            (net_deposits_jpy, trade_cash_flow_jpy)
            - net_deposits_jpy = deposits - withdrawals
            - trade_cash_flow_jpy = sells - buys
        """
        query = """
            SELECT side, total_amount, currency, fx_rate_to_jpy
            FROM trades
            WHERE side IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAW')
        """
        rows = self.db.execute_query(query) or []

        net_deposits_jpy = Decimal("0")
        trade_cash_flow_jpy = Decimal("0")

        for side, amount, currency, fx_rate in rows:
            rate = Decimal("1")
            if currency and currency != "JPY":
                rate = fx_rate if fx_rate is not None else current_usd_jpy_rate

            amt = amount if amount is not None else Decimal("0")
            if side == "DEPOSIT":
                net_deposits_jpy += amt * rate
            elif side == "WITHDRAW":
                net_deposits_jpy -= amt * rate
            elif side == "BUY":
                trade_cash_flow_jpy -= amt * rate
            elif side == "SELL":
                trade_cash_flow_jpy += amt * rate

        return net_deposits_jpy, trade_cash_flow_jpy

    def _candidate_symbol_keys(self, symbol: str, symbol_key: Optional[str], market: Optional[str]) -> list[str]:
        keys = []
        raw = symbol
        if symbol_key:
            keys.append(symbol_key)
            if ":" in symbol_key:
                raw = symbol_key.split(":", 1)[1]
            else:
                raw = symbol_key
        # bare symbol (legacy)
        keys.append(raw)
        # market-prefixed candidates
        if market:
            keys.append(f"{market}:{raw}")
        keys.append(f"US:{raw}")
        keys.append(f"JP:{raw}")
        # de-dup preserve order
        seen = set()
        dedup = []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                dedup.append(k)
        return dedup

    def _get_best_performer(self, current_fx_rate: Decimal) -> Optional[str]:
        current_year = datetime.now().year
        start_date = f"{current_year}-01-01"

        query = """
            SELECT symbol, market, realized_pnl, fx_rate_to_jpy, asset_type 
            FROM trades 
            WHERE trade_date >= %s AND realized_pnl IS NOT NULL AND realized_pnl != 0
        """
        rows = self.db.execute_query(query, (start_date,))

        if not rows:
            return None

        pnl_by_symbol = {}
        for r in rows:
            symbol, market, pnl, fx_captured, asset_type = r

            pnl_val = pnl
            if market == "US" or asset_type in ("US", "ETF", "FX"):
                pnl_val = pnl * current_fx_rate

            pnl_by_symbol[symbol] = pnl_by_symbol.get(symbol, Decimal("0")) + pnl_val

        if not pnl_by_symbol:
            return None

        best = max(pnl_by_symbol.items(), key=lambda x: x[1])
        if best[1] > 0:
            return best[0]
        return None

    def get_all_symbols(self) -> List[dict]:
        """全銘柄リストを取得（オートコンプリート用）"""
        self.db.connect()
        try:
            query = "SELECT symbol_key, name, market FROM instruments ORDER BY symbol_key ASC"
            rows = self.db.execute_query(query)
            if not rows:
                return []
            return [{"symbol": r[0], "name": r[1], "market": r[2]} for r in rows]
        finally:
            self.db.disconnect()
