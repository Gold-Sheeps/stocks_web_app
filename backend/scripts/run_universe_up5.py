from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value}") from exc


def _years_back(d: date, years: int) -> date:
    y = d.year - int(years)
    try:
        return d.replace(year=y)
    except ValueError:
        return d.replace(month=2, day=28, year=y)


def _normalize_symbol(raw: str) -> str:
    s = str(raw).strip().upper()
    if not s:
        return ""
    return s if ":" in s else f"US:{s}"


def _parse_tickers(raw: str | None) -> List[str]:
    if raw is None:
        return []
    out: List[str] = []
    for tok in str(raw).split(","):
        sym = _normalize_symbol(tok)
        if sym and sym not in out:
            out.append(sym)
    return out


def _load_tickers_file(path: str | None) -> List[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"tickers file not found: {path}")
    out: List[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        sym = _normalize_symbol(line)
        if sym and sym not in out:
            out.append(sym)
    return out


def _parse_fixed_thresholds(raw: str | None) -> str:
    if raw is None:
        return "0.5,0.6,0.7,0.75,0.8"
    vals = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not vals:
        raise ValueError("fixed-thresholds must not be empty.")
    for tok in vals:
        v = float(tok)
        if v < 0.0 or v > 1.0:
            raise ValueError(f"fixed-thresholds value out of range [0,1]: {v}")
    return ",".join(vals)


def _as_symbol_slug(sym: str) -> str:
    return "".join(ch for ch in sym.lower() if ch.isalnum()) or "ticker"


def _run_single(
    script_path: Path,
    ticker: str,
    asof: str,
    start: str,
    end: str,
    fixed_thresholds: str,
    no_db_check: bool,
    fast: bool,
) -> Tuple[int, Path]:
    out_name = f"backtest_{_as_symbol_slug(ticker)}_up5_2w_{asof.replace('-', '')}.json"
    out_path = script_path.parents[1] / "ml_predictor_data" / out_name
    cmd = [
        sys.executable,
        str(script_path),
        "--asof",
        asof,
        "--ticker",
        ticker,
        "--start",
        start,
        "--end",
        end,
        "--label-mode",
        "up5_2w",
        "--horizon",
        "10",
        "--target-return",
        "0.05",
        "--threshold-search",
        "--fixed-thresholds",
        fixed_thresholds,
        "--output-name",
        out_name,
    ]
    if no_db_check:
        cmd.append("--no-db-check")
    if fast:
        cmd.append("--fast")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise RuntimeError(f"{ticker}: backtest failed ({proc.returncode}): {msg}")
    return proc.returncode, out_path


def _p70_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    metrics = payload.get("metrics", {}) or {}
    diagnostics = payload.get("diagnostics", {}) or {}
    fixed_metrics = diagnostics.get("fixed_threshold_metrics", {}) or {}
    if isinstance(fixed_metrics, dict):
        p70 = fixed_metrics.get("0.7", {}) or fixed_metrics.get("0.70", {}) or {}
    else:
        fixed_rows = fixed_metrics if isinstance(fixed_metrics, list) else []
        p70 = next(
            (r for r in fixed_rows if isinstance(r, dict) and abs(float(r.get("t_buy", -1.0)) - 0.7) < 1e-12),
            None,
        ) or {}
    n_trades = int(metrics.get("n_trades_at_p70") or p70.get("n_trades") or 0)
    precision = metrics.get("precision_at_p70")
    if precision is None:
        precision = p70.get("precision", p70.get("win_rate"))
    coverage = metrics.get("coverage_at_p70")
    if coverage is None:
        coverage = p70.get("coverage")
    avg_return = metrics.get("avg_return_at_p70")
    if avg_return is None:
        avg_return = p70.get("avg_return")
    n_samples = p70.get("n_samples")
    if n_samples is None and coverage not in (None, 0):
        n_samples = int(round(float(n_trades) / float(coverage)))
    return {
        "precision_at_p70": float(precision) if precision is not None else None,
        "coverage_at_p70": float(coverage) if coverage is not None else None,
        "n_trades_at_p70": n_trades,
        "avg_return_at_p70": float(avg_return) if avg_return is not None else None,
        "n_samples": int(n_samples) if n_samples is not None else None,
    }


def _micro_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_trades = int(sum(int(r.get("n_trades_at_p70") or 0) for r in rows))
    total_samples = int(sum(int(r.get("n_samples") or 0) for r in rows))
    weighted_wins = float(
        sum(float(r.get("precision_at_p70") or 0.0) * int(r.get("n_trades_at_p70") or 0) for r in rows)
    )
    weighted_returns = float(
        sum(float(r.get("avg_return_at_p70") or 0.0) * int(r.get("n_trades_at_p70") or 0) for r in rows)
    )
    return {
        "precision_at_p70_micro": (float(weighted_wins / total_trades) if total_trades > 0 else None),
        "coverage_at_p70_micro": (float(total_trades / total_samples) if total_samples > 0 else None),
        "n_trades_at_p70_micro": total_trades,
        "avg_return_at_p70_micro": (float(weighted_returns / total_trades) if total_trades > 0 else None),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run up5_2w backtest for a ticker universe.")
    parser.add_argument("--asof", required=True, help="As-of date (YYYY-MM-DD).")
    parser.add_argument("--tickers", default=None, help='Comma-separated tickers. Example: "US:AAPL,US:MSFT".')
    parser.add_argument("--tickers-file", default=None, help="Path to a file containing one ticker per line.")
    parser.add_argument("--allow-nvda", action=argparse.BooleanOptionalAction, default=False, help="Include NVDA (default: excluded).")
    parser.add_argument("--start", default=None, help="Backtest start date (YYYY-MM-DD). Default: asof-20y.")
    parser.add_argument("--end", default=None, help="Backtest end date (YYYY-MM-DD). Default: asof.")
    parser.add_argument("--fixed-thresholds", default=None, help='Fixed thresholds. Default: "0.5,0.6,0.7,0.75,0.8".')
    parser.add_argument("--min-p70-trades", type=int, default=300, help="Minimum micro p70 trades for evaluable universe.")
    parser.add_argument("--no-db-check", action="store_true", help="Pass --no-db-check to per-ticker run.")
    parser.add_argument("--fast", action="store_true", help="Pass --fast to per-ticker run.")
    parser.add_argument("--output-prefix", default=None, help="Optional prefix for universe summary files.")
    parser.add_argument("--out-prefix", default=None, help="Alias of --output-prefix.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    asof = _parse_iso_date(args.asof, "asof")
    start_date = _parse_iso_date(args.start, "start") if args.start else _years_back(asof, 20)
    end_date = _parse_iso_date(args.end, "end") if args.end else asof
    if start_date > end_date:
        raise ValueError(f"start must be <= end: {start_date} > {end_date}")

    fixed_thresholds = _parse_fixed_thresholds(args.fixed_thresholds)
    tickers = list(dict.fromkeys(_parse_tickers(args.tickers) + _load_tickers_file(args.tickers_file)))
    if not tickers:
        raise ValueError("No tickers provided. Use --tickers or --tickers-file.")
    if not args.allow_nvda:
        tickers = [t for t in tickers if t not in ("US:NVDA", "NVDA")]
    if not tickers:
        raise ValueError("Ticker list is empty after NVDA exclusion. Use --allow-nvda to include NVDA.")

    script_path = Path(__file__).resolve().parent / "run_backtest_nvda.py"
    out_dir = script_path.parents[1] / "ml_predictor_data"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for ticker in tickers:
        try:
            _, json_path = _run_single(
                script_path=script_path,
                ticker=ticker,
                asof=asof.isoformat(),
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                fixed_thresholds=fixed_thresholds,
                no_db_check=bool(args.no_db_check),
                fast=bool(args.fast),
            )
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            p70 = _p70_row(payload)
            rows.append(
                {
                    "ticker": payload.get("ticker", ticker),
                    "json_path": str(json_path),
                    **p70,
                }
            )
            print(
                "[DONE] {t} precision_at_p70={p} n_trades_at_p70={n} coverage_at_p70={c}".format(
                    t=ticker,
                    p=p70["precision_at_p70"],
                    n=p70["n_trades_at_p70"],
                    c=p70["coverage_at_p70"],
                )
            )
        except Exception as exc:
            warnings.append(str(exc))
            print(f"[WARN] {exc}")

    micro = _micro_metrics(rows)
    total_trades = int(micro["n_trades_at_p70_micro"])
    precision_micro = micro["precision_at_p70_micro"]
    coverage_micro = micro["coverage_at_p70_micro"]
    avg_return_micro = micro["avg_return_at_p70_micro"]
    evaluable = total_trades >= int(args.min_p70_trades)
    if not evaluable:
        warnings.append(
            f"not_evaluable: n_trades_at_p70_micro={total_trades} < min_p70_trades={int(args.min_p70_trades)}"
        )

    prefix = args.out_prefix or args.output_prefix or f"universe_up5_{asof.strftime('%Y%m%d')}"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "ticker",
                "precision_at_p70",
                "n_trades_at_p70",
                "coverage_at_p70",
                "avg_return_at_p70",
                "n_samples",
                "json_path",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    summary = {
        "as_of": asof.isoformat(),
        "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "tickers": tickers,
        "fixed_thresholds": fixed_thresholds,
        "universe_metrics": {
            "precision_at_p70_micro": precision_micro,
            "coverage_at_p70_micro": coverage_micro,
            "n_trades_at_p70_micro": total_trades,
            "avg_return_at_p70_micro": avg_return_micro,
            "is_evaluable": evaluable,
            "min_p70_trades": int(args.min_p70_trades),
        },
        "per_ticker": rows,
        "warnings": warnings,
    }
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("")
    print("[UNIVERSE SUMMARY]")
    print(f"  tickers_requested: {len(tickers)}")
    print(f"  tickers_succeeded: {len(rows)}")
    print(f"  precision_at_p70_micro: {precision_micro}")
    print(f"  coverage_at_p70_micro: {coverage_micro}")
    print(f"  n_trades_at_p70_micro: {total_trades}")
    print(f"  avg_return_at_p70_micro: {avg_return_micro}")
    print(f"  is_evaluable: {evaluable}")
    print(f"  csv: {csv_path}")
    print(f"  json: {json_path}")
    if warnings:
        print("  warnings:")
        for w in warnings:
            print(f"    - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
