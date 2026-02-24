"""Create the ai_predictions table (idempotent - uses IF NOT EXISTS)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database


def create_table() -> None:
    db = Database()
    if not db.connect():
        print("[ERROR] Failed to connect to database.")
        return
    try:
        with db.connection.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_predictions (
                    symbol_key      TEXT        NOT NULL,
                    asof            DATE        NOT NULL,
                    p_up5           REAL        NOT NULL,
                    threshold_buy   REAL        NOT NULL,
                    decision        TEXT        NOT NULL,
                    cal_method      TEXT        NOT NULL DEFAULT 'none',
                    artifact_path   TEXT,
                    updated_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT ai_predictions_pk PRIMARY KEY (symbol_key, asof, cal_method)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_pred_symbol_key
                    ON ai_predictions (symbol_key)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_pred_asof
                    ON ai_predictions (asof)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_pred_symbol_asof
                    ON ai_predictions (symbol_key, asof DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_pred_symbol_asof_cal
                    ON ai_predictions (symbol_key, asof DESC, cal_method)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_pred_decision
                    ON ai_predictions (decision)
            """)
            db.connection.commit()
        print("[OK] ai_predictions table and indexes are ready.")
    except Exception as e:
        db.connection.rollback()
        print(f"[ERROR] {e}")
    finally:
        db.disconnect()


if __name__ == "__main__":
    create_table()
