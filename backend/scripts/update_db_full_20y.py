from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.data_service import DataService
from app.services.indicator_service import IndicatorService
from app.services.rs_service import RsService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh 20-year market data and run all DB-side computations in one command."
    )
    parser.add_argument("--years", type=int, default=20, help="How many years to backfill (default: 20).")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today).")
    parser.add_argument(
        "--symbols",
        default=None,
        help='Optional comma-separated symbol keys. Example: "US:NVDA,US:QQQ".',
    )
    parser.add_argument(
        "--include-inactive",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include inactive instruments when loading symbols from DB.",
    )
    parser.add_argument(
        "--include-existing-price-keys",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Union symbols with existing keys in price_daily (default: enabled).",
    )
    parser.add_argument("--source", default="yfinance", choices=["yfinance"], help="Price refresh source.")
    parser.add_argument("--chunk-size", type=int, default=200, help="Symbols per refresh batch.")
    parser.add_argument("--max-retries", type=int, default=3, help="Retry count passed to refresh script.")
    parser.add_argument(
        "--batch-retries",
        type=int,
        default=2,
        help="Retry count for failed refresh batches in this orchestrator (default: 2).",
    )
    parser.add_argument(
        "--skip-indicators",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip indicator_daily calculation.",
    )
    parser.add_argument(
        "--skip-rs",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip rs_ratings update.",
    )
    parser.add_argument(
        "--skip-sector-rotation",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip sector_rotation calculation.",
    )
    parser.add_argument(
        "--run-full-refresh",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also run scripts/full_db_refresh.py for symbol master/fundamentals/CANSLIM pipeline.",
    )
    parser.add_argument(
        "--full-refresh-backfill-days",
        type=int,
        default=14,
        help="backfill-days passed to full_db_refresh.py (default: 14).",
    )
    parser.add_argument(
        "--full-refresh-delay",
        type=float,
        default=0.5,
        help="delay passed to full_db_refresh.py (default: 0.5).",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip fundamentals update in full_db_refresh.py.",
    )
    parser.add_argument(
        "--skip-canslim",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip CANSLIM batch in full_db_refresh.py.",
    )
    return parser.parse_args()


def _parse_date(value: Optional[str]) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _start_date_from_years(end_date: date, years: int) -> date:
    target_year = end_date.year - years
    try:
        return end_date.replace(year=target_year)
    except ValueError:
        return end_date.replace(year=target_year, month=2, day=28)


def _normalize_symbol_key(raw: str) -> str:
    s = str(raw).strip().upper()
    if not s:
        return s
    return s if ":" in s else f"US:{s}"


def _load_symbols_from_db(include_inactive: bool, include_existing_price_keys: bool) -> List[str]:
    db = Database()
    if not db.connect():
        raise RuntimeError("DB connect failed while loading symbols.")
    out: Set[str] = set()
    try:
        if include_inactive:
            q_inst = "SELECT DISTINCT symbol_key FROM instruments WHERE symbol_key IS NOT NULL"
            rows = db.execute_query(q_inst) or []
        else:
            q_inst = (
                "SELECT DISTINCT symbol_key FROM instruments "
                "WHERE is_active = true AND symbol_key IS NOT NULL"
            )
            rows = db.execute_query(q_inst) or []
        for r in rows:
            out.add(_normalize_symbol_key(r[0]))

        if include_existing_price_keys:
            q_price = "SELECT DISTINCT symbol_key FROM price_daily WHERE symbol_key IS NOT NULL"
            rows_p = db.execute_query(q_price) or []
            for r in rows_p:
                out.add(_normalize_symbol_key(r[0]))
    finally:
        db.disconnect()
    return sorted([s for s in out if s])


