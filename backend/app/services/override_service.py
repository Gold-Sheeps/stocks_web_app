"""
Override Service (Phase 1)
CRUD + Audit for data_overrides / override_audit tables.

DB操作は既存の Database クラス (app.db.database) を使用。
  - execute_query(sql, params)  → SELECT用、fetchall() を返す
  - execute_command(sql, params) → INSERT/UPDATE/DELETE用、commit して bool 返す
  - cursor / connection への直接アクセスは RETURNING が必要な場合のみ
    (indicator_service.py:193 と同じパターン)
"""
from datetime import date, datetime
from typing import Optional, List
from app.db.database import Database


# ============================================================
# Override 可能フィールド定義 (コード内定数)
# ============================================================
OVERRIDE_FIELDS = {
    "fundamentals": {
        "label": "ファンダメンタル",
        "fields": [
            {"name": "eps_diluted_q",      "label": "EPS（希薄化後）",   "type": "number"},
            {"name": "revenue_q",          "label": "売上高",            "type": "number"},
            {"name": "net_income_q",       "label": "純利益",            "type": "number"},
            {"name": "roe_ttm",            "label": "ROE (TTM)",         "type": "number"},
            {"name": "ocf_q",             "label": "営業CF",            "type": "number"},
            {"name": "fcf_q",             "label": "フリーCF",          "type": "number"},
            {"name": "gross_margin_q",     "label": "粗利率",           "type": "number"},
            {"name": "operating_margin_q", "label": "営業利益率",       "type": "number"},
            {"name": "shares_float",       "label": "浮動株数",         "type": "number"},
            {"name": "inst_ownership_pct", "label": "機関保有率(%)",    "type": "number"},
        ],
    },
    "market": {
        "label": "マーケット・マクロ",
        "fields": [
            {"name": "vix",         "label": "VIX",            "type": "number"},
            {"name": "dxy",         "label": "ドル指数",        "type": "number"},
            {"name": "us10y_yield", "label": "米10年債利回り",   "type": "number"},
            {"name": "wti_crude",   "label": "WTI原油",        "type": "number"},
            {"name": "gold_spot",   "label": "金スポット",      "type": "number"},
        ],
    },
    "metadata": {
        "label": "メタデータ",
        "fields": [
            {"name": "sector",             "label": "セクター",     "type": "text"},
            {"name": "industry",           "label": "業種",         "type": "text"},
            {"name": "country",            "label": "国",           "type": "text"},
            {"name": "currency",           "label": "通貨",         "type": "text"},
            {"name": "shares_outstanding", "label": "発行株式数",   "type": "number"},
        ],
    },
    "model": {
        "label": "モデルパラメータ",
        "fields": [
            {"name": "decay_lambda",      "label": "λ（減衰係数）",       "type": "number"},
            {"name": "upper_barrier_pct",  "label": "上側バリア幅(%)",     "type": "number"},
            {"name": "lower_barrier_pct",  "label": "下側バリア幅(%)",     "type": "number"},
            {"name": "trading_fee_pct",    "label": "取引手数料(%)",       "type": "number"},
            {"name": "slippage_bps",       "label": "スリッページ(bps)",   "type": "number"},
        ],
    },
}


