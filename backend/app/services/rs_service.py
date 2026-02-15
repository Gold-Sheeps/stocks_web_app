import pandas as pd
import numpy as np
from datetime import datetime
from app.db.database import Database


class RsService:
    """
    IBD Style RS Rating Service
    Calculates Relative Strength Rating (1-99) based on 12-month price performance.
    """

    def __init__(self):
        self.db = Database()

    def _ensure_table_exists(self):
        """Create rs_ratings table if it does not exist"""
        query = """
            CREATE TABLE IF NOT EXISTS rs_ratings (
                symbol_key TEXT PRIMARY KEY,
                rating INTEGER,
                raw_score DOUBLE PRECISION,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """
        self.db.execute_command(query)

    def update_rs_ratings(self):
        """
        Main process: Fetch data, Calculate RS Score, Rank, and Save to DB.
        """
        self.db.connect()
        try:
            print("--- Starting RS Rating Update ---")

            # 0. Ensure table exists
            self._ensure_table_exists()

            # 1. Fetch Price Data (Last ~1.5 years to ensure 252 trading days coverage)
            # Fetching all active symbols' data in one go is more efficient than N queries
            print("Fetching price data from DB...")
            query = """
                SELECT p.symbol_key, p.trading_date, p.close
                FROM price_daily p
                JOIN instruments i ON p.symbol_key = i.symbol_key
                WHERE i.is_active = true
                  AND p.trading_date >= CURRENT_DATE - INTERVAL '18 months'
                ORDER BY p.symbol_key, p.trading_date ASC
            """
            rows = self.db.execute_query(query)

            if not rows:
                print("No price data found.")
                return

            # Load into DataFrame
            df = pd.DataFrame(rows, columns=["symbol_key", "trading_date", "close"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df.dropna(subset=["close"], inplace=True)

            print(f"Data loaded: {len(df)} rows. Calculating scores...")

            # 2. Calculate Weighted RS Score
            # Formula: 0.4*P3 + 0.2*P6 + 0.2*P9 + 0.2*P12
            # Pn = (Current - Price_n_months_ago) / Price_n_months_ago

            # Group by symbol
            grouped = df.groupby("symbol_key")["close"]

            # Calculate lagged prices (using shift)
            # shift(N) gets the value from N rows before (since sorted ASC)
            # 3 months ~ 63 days, 6m ~ 126, 9m ~ 189, 12m ~ 252
            df["close_63"] = grouped.shift(63)
            df["close_126"] = grouped.shift(126)
            df["close_189"] = grouped.shift(189)
            df["close_252"] = grouped.shift(252)

            # We only care about the LATEST row for each symbol to calculate the current rating
            # Take the last row of each symbol
            latest_df = df.groupby("symbol_key").tail(1).copy()

            # Filter out symbols that don't have enough history (at least 252 days preferred, but strict check on close_252)
            # If close_252 is NaN, we can't calculate the full score.
            valid_df = latest_df.dropna(
                subset=["close", "close_63", "close_126", "close_189", "close_252"]
            ).copy()

            if valid_df.empty:
                print("No symbols have enough history (252 days) for RS calculation.")
                return

            # Calculate ROC (Rate of Change)
            # roc_3m = (current - p3) / p3
            valid_df["roc_3m"] = (valid_df["close"] - valid_df["close_63"]) / valid_df[
                "close_63"
            ]
            valid_df["roc_6m"] = (valid_df["close"] - valid_df["close_126"]) / valid_df[
                "close_126"
            ]
            valid_df["roc_9m"] = (valid_df["close"] - valid_df["close_189"]) / valid_df[
                "close_189"
            ]
            valid_df["roc_12m"] = (
                valid_df["close"] - valid_df["close_252"]
            ) / valid_df["close_252"]

            # Calculate Raw Score
            # Weight: 40% for 3m, 20% for others
            valid_df["raw_score"] = (
                (valid_df["roc_3m"] * 0.4)
                + (valid_df["roc_6m"] * 0.2)
                + (valid_df["roc_9m"] * 0.2)
                + (valid_df["roc_12m"] * 0.2)
            ) * 100

            # 3. Calculate Percentile Ranking (1-99)
            # rank(pct=True) returns 0.0 to 1.0
            valid_df["rank_pct"] = valid_df["raw_score"].rank(pct=True)
            # Convert to 1-99 integer
            valid_df["rating"] = (valid_df["rank_pct"] * 98) + 1
            valid_df["rating"] = valid_df["rating"].astype(int)

            print(f"Calculated ratings for {len(valid_df)} symbols.")

            # 4. Save to DB (Upsert)
            upsert_query = """
                INSERT INTO rs_ratings (symbol_key, rating, raw_score, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol_key) DO UPDATE SET
                    rating = EXCLUDED.rating,
                    raw_score = EXCLUDED.raw_score,
                    updated_at = CURRENT_TIMESTAMP
            """

            data_to_insert = []
            for _, row in valid_df.iterrows():
                data_to_insert.append(
                    (row["symbol_key"], int(row["rating"]), float(row["raw_score"]))
                )

            # Batch insert
            with self.db.connection.cursor() as cursor:
                from psycopg.extras import execute_batch

                # Note: psycopg3 uses executemany, psycopg2 uses extras.execute_batch
                # Assuming standard DB wrapper usage, we try executemany first
                cursor.executemany(upsert_query, data_to_insert)

            self.db.connection.commit()
            print("--- RS Rating Update Completed Successfully ---")

        except Exception as e:
            print(f"Error updating RS ratings: {e}")
            if self.db.connection:
                self.db.connection.rollback()
            raise e
        finally:
            self.db.disconnect()
