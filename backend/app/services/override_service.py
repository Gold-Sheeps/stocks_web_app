"""
Override Service (Phase 1)
CRUD + Audit for data_overrides / override_audit tables.
"""
from datetime import date, datetime
from typing import Optional, List
from app.db.database import Database


OVERRIDE_FIELDS = {
    "fundamentals": {
        "label": "Fundamentals",
        "fields": [
            {"name": "eps_diluted_q", "label": "EPS (Diluted)", "type": "number"},
            {"name": "revenue_q", "label": "Revenue", "type": "number"},
            {"name": "net_income_q", "label": "Net Income", "type": "number"},
            {"name": "roe_ttm", "label": "ROE (TTM)", "type": "number"},
            {"name": "ocf_q", "label": "Operating CF", "type": "number"},
            {"name": "fcf_q", "label": "Free CF", "type": "number"},
            {"name": "gross_margin_q", "label": "Gross Margin", "type": "number"},
            {"name": "operating_margin_q", "label": "Operating Margin", "type": "number"},
            {"name": "shares_float", "label": "Shares Float", "type": "number"},
            {"name": "inst_ownership_pct", "label": "Inst Ownership %", "type": "number"}
        ]
    },
    "market": {
        "label": "Market & Macro",
        "fields": [
            {"name": "vix", "label": "VIX", "type": "number"},
            {"name": "dxy", "label": "Dollar Index", "type": "number"},
            {"name": "us10y_yield", "label": "US10Y Yield", "type": "number"},
            {"name": "wti_crude", "label": "WTI Crude", "type": "number"},
            {"name": "gold_spot", "label": "Gold Spot", "type": "number"}
        ]
    },
    "metadata": {
        "label": "Metadata",
        "fields": [
            {"name": "sector", "label": "Sector", "type": "text"},
            {"name": "industry", "label": "Industry", "type": "text"},
            {"name": "country", "label": "Country", "type": "text"},
            {"name": "currency", "label": "Currency", "type": "text"},
            {"name": "shares_outstanding", "label": "Shares Outstanding", "type": "number"}
        ]
    },
    "model": {
        "label": "Model Parameters",
        "fields": [
            {"name": "decay_lambda", "label": "Lambda (Decay)", "type": "number"},
            {"name": "upper_barrier_pct", "label": "Upper Barrier %", "type": "number"},
            {"name": "lower_barrier_pct", "label": "Lower Barrier %", "type": "number"},
            {"name": "trading_fee_pct", "label": "Trading Fee %", "type": "number"},
            {"name": "slippage_bps", "label": "Slippage (bps)", "type": "number"}
        ]
    }
}

