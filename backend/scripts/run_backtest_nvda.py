from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

# Add backend root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.prediction_service import PredictionService


@dataclass
class SymbolFreshness:
    symbol: str
    min_date: str | None
    max_date: str | None
    rows: int
    status: str = "OK"


def _normalize_ticker_inputs(ticker: str) -> tuple[str, str]:
    t = str(ticker).strip().upper()
    if ":" in t:
        symbol_key = t
        service_ticker = t.split(":", 1)[1]
    else:
        service_ticker = t
        symbol_key = f"US:{t}"
    return service_ticker, symbol_key


def _safe_ticker_slug(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(ticker).strip().lower()) or "ticker"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run DB-only single-ticker backtest and save metrics JSON."
    )
    parser.add_argument("--asof", required=True, help="As-of date (YYYY-MM-DD).")
    parser.add_argument("--ticker", default="US:AAPL", help="Target ticker (default: US:AAPL).")
    parser.add_argument("--start", help="Backtest start date (YYYY-MM-DD).")
    parser.add_argument("--end", help="Backtest end date (YYYY-MM-DD).")
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
    parser.add_argument(
        "--lookback",
        default="5y",
        help="Required lookback window for preflight (e.g. 5y, 1825d). Default: 5y.",
    )
    parser.add_argument(
        "--auto-fill-missing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="If enabled, fetch only missing DB ranges then continue (default: disabled).",
    )
    parser.add_argument(
        "--preflight-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run preflight only and exit without refresh/backtest.",
    )
    parser.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable faster backtest defaults (shorter window, fewer folds, no band sweep).",
    )
    parser.add_argument(
        "--max-folds",
        type=int,
        default=None,
        help="Upper bound of walk-forward folds.",
    )
    parser.add_argument(
        "--fold-step",
        type=int,
        default=None,
        help="Walk-forward step size in trading days.",
    )
    parser.add_argument(
        "--train-window",
        type=int,
        default=None,
        help="Training window size in trading days for each fold.",
    )
    parser.add_argument(
        "--no-band-sweep",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip best flat-band sweep.",
    )
    parser.add_argument(
        "--no-ensemble",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use a single classifier seed in walk-forward backtest.",
    )
    parser.add_argument(
        "--purge-days",
        type=int,
        default=None,
        help="Purged CV gap in trading days. Default: horizon days.",
    )
    parser.add_argument(
        "--embargo-mode",
        default="pct",
        choices=["pct", "days"],
        help="Embargo mode: pct (default, n*embargo-pct) or days (fixed embargo-days).",
    )
    parser.add_argument(
        "--embargo-days",
        type=int,
        default=5,
        help="Fixed embargo gap in trading days (used when --embargo-mode days).",
    )
    parser.add_argument(
        "--embargo-pct",
        type=float,
        default=0.01,
        help="Embargo ratio of dataset length (used when --embargo-mode pct).",
    )
    parser.add_argument(
        "--prob-mode",
        default="meta",
        choices=["direct", "meta"],
        help="Probability mode for 3-class output (default: meta).",
    )
    parser.add_argument(
        "--temp-scale",
        default="on",
        choices=["on", "off", "global", "fold"],
        help="Temperature scaling mode: on|global|fold|off (default: on).",
    )
    parser.add_argument(
        "--meta-band-mode",
        default="fixed",
        choices=["fixed", "quantile"],
        help="Meta-label band mode (default: fixed).",
    )
    parser.add_argument(
        "--meta-band-q",
        type=float,
        default=0.65,
        help="Quantile target for meta band in quantile mode (default: 0.65).",
    )
    parser.add_argument(
        "--meta-band-min-pct",
        type=float,
        default=0.5,
        help="Lower clip for meta band percent (default: 0.5).",
    )
    parser.add_argument(
        "--meta-band-max-pct",
        type=float,
        default=10.0,
        help="Upper clip for meta band percent (default: 10.0).",
    )
    parser.add_argument(
        "--label-mode",
        default="fixed",
        choices=["fixed", "tbm", "up5_2w"],
        help="Label generation mode (default: fixed).",
    )
    parser.add_argument(
        "--target-return",
        type=float,
        default=None,
        help="Positive target return threshold (fraction) for up5_2w mode (default: 0.05).",
    )
    parser.add_argument(
        "--tbm-vol-span",
        type=int,
        default=20,
        help="EWMA span for TBM volatility estimate (default: 20).",
    )
    parser.add_argument(
        "--tbm-k",
        type=float,
        default=1.5,
        help="TBM barrier multiplier k (default: 1.5).",
    )
    parser.add_argument(
        "--save-run-args-full",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Save full CLI/effective run args to output JSON metadata (default: disabled).",
    )
    parser.add_argument(
        "--feature-set",
        default="v2_ohlcv_ta_relregime_event",
        choices=["v2_ohlcv_ta_relregime_event", "v3_ohlcv_ta_relregime_event_fundflow"],
        help="Feature set version to use (default: v2_ohlcv_ta_relregime_event).",
    )
    parser.add_argument(
        "--regime-symbols",
        default="US:QQQ,US:SPY",
        help='Regime symbols list (comma-separated). Example: "US:QQQ,US:SPY".',
    )
    parser.add_argument(
        "--relative-symbols",
        default="US:QQQ",
        help='Relative strength symbols list (comma-separated). Example: "US:QQQ".',
    )
    parser.add_argument(
        "--sector-symbols",
        default=None,
        help='Sector symbols list (comma-separated). Example: "US:^SOX,US:SMH". Empty string disables.',
    )
    parser.add_argument(
        "--class-weight-mode",
        default="off",
        choices=["off", "balanced", "custom"],
        help="Class weighting mode for down/flat/up (default: off).",
    )
    parser.add_argument(
        "--class-weight",
        default=None,
        help='Custom class weights. Example: "down=3.0,flat=1.0,up=1.0"',
    )
    parser.add_argument(
        "--threshold-mode",
        default="argmax",
        choices=["argmax", "threshold"],
        help="Final class decision mode (default: argmax).",
    )
    parser.add_argument("--t-down", type=float, default=None, help="Down threshold for threshold-mode.")
    parser.add_argument("--t-up", type=float, default=None, help="Up threshold for threshold-mode.")
    parser.add_argument(
        "--threshold-search",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Search (t_down,t_up) on CV evaluation predictions (default: disabled).",
    )
    parser.add_argument(
        "--down-recall-min",
        type=float,
        default=0.10,
        help="Constraint for threshold-search: minimum down recall (default: 0.10).",
    )
    parser.add_argument(
        "--pred-down-min-pct",
        type=float,
        default=0.05,
        help="Constraint for threshold-search: minimum predicted down ratio (default: 0.05).",
    )
    parser.add_argument(
        "--threshold-grid",
        default=None,
        help='Optional threshold grid format: "down=0.2,0.3;up=0.4,0.5".',
    )
    parser.add_argument(
        "--t-buy-grid",
        default=None,
        help='Optional BUY-threshold grid for up5_2w mode. Format: "0.50,0.55,...".',
    )
    parser.add_argument(
        "--fixed-thresholds",
        default=None,
        help='Fixed BUY thresholds for up5_2w diagnostics. Format: "0.5,0.6,0.7,0.75,0.8".',
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.05,
        help="Minimum BUY coverage constraint for up5_2w threshold search (default: 0.05).",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=50,
        help="Minimum BUY trades constraint for up5_2w threshold search (default: 50).",
    )
    parser.add_argument(
        "--apply-best-band",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run band sweep first and apply best_flat_band_pct to final evaluation (default: disabled).",
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help="Optional output JSON filename (saved under backend/ml_predictor_data).",
    )
    return parser.parse_args()


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value}") from exc


