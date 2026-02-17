
import json
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from app.db.database import Database
from app.models.schemas import WatchlistRequest, WatchlistItem, StockInfo
from app.services.monitor_service import MonitorService

class WatchlistService:
    def __init__(self):
        self.db = Database()
        self.db.connect()

    def _get_symbol_key(self, symbol: str, market: str = 'US') -> str:
        """
        シンボルと市場からsymbol_keyを取得・生成する。
        Phase 3: 互換性維持のため、入力がsymbolのみの場合はUSとみなして変換するロジックを含む。
        実運用ではinstrumentsテーブルの検索を推奨。
        """
        # クエリでinstrumentsを検索
        # symbolが 'US:NVDA' 形式ならそのまま返す
        if ':' in symbol:
             return symbol

        # symbolのみ (例: NVDA) の場合
        # 1. symbol_keyとしてinstrumentsを検索 (仮に symbol='NVDA' が symbol_key='US:NVDA' とマッチするかは不明だが、
        #    instrumentsテーブルには symbol列がないため、symbol_keyのSuffixマッチ等を試すか、
        #    'US:' + symbol で決め打ちする)
        
        # 暫定ルールに従い 'US:{symbol}' を正とする
        return f"{market}:{symbol}"

    def get_watchlist(self) -> List[WatchlistItem]:
        """ウォッチリストの全項目を取得し、最新の価格/指標データを付与して返す"""
        try:
            # 1. ウォッチリスト取得 (symbol_keyを優先使用)
            # symbol列はレガシー互換用。基本はsymbol_keyを使う。
            query = "SELECT id, symbol, symbol_key, status, memo, tags, fair_value_min, fair_value_max, alert_config, updated_at, entry_price, entry_date FROM watchlist"
            
            watchlist_rows = self.db.execute_query(query)
            if not watchlist_rows:
                return []
            
            watchlist_items = []
            
            # symbol_keysリスト作成
            # Phase 2移行済みならsymbol_keyは埋まっているはずだが、念のためFallback
            symbol_keys = []
            row_map = []
            
            for row in watchlist_rows:
                # unpack
                sys_id, symbol, s_key, status, memo, tags, fv_min, fv_max, alert_cfg, updated_at, entry_price, entry_date = row
                
                final_key = s_key
                if not final_key:
                    # Fallback logic (Should ideally not happen after Phase 2 migration)
                     final_key = f"US:{symbol}"
                
                symbol_keys.append(final_key)
                row_map.append({
                    'id': sys_id,
                    'symbol': symbol, # 表示用
                    'symbol_key': final_key,
                    'status': status,
                    'memo': memo,
                    'tags': tags,
                    'fv_min': fv_min,
                    'fv_max': fv_max,
                    'alert_cfg': alert_cfg,
                    'updated_at': updated_at,
                    'entry_price': entry_price,
                    'entry_date': entry_date
                })

            if not symbol_keys:
                return []

            # Placeholders for IN query
            placeholders = ', '.join(['%s'] * len(symbol_keys))
            query_params = tuple(symbol_keys)
            
            # --- Bulk Fetch Data using symbol_key ---

            # 2. Instruments (Name, Market)
            inst_query = f"SELECT symbol_key, name, market FROM instruments WHERE symbol_key IN ({placeholders})"
            inst_rows = self.db.execute_query(inst_query, query_params)
            inst_map = {row[0]: {'name': row[1], 'market': row[2]} for row in inst_rows}
            
            # 3. Price (Close, Volume, TradingDate) - Latest
            price_query = f"""
                SELECT DISTINCT ON (symbol_key) symbol_key, close, volume, trading_date
                FROM price_daily
                WHERE symbol_key IN ({placeholders})
                ORDER BY symbol_key, trading_date DESC
            """
            price_rows = self.db.execute_query(price_query, query_params)
            price_map = {row[0]: {'close': row[1], 'volume': row[2], 'date': row[3]} for row in price_rows}

            # 4. Indicators - Latest
            ind_query = f"""
                SELECT DISTINCT ON (symbol_key) 
                    symbol_key, 
                    rsi14, macd, macd_signal, ema21, sma50, sma200, pivot, rs_rating, 
                    institution_flag, accum_dist, trading_date
                FROM indicator_daily
                WHERE symbol_key IN ({placeholders})
                ORDER BY symbol_key, trading_date DESC
            """
            ind_rows = self.db.execute_query(ind_query, query_params)
            ind_map = {}
            for row in ind_rows:
                ind_map[row[0]] = {
                    'rsi': row[1], 'macd': row[2], 'macd_signal': row[3], 
                    'ema21': row[4], 'sma50': row[5], 'sma200': row[6], 
                    'pivot': row[7], 'rs_rating': int(row[8]) if row[8] is not None else None,
                    'institution_flag': row[9], 'accum_dist': row[10],
                    'date': row[11]
                }

            # 5. Build Result List
            for item_data in row_map:
                s_key = item_data['symbol_key']
                
                # Basic Info
                inst_info = inst_map.get(s_key, {})
                name = inst_info.get('name', item_data['symbol']) # Default to symbol if name missing
                market = inst_info.get('market', 'US')
                
                # Price Info
                price_info = price_map.get(s_key, {})
                current_price = price_info.get('close')
                volume = price_info.get('volume')
                last_date = price_info.get('date')
                
                # Defensive: Treat 0 or negative as None
                if current_price is not None and current_price <= 0:
                    current_price = None

                # Change % Calculation (Current vs Prev Close from DB)
                change_pct = None
                if current_price and last_date:
                    # Fetch prev close for this specific key
                    q_prev = """
                        SELECT close FROM price_daily 
                        WHERE symbol_key = %s AND trading_date < %s 
                        ORDER BY trading_date DESC LIMIT 1
                    """
                    prev_res = self.db.execute_query(q_prev, (s_key, last_date))
                    if prev_res and prev_res[0][0]:
                        prev_close = prev_res[0][0]
                        if prev_close > 0:
                            change_pct = ((current_price - prev_close) / prev_close) * 100

                # Entry Price Calculations
                entry_price = item_data['entry_price']
                change_from_entry = None
                change_pct_from_entry = None
                
                if isinstance(entry_price, (float, str)):
                    entry_price = Decimal(str(entry_price))
                
                # Defensive: Treat 0 or negative entry as None
                if entry_price is not None and entry_price <= 0:
                     entry_price = None

                if current_price and entry_price:
                    change_from_entry = current_price - entry_price
                    if entry_price > 0:
                        change_pct_from_entry = ((current_price - entry_price) / entry_price) * 100

                # Indicators
                ind_info = ind_map.get(s_key, {})
                
                # Buy Zone Logic
                is_buy_zone = False
                pivot_val = ind_info.get('pivot')
                if current_price and pivot_val and pivot_val > 0:
                     if pivot_val <= current_price <= (pivot_val * Decimal('1.05')):
                         is_buy_zone = True

                item = WatchlistItem(
                    id=item_data['id'],
                    symbol=item_data['symbol'], # Display Symbol (Legacy)
                    name=name,
                    market=market,
                    status=item_data['status'],
                    memo=item_data['memo'],
                    tags=item_data['tags'] if item_data['tags'] else [],
                    fair_value_min=item_data['fv_min'],
                    fair_value_max=item_data['fv_max'],
                    alert_config=item_data['alert_cfg'] if item_data['alert_cfg'] else {},
                    current_price=current_price,
                    change_pct=change_pct,
                    volume=volume,
                    rsi=ind_info.get('rsi'),
                    macd=ind_info.get('macd'),
                    macd_signal=ind_info.get('macd_signal'),
                    ema21=ind_info.get('ema21'),
                    sma50=ind_info.get('sma50'),
                    sma200=ind_info.get('sma200'),
                    pivot=pivot_val,
                    rs_rating=ind_info.get('rs_rating'),
                    institution_flag=bool(ind_info.get('institution_flag')) if ind_info.get('institution_flag') is not None else False,
                    buy_zone=is_buy_zone,
                    last_updated=item_data['updated_at'],
                    entry_price=entry_price,
                    entry_date=item_data['entry_date'],
                    change_from_entry=change_from_entry,
                    change_pct_from_entry=change_pct_from_entry
                )
                watchlist_items.append(item)
                
            return watchlist_items

        except Exception as e:
            print(f"Error in get_watchlist: {e}", flush=True)
            import traceback
            traceback.print_exc()
            raise e
        finally:
            self.db.disconnect()

    def add_to_watchlist(self, req: WatchlistRequest) -> bool:
        """ウォッチリストに銘柄を追加"""
        try:
            # 1. symbol_keyの決定
            # APIリクエストには market が含まれていない場合が多い (WatchlistRequestスキーマ依存)
            # 暫定的に 'US' マーケットとしてキーを生成
            symbol_key = self._get_symbol_key(req.symbol, 'US')

            # 2. 重複チェック
            # symbol_key でチェックすべきだが、念のため symbol も確認
            check_query = "SELECT id FROM watchlist WHERE symbol_key = %s OR symbol = %s"
            existing = self.db.execute_query(check_query, (symbol_key, req.symbol))
            
            if existing:
                # Update existing
                return self.update_watchlist_item(req)
            
            # 3. エントリー価格用の現在値取得
            price_query = "SELECT close FROM price_daily WHERE symbol_key = %s ORDER BY trading_date DESC LIMIT 1"
            price_res = self.db.execute_query(price_query, (symbol_key,))
            entry_price = price_res[0][0] if price_res else None
            
            # 4. Insert
            # symbol_key と symbol 両方を保存 (互換性維持)
            insert_query = """
                INSERT INTO watchlist 
                (symbol, symbol_key, status, memo, tags, fair_value_min, fair_value_max, alert_config, updated_at, entry_price, entry_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, CURRENT_DATE)
            """
            self.db.execute_command(insert_query, (
                req.symbol,
                symbol_key,
                req.status, 
                req.memo, 
                req.tags, 
                req.fair_value_min, 
                req.fair_value_max, 
                json.dumps(req.alert_config) if req.alert_config else None,
                entry_price
            ))
            return True
        except Exception as e:
            print(f"Error adding to watchlist: {e}")
            if getattr(self.db, "connection", None):
                self.db.connection.rollback()
            return False
        finally:
            self.db.disconnect()

    def update_watchlist_item(self, req: WatchlistRequest) -> bool:
        """ウォッチリスト項目を更新"""
        try:
            # symbol_key で更新対象を特定したいが、リクエストは symbol しかない
            # symbol カラムを使って特定する (互換性)
            
            update_query = """
                UPDATE watchlist
                SET status = %s, memo = %s, tags = %s, 
                    fair_value_min = %s, fair_value_max = %s, 
                    alert_config = %s, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = %s
            """
            # Note: もし symbol_key も正しく更新したい場合は、WHERE symbol_key = ... にすべきだが
            # クライアントが symbol しか送ってこない場合、symbolカラムでのマッチが確実。
            
            self.db.execute_command(update_query, (
                req.status, 
                req.memo, 
                req.tags, 
                req.fair_value_min, 
                req.fair_value_max, 
                json.dumps(req.alert_config) if req.alert_config else None,
                req.symbol
            ))
            return True
        except Exception as e:
            print(f"Error updating watchlist: {e}")
            if getattr(self.db, "connection", None):
                self.db.connection.rollback()
            return False
        finally:
            self.db.disconnect()

    def delete_from_watchlist(self, symbol: str) -> bool:
        """ウォッチリストから削除"""
        try:
            # 互換性のため symbol カラムで削除
            query = "DELETE FROM watchlist WHERE symbol = %s"
            self.db.execute_command(query, (symbol,))
            return True
        except Exception as e:
            print(f"Error deleting from watchlist: {e}")
            if getattr(self.db, "connection", None):
                self.db.connection.rollback()
            return False
        finally:
            self.db.disconnect()
