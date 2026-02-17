from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

# Add backend root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.prediction_service import PredictionService


def main() -> int:
    ticker = "NVDA"
    as_of = date.today().isoformat()

    svc = PredictionService()

    print("[SMOKE] forecast")
    pred = svc.predict(
        ticker=ticker,
        as_of_date=as_of,
        flat_band_pct=2.0,
        horizon_trading_days=15,
        calibration=os.environ.get("PRED_CALIBRATION", "sigmoid"),
    )
    _assert_db_only(pred, "forecast")
    _print_db_meta(pred, "forecast")
    print(json.dumps(pred, indent=2, ensure_ascii=False))

    print("\n[SMOKE] backtest")
    bt_base = svc.backtest(
        ticker=ticker,
        as_of_date=as_of,
        flat_band_pct=2.0,
        horizon_trading_days=15,
        calibration=os.environ.get("PRED_CALIBRATION", "sigmoid"),
    )
    _assert_db_only(bt_base, "backtest")
    _print_db_meta(bt_base, "backtest")
    print(json.dumps(bt_base, indent=2, ensure_ascii=False))

    best_band = bt_base.get("best_flat_band_pct", 2.0)
    print(f"\n[SMOKE] backtest (best_band={best_band})")
    bt_best = svc.backtest(
        ticker=ticker,
        as_of_date=as_of,
        flat_band_pct=float(best_band),
        horizon_trading_days=15,
        calibration=os.environ.get("PRED_CALIBRATION", "sigmoid"),
    )
    _assert_db_only(bt_best, "backtest_best")
    _print_db_meta(bt_best, "backtest_best")
    print(json.dumps(bt_best, indent=2, ensure_ascii=False))
    return 0


def _assert_db_only(payload: dict, label: str) -> None:
    meta = payload.get("meta", {})
    sources = meta.get("sources", {})
    src_text = json.dumps(sources, ensure_ascii=False).lower()
    if "db:price_daily" not in src_text:
        raise RuntimeError(f"[{label}] DB source is missing in meta.sources: {sources}")
    banned = ["yfinance", "http", "sample_csv"]
    for token in banned:
        if token in src_text:
            raise RuntimeError(f"[{label}] Non-DB source detected in meta.sources: {sources}")


def _print_db_meta(payload: dict, label: str) -> None:
    meta = payload.get("meta", {})
    print(f"\n[{label}] symbols={meta.get('db_symbols_used')}")
    print(f"[{label}] sources={meta.get('sources')}")
    print(f"[{label}] origins={meta.get('origins')}")
    print(f"[{label}] db_freshness={meta.get('db_freshness')}")


if __name__ == "__main__":
    raise SystemExit(main())