def _parse_class_weight_map(raw: str | None) -> Dict[str, float] | None:
    if raw is None:
        return None
    out: Dict[str, float] = {}
    for tok in str(raw).split(","):
        s = tok.strip()
        if not s:
            continue
        if "=" not in s:
            raise ValueError(f"class-weight token must be key=value: {s}")
        k, v = s.split("=", 1)
        key = k.strip().lower()
        if key not in ("down", "flat", "up"):
            raise ValueError(f"class-weight key must be down|flat|up: {key}")
        out[key] = float(v.strip())
    if not out:
        return None
    for key in ("down", "flat", "up"):
        out.setdefault(key, 1.0)
    return out


def _parse_threshold_grid(raw: str | None) -> Dict[str, List[float]] | None:
    if raw is None:
        return None
    out: Dict[str, List[float]] = {}
    for part in str(raw).split(";"):
        s = part.strip()
        if not s:
            continue
        if "=" not in s:
            raise ValueError(f"threshold-grid token must be key=list: {s}")
        key, vals = s.split("=", 1)
        k = key.strip().lower()
        if k not in ("down", "up"):
            raise ValueError(f"threshold-grid key must be down|up: {k}")
        arr = [float(x.strip()) for x in vals.split(",") if x.strip()]
        if not arr:
            raise ValueError(f"threshold-grid {k} must not be empty.")
        out[k] = arr
    return out if out else None


def _parse_t_buy_grid(raw: str | None) -> List[float] | None:
    if raw is None:
        return None
    vals = [float(x.strip()) for x in str(raw).split(",") if x.strip()]
    if not vals:
        raise ValueError("t-buy-grid must not be empty.")
    for v in vals:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"t-buy-grid value out of range [0,1]: {v}")
    return vals


def _parse_fixed_thresholds(raw: str | None) -> List[float]:
    if raw is None:
        vals = [0.5, 0.6, 0.7, 0.75, 0.8]
    else:
        vals = [float(x.strip()) for x in str(raw).split(",") if x.strip()]
        if not vals:
            raise ValueError("fixed-thresholds must not be empty.")
    for v in vals:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"fixed-thresholds value out of range [0,1]: {v}")
    return vals