class OverrideService:
    """Data Override 縺ｮ CRUD + Preview + Audit"""

    def __init__(self):
        self.db = Database()

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    def get_fields(self) -> dict:
        """Override 蜿ｯ閭ｽ繝輔ぅ繝ｼ繝ｫ繝牙ｮ夂ｾｩ繧定ｿ斐☆"""
        return {"categories": OVERRIDE_FIELDS}

    # ------------------------------------------------------------------
    # Symbols (instruments 繝・・繝悶Ν縺九ｉ蜿門ｾ・
    # ------------------------------------------------------------------
    def get_symbols(self) -> list:
        """
        instruments 繝・・繝悶Ν縺九ｉ驫俶氛荳隕ｧ繧定ｿ斐☆縲・
        譬ｹ諡: stock_detail_service.py:36 竊・FROM instruments i
              indicator_service.py:220  竊・SELECT symbol_key FROM instruments WHERE is_active = true
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
        Override 繧呈眠隕冗匳骭ｲ縺励∥udit 繝ｭ繧ｰ繧ょ酔譎りｨ倬鹸縺吶ｋ縲・
        RETURNING 繧剃ｽｿ縺・◆繧・cursor 繧堤峩謗･謫堺ｽ懊☆繧九・
        (indicator_service.py:193 縺ｨ蜷後ヱ繧ｿ繝ｼ繝ｳ)
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
    # Preview (Phase 1: diff 縺ｮ縺ｿ縲Ｇorecast_impact 縺ｯ null)
    # ------------------------------------------------------------------
    def preview(self, data: dict) -> dict:
        """Return simple diff preview (Phase 1)."""
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

            # 縺ｾ縺壼ｭ伜惠遒ｺ隱・
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

    # ==================================================================
    # NEW: Incremental symbol search
    # ==================================================================
    def search_symbols(self, query: str, limit: int = 20) -> list:
        db = Database()
        db.connect()
        try:
            if not query or not query.strip():
                rows = db.execute_query(
                    "SELECT symbol_key, name FROM instruments WHERE is_active = true ORDER BY symbol_key LIMIT %s",
                    (limit,),
                )
            else:
                like = f"%{query.upper()}%"
                rows = db.execute_query(
                    """SELECT symbol_key, name FROM instruments
                       WHERE is_active = true
                         AND (UPPER(symbol_key) LIKE %s OR UPPER(COALESCE(name, '')) LIKE %s)
                       ORDER BY symbol_key LIMIT %s""",
                    (like, like, limit),
                )
            return [{"symbol_key": r[0], "name": r[1] or r[0]} for r in (rows or [])]
        finally:
            db.disconnect()

    # ==================================================================
    # NEW: Get all data for a symbol with missing detection
    # ==================================================================
    def get_symbol_data(self, symbol_key: str, category: str = "all") -> dict:
        db = Database()
        db.connect()
        try:
            # fundamentals tables use raw ticker (strip market prefix)
            raw = symbol_key.split(":")[-1] if ":" in symbol_key else symbol_key
            result: dict = {
                "symbol": symbol_key,
                "categories": {},
                "missing_count": 0,
                "total_count": 0,
            }
            if category in ("all", "price"):
                result["categories"]["price"] = self._get_table_data(
                    db, "price_daily", "symbol_key", symbol_key,
                    label="価格データ", order_by="trading_date DESC", limit=1,
                )
            if category in ("all", "indicator"):
                result["categories"]["indicator"] = self._get_table_data(
                    db, "indicator_daily", "symbol_key", symbol_key,
                    label="テクニカル指標", order_by="trading_date DESC", limit=1,
                )
            if category in ("all", "fundamental"):
                result["categories"]["fundamental"] = self._get_table_data(
                    db, "fundamentals", "symbol", raw,
                    label="ファンダメンタルズ", order_by="period_end_date DESC", limit=5,
                )
            if category in ("all", "ratio"):
                result["categories"]["ratio"] = self._get_table_data(
                    db, "fundamentals_ratios_latest", "symbol", raw,
                    label="財務比率", order_by=None, limit=1,
                )
            for cat_data in result["categories"].values():
                for field in cat_data.get("fields", []):
                    result["total_count"] += 1
                    if field.get("missing"):
                        result["missing_count"] += 1
            return result
        finally:
            db.disconnect()

    def _get_table_data(
        self, db, table: str, key_col: str, symbol: str,
        label: str = "", order_by: str = None, limit: int = 1,
    ) -> dict:
        cols = db.execute_query(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        if not cols:
            return {"label": label, "table": table, "key_col": key_col,
                    "fields": [], "rows": [], "row_count": 0}

        col_names = [c[0] for c in cols]
        col_types = {c[0]: c[1] for c in cols}
        order_clause = f"ORDER BY {order_by}" if order_by else ""
        limit_clause = f"LIMIT {limit}" if limit else ""

        rows = db.execute_query(
            f"SELECT * FROM {table} WHERE {key_col} = %s {order_clause} {limit_clause}",
            (symbol,),
        )

        data_rows = []
        for row in (rows or []):
            row_data = {}
            for i, col_name in enumerate(col_names):
                val = row[i]
                if hasattr(val, "isoformat"):
                    val = str(val)
                row_data[col_name] = {
                    "value": str(val) if val is not None else None,
                    "missing": val is None,
                    "type": col_types[col_name],
                }
            data_rows.append(row_data)

        if data_rows:
            fields = [
                {"name": c, "type": col_types[c],
                 "missing": data_rows[0][c]["missing"],
                 "value": data_rows[0][c]["value"]}
                for c in col_names
            ]
        else:
            fields = [{"name": c, "type": col_types[c], "missing": True, "value": None}
                      for c in col_names]

        return {
            "label": label, "table": table, "key_col": key_col, "symbol": symbol,
            "fields": fields, "rows": data_rows, "row_count": len(data_rows),
        }

    # ==================================================================
    # NEW: Direct table update with audit
    # ==================================================================
    _ALLOWED_TABLES = frozenset(
        {"price_daily", "indicator_daily", "fundamentals", "fundamentals_ratios_latest"}
    )
    _REQUIRED_DATE = {
        "price_daily": "trading_date",
        "indicator_daily": "trading_date",
        "fundamentals": "period_end_date",
    }

    def direct_update(self, symbol_key: str, payload: dict) -> dict:
        import json as _json

        table = payload.get("table", "")
        updates: dict = payload.get("updates", {})
        row_id: dict = payload.get("row_id", {})

        if table not in self._ALLOWED_TABLES:
            return {"error": f"table not allowed: {table}"}
        if not updates:
            return {"error": "no updates provided"}

        # Require date key for time-series tables
        req_date = self._REQUIRED_DATE.get(table)
        if req_date and req_date not in row_id:
            return {"error": f"{req_date} is required in row_id for table {table}"}

        db = Database()
        db.connect()
        try:
            # Ensure audit table exists (committed separately)
            db.execute_command("""
                CREATE TABLE IF NOT EXISTS direct_edit_audit (
                    id SERIAL PRIMARY KEY,
                    symbol_key VARCHAR(50),
                    table_name VARCHAR(100),
                    field_name VARCHAR(100),
                    old_value TEXT,
                    new_value TEXT,
                    row_id JSONB,
                    performed_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Validate column names
            actual = db.execute_query(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s",
                (table,),
            )
            valid_cols = {r[0]: r[1] for r in (actual or [])}
            bad = [c for c in updates if c not in valid_cols]
            if bad:
                return {"error": f"invalid columns: {bad}"}

            # Determine key
            if table in ("fundamentals", "fundamentals_ratios_latest"):
                key_col = "symbol"
                sym_val = symbol_key.split(":")[-1] if ":" in symbol_key else symbol_key
            else:
                key_col = "symbol_key"
                sym_val = symbol_key

            # Build WHERE
            where_parts = [f"{key_col} = %s"]
            where_vals: list = [sym_val]
            for k, v in row_id.items():
                where_parts.append(f"{k} = %s")
                where_vals.append(v)
            where_clause = " AND ".join(where_parts)

            # Fetch old values for audit
            col_list = ", ".join(updates.keys())
            old_rows = db.execute_query(
                f"SELECT {col_list} FROM {table} WHERE {where_clause}",
                tuple(where_vals),
            )
            old_vals = list(old_rows[0]) if old_rows else [None] * len(updates)

            # Type coercion
            numeric_types = {"double precision", "numeric", "real", "bigint", "integer", "smallint"}
            typed_vals = []
            for col, val in updates.items():
                if val is None or val == "":
                    typed_vals.append(None)
                elif valid_cols.get(col, "") in numeric_types:
                    try:
                        typed_vals.append(float(val))
                    except (ValueError, TypeError):
                        typed_vals.append(val)
                else:
                    typed_vals.append(val)

            # UPDATE + audit in one transaction
            set_clause = ", ".join(f"{col} = %s" for col in updates.keys())
            db.cursor.execute(
                f"UPDATE {table} SET {set_clause} WHERE {where_clause}",
                tuple(typed_vals + where_vals),
            )
            affected = db.cursor.rowcount

            for i, (col, new_val) in enumerate(updates.items()):
                old_val = old_vals[i] if i < len(old_vals) else None
                db.cursor.execute(
                    """INSERT INTO direct_edit_audit
                           (symbol_key, table_name, field_name, old_value, new_value, row_id)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        symbol_key, table, col,
                        str(old_val) if old_val is not None else None,
                        str(new_val) if new_val is not None else None,
                        _json.dumps(row_id),
                    ),
                )
            db.connection.commit()

            return {"status": "ok", "updated": len(updates), "rows_affected": affected, "table": table}
        except Exception as e:
            if db.connection:
                db.connection.rollback()
            return {"error": str(e)}
        finally:
            db.disconnect()

    # ==================================================================
    # NEW: Market Environment data
    # ==================================================================
    def get_market_environment_data(self) -> dict:
        import json as _json
        db = Database()
        db.connect()
        try:
            rows = db.execute_query(
                "SELECT check_date, data FROM market_environment ORDER BY check_date DESC LIMIT 1"
            )
            if not rows:
                return {"fields": {}, "check_date": None, "missing_count": 0, "total_count": 0}
            check_date = str(rows[0][0])
            data = rows[0][1]
            if not isinstance(data, dict):
                data = _json.loads(data)
            return {
                "check_date": check_date,
                "fields": data,
                "missing_count": sum(1 for v in data.values() if v is None),
                "total_count": len(data),
            }
        finally:
            db.disconnect()

    def update_market_environment(self, updates: dict) -> dict:
        import json as _json
        db = Database()
        db.connect()
        try:
            rows = db.execute_query(
                "SELECT check_date, data FROM market_environment ORDER BY check_date DESC LIMIT 1"
            )
            if not rows:
                return {"error": "no market environment data found"}
            check_date = rows[0][0]
            current = rows[0][1]
            if not isinstance(current, dict):
                current = _json.loads(current)

            for k, v in updates.items():
                if v is None or v == "":
                    current[k] = None
                    continue
                orig = current.get(k)
                if isinstance(orig, bool):
                    current[k] = str(v).lower() in ("true", "1", "yes")
                elif isinstance(orig, int):
                    try:
                        current[k] = int(float(v))
                    except (ValueError, TypeError):
                        current[k] = v
                elif isinstance(orig, float):
                    try:
                        current[k] = float(v)
                    except (ValueError, TypeError):
                        current[k] = v
                else:
                    current[k] = v

            db.execute_command(
                "UPDATE market_environment SET data = %s, updated_at = NOW() WHERE check_date = %s",
                (_json.dumps(current), check_date),
            )
            return {"status": "ok", "updated": len(updates), "check_date": str(check_date)}
        except Exception as e:
            if db.connection:
                db.connection.rollback()
            return {"error": str(e)}
        finally:
            db.disconnect()

    # ==================================================================
    # NEW: CSV Export
    # ==================================================================
    def export_csv_data(self, symbol_key: str, category: str = "all") -> str:
        import csv, io
        data = self.get_symbol_data(symbol_key, category)
        output = io.StringIO()
        writer = csv.writer(output)
        for cat_data in data["categories"].values():
            writer.writerow([
                f"# Category: {cat_data['label']} | Table: {cat_data['table']} | Symbol: {symbol_key}"
            ])
            writer.writerow(["field_name", "current_value", "new_value", "data_type", "missing"])
            for field in cat_data["fields"]:
                writer.writerow([
                    field["name"],
                    field["value"] or "",
                    "",
                    field["type"],
                    "YES" if field["missing"] else "",
                ])
            writer.writerow([])
        return output.getvalue()

    # ==================================================================
    # NEW: CSV Import
    # ==================================================================
    def import_from_csv(self, content: str) -> dict:
        import csv, io, re
        if content.startswith("\ufeff"):
            content = content[1:]

        reader = csv.reader(io.StringIO(content))
        current_table: Optional[str] = None
        current_symbol: Optional[str] = None
        batch: dict = {}

        for row in reader:
            if not row or not row[0].strip():
                continue
            if row[0].startswith("#"):
                m_tbl = re.search(r"Table: (\w+)", row[0])
                m_sym = re.search(r"Symbol: ([^\s|]+)", row[0])
                if m_tbl:
                    current_table = m_tbl.group(1)
                if m_sym:
                    current_symbol = m_sym.group(1)
                continue
            if row[0] == "field_name":
                continue
            if len(row) >= 3 and row[2].strip() and current_table and current_symbol:
                key = f"{current_table}|{current_symbol}"
                if key not in batch:
                    batch[key] = {"table": current_table, "symbol": current_symbol, "fields": {}}
                batch[key]["fields"][row[0]] = row[2].strip()

        results = []
        for item in batch.values():
            r = self.direct_update(
                item["symbol"],
                {"table": item["table"], "updates": item["fields"], "row_id": {}},
            )
            results.append({"table": item["table"], "symbol": item["symbol"], "result": r})

        return {"status": "ok", "applied": len(batch), "results": results}