class OverrideService:
    """Data Override の CRUD + Preview + Audit"""

    def __init__(self):
        self.db = Database()

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    def get_fields(self) -> dict:
        """Override 可能フィールド定義を返す"""
        return {"categories": OVERRIDE_FIELDS}

    # ------------------------------------------------------------------
    # Symbols (instruments テーブルから取得)
    # ------------------------------------------------------------------
    def get_symbols(self) -> list:
        """
        instruments テーブルから銘柄一覧を返す。
        根拠: stock_detail_service.py:36 → FROM instruments i
              indicator_service.py:220  → SELECT symbol_key FROM instruments WHERE is_active = true
        """
        self.db.connect()
        try:
            rows = self.db.execute_query(
                "SELECT symbol_key, name FROM instruments WHERE is_active = true ORDER BY symbol_key"
            )
            if not rows:
                return []
            return [{"symbol": r[0], "name": r[1] or r[0]} for r in rows]
        finally:
            self.db.disconnect()

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------
    def list_overrides(
        self,
        scope: Optional[str] = None,
        scope_key: Optional[str] = None,
        category: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> list:
        self.db.connect()
        try:
            conditions: list[str] = []
            params: list = []

            if scope:
                conditions.append("scope = %s")
                params.append(scope)
            if scope_key:
                conditions.append("scope_key = %s")
                params.append(scope_key)
            if category:
                conditions.append("category = %s")
                params.append(category)
            if enabled is not None:
                conditions.append("enabled = %s")
                params.append(enabled)

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            sql = f"""
                SELECT id, scope, scope_key, category, field_name, period,
                       override_value, original_value, priority, reason,
                       effective_start, effective_end, enabled, created_by,
                       created_at, updated_at
                FROM data_overrides
                {where}
                ORDER BY updated_at DESC
            """
            rows = self.db.execute_query(sql, tuple(params) if params else None)
            if not rows:
                return []

            cols = [
                "id", "scope", "scope_key", "category", "field_name", "period",
                "override_value", "original_value", "priority", "reason",
                "effective_start", "effective_end", "enabled", "created_by",
                "created_at", "updated_at",
            ]
            result = []
            for row in rows:
                item = {}
                for i, col in enumerate(cols):
                    val = row[i]
                    if isinstance(val, (datetime, date)):
                        val = str(val)
                    item[col] = val
                result.append(item)
            return result
        finally:
            self.db.disconnect()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_override(self, data: dict) -> dict:
        """
        Override を新規登録し、audit ログも同時記録する。
        RETURNING を使うため cursor を直接操作する。
        (indicator_service.py:193 と同パターン)
        """
        self.db.connect()
        try:
            insert_sql = """
                INSERT INTO data_overrides
                    (scope, scope_key, category, field_name, period,
                     override_value, original_value, priority, reason,
                     effective_start, effective_end, created_by)
                VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s)
                RETURNING id
            """
            params = (
                data["scope"],
                data["scope_key"],
                data["category"],
                data["field_name"],
                data.get("period"),
                str(data["override_value"]),
                str(data["original_value"]) if data.get("original_value") is not None else None,
                data.get("priority", "normal"),
                data["reason"],
                data.get("effective_start"),
                data.get("effective_end"),
                data.get("created_by", "admin"),
            )

            self.db.cursor.execute(insert_sql, params)
            row = self.db.cursor.fetchone()
            new_id = row[0] if row else None

            # Audit log
            self.db.cursor.execute(
                """
                INSERT INTO override_audit
                    (override_id, action, new_value, reason, performed_by)
                VALUES (%s, 'create', %s, %s, %s)
                """,
                (new_id, str(data["override_value"]), data["reason"], data.get("created_by", "admin")),
            )
            self.db.connection.commit()
            return {"id": new_id, "status": "created"}

        except Exception as e:
            if self.db.connection:
                self.db.connection.rollback()
            raise e
        finally:
            self.db.disconnect()

    # ------------------------------------------------------------------
    # Preview (Phase 1: diff のみ。forecast_impact は null)
    # ------------------------------------------------------------------
    def preview(self, data: dict) -> dict:
        """保存せず diff 情報だけ返す。Phase 2 で forecast_impact を追加。"""
        before = data.get("original_value")
        after = data.get("override_value")

        change_pct = None
        if before is not None and after is not None:
            try:
                b = float(before)
                a = float(after)
                if b != 0:
                    change_pct = round(((a - b) / abs(b)) * 100, 2)
            except (ValueError, TypeError):
                pass

        return {
            "diff": {
                "field": data.get("field_name"),
                "before": before,
                "after": after,
                "change_pct": change_pct,
            },
            "affected_features": [],       # TODO Phase 2
            "forecast_impact": None,        # TODO Phase 2
        }

    # ------------------------------------------------------------------
    # Activate / Deactivate
    # ------------------------------------------------------------------
    def set_enabled(self, override_id: int, enabled: bool, user: str = "admin") -> dict:
        self.db.connect()
        try:
            action = "activate" if enabled else "deactivate"

            # まず存在確認
            check = self.db.execute_query(
                "SELECT id, override_value FROM data_overrides WHERE id = %s", (override_id,)
            )
            if not check:
                return {"error": "not_found"}

            self.db.cursor.execute(
                "UPDATE data_overrides SET enabled = %s, updated_at = NOW() WHERE id = %s",
                (enabled, override_id),
            )
            self.db.cursor.execute(
                """
                INSERT INTO override_audit (override_id, action, performed_by)
                VALUES (%s, %s, %s)
                """,
                (override_id, action, user),
            )
            self.db.connection.commit()
            return {"id": override_id, "status": action}

        except Exception as e:
            if self.db.connection:
                self.db.connection.rollback()
            raise e
        finally:
            self.db.disconnect()

    # ------------------------------------------------------------------
    # Rollback (= deactivate + audit "rollback")
    # ------------------------------------------------------------------
    def rollback(self, override_id: int, user: str = "admin") -> dict:
        self.db.connect()
        try:
            check = self.db.execute_query(
                "SELECT id FROM data_overrides WHERE id = %s", (override_id,)
            )
            if not check:
                return {"error": "not_found"}

            self.db.cursor.execute(
                "UPDATE data_overrides SET enabled = FALSE, updated_at = NOW() WHERE id = %s",
                (override_id,),
            )
            self.db.cursor.execute(
                """
                INSERT INTO override_audit (override_id, action, reason, performed_by)
                VALUES (%s, 'rollback', 'rollback requested', %s)
                """,
                (override_id, user),
            )
            self.db.connection.commit()
            return {"id": override_id, "status": "rollback"}

        except Exception as e:
            if self.db.connection:
                self.db.connection.rollback()
            raise e
        finally:
            self.db.disconnect()

    # ------------------------------------------------------------------
    # Audit Log
    # ------------------------------------------------------------------
    def get_audit_log(self, limit: int = 20) -> list:
        self.db.connect()
        try:
            sql = """
                SELECT a.performed_at, a.performed_by, a.action,
                       o.scope_key, o.field_name, o.period,
                       a.old_value, a.new_value, a.reason
                FROM override_audit a
                LEFT JOIN data_overrides o ON a.override_id = o.id
                ORDER BY a.performed_at DESC
                LIMIT %s
            """
            rows = self.db.execute_query(sql, (limit,))
            if not rows:
                return []
            return [
                {
                    "timestamp": str(r[0]) if r[0] else "",
                    "user": r[1] or "system",
                    "action": r[2],
                    "scope_key": r[3],
                    "field_name": r[4],
                    "period": r[5],
                    "old_value": r[6],
                    "new_value": r[7],
                    "reason": r[8],
                }
                for r in rows
            ]
        finally:
            self.db.disconnect()
