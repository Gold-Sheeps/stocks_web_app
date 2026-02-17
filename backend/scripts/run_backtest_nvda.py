from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

# Add backend root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.prediction_service import PredictionService


@dataclass
class SymbolFreshness:
    symbol: str
    max_date: str | None
    rows: int
    status: str = "OK"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run DB-only NVDA backtest and save metrics JSON."
    )
    parser.add_argument("--asof", required=True, help="As-of date (YYYY-MM-DD).")
    parser.add_argument("--start", required=True, help="Backtest start date (YYYY-MM-DD).")
    parser.add_argument("--end", required=True, help="Backtest end date (YYYY-MM-DD).")
    parser.add_argument("--horizon", type=int, default=15, help="Horizon in trading days.")
    parser.add_argument("--flat_band", type=float, default=2.0, help="Flat band in percent.")
    parser.add_argument(
        "--calibration",
        default="sigmoid",
        choices=["sigmoid", "isotonic"],
        help="Calibration method.",
    )
    parser.add_argument(
        "--db-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run DB freshness checks before backtest (default: enabled).",
    )
    parser.add_argument(
        "--target_coverage",
        type=float,
        default=0.8,
        help="Target interval coverage for split conformal (default: 0.8).",
    )
    return parser.parse_args()


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value}") from exc


def _fetch_symbol_freshness(symbol: str, as_of: date) -> SymbolFreshness:
    db = Database()
    q = """
        SELECT COUNT(*), MAX(trading_date)
        FROM price_daily
        WHERE symbol_key = %s AND trading_date <= %s
    """
    try:
        if not db.connect():
            raise RuntimeError("Failed to connect DB for freshness check.")
        rows = db.execute_query(q, (symbol, as_of))
        if not rows:
            return SymbolFreshness(symbol=symbol, max_date=None, rows=0)
        count, max_dt = rows[0]
        return SymbolFreshness(
            symbol=symbol,
            max_date=max_dt.isoformat() if max_dt is not None else None,
            rows=int(count or 0),
        )
    finally:
        db.disconnect()


def _run_db_check(as_of: date) -> Dict[str, Any]:
    symbols = ["US:NVDA", "US:QQQ", "US:^SOX", "US:SMH"]
    checks: List[SymbolFreshness] = []
    warnings: List[str] = []

    for sym in symbols:
        checks.append(_fetch_symbol_freshness(sym, as_of))

    nvda = next((c for c in checks if c.symbol == "US:NVDA"), None)
    effective_as_of = nvda.max_date if nvda and nvda.max_date else as_of.isoformat()
    is_trading_day = effective_as_of == as_of.isoformat()

    for c in checks:
        if c.max_date is None:
            c.status = "WARN"
            warnings.append(f"{c.symbol}: no rows up to as_of={as_of.isoformat()}.")
            continue
        if c.max_date < effective_as_of:
            c.status = "WARN"
            warnings.append(
                f"{c.symbol}: max_date {c.max_date} is older than effective_as_of {effective_as_of}."
            )
        else:
            c.status = "OK"

    max_dates = [c.max_date for c in checks if c.max_date is not None]
    return {
        "db_table": "price_daily",
        "as_of_date": as_of.isoformat(),
        "effective_as_of": effective_as_of,
        "is_trading_day": is_trading_day,
        "symbols": [c.symbol for c in checks],
        "max_date": max(max_dates) if max_dates else None,
        "rows": int(sum(c.rows for c in checks)),
        "by_symbol": {
            c.symbol: {"max_date": c.max_date, "rows": c.rows, "status": c.status}
            for c in checks
        },
        "warnings": warnings,
    }