def _fixed_threshold_metrics_to_map(rows: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = row.get("t_buy")
        if t is None:
            continue
        key = f"{float(t):g}"
        out[key] = {
            "precision": row.get("precision", row.get("win_rate")),
            "coverage": row.get("coverage"),
            "n_trades": row.get("n_trades"),
            "avg_return": row.get("avg_return"),
            "confusion_matrix": row.get("confusion_matrix", row.get("confusion_2x2")),
        }
    return out


def _fixed_threshold_keys_to_list(metrics_map: Dict[str, Dict[str, Any]]) -> List[float]:
    vals: List[float] = []
    for k in metrics_map.keys():
        try:
            vals.append(float(k))
        except Exception:
            continue
    return sorted(vals)


def _parse_symbol_list(raw: str | None) -> List[str]:
    if raw is None:
        return []
    out: List[str] = []
    for tok in str(raw).split(","):
        s = tok.strip().upper()
        if not s:
            continue
        if ":" not in s:
            s = f"US:{s}"
        if s not in out:
            out.append(s)
    return out


def _parse_lookback_start(effective_as_of: date, lookback: str) -> date:
    raw = str(lookback).strip().lower()
    if not raw:
        raise ValueError("lookback must not be empty.")

    if raw.endswith("y"):
        years = int(raw[:-1])
        target_year = effective_as_of.year - years
        try:
            return effective_as_of.replace(year=target_year)
        except ValueError:
            # Leap-day handling (e.g. Feb-29 -> Feb-28).
            return effective_as_of.replace(month=2, day=28, year=target_year)
    if raw.endswith("d"):
        days = int(raw[:-1])
        return effective_as_of - timedelta(days=days)
    if raw.isdigit():
        years = int(raw)
        target_year = effective_as_of.year - years
        try:
            return effective_as_of.replace(year=target_year)
        except ValueError:
            return effective_as_of.replace(month=2, day=28, year=target_year)

    raise ValueError(f"Unsupported lookback format: {lookback} (use like 5y or 1825d)")


def _fetch_symbol_freshness(symbol: str, as_of: date) -> SymbolFreshness:
    db = Database()
    q = """
        SELECT COUNT(*), MIN(trading_date), MAX(trading_date)
        FROM price_daily
        WHERE symbol_key = %s AND trading_date <= %s
    """
    try:
        if not db.connect():
            raise RuntimeError("Failed to connect DB for freshness check.")
        rows = db.execute_query(q, (symbol, as_of))
        if not rows:
            return SymbolFreshness(symbol=symbol, min_date=None, max_date=None, rows=0)
        count, min_dt, max_dt = rows[0]
        return SymbolFreshness(
            symbol=symbol,
            min_date=min_dt.isoformat() if min_dt is not None else None,
            max_date=max_dt.isoformat() if max_dt is not None else None,
            rows=int(count or 0),
        )
    finally:
        db.disconnect()


def _run_db_check(
    as_of: date,
    lookback: str,
    horizon: int,
    target_symbol: str = "US:NVDA",
    extra_symbols: List[str] | None = None,
) -> Dict[str, Any]:
    symbols = [target_symbol]
    if extra_symbols:
        symbols.extend(extra_symbols)
    # keep stable order while removing duplicates
    symbols = list(dict.fromkeys(symbols))
    checks: List[SymbolFreshness] = []
    warnings: List[str] = []
    missing_ranges: Dict[str, List[Dict[str, str]]] = {}

    for sym in symbols:
        checks.append(_fetch_symbol_freshness(sym, as_of))

    target = next((c for c in checks if c.symbol == target_symbol), None)
    effective_as_of = target.max_date if target and target.max_date else as_of.isoformat()
    effective_as_of_date = _parse_iso_date(effective_as_of, "effective_as_of")
    required_start = _parse_lookback_start(effective_as_of_date, lookback)
    is_trading_day = effective_as_of == as_of.isoformat()

    for c in checks:
        ranges: List[Dict[str, str]] = []
        min_dt = _parse_iso_date(c.min_date, "min_date") if c.min_date else None
        max_dt = _parse_iso_date(c.max_date, "max_date") if c.max_date else None

        if c.max_date is None:
            c.status = "WARN"
            ranges.append(
                {
                    "from": required_start.isoformat(),
                    "to": effective_as_of,
                    "reason": "all_missing",
                }
            )
            warnings.append(
                f"{c.symbol}: no rows up to effective_as_of={effective_as_of}."
            )
        else:
            if min_dt is not None and min_dt > required_start:
                ranges.append(
                    {
                        "from": required_start.isoformat(),
                        "to": (min_dt - timedelta(days=1)).isoformat(),
                        "reason": "head_missing",
                    }
                )
            if max_dt is not None and max_dt < effective_as_of_date:
                ranges.append(
                    {
                        "from": (max_dt + timedelta(days=1)).isoformat(),
                        "to": effective_as_of,
                        "reason": "tail_missing",
                    }
                )

        if ranges:
            c.status = "WARN"
            missing_ranges[c.symbol] = ranges
            warnings.append(f"{c.symbol}: missing_ranges={ranges}")
        else:
            c.status = "OK"

    max_dates = [c.max_date for c in checks if c.max_date is not None]
    missing_symbols = sorted(missing_ranges.keys())
    return {
        "db_table": "price_daily",
        "as_of_date": as_of.isoformat(),
        "effective_as_of": effective_as_of,
        "required_start": required_start.isoformat(),
        "lookback": lookback,
        "horizon": horizon,
        "is_trading_day": is_trading_day,
        "symbols": [c.symbol for c in checks],
        "max_date": max(max_dates) if max_dates else None,
        "rows": int(sum(c.rows for c in checks)),
        "missing_symbols": missing_symbols,
        "missing_ranges": missing_ranges,
        "by_symbol": {
            c.symbol: {
                "min_date": c.min_date,
                "max_date": c.max_date,
                "rows": c.rows,
                "status": c.status,
                "missing_ranges": missing_ranges.get(c.symbol, []),
            }
            for c in checks
        },
        "warnings": warnings,
    }


def _run_conditional_fill(missing_ranges: Dict[str, List[Dict[str, str]]]) -> bool:
    script_path = Path(__file__).resolve().parents[0] / "refresh_market_data.py"
    all_ok = True

    for symbol, ranges in missing_ranges.items():
        for r in ranges:
            start = r["from"]
            end = r["to"]
            if start > end:
                continue
            cmd = [
                sys.executable,
                str(script_path),
                "--symbols",
                symbol,
                "--start",
                start,
                "--end",
                end,
                "--source",
                "yfinance",
            ]
            reason = r.get("reason", "")
            if reason in ("head_missing", "all_missing"):
                cmd.append("--force-start")

            print(
                f"[AUTO-FILL] refresh {symbol} {start} -> {end} reason={reason or 'unknown'}"
            )
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.stdout:
                print(proc.stdout.rstrip())
            if proc.returncode != 0:
                all_ok = False
                print(
                    f"[ERROR] refresh failed for {symbol} {start}->{end} "
                    f"(exit={proc.returncode})"
                )
                if proc.stderr:
                    print(proc.stderr.rstrip())
    return all_ok


def main() -> int:
    args = _parse_args()
    service_ticker, target_symbol = _normalize_ticker_inputs(args.ticker)
    class_weight_map = _parse_class_weight_map(args.class_weight)
    if args.class_weight_mode == "custom" and class_weight_map is None:
        raise ValueError("--class-weight-mode custom requires --class-weight.")
    threshold_grid = _parse_threshold_grid(args.threshold_grid)
    t_buy_grid = _parse_t_buy_grid(args.t_buy_grid)
    fixed_thresholds = _parse_fixed_thresholds(args.fixed_thresholds)
    regime_symbols = _parse_symbol_list(args.regime_symbols)
    relative_symbols = _parse_symbol_list(args.relative_symbols)
    sector_symbols = _parse_symbol_list(args.sector_symbols)
    if args.sector_symbols is None and target_symbol == "US:NVDA":
        sector_symbols = ["US:^SOX", "US:SMH"]
    ref_symbols = list(dict.fromkeys(regime_symbols + relative_symbols + sector_symbols))
    effective_horizon = int(args.horizon)
    if str(args.label_mode).lower() == "up5_2w" and effective_horizon == 15:
        effective_horizon = 10
    effective_target_return = 0.05 if args.target_return is None else float(args.target_return)

    as_of = _parse_iso_date(args.asof, "asof")
    end = _parse_iso_date(args.end, "end") if args.end else as_of
    start = _parse_iso_date(args.start, "start") if args.start else end
    if start > end:
        raise ValueError(f"start must be <= end: {start} > {end}")
    if as_of != end:
        print(
            f"[WARN] asof ({as_of.isoformat()}) != end ({end.isoformat()}); "
            "backtest uses asof for leak-safe cutoff."
        )

    effective_lookback = args.lookback
    effective_max_folds = args.max_folds
    effective_fold_step = args.fold_step
    effective_train_window = args.train_window
    effective_include_band_sweep = not args.no_band_sweep
    effective_use_ensemble = not args.no_ensemble

    if args.fast:
        if args.lookback == "5y":
            effective_lookback = "2y"
        if effective_max_folds is None:
            effective_max_folds = 8
        if effective_fold_step is None:
            effective_fold_step = 20
        if effective_train_window is None:
            effective_train_window = 504
        effective_include_band_sweep = False
        effective_use_ensemble = False
        if str(args.label_mode).lower() == "up5_2w":
            print("[WARN] fast mode enabled for up5_2w: fold/window reduction may change threshold stability vs non-fast.")
    if args.apply_best_band:
        effective_include_band_sweep = True

    print("[RUN CONFIG]")
    print(f"  preflight_only={args.preflight_only}")
    print(f"  fast={args.fast}")
    print(f"  lookback={effective_lookback}")
    print(f"  max_folds={effective_max_folds}")
    print(f"  fold_step={effective_fold_step}")
    print(f"  train_window={effective_train_window}")
    print(f"  include_band_sweep={effective_include_band_sweep}")
    print(f"  use_ensemble={effective_use_ensemble}")
    print(f"  purge_days={args.purge_days if args.purge_days is not None else effective_horizon}")
    print(f"  embargo_mode={args.embargo_mode}")
    print(f"  embargo_days={args.embargo_days}")
    print(f"  embargo_pct={args.embargo_pct}")
    print(f"  prob_mode={args.prob_mode}")
    print(f"  temp_scale={args.temp_scale}")
    print(f"  meta_band_mode={args.meta_band_mode}")
    print(f"  meta_band_q={args.meta_band_q}")
    print(f"  meta_band_min_pct={args.meta_band_min_pct}")
    print(f"  meta_band_max_pct={args.meta_band_max_pct}")
    print(f"  label_mode={args.label_mode}")
    print(f"  target_return={effective_target_return}")
    print(f"  tbm_vol_span={args.tbm_vol_span}")
    print(f"  tbm_k={args.tbm_k}")
    print(f"  apply_best_band={args.apply_best_band}")
    print(f"  feature_set={args.feature_set}")
    print(f"  regime_symbols={regime_symbols}")
    print(f"  relative_symbols={relative_symbols}")
    print(f"  sector_symbols={sector_symbols}")
    print(f"  class_weight_mode={args.class_weight_mode}")
    print(f"  class_weight={class_weight_map}")
    print(f"  threshold_mode={args.threshold_mode}")
    print(f"  t_down={args.t_down}")
    print(f"  t_up={args.t_up}")
    print(f"  threshold_search={args.threshold_search}")
    print(f"  down_recall_min={args.down_recall_min}")
    print(f"  pred_down_min_pct={args.pred_down_min_pct}")
    print(f"  threshold_grid={threshold_grid}")
    print(f"  t_buy_grid={t_buy_grid}")
    print(f"  fixed_thresholds={fixed_thresholds}")
    print(f"  min_coverage={args.min_coverage}")
    print(f"  min_trades={args.min_trades}")
    print(f"  ticker={service_ticker} ({target_symbol})")

    should_run_preflight = bool(args.db_check or args.auto_fill_missing or args.preflight_only)
    db_check = (
        _run_db_check(
            as_of,
            lookback=effective_lookback,
            horizon=effective_horizon,
            target_symbol=target_symbol,
            extra_symbols=ref_symbols,
        )
        if should_run_preflight
        else None
    )
    if db_check:
        print("[DB CHECK] price_daily freshness + preflight (trade_date <= as_of)")
        print(f"  as_of_date={db_check['as_of_date']}")
        print(f"  effective_as_of={db_check['effective_as_of']}")
        print(f"  required_start={db_check['required_start']} (lookback={db_check['lookback']})")
        print(f"  is_trading_day={db_check['is_trading_day']}")
        for sym, detail in db_check["by_symbol"].items():
            print(
                f"  - {sym}: min_date={detail['min_date']} max_date={detail['max_date']} "
                f"rows={detail['rows']} status={detail['status']}"
            )
            if detail.get("missing_ranges"):
                print(f"    missing_ranges={detail['missing_ranges']}")
        for w in db_check["warnings"]:
            print(f"[WARN] {w}")
        print("[PREFLIGHT JSON]")
        print(
            json.dumps(
                {
                    "missing_symbols": db_check.get("missing_symbols", []),
                    "missing_ranges": db_check.get("missing_ranges", {}),
                },
                ensure_ascii=False,
            )
        )

        if args.preflight_only:
            print("[PRECHECK] preflight-only mode: exit without refresh/backtest.")
            return 0

        missing_symbols = db_check.get("missing_symbols", [])
        missing_ranges = db_check.get("missing_ranges", {})
        if missing_symbols:
            if not args.auto_fill_missing:
                joined = ",".join(missing_symbols)
                eff = db_check["effective_as_of"]
                req_start = db_check["required_start"]
                print("[ERROR] Missing DB data detected. External fetch is disabled.")
                print(
                    "[HINT] Fill only missing symbols/ranges, then rerun. Example:"
                )
                print(
                    "  python backend/scripts/refresh_market_data.py "
                    f'--symbols "{joined}" --start {req_start} --end {eff} --source yfinance'
                )
                print(
                    "  python backend/scripts/run_backtest_nvda.py ... --db-check "
                    f"--ticker {service_ticker} --auto-fill-missing"
                )
                return 1

            print("[AUTO-FILL] Missing DB ranges detected. Starting conditional refresh.")
            ok = _run_conditional_fill(missing_ranges)
            if not ok:
                print("[ERROR] One or more auto-fill refresh jobs failed.")
                return 1

            db_check = _run_db_check(
                as_of,
                lookback=effective_lookback,
                horizon=effective_horizon,
                target_symbol=target_symbol,
                extra_symbols=ref_symbols,
            )
            unresolved = db_check.get("missing_symbols", [])
            if unresolved:
                print("[ERROR] Missing DB ranges remain after auto-fill.")
                print(
                    json.dumps(
                        {
                            "missing_symbols": unresolved,
                            "missing_ranges": db_check.get("missing_ranges", {}),
                        },
                        ensure_ascii=False,
                    )
                )
                return 1
            print("[AUTO-FILL] Missing DB ranges resolved. Continue with DB-only backtest.")

    svc = PredictionService()
    bt_payload = svc.backtest(
        ticker=service_ticker,
        as_of_date=as_of.isoformat(),
        flat_band_pct=args.flat_band,
        horizon_trading_days=effective_horizon,
        calibration=args.calibration,
        target_interval_coverage=args.target_coverage,
        feature_version=args.feature_set,
        include_band_sweep=effective_include_band_sweep,
        max_folds=effective_max_folds,
        fold_step=effective_fold_step,
        train_window=effective_train_window,
        use_ensemble=effective_use_ensemble,
        purge_days=args.purge_days,
        embargo_mode=args.embargo_mode,
        embargo_days=args.embargo_days,
        embargo_pct=args.embargo_pct,
        prob_mode=args.prob_mode,
        temp_scale=args.temp_scale,
        meta_band_mode=args.meta_band_mode,
        meta_band_q=args.meta_band_q,
        meta_band_min_pct=args.meta_band_min_pct,
        meta_band_max_pct=args.meta_band_max_pct,
        label_mode=args.label_mode,
        target_return=effective_target_return,
        tbm_vol_span=args.tbm_vol_span,
        tbm_k=args.tbm_k,
        class_weight_mode=args.class_weight_mode,
        class_weight=class_weight_map,
        threshold_mode=args.threshold_mode,
        t_down=args.t_down,
        t_up=args.t_up,
        threshold_search=args.threshold_search,
        down_recall_min=args.down_recall_min,
        pred_down_min_pct=args.pred_down_min_pct,
        threshold_grid=threshold_grid,
        t_buy_grid=t_buy_grid,
        fixed_thresholds=fixed_thresholds,
        min_coverage=args.min_coverage,
        min_trades=args.min_trades,
        regime_symbols=regime_symbols,
        relative_symbols=relative_symbols,
        sector_symbols=sector_symbols,
    )
    applied_flat_band = float(args.flat_band)
    pre_band_payload = None
    if args.apply_best_band:
        pre_band_payload = bt_payload
        best_band = float(pre_band_payload.get("best_flat_band_pct", args.flat_band))
        print(f"[BEST BAND] sweep best_flat_band_pct={best_band:.4f}; rerun final evaluation with best band.")
        bt_payload = svc.backtest(
            ticker=service_ticker,
            as_of_date=as_of.isoformat(),
            flat_band_pct=best_band,
            horizon_trading_days=effective_horizon,
            calibration=args.calibration,
            target_interval_coverage=args.target_coverage,
            feature_version=args.feature_set,
            include_band_sweep=True,
            max_folds=effective_max_folds,
            fold_step=effective_fold_step,
            train_window=effective_train_window,
            use_ensemble=effective_use_ensemble,
            purge_days=args.purge_days,
            embargo_mode=args.embargo_mode,
            embargo_days=args.embargo_days,
            embargo_pct=args.embargo_pct,
            prob_mode=args.prob_mode,
            temp_scale=args.temp_scale,
            meta_band_mode=args.meta_band_mode,
            meta_band_q=args.meta_band_q,
            meta_band_min_pct=args.meta_band_min_pct,
            meta_band_max_pct=args.meta_band_max_pct,
            label_mode=args.label_mode,
            target_return=effective_target_return,
            tbm_vol_span=args.tbm_vol_span,
            tbm_k=args.tbm_k,
            class_weight_mode=args.class_weight_mode,
            class_weight=class_weight_map,
            threshold_mode=args.threshold_mode,
            t_down=args.t_down,
            t_up=args.t_up,
            threshold_search=args.threshold_search,
            down_recall_min=args.down_recall_min,
            pred_down_min_pct=args.pred_down_min_pct,
            threshold_grid=threshold_grid,
            t_buy_grid=t_buy_grid,
            fixed_thresholds=fixed_thresholds,
            min_coverage=args.min_coverage,
            min_trades=args.min_trades,
            regime_symbols=regime_symbols,
            relative_symbols=relative_symbols,
            sector_symbols=sector_symbols,
        )
        applied_flat_band = best_band

    backtest = bt_payload.get("backtest", {})
    metrics = {
        "accuracy": backtest.get("accuracy"),
        "macro_f1": backtest.get("macro_f1"),
        "logloss": backtest.get("logloss"),
        "brier": backtest.get("brier"),
        "ece": backtest.get("ece"),
        "win_rate_at_best": backtest.get("win_rate_at_best"),
        "coverage_at_best": backtest.get("coverage_at_best"),
        "n_trades_at_best": backtest.get("n_trades_at_best"),
        "avg_return_at_best": backtest.get("avg_return_at_best"),
        "median_return_at_best": backtest.get("median_return_at_best"),
        "precision_at_p70": backtest.get("precision_at_p70"),
        "coverage_at_p70": backtest.get("coverage_at_p70"),
        "n_trades_at_p70": backtest.get("n_trades_at_p70"),
        "avg_return_at_p70": backtest.get("avg_return_at_p70"),
        "interval_target_coverage": backtest.get("interval_target_coverage"),
        "interval_q": backtest.get("interval_q"),
        "interval_width_mean": backtest.get("interval_width_mean"),
        "interval_coverage": backtest.get("interval_coverage"),
        "bias": backtest.get("bias"),
        "bias_abs": backtest.get("bias_abs"),
    }
    fixed_threshold_metrics_map = _fixed_threshold_metrics_to_map(backtest.get("fixed_threshold_metrics"))
    diagnostics = {
        "class_ratio": backtest.get("class_ratio"),
        "confusion_matrix": backtest.get("confusion_matrix"),
        "confusion_2x2": backtest.get("confusion_matrix"),
        "class_metrics": backtest.get("class_metrics"),
        "trading_metrics": backtest.get("trading_metrics"),
        "label_distribution": backtest.get("label_distribution"),
        "prediction_distribution": backtest.get("prediction_distribution"),
        "threshold_selection": backtest.get("threshold_selection"),
        "fixed_threshold_metrics": fixed_threshold_metrics_map,
        "prob_up5_summary": backtest.get("prob_up5_summary"),
        "prob_calibration_audit": backtest.get("prob_calibration_audit"),
    }
    validation = backtest.get("validation", {})
    meta_metrics = backtest.get("meta_metrics", {})
    temperature_scaling = backtest.get("temperature_scaling", {})
    prob_calibration = backtest.get("prob_calibration", {})
    baselines = backtest.get("baselines", {})
    threshold_selection = backtest.get("threshold_selection")
    threshold_warnings = backtest.get("warnings", [])
    thresholds_applied = backtest.get("thresholds_applied")
    effective_t_buy_grid = (
        [float(c.get("t_buy")) for c in (threshold_selection or {}).get("candidates", []) if c.get("t_buy") is not None]
        if isinstance(threshold_selection, dict)
        else None
    )
    if not effective_t_buy_grid:
        effective_t_buy_grid = t_buy_grid
    effective_fixed_thresholds = _fixed_threshold_keys_to_list(fixed_threshold_metrics_map)
    if not effective_fixed_thresholds:
        effective_fixed_thresholds = fixed_thresholds
    effective_class_weight = backtest.get("class_weight")
    interval_audit = {
        "target_name": backtest.get("interval_target_name"),
        "target_unit": backtest.get("interval_target_unit"),
        "target_transform": backtest.get("target_transform"),
        "target_scale_factor": backtest.get("target_scale_factor"),
        "target_y_source": backtest.get("interval_target_y_source"),
        "target_mismatch_warn": backtest.get("interval_target_mismatch_warn", False),
        "sample": backtest.get("interval_audit_sample", []),
    }
    if interval_audit["target_mismatch_warn"]:
        print(
            "[WARN] Interval target mismatch flag is ON "
            f"(name={interval_audit['target_name']} unit={interval_audit['target_unit']} "
            f"y_source={interval_audit['target_y_source']})"
        )

    service_meta = bt_payload.get("meta", {})
    used_feature_columns = service_meta.get("used_feature_columns", [])
    feature_hash = service_meta.get("feature_hash")
    if feature_hash is None:
        feature_hash = hashlib.sha256(
            json.dumps(used_feature_columns, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    config_hash_payload = {
        "ticker": target_symbol,
        "as_of": as_of.isoformat(),
        "horizon": effective_horizon,
        "flat_band_input": args.flat_band,
        "flat_band_applied": applied_flat_band,
        "calibration": args.calibration,
        "prob_mode": args.prob_mode,
        "temp_scale": args.temp_scale,
        "class_weight_mode": args.class_weight_mode,
        "class_weight": effective_class_weight,
        "threshold_mode": args.threshold_mode,
        "t_down": args.t_down,
        "t_up": args.t_up,
        "threshold_search_enabled": bool(args.threshold_search),
        "down_recall_min": float(args.down_recall_min),
        "pred_down_min_pct": float(args.pred_down_min_pct),
        "threshold_grid": threshold_grid,
        "thresholds_applied": thresholds_applied,
        "meta_band_mode": args.meta_band_mode,
        "label_mode": args.label_mode,
        "target_return": effective_target_return,
        "t_buy_grid": effective_t_buy_grid,
        "fixed_thresholds": effective_fixed_thresholds,
        "min_coverage": float(args.min_coverage),
        "min_trades": int(args.min_trades),
        "tbm_vol_span": args.tbm_vol_span,
        "tbm_k": args.tbm_k,
        "feature_set": args.feature_set,
        "regime_symbols": regime_symbols,
        "relative_symbols": relative_symbols,
        "sector_symbols": sector_symbols,
        "feature_hash": feature_hash,
        "use_ensemble": effective_use_ensemble,
        "ensemble_seeds": [11, 29] if effective_use_ensemble else [11],
        "purge_days": validation.get("purge_days", args.purge_days if args.purge_days is not None else effective_horizon),
        "embargo_mode": validation.get("embargo_mode", args.embargo_mode),
        "embargo_days": validation.get("embargo_days", args.embargo_days),
        "embargo_pct": validation.get("embargo_pct", args.embargo_pct),
        "folds_used": validation.get("folds_used"),
        "class_weight_mode": args.class_weight_mode,
        "class_weight": effective_class_weight,
        "threshold_mode": args.threshold_mode,
        "threshold_search_enabled": bool(args.threshold_search),
        "down_recall_min": float(args.down_recall_min),
        "pred_down_min_pct": float(args.pred_down_min_pct),
        "thresholds_applied": thresholds_applied,
    }
    config_hash = hashlib.sha256(
        json.dumps(config_hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    output = {
        "ticker": target_symbol,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "as_of": as_of.isoformat()},
        "metrics": metrics,
        "diagnostics": diagnostics,
        "meta": {
            "sources": service_meta.get("sources", {}),
            "origins": service_meta.get("origins", {}),
            "used_feature_columns": used_feature_columns,
            "n_features": service_meta.get("n_features"),
            "feature_hash": feature_hash,
            "config_hash": config_hash,
            "db_freshness": db_check if db_check is not None else service_meta.get("db_freshness", {}),
            "as_of_date": as_of.isoformat(),
            "effective_as_of": (db_check or {}).get("effective_as_of", as_of.isoformat()),
            "is_trading_day": (db_check or {}).get("is_trading_day", True),
            "target_name": backtest.get("interval_target_name", "close_future"),
            "target_unit": backtest.get("interval_target_unit", "price"),
            "target_transform": backtest.get("target_transform", "raw_price"),
            "target_scale_factor": backtest.get("target_scale_factor", 1.0),
            "run_params": {
                "as_of": as_of.isoformat(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "horizon": effective_horizon,
                "horizon_trading_days": effective_horizon,
                "effective_as_of": (db_check or {}).get("effective_as_of", as_of.isoformat()),
                "flat_band": args.flat_band,
                "flat_band_applied": applied_flat_band,
                "model": service_meta.get("model_version"),
                "calibration": args.calibration,
                "feature_set": service_meta.get("feature_set_used"),
                "feature_version": service_meta.get("feature_version", args.feature_set),
                "regime_symbols": regime_symbols,
                "relative_symbols": relative_symbols,
                "sector_symbols": sector_symbols,
                "feature_hash": feature_hash,
                "config_hash": config_hash,
                "target_coverage": args.target_coverage,
                "target_interval_coverage": args.target_coverage,
                "lookback": effective_lookback,
                "preflight_only": args.preflight_only,
                "fast": args.fast,
                "max_folds": effective_max_folds,
                "fold_step": effective_fold_step,
                "train_window": effective_train_window,
                "band_sweep_enabled": effective_include_band_sweep,
                "use_ensemble": effective_use_ensemble,
                "ensemble_seeds": [11, 29] if effective_use_ensemble else [11],
                "purge_days": validation.get("purge_days", args.purge_days if args.purge_days is not None else effective_horizon),
                "embargo_mode": validation.get("embargo_mode", args.embargo_mode),
                "embargo_days": validation.get("embargo_days", args.embargo_days),
                "embargo_pct": validation.get("embargo_pct", args.embargo_pct),
                "folds_used": validation.get("folds_used"),
                "target_transform": backtest.get("target_transform", "raw_price"),
                "target_scale_factor": backtest.get("target_scale_factor", 1.0),
                "prob_mode": args.prob_mode,
                "temp_scale": args.temp_scale,
                "class_weight_mode": args.class_weight_mode,
                "class_weight": effective_class_weight,
                "threshold_mode": args.threshold_mode,
                "t_down": args.t_down,
                "t_up": args.t_up,
                "threshold_search_enabled": bool(args.threshold_search),
                "down_recall_min": float(args.down_recall_min),
                "pred_down_min_pct": float(args.pred_down_min_pct),
                "thresholds_applied": thresholds_applied,
                "meta_band_mode": args.meta_band_mode,
                "meta_band_q": args.meta_band_q,
                "meta_band_min_pct": args.meta_band_min_pct,
                "meta_band_max_pct": args.meta_band_max_pct,
                "label_mode": args.label_mode,
                "target_return": effective_target_return,
                "t_buy_grid": effective_t_buy_grid,
                "fixed_thresholds": effective_fixed_thresholds,
                "min_coverage": float(args.min_coverage),
                "min_trades": int(args.min_trades),
                "tbm_vol_span": args.tbm_vol_span,
                "tbm_k": args.tbm_k,
                "temperature_best_T": temperature_scaling.get("best_T"),
            },
            "validation": validation,
            "meta_metrics": meta_metrics,
            "temperature_scaling": temperature_scaling,
            "interval_target_mismatch_warn": interval_audit.get("target_mismatch_warn", False),
            "threshold_selection": threshold_selection,
            "warnings": threshold_warnings,
        },
        "meta_metrics": meta_metrics,
        "temperature_scaling": temperature_scaling,
        "prob_calibration": prob_calibration,
        "baselines": baselines,
        "interval_audit": interval_audit,
        "best_flat_band_pct": bt_payload.get("best_flat_band_pct"),
        "band_sweep": bt_payload.get("band_sweep", []),
    }
    if pre_band_payload is not None:
        output["best_band_selection"] = {
            "enabled": True,
            "base_flat_band_input": float(args.flat_band),
            "selected_best_flat_band_pct": float(pre_band_payload.get("best_flat_band_pct", args.flat_band)),
            "selection_band_sweep": pre_band_payload.get("band_sweep", []),
        }

    if args.save_run_args_full:
        output["metadata"] = {
            "run_args_full": {
                "cli_args_raw": vars(args).copy(),
                "effective_args": {
                    "ticker_input": args.ticker,
                    "service_ticker": service_ticker,
                    "target_symbol": target_symbol,
                    "as_of": as_of.isoformat(),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "lookback_effective": effective_lookback,
                    "max_folds_effective": effective_max_folds,
                    "fold_step_effective": effective_fold_step,
                    "train_window_effective": effective_train_window,
                    "band_sweep_enabled_effective": effective_include_band_sweep,
                    "use_ensemble_effective": effective_use_ensemble,
                    "temp_scale_effective": (args.temp_scale == "on"),
                },
                "saved_at": datetime.now().isoformat(),
            }
        }

    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    ticker_slug = _safe_ticker_slug(service_ticker)
    if args.output_name:
        out_path = out_dir / str(args.output_name)
    else:
        out_path = out_dir / f"backtest_{ticker_slug}_{str(args.label_mode).lower()}_{as_of.strftime('%Y%m%d')}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("")
    print("[BACKTEST SUMMARY]")
    print(f"  ticker: {output['ticker']}")
    print(f"  as_of: {as_of.isoformat()}  period: {start.isoformat()} -> {end.isoformat()}")
    print(f"  effective_as_of: {output['meta']['effective_as_of']}")
    if metrics["accuracy"] is not None:
        print(f"  accuracy: {metrics['accuracy']:.4f}")
    if metrics["macro_f1"] is not None:
        print(f"  macro_f1: {metrics['macro_f1']:.4f}")
    if metrics["logloss"] is not None:
        print(f"  logloss: {metrics['logloss']:.4f}")
    if metrics["brier"] is not None:
        print(f"  brier: {metrics['brier']:.4f}")
    if metrics["ece"] is not None:
        print(f"  ece: {metrics['ece']:.4f}")
    if metrics["interval_target_coverage"] is not None and metrics["interval_coverage"] is not None:
        print(
            f"  interval_target_coverage: {metrics['interval_target_coverage']:.4f}  "
            f"interval_coverage: {metrics['interval_coverage']:.4f}"
        )
    if metrics["interval_width_mean"] is not None:
        print(f"  interval_width_mean: {metrics['interval_width_mean']:.4f}")
    if metrics["win_rate_at_best"] is not None:
        t_buy_best = (thresholds_applied or {}).get("t_buy")
        print(
            "  best: t_buy={tb} win_rate={wr:.4f} coverage={cv:.4f} n_trades={nt} avg_return={ar:+.4f}".format(
                tb=t_buy_best,
                wr=float(metrics["win_rate_at_best"]),
                cv=float(metrics["coverage_at_best"] or 0.0),
                nt=int(metrics["n_trades_at_best"] or 0),
                ar=float(metrics["avg_return_at_best"] or 0.0),
            )
        )
    print(f"  json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
