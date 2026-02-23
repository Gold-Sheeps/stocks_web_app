"""
Fetch quarterly fundamentals & ratios from Yahoo Finance for watchlist/portfolio symbols.

Reuses core logic from src/update_fundamentals_eps.py with a watchlist+portfolio universe
and explicit --symbols CLI support.

Usage:
    python backend/scripts/fetch_fundamentals.py
    python backend/scripts/fetch_fundamentals.py --symbols "US:AAPL,US:MSFT,US:GOOGL"
    python backend/scripts/fetch_fundamentals.py --max-symbols 50 --delay 0.5
    python backend/scripts/fetch_fundamentals.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Resolve repo root (backend/scripts -> backend -> repo_root)
REPO_ROOT = Path(__file__).resolve().parents[2]

# Add src/ to sys.path so we can import update_fundamentals_eps
sys.path.insert(0, str(REPO_ROOT / "src"))

# yfinance timezone cache fix before yfinance import
_YF_CACHE_DIR = REPO_ROOT / ".cache" / "yfinance"
_YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YFINANCE_TZ_CACHE_LOCATION", str(_YF_CACHE_DIR))

import psycopg

from update_fundamentals_eps import (
    ensure_schema,
    fetch_quarterly_fundamentals,
    fetch_ratio_snapshot,
    is_equity_symbol,
    upsert_comparison_latest,
    upsert_fundamental,
    upsert_ratios_latest,
)

DB_CONNINFO = "host=localhost port=5432 dbname=postgres user=postgres password=test"


def _extract_raw(symbol_key: str) -> str | None:
    """'US:AAPL' -> 'AAPL', 'AAPL' -> 'AAPL'"""
    if not symbol_key:
        return None
    if ":" in symbol_key:
        return symbol_key.split(":", 1)[1].strip()
    return symbol_key.strip()


def collect_watchlist_portfolio_symbols(conn: psycopg.Connection) -> list[str]:
    """Get raw ticker symbols from watchlist and trades (portfolio) tables."""
    symbols: set[str] = set()
    with conn.cursor() as cur:
        # Watchlist
        try:
            cur.execute(
                "SELECT DISTINCT symbol_key FROM watchlist WHERE symbol_key IS NOT NULL"
            )
            for (sk,) in cur.fetchall():
                raw = _extract_raw(sk)
                if raw and is_equity_symbol(raw):
                    symbols.add(raw)
        except Exception as e:
            print(f"[WARN] watchlist query failed: {e}")

        # Portfolio (trades table)
        try:
            cur.execute(
                "SELECT DISTINCT symbol_key FROM trades WHERE symbol_key IS NOT NULL"
            )
            for (sk,) in cur.fetchall():
                raw = _extract_raw(sk)
                if raw and is_equity_symbol(raw):
                    symbols.add(raw)
        except Exception as e:
            print(f"[WARN] trades query failed: {e}")

    return sorted(symbols)


def parse_explicit_symbols(symbols_str: str) -> list[str]:
    """Parse 'US:AAPL,US:MSFT' -> ['AAPL', 'MSFT'] (raw yfinance tickers)."""
    result: list[str] = []
    for s in symbols_str.split(","):
        s = s.strip().upper()
        if not s:
            continue
        raw = _extract_raw(s)
        if raw and is_equity_symbol(raw):
            result.append(raw)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch fundamentals & ratios for watchlist/portfolio symbols."
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated symbol keys, e.g. US:AAPL,US:MSFT. Default: watchlist+portfolio from DB",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=200,
        help="Maximum number of symbols to process (default: 200)",
    )
    parser.add_argument(
        "--quarters",
        type=int,
        default=8,
        help="Max quarterly periods to upsert per symbol (default: 8)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay seconds between Yahoo Finance calls (default: 0.5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print symbols that would be fetched without making API calls or DB writes",
    )
    args = parser.parse_args()

    conn = psycopg.connect(DB_CONNINFO)
    processed = saved = failed = no_data = 0

    try:
        ensure_schema(conn)

        if args.symbols:
            symbols = parse_explicit_symbols(args.symbols)
        else:
            symbols = collect_watchlist_portfolio_symbols(conn)

        if args.max_symbols and args.max_symbols > 0:
            symbols = symbols[: args.max_symbols]

        total = len(symbols)
        print(
            f"[FUND] targets={total}, quarters={args.quarters}, "
            f"delay={args.delay}s, dry_run={args.dry_run}"
        )

        if total == 0:
            print("[FUND] No symbols found. Add stocks to watchlist or portfolio.")
            return 0

        for idx, raw_symbol in enumerate(symbols, 1):
            processed += 1
            try:
                if args.dry_run:
                    print(f"[DRY-RUN] {idx}/{total} {raw_symbol}: would fetch")
                    continue

                rows = fetch_quarterly_fundamentals(
                    raw_symbol,
                    max_quarters=args.quarters,
                    include_snapshot=False,
                )

                if not rows:
                    no_data += 1
                    print(f"[{idx}/{total}] {raw_symbol}: no quarterly fundamentals available")
                else:
                    for row in rows:
                        upsert_fundamental(conn, raw_symbol, row)
                    upsert_comparison_latest(conn, raw_symbol, rows)

                    ratio = fetch_ratio_snapshot(raw_symbol)
                    upsert_ratios_latest(conn, raw_symbol, ratio)

                    conn.commit()
                    saved += len(rows)
                    latest = rows[0]
                    print(
                        f"[{idx}/{total}] {raw_symbol}: rows={len(rows)} "
                        f"latest={latest['period_end_date']} "
                        f"eps={latest['eps']} revenue={latest['revenue']}"
                    )

            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"[{idx}/{total}] {raw_symbol}: FAILED ({e})")

            if idx % 10 == 0:
                print(f"[PROGRESS] {idx}/{total} completed")

            time.sleep(max(args.delay, 0))

        print("=" * 60)
        print(
            f"[DONE] processed={processed} saved_rows={saved} "
            f"no_data={no_data} failed={failed}"
        )
        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
