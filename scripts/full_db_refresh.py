"""
Unified DB refresh pipeline using existing project scripts.

This orchestrator intentionally reuses original update flows:
- ETF + benchmark fetch
- Market indices fetch
- Sector constituents fetch
- Individual US stock price update
- Indicator recalculation
- RS rating recalculation
- CANSLIM analysis refresh (report generation)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]


def _query_one(cur: psycopg.Cursor, sql: str):
    cur.execute(sql)
    row = cur.fetchone()
    return row[0] if row else None


def db_snapshot() -> dict:
    conn = psycopg.connect(
        "host=localhost port=5432 dbname=postgres user=postgres password=test"
    )
    try:
        cur = conn.cursor()
        snap = {
            "price_daily_max_date": _query_one(cur, "SELECT MAX(trading_date) FROM price_daily"),
            "indicator_daily_max_date": _query_one(
                cur, "SELECT MAX(trading_date) FROM indicator_daily"
            ),
            "rs_ratings_count": _query_one(cur, "SELECT COUNT(*) FROM rs_ratings"),
            "rs_ratings_updated_at_max": _query_one(cur, "SELECT MAX(updated_at) FROM rs_ratings"),
        }
        cur.close()
        return snap
    finally:
        conn.close()


def run_step(title: str, cmd: list[str], dry_run: bool) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    print("CMD:", " ".join(cmd))
    if dry_run:
        return

    subprocess.run(cmd, cwd=str(ROOT), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run unified ETF + individual stock + indicators + RS DB refresh."
    )
    parser.add_argument("--delay", type=float, default=0.5, help="Delay for update_recent_prices.py")
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=14,
        help="Backfill window for individual stock price update.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands only")
    parser.add_argument(
        "--skip-etf",
        action="store_true",
        help="Skip ETF/indices/sector-constituents steps",
    )
    parser.add_argument(
        "--skip-individual",
        action="store_true",
        help="Skip individual stock price update step",
    )
    parser.add_argument(
        "--skip-indicators",
        action="store_true",
        help="Skip indicator recalculation step",
    )
    parser.add_argument(
        "--skip-rs",
        action="store_true",
        help="Skip RS recalculation step",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="Skip fundamentals(EPS/revenue) update step",
    )
    parser.add_argument(
        "--skip-canslim",
        action="store_true",
        help="Skip CANSLIM analysis step",
    )
    args = parser.parse_args()

    py = sys.executable

    before = db_snapshot()
    print("Before:", before)

    if before["price_daily_max_date"] is None:
        start_date = date.today() - timedelta(days=max(args.backfill_days, 30))
    else:
        start_date = before["price_daily_max_date"] - timedelta(days=max(args.backfill_days, 1))
    end_date = date.today()

    if not args.skip_etf:
        run_step("STEP 1/9 ETF prices + benchmark", [py, "src/fetch_sector_etfs.py"], args.dry_run)
        run_step("STEP 2/9 Market indices", [py, "src/fetch_market_indices.py"], args.dry_run)
        run_step(
            "STEP 3/9 Sector constituents",
            [py, "src/fetch_sector_constituents.py"],
            args.dry_run,
        )
        run_step(
            "STEP 4/9 Monitor assets (indices/fx/metals/crypto)",
            [py, "backend/scripts/update_monitor_data.py"],
            args.dry_run,
        )

    if not args.skip_individual:
        run_step(
            "STEP 5/9 Individual US stock prices",
            [
                py,
                "src/update_recent_prices.py",
                "--start",
                start_date.isoformat(),
                "--end",
                end_date.isoformat(),
                "--delay",
                str(args.delay),
            ],
            args.dry_run,
        )

    if not args.skip_indicators:
        run_step("STEP 6/9 Indicator recalculation", [py, "src/calculate_indicators_batch.py"], args.dry_run)

    if not args.skip_rs:
        run_step("STEP 7/9 RS rating recalculation", [py, "backend/scripts/update_rs_rating.py"], args.dry_run)

    if not args.skip_fundamentals:
        run_step(
            "STEP 8/9 Fundamentals (quarterly full set)",
            [
                py,
                "src/update_fundamentals_eps.py",
                "--universe",
                "sector",
                "--quarters",
                "8",
                "--include-snapshot",
                "--delay",
                "0.2",
            ],
            args.dry_run,
        )

    if not args.skip_canslim:
        run_step(
            "STEP 9/9 CANSLIM analysis",
            [py, "scripts/run_canslim_batch.py", "--limit", "200", "--min-rs", "80"],
            args.dry_run,
        )

    after = db_snapshot()
    print("\n" + "=" * 72)
    print("Verification")
    print("=" * 72)
    print("After:", after)
    print("Completed at:", datetime.now().isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
