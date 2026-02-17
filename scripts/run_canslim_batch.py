"""
Lightweight CANSLIM batch runner for Data Update.

Outputs:
- rs_ranking_result.csv
- canslim_breakout_report.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import traceback

import pandas as pd
import psycopg


ROOT = Path(__file__).resolve().parents[1]
RS_DIR = ROOT / "src" / "rs_rating"
if str(RS_DIR) not in sys.path:
    sys.path.insert(0, str(RS_DIR))

import canslim_entory_point  # noqa: E402


def run(limit: int, min_rs: int) -> int:
    conn = psycopg.connect(
        "host=localhost port=5432 dbname=postgres user=postgres password=test"
    )
    rows: list[dict] = []
    rs_rows: list[tuple] = []

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol_key, rating
                FROM rs_ratings
                WHERE symbol_key LIKE 'US:%%' AND rating >= %s
                ORDER BY rating DESC, symbol_key ASC
                LIMIT %s
                """,
                (min_rs, limit),
            )
            rs_rows = cur.fetchall() or []

            if not rs_rows:
                print("[CANSLIM] No RS rows found. Generating empty report.")
                out = ROOT / "canslim_breakout_report.csv"
                pd.DataFrame([]).to_csv(out, index=False, encoding="utf-8-sig")
                return 0

            for symbol_key, rs_rating in rs_rows:
                cur.execute(
                    """
                    SELECT trading_date, high, close, volume
                    FROM price_daily
                    WHERE symbol_key = %s
                    ORDER BY trading_date DESC
                    LIMIT 320
                    """,
                    (symbol_key,),
                )
                price_rows = cur.fetchall() or []
                if not price_rows:
                    continue

                df = pd.DataFrame(price_rows, columns=["Date", "High", "Close", "Volume"])
                df = df.sort_values("Date")

                result = canslim_entory_point.check_breakout_candidate(df, ticker_symbol=symbol_key)
                if not isinstance(result, dict) or "error" in result:
                    continue

                rows.append(
                    {
                        "Ticker": symbol_key.split(":", 1)[1],
                        "ApiTicker": symbol_key,
                        "RS_Rating": int(rs_rating),
                        "base_break": bool(result.get("base_break", False)),
                        "within_buy_range": bool(result.get("within_buy_range", False)),
                        "volume_up_on_break": bool(result.get("volume_up_on_break", False)),
                        "pivot": result.get("pivot"),
                        "buy_upper": result.get("buy_upper"),
                        "current_price": result.get("current_price"),
                        "vol_ratio": result.get("vol_ratio"),
                        "high_52w": result.get("high_52w"),
                        "near_high": result.get("near_high"),
                    }
                )
    finally:
        conn.close()

    report = pd.DataFrame(rows).sort_values(["RS_Rating", "Ticker"], ascending=[False, True]) if rows else pd.DataFrame([])
    out = ROOT / "canslim_breakout_report.csv"
    report.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[CANSLIM] Breakout report saved: {out} rows={len(report)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CANSLIM batch for Data Update.")
    parser.add_argument("--limit", type=int, default=200, help="Max number of top RS symbols to analyze.")
    parser.add_argument("--min-rs", type=int, default=80, help="Minimum RS rating threshold.")
    args = parser.parse_args()

    try:
        return run(limit=args.limit, min_rs=args.min_rs)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