def main() -> int:
    args = _parse_args()

    as_of = _parse_iso_date(args.asof, "asof")
    start = _parse_iso_date(args.start, "start")
    end = _parse_iso_date(args.end, "end")
    if start > end:
        raise ValueError(f"start must be <= end: {start} > {end}")
    if as_of != end:
        print(
            f"[WARN] asof ({as_of.isoformat()}) != end ({end.isoformat()}); "
            "backtest uses asof for leak-safe cutoff."
        )

    db_check = _run_db_check(as_of) if args.db_check else None
    if db_check:
        print("[DB CHECK] price_daily freshness (trade_date <= as_of)")
        print(f"  as_of_date={db_check['as_of_date']}")
        print(f"  effective_as_of={db_check['effective_as_of']}")
        print(f"  is_trading_day={db_check['is_trading_day']}")
        for sym, detail in db_check["by_symbol"].items():
            print(
                f"  - {sym}: max_date={detail['max_date']} rows={detail['rows']} status={detail['status']}"
            )
        for w in db_check["warnings"]:
            print(f"[WARN] {w}")

    svc = PredictionService()
    bt_payload = svc.backtest(
        ticker="NVDA",
        as_of_date=as_of.isoformat(),
        flat_band_pct=args.flat_band,
        horizon_trading_days=args.horizon,
        calibration=args.calibration,
        target_interval_coverage=args.target_coverage,
        include_band_sweep=True,
    )

    backtest = bt_payload.get("backtest", {})
    metrics = {
        "accuracy": backtest.get("accuracy"),
        "macro_f1": backtest.get("macro_f1"),
        "logloss": backtest.get("logloss"),
        "brier": backtest.get("brier"),
        "ece": backtest.get("ece"),
        "interval_target_coverage": backtest.get("interval_target_coverage"),
        "interval_q": backtest.get("interval_q"),
        "interval_width_mean": backtest.get("interval_width_mean"),
        "interval_coverage": backtest.get("interval_coverage"),
    }
    diagnostics = {
        "class_ratio": backtest.get("class_ratio"),
        "confusion_matrix": backtest.get("confusion_matrix"),
        "class_metrics": backtest.get("class_metrics"),
    }

    service_meta = bt_payload.get("meta", {})
    output = {
        "ticker": "US:NVDA",
        "period": {"start": start.isoformat(), "end": end.isoformat(), "as_of": as_of.isoformat()},
        "metrics": metrics,
        "diagnostics": diagnostics,
        "meta": {
            "sources": service_meta.get("sources", {}),
            "origins": service_meta.get("origins", {}),
            "db_freshness": db_check if db_check is not None else service_meta.get("db_freshness", {}),
            "as_of_date": as_of.isoformat(),
            "effective_as_of": (db_check or {}).get("effective_as_of", as_of.isoformat()),
            "is_trading_day": (db_check or {}).get("is_trading_day", True),
            "target_name": backtest.get("interval_target_name", "close_future"),
            "target_unit": backtest.get("interval_target_unit", "price"),
            "run_params": {
                "as_of": as_of.isoformat(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "horizon": args.horizon,
                "flat_band": args.flat_band,
                "model": service_meta.get("model_version"),
                "calibration": args.calibration,
                "feature_set": service_meta.get("feature_set_used"),
                "target_coverage": args.target_coverage,
                "target_interval_coverage": args.target_coverage,
                "ensemble_seeds": [11, 29],
            },
        },
        "best_flat_band_pct": bt_payload.get("best_flat_band_pct"),
        "band_sweep": bt_payload.get("band_sweep", []),
    }

    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"backtest_nvda_{as_of.strftime('%Y%m%d')}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("")
    print("[BACKTEST SUMMARY]")
    print(f"  ticker: {output['ticker']}")
    print(f"  as_of: {as_of.isoformat()}  period: {start.isoformat()} -> {end.isoformat()}")
    print(f"  effective_as_of: {output['meta']['effective_as_of']}")
    print(f"  accuracy: {metrics['accuracy']:.4f}")
    print(f"  macro_f1: {metrics['macro_f1']:.4f}")
    print(f"  logloss: {metrics['logloss']:.4f}")
    print(f"  brier: {metrics['brier']:.4f}")
    print(f"  ece: {metrics['ece']:.4f}")
    print(
        f"  interval_target_coverage: {metrics['interval_target_coverage']:.4f}  "
        f"interval_coverage: {metrics['interval_coverage']:.4f}"
    )
    print(f"  interval_width_mean: {metrics['interval_width_mean']:.4f}")
    print(f"  json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
