from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compare temp-scale off vs fold for DB-only backtest."
    )
    p.add_argument("--ticker", default="US:NVDA")
    p.add_argument("--asof", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizon", type=int, default=15)
    p.add_argument("--flat-band", type=float, default=2.0)
    p.add_argument("--calibration", default="sigmoid", choices=["sigmoid", "isotonic"])
    p.add_argument("--prob-mode", default="meta", choices=["meta", "direct"])
    p.add_argument("--label-mode", default="tbm", choices=["fixed", "tbm"])
    p.add_argument("--tbm-k", type=float, default=3.5)
    p.add_argument("--tbm-vol-span", type=int, default=10)
    p.add_argument("--fast", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--db-check", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument(
        "--auto-fill-missing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Pass-through to run_backtest_nvda.py (default: disabled).",
    )
    p.add_argument("--out", default="backend/ml_predictor_data/temp_scale_compare.json")
    return p.parse_args()


def _slug(ticker: str) -> str:
    t = str(ticker).strip().upper()
    raw = t.split(":", 1)[1] if ":" in t else t
    return "".join(ch for ch in raw.lower() if ch.isalnum()) or "ticker"


def _run_mode(args: argparse.Namespace, mode: str) -> Dict[str, Any]:
    cmd: List[str] = [
        sys.executable,
        "backend/scripts/run_backtest_nvda.py",
        "--ticker",
        args.ticker,
        "--asof",
        args.asof,
        "--start",
        args.start,
        "--end",
        args.end,
        "--horizon",
        str(args.horizon),
        "--flat_band",
        str(args.flat_band),
        "--calibration",
        args.calibration,
        "--prob-mode",
        args.prob_mode,
        "--temp-scale",
        mode,
        "--label-mode",
        args.label_mode,
        "--tbm-k",
        str(args.tbm_k),
        "--tbm-vol-span",
        str(args.tbm_vol_span),
    ]
    cmd.append("--db-check" if args.db_check else "--no-db-check")
    cmd.append("--fast" if args.fast else "--no-fast")
    cmd.append("--auto-fill-missing" if args.auto_fill_missing else "--no-auto-fill-missing")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"run_backtest failed (mode={mode}, rc={proc.returncode})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    ymd = args.asof.replace("-", "")
    src = Path("backend/ml_predictor_data") / f"backtest_{_slug(args.ticker)}_{ymd}.json"
    if not src.exists():
        raise FileNotFoundError(f"Expected backtest JSON not found: {src}")
    dst = src.with_name(src.stem + f"_{mode}.json")
    shutil.copy2(src, dst)
    payload = json.loads(dst.read_text(encoding="utf-8"))

    bt = payload
    metrics = bt.get("metrics", {}) or {}
    baselines = bt.get("baselines", {}) or {}
    fold_stats = ((bt.get("meta_metrics") or {}).get("fold_stats") or [])
    ext_before = [float(r["extreme_rate_before_temp"]) for r in fold_stats if "extreme_rate_before_temp" in r]
    ext_after = [float(r["extreme_rate_after_temp"]) for r in fold_stats if "extreme_rate_after_temp" in r]
    return {
        "mode": mode,
        "json_path": str(dst),
        "logloss": metrics.get("logloss"),
        "brier": metrics.get("brier"),
        "prior_logloss": ((baselines.get("baseline_prior") or {}).get("logloss")),
        "prior_brier": ((baselines.get("baseline_prior") or {}).get("brier")),
        "beats_prior_logloss": ((baselines.get("model_vs_baseline") or {}).get("beats_prior_logloss")),
        "beats_prior_brier": ((baselines.get("model_vs_baseline") or {}).get("beats_prior_brier")),
        "extreme_rate_before_temp_mean": (sum(ext_before) / len(ext_before)) if ext_before else None,
        "extreme_rate_after_temp_mean": (sum(ext_after) / len(ext_after)) if ext_after else None,
        "folds_with_temp_stats": len(ext_after),
    }


def main() -> int:
    args = _parse_args()
    off = _run_mode(args, "off")
    fold = _run_mode(args, "fold")

    delta_logloss = (
        float(fold["logloss"]) - float(off["logloss"])
        if off["logloss"] is not None and fold["logloss"] is not None
        else None
    )
    delta_brier = (
        float(fold["brier"]) - float(off["brier"])
        if off["brier"] is not None and fold["brier"] is not None
        else None
    )
    extreme_non_increase = None
    if fold["extreme_rate_before_temp_mean"] is not None and fold["extreme_rate_after_temp_mean"] is not None:
        extreme_non_increase = bool(fold["extreme_rate_after_temp_mean"] <= fold["extreme_rate_before_temp_mean"])

    out = {
        "config": {
            "ticker": args.ticker,
            "asof": args.asof,
            "start": args.start,
            "end": args.end,
            "horizon": args.horizon,
            "flat_band": args.flat_band,
            "calibration": args.calibration,
            "prob_mode": args.prob_mode,
            "label_mode": args.label_mode,
            "tbm_k": args.tbm_k,
            "tbm_vol_span": args.tbm_vol_span,
            "fast": args.fast,
            "db_check": args.db_check,
            "auto_fill_missing": args.auto_fill_missing,
        },
        "off": off,
        "fold": fold,
        "comparison": {
            "delta_logloss_fold_minus_off": delta_logloss,
            "delta_brier_fold_minus_off": delta_brier,
            "fold_improves_logloss": (delta_logloss is not None and delta_logloss < 0.0),
            "fold_improves_brier": (delta_brier is not None and delta_brier < 0.0),
            "extreme_rate_non_increase_in_fold": extreme_non_increase,
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print("[TEMP SCALE COMPARE]")
    print(f"off:  logloss={off['logloss']} brier={off['brier']} prior_ll={off['prior_logloss']} prior_br={off['prior_brier']}")
    print(f"fold: logloss={fold['logloss']} brier={fold['brier']} prior_ll={fold['prior_logloss']} prior_br={fold['prior_brier']}")
    print(f"delta(logloss,brier)=({delta_logloss},{delta_brier})")
    print(
        "fold extreme(before/after)={b}/{a} non_increase={ok}".format(
            b=fold["extreme_rate_before_temp_mean"],
            a=fold["extreme_rate_after_temp_mean"],
            ok=extreme_non_increase,
        )
    )
    print(f"json: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
