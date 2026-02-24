"""Safely migrate ai_predictions PK to (symbol_key, asof, cal_method).

This migration is designed to preserve existing rows and support coexistence of
multiple calibration outputs (none / isotonic / platt) for the same symbol/date.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database


def _print_table_shape(db: Database) -> None:
    rows = db.execute_query(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'ai_predictions'
        ORDER BY ordinal_position
        """
    ) or []
    print("[INFO] columns:")
    for r in rows:
        print(f"  - {r[0]} {r[1]} null={r[2]} default={r[3]}")


def _constraint_names(db: Database) -> list[str]:
    rows = db.execute_query(
        """
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'ai_predictions'::regclass
        ORDER BY conname
        """
    ) or []
    return [str(r[0]) for r in rows]


def migrate() -> int:
    db = Database()
    if not db.connect():
        print("[ERROR] DB connection failed.")
        return 1
    try:
        cur = db.cursor
        assert cur is not None

        # Create table if missing (new schema).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_predictions (
                symbol_key      TEXT        NOT NULL,
                asof            DATE        NOT NULL,
                p_up5           DOUBLE PRECISION,
                threshold_buy   DOUBLE PRECISION,
                decision        TEXT,
                cal_method      TEXT        NOT NULL DEFAULT 'none',
                artifact_path   TEXT,
                updated_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT ai_predictions_pk PRIMARY KEY (symbol_key, asof, cal_method)
            )
            """
        )

        # Ensure cal_method column exists and is populated.
        cur.execute(
            """
            ALTER TABLE ai_predictions
            ADD COLUMN IF NOT EXISTS cal_method TEXT
            """
        )
        cur.execute(
            """
            UPDATE ai_predictions
            SET cal_method = 'platt'
            WHERE cal_method IS NULL OR BTRIM(cal_method) = ''
            """
        )
        cur.execute(
            """
            ALTER TABLE ai_predictions
            ALTER COLUMN cal_method SET DEFAULT 'none'
            """
        )
        cur.execute(
            """
            ALTER TABLE ai_predictions
            ALTER COLUMN cal_method SET NOT NULL
            """
        )

        # Deduplicate exact PK target collisions before adding new PK.
        # Keep the most recently updated row if duplicates exist.
        cur.execute(
            """
            DELETE FROM ai_predictions a
            USING ai_predictions b
            WHERE a.ctid < b.ctid
              AND a.symbol_key = b.symbol_key
              AND a.asof = b.asof
              AND a.cal_method = b.cal_method
            """
        )

        # Drop legacy unique constraint on (symbol_key, asof) if present.
        legacy_unique_rows = db.execute_query(
            """
            SELECT c.conname
            FROM pg_constraint c
            WHERE c.conrelid = 'ai_predictions'::regclass
              AND c.contype IN ('u','p')
              AND EXISTS (
                    SELECT 1
                    FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                    GROUP BY c.conname
                    HAVING array_agg(a.attname::text ORDER BY k.ord) = ARRAY['symbol_key','asof']::text[]
              )
            """
        ) or []
        for (name,) in legacy_unique_rows:
            print(f"[INFO] dropping legacy constraint: {name}")
            cur.execute(f'ALTER TABLE ai_predictions DROP CONSTRAINT IF EXISTS "{name}"')

        # Drop existing PK (e.g., on id or old composite) if it is not target PK.
        pk_rows = db.execute_query(
            """
            SELECT c.conname
            FROM pg_constraint c
            WHERE c.conrelid = 'ai_predictions'::regclass
              AND c.contype = 'p'
            """
        ) or []
        for (pk_name,) in pk_rows:
            cols_rows = db.execute_query(
                """
                SELECT a.attname
                FROM pg_constraint c
                JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                WHERE c.conrelid = 'ai_predictions'::regclass
                  AND c.conname = %s
                ORDER BY k.ord
                """,
                (pk_name,),
            ) or []
            cols = [str(r[0]) for r in cols_rows]
            if cols != ["symbol_key", "asof", "cal_method"]:
                print(f"[INFO] dropping PK {pk_name} columns={cols}")
                cur.execute(f'ALTER TABLE ai_predictions DROP CONSTRAINT IF EXISTS "{pk_name}"')

        # Add target PK if absent.
        pk_target_exists = db.execute_query(
            """
            SELECT 1
            FROM pg_constraint c
            WHERE c.conrelid = 'ai_predictions'::regclass
              AND c.contype = 'p'
              AND (
                SELECT array_agg(a.attname::text ORDER BY k.ord)
                FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
              ) = ARRAY['symbol_key','asof','cal_method']::text[]
            """
        )
        if not pk_target_exists:
            print("[INFO] adding composite PK (symbol_key, asof, cal_method)")
            cur.execute(
                """
                ALTER TABLE ai_predictions
                ADD CONSTRAINT ai_predictions_pk PRIMARY KEY (symbol_key, asof, cal_method)
                """
            )

        # Helpful indexes for UI/API queries.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_pred_symbol_key ON ai_predictions (symbol_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_pred_asof ON ai_predictions (asof)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_pred_symbol_asof ON ai_predictions (symbol_key, asof DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_pred_symbol_asof_cal ON ai_predictions (symbol_key, asof DESC, cal_method)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_pred_decision ON ai_predictions (decision)"
        )

        db.connection.commit()

        print("[OK] ai_predictions migration completed.")
        _print_table_shape(db)
        print("[INFO] constraints:", _constraint_names(db))
        return 0
    except Exception as e:
        if db.connection:
            db.connection.rollback()
        print(f"[ERROR] migration failed: {e}")
        return 1
    finally:
        db.disconnect()


if __name__ == "__main__":
    raise SystemExit(migrate())
