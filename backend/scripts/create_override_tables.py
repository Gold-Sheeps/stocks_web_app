"""
Data Override テーブル作成スクリプト (Phase 1)
既存の create_data_update_tables.py と同じパターンで、Database クラスを使う。
"""
import sys
import os

# backend ディレクトリをパスに追加 (scripts/ の親)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.database import Database


def create_tables():
    db = Database()
    if not db.connect():
        print("DB接続失敗")
        return

    try:
        print("Creating override tables...")

        # 1. data_overrides
        db.execute_command("""
            CREATE TABLE IF NOT EXISTS data_overrides (
                id              SERIAL PRIMARY KEY,
                scope           VARCHAR(20) NOT NULL,
                scope_key       VARCHAR(50) NOT NULL,
                category        VARCHAR(30) NOT NULL,
                field_name      VARCHAR(80) NOT NULL,
                period          VARCHAR(20),
                override_value  TEXT NOT NULL,
                original_value  TEXT,
                priority        VARCHAR(20) DEFAULT 'normal',
                reason          TEXT NOT NULL,
                effective_start DATE,
                effective_end   DATE,
                enabled         BOOLEAN DEFAULT TRUE,
                created_by      VARCHAR(100) DEFAULT 'admin',
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        print("  ✓ data_overrides")

        db.execute_command("""
            CREATE INDEX IF NOT EXISTS idx_ov_scope
            ON data_overrides(scope, scope_key, field_name);
        """)
        db.execute_command("""
            CREATE INDEX IF NOT EXISTS idx_ov_enabled
            ON data_overrides(enabled, scope, scope_key);
        """)
        print("  ✓ indexes")

        # 2. override_audit
        db.execute_command("""
            CREATE TABLE IF NOT EXISTS override_audit (
                id              SERIAL PRIMARY KEY,
                override_id     INTEGER REFERENCES data_overrides(id),
                action          VARCHAR(30) NOT NULL,
                old_value       TEXT,
                new_value       TEXT,
                reason          TEXT,
                performed_by    VARCHAR(100) DEFAULT 'admin',
                performed_at    TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        print("  ✓ override_audit")

        print("Done.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.disconnect()


if __name__ == "__main__":
    create_tables()
