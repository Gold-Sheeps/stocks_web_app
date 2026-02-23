import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from app.db.database import Database


TECHNICAL_COLUMNS = [
    ("vwap20", "NUMERIC(12, 4)"),
    ("obv", "BIGINT"),
    ("mfi14", "NUMERIC(8, 4)"),
    ("plus_di14", "NUMERIC(8, 4)"),
    ("minus_di14", "NUMERIC(8, 4)"),
    ("adx14", "NUMERIC(8, 4)"),
    ("bb_upper20", "NUMERIC(12, 4)"),
    ("bb_lower20", "NUMERIC(12, 4)"),
    ("bb_width20", "NUMERIC(12, 4)"),
    ("bb_percent_b", "NUMERIC(8, 4)"),
    ("ichimoku_tenkan9", "NUMERIC(12, 4)"),
    ("ichimoku_kijun26", "NUMERIC(12, 4)"),
    ("ichimoku_senkou_a", "NUMERIC(12, 4)"),
    ("ichimoku_senkou_b", "NUMERIC(12, 4)"),
    ("ichimoku_chikou", "NUMERIC(12, 4)"),
]


def add_columns():
    db = Database()
    if not db.connect():
        print("Failed to connect to database")
        return 1

    try:
        with db.connection.cursor() as cursor:
            for col_name, col_type in TECHNICAL_COLUMNS:
                cursor.execute(
                    f"ALTER TABLE indicator_daily ADD COLUMN IF NOT EXISTS {col_name} {col_type};"
                )
                print(f"Ensured column: {col_name} ({col_type})")

        db.connection.commit()
        print("Phase 5-2 indicator columns migration completed successfully")
        return 0
    except Exception as e:
        print(f"Error during migration: {e}")
        db.connection.rollback()
        return 1
    finally:
        db.disconnect()


if __name__ == "__main__":
    raise SystemExit(add_columns())