def _chunk(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _run_refresh_batches(
    symbols: List[str],
    start_date: date,
    end_date: date,
    source: str,
    chunk_size: int,
    max_retries: int,
    batch_retries: int,
) -> Dict[str, int]:
    script_path = Path(__file__).resolve().parent / "refresh_market_data.py"
    batches = _chunk(symbols, max(1, int(chunk_size)))
    success = 0
    failed = 0
    retried_batches = 0
    total_attempts = 0
    for idx, group in enumerate(batches, start=1):
        symbols_csv = ",".join(group)
        cmd = [
            sys.executable,
            str(script_path),
            "--symbols",
            symbols_csv,
            "--start",
            start_date.isoformat(),
            "--end",
            end_date.isoformat(),
            "--source",
            source,
            "--max-retries",
            str(max_retries),
            "--force-start",
        ]
        max_batch_attempts = max(1, int(batch_retries) + 1)
        batch_ok = False

        for attempt in range(1, max_batch_attempts + 1):
            total_attempts += 1
            if attempt == 1:
                print(f"[REFRESH] batch {idx}/{len(batches)} symbols={len(group)}")
            else:
                retried_batches += 1
                print(
                    f"[REFRESH][RETRY] batch {idx}/{len(batches)} attempt {attempt}/{max_batch_attempts}"
                )

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.stdout:
                print(proc.stdout.rstrip())

            if proc.returncode == 0:
                success += 1
                batch_ok = True
                break

            print(f"[REFRESH][ERROR] batch {idx} failed (exit={proc.returncode})")
            if proc.stderr:
                print(proc.stderr.rstrip())
            if attempt < max_batch_attempts:
                # Short exponential backoff between batch retries.
                time.sleep(float(2 ** (attempt - 1)))

        if not batch_ok:
            failed += 1
    return {
        "batches_total": len(batches),
        "batches_success": success,
        "batches_failed": failed,
        "batches_retried": retried_batches,
        "batch_attempts_total": total_attempts,
    }


def _run_indicators(symbols: List[str]) -> Dict[str, int]:
    svc = IndicatorService()
    processed = 0
    failed = 0
    for idx, symbol_key in enumerate(symbols, start=1):
        try:
            print(f"[IND] {idx}/{len(symbols)} {symbol_key}")
            svc.calculate_and_save_indicators(symbol_key)
            processed += 1
        except Exception as exc:
            failed += 1
            print(f"[IND][ERROR] {symbol_key}: {exc}")
    return {"processed": processed, "failed": failed}


def _run_rs() -> None:
    svc = RsService()
    svc.update_rs_ratings()


def _run_sector_rotation() -> None:
    svc = DataService()
    svc.calculate_sector_rotation()


def _run_full_refresh_pipeline(
    backfill_days: int,
    delay: float,
    skip_indicators: bool,
    skip_rs: bool,
    skip_fundamentals: bool,
    skip_canslim: bool,
) -> Dict[str, str | int]:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "full_db_refresh.py"
    if not script_path.exists():
        return {"status": "missing", "returncode": -1, "message": f"script not found: {script_path}"}
    cmd = [
        sys.executable,
        str(script_path),
        "--backfill-days",
        str(int(backfill_days)),
        "--delay",
        str(float(delay)),
    ]
    if skip_indicators:
        cmd.append("--skip-indicators")
    if skip_rs:
        cmd.append("--skip-rs")
    if skip_fundamentals:
        cmd.append("--skip-fundamentals")
    if skip_canslim:
        cmd.append("--skip-canslim")
    print(f"[FULL PIPELINE] {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.rstrip())
        return {"status": "failed", "returncode": int(proc.returncode), "message": "full_db_refresh failed"}
    return {"status": "ok", "returncode": 0, "message": "full_db_refresh completed"}


def main() -> int:
    args = _parse_args()
    end_date = _parse_date(args.end)
    start_date = _start_date_from_years(end_date, int(args.years))

    if args.symbols:
        symbols = sorted({_normalize_symbol_key(s) for s in str(args.symbols).split(",") if s.strip()})
    else:
        symbols = _load_symbols_from_db(
            include_inactive=bool(args.include_inactive),
            include_existing_price_keys=bool(args.include_existing_price_keys),
        )
    if not symbols:
        raise RuntimeError("No symbols resolved. Check instruments/price_daily or pass --symbols explicitly.")

    print("[RUN CONFIG]")
    print(f"  symbols={len(symbols)}")
    print(f"  period={start_date.isoformat()} -> {end_date.isoformat()} (years={args.years})")
    print(f"  source={args.source}")
    print(f"  chunk_size={args.chunk_size} max_retries={args.max_retries}")
    print(f"  batch_retries={args.batch_retries}")
    print(f"  skip_indicators={args.skip_indicators}")
    print(f"  skip_rs={args.skip_rs}")
    print(f"  skip_sector_rotation={args.skip_sector_rotation}")
    print(f"  run_full_refresh={args.run_full_refresh}")
    print(f"  skip_fundamentals={args.skip_fundamentals}")
    print(f"  skip_canslim={args.skip_canslim}")

    refresh_result = _run_refresh_batches(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        source=args.source,
        chunk_size=args.chunk_size,
        max_retries=args.max_retries,
        batch_retries=int(args.batch_retries),
    )

    indicators_result = {"processed": 0, "failed": 0}
    if not args.skip_indicators:
        indicators_result = _run_indicators(symbols)

    rs_status = "skipped"
    if not args.skip_rs:
        _run_rs()
        rs_status = "ok"

    rotation_status = "skipped"
    if not args.skip_sector_rotation:
        _run_sector_rotation()
        rotation_status = "ok"

    full_refresh_result: Dict[str, str | int] = {"status": "skipped", "returncode": 0, "message": "skipped"}
    if args.run_full_refresh:
        full_refresh_result = _run_full_refresh_pipeline(
            backfill_days=int(args.full_refresh_backfill_days),
            delay=float(args.full_refresh_delay),
            skip_indicators=bool(args.skip_indicators),
            skip_rs=bool(args.skip_rs),
            skip_fundamentals=bool(args.skip_fundamentals),
            skip_canslim=bool(args.skip_canslim),
        )

    payload = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat(), "years": int(args.years)},
        "symbols_count": len(symbols),
        "refresh": refresh_result,
        "indicators": indicators_result,
        "rs_update": rs_status,
        "sector_rotation": rotation_status,
        "full_refresh_pipeline": full_refresh_result,
        "args": vars(args),
    }
    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"update_db_full_20y_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("[FULL UPDATE SUMMARY]")
    print(
        "  refresh batches: {ok}/{all} (failed={ng}, retried={rt}, attempts={att})".format(
            ok=refresh_result["batches_success"],
            all=refresh_result["batches_total"],
            ng=refresh_result["batches_failed"],
            rt=refresh_result.get("batches_retried", 0),
            att=refresh_result.get("batch_attempts_total", refresh_result["batches_total"]),
        )
    )
    print(
        "  indicators: processed={p} failed={f}".format(
            p=indicators_result["processed"],
            f=indicators_result["failed"],
        )
    )
    print(f"  rs_update={rs_status} sector_rotation={rotation_status}")
    print(
        "  full_refresh_pipeline={st} rc={rc}".format(
            st=full_refresh_result.get("status"),
            rc=full_refresh_result.get("returncode"),
        )
    )
    print(f"  json: {out_path}")

    if refresh_result["batches_failed"] > 0:
        return 1
    if str(full_refresh_result.get("status")) == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
