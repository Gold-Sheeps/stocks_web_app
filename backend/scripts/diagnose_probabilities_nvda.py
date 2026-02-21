from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run 4 probability-mode backtests and compare diagnostics."
    )
    parser.add_argument("--asof", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--horizon", type=int, required=True)
    parser.add_argument("--flat_band", type=float, required=True)
    parser.add_argument("--calibration", required=True, choices=["sigmoid", "isotonic"])
    parser.add_argument(
        "--db-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --db-check/--no-db-check to run_backtest_nvda.py",
    )
    parser.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --fast/--no-fast to run_backtest_nvda.py",
    )
    parser.add_argument(
        "--prob-mode",
        default=None,
        choices=["direct", "meta"],
        help="Optional filter: run only this prob mode (default: run both).",
    )
    parser.add_argument(
        "--meta-band-mode",
        default="fixed",
        choices=["fixed", "quantile"],
        help="Pass-through meta band mode to run_backtest_nvda.py.",
    )
    parser.add_argument(
        "--meta-band-q",
        type=float,
        default=0.65,
        help="Pass-through meta band quantile.",
    )
    parser.add_argument(
        "--meta-band-min-pct",
        type=float,
        default=0.5,
        help="Pass-through meta band min pct.",
    )
    parser.add_argument(
        "--meta-band-max-pct",
        type=float,
        default=10.0,
        help="Pass-through meta band max pct.",
    )
    parser.add_argument(
        "--label-mode",
        default="fixed",
        choices=["fixed", "tbm"],
        help="Pass-through label mode to run_backtest_nvda.py.",
    )
    parser.add_argument(
        "--tbm-vol-span",
        type=int,
        default=20,
        help="Pass-through TBM vol span.",
    )
    parser.add_argument(
        "--tbm-k",
        type=float,
        default=1.5,
        help="Pass-through TBM barrier multiplier.",
    )
    return parser.parse_args()


def _extract_json_path(stdout: str, asof: str) -> Path:
    candidates = [ln.strip() for ln in (stdout or "").splitlines() if "json:" in ln.lower()]
    for ln in reversed(candidates):
        if "json:" in ln:
            return Path(ln.split("json:", 1)[1].strip())
    ymd = asof.replace("-", "")
    return Path("backend/ml_predictor_data") / f"backtest_nvda_{ymd}.json"


def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _case_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    metrics = payload.get("metrics", {}) or {}
    baselines = payload.get("baselines", {}) or {}
    prior = baselines.get("baseline_prior", {}) or {}
    mvs = baselines.get("model_vs_baseline", {}) or {}
    meta_metrics = payload.get("meta_metrics", {}) or {}
    temp = payload.get("temperature_scaling", {}) or {}
    fold_stats = meta_metrics.get("fold_stats") or []
    fallback_count = sum([1 for f in fold_stats if f.get("side_fallback_used")])
    meta_rate_vals = [float(f.get("meta_pos_rate_train", 0.0)) for f in fold_stats if f.get("meta_pos_rate_train") is not None]
    return {
        "logloss": metrics.get("logloss"),
        "brier": metrics.get("brier"),
        "ece": metrics.get("ece"),
        "accuracy": metrics.get("accuracy"),
        "macro_f1": metrics.get("macro_f1"),
        "baseline_prior_logloss": prior.get("logloss"),
        "baseline_prior_brier": prior.get("brier"),
        "beats_prior_logloss": mvs.get("beats_prior_logloss"),
        "beats_prior_brier": mvs.get("beats_prior_brier"),
        "p_trade_mean": meta_metrics.get("p_trade_mean"),
        "p_trade_std": meta_metrics.get("p_trade_std"),
        "side_samples_train": meta_metrics.get("side_samples_train"),
        "side_samples_calib": meta_metrics.get("side_samples_calib"),
        "side_samples_test": meta_metrics.get("side_samples_test"),
        "meta_warnings": meta_metrics.get("warnings") or [],
        "meta_label_rate_train": meta_metrics.get("meta_label_rate_train"),
        "fold_side_fallback_count": fallback_count,
        "fold_count": len(fold_stats),
        "meta_pos_rate_train_min": min(meta_rate_vals) if meta_rate_vals else None,
        "meta_pos_rate_train_max": max(meta_rate_vals) if meta_rate_vals else None,
        "temp_enabled": temp.get("enabled"),
        "best_T": temp.get("best_T"),
    }


def _fmt(x: Any, nd: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _print_table(rows: List[Dict[str, Any]]) -> None:
    print("[DIAGNOSE] 4-case comparison")
    print(
        "case | status | logloss | brier | ece | acc | f1 | prior_ll | prior_br | beat_prior(ll/br) | p_trade(mean/std) | side(t/c/test,fallback) | temp_T"
    )
    for r in rows:
        s = r.get("summary", {}) or {}
        warns = s.get("meta_warnings") or []
        warn_tag = f" w={len(warns)}" if warns else ""
        print(
            "{case} | {st} | {ll} | {br} | {ece} | {acc} | {f1} | {pll} | {pbr} | {bll}/{bbr} | {pm}/{ps}{wt} | {stn}/{scn}/{sen} | {t}".format(
                case=r["case_id"],
                st=r["status"],
                ll=_fmt(s.get("logloss")),
                br=_fmt(s.get("brier")),
                ece=_fmt(s.get("ece")),
                acc=_fmt(s.get("accuracy")),
                f1=_fmt(s.get("macro_f1")),
                pll=_fmt(s.get("baseline_prior_logloss")),
                pbr=_fmt(s.get("baseline_prior_brier")),
                bll=s.get("beats_prior_logloss"),
                bbr=s.get("beats_prior_brier"),
                pm=_fmt(s.get("p_trade_mean")),
                ps=_fmt(s.get("p_trade_std")),
                wt=warn_tag,
                stn=s.get("side_samples_train"),
                scn=s.get("side_samples_calib"),
                sen=f"{s.get('side_samples_test')},{s.get('fold_side_fallback_count')}/{s.get('fold_count')}",
                t=_fmt(s.get("best_T")),
            )
        )


def _diagnose_first_cause(rows: List[Dict[str, Any]]) -> str:
    ok = [r for r in rows if r.get("status") == "OK" and r.get("summary")]
    by_case = {r["case_id"]: r["summary"] for r in ok}
    meta_on = by_case.get("meta_on")
    meta_off = by_case.get("meta_off")
    direct_on = by_case.get("direct_on")
    direct_off = by_case.get("direct_off")

    if meta_on and (meta_on.get("p_trade_std") is not None) and float(meta_on["p_trade_std"]) < 0.02:
        return "First candidate: meta model is near-constant (p_trade_std too small)."
    if meta_on and meta_on.get("fold_count") and (meta_on.get("fold_side_fallback_count", 0) / max(1, meta_on.get("fold_count", 1))) > 0.3:
        return "First candidate: side-model fallback is frequent (insufficient side labels per fold)."
    if meta_on and meta_on.get("meta_pos_rate_train_min") is not None:
        if float(meta_on["meta_pos_rate_train_min"]) < 0.01 or float(meta_on.get("meta_pos_rate_train_max", 0.0)) > 0.99:
            return "First candidate: meta label rate is extreme in folds (band/label setup issue)."
    if meta_on and len(meta_on.get("meta_warnings") or []) > 0:
        return "First candidate: side-model fallback triggered (label sparsity / band setting)."
    if meta_on and meta_off and isinstance(meta_on.get("logloss"), (int, float)) and isinstance(meta_off.get("logloss"), (int, float)):
        if float(meta_on["logloss"]) > float(meta_off["logloss"]):
            return "First candidate: temperature scaling harms meta mode (meta_on worse than meta_off)."
    if direct_on and direct_off and isinstance(direct_on.get("logloss"), (int, float)) and isinstance(direct_off.get("logloss"), (int, float)):
        if float(direct_on["logloss"]) > float(direct_off["logloss"]):
            return "First candidate: temperature scaling harms direct mode (direct_on worse than direct_off)."
    if meta_on and direct_on and isinstance(meta_on.get("logloss"), (int, float)) and isinstance(direct_on.get("logloss"), (int, float)):
        if abs(float(meta_on["logloss"]) - float(direct_on["logloss"])) < 0.01:
            return "First candidate: meta architecture adds little vs direct (similar logloss)."
    return "First candidate: model probability quality issue remains; inspect fold-level calibration and labels."


def main() -> int:
    args = _parse_args()
    script = Path("backend/scripts/run_backtest_nvda.py")
    out_dir = Path("backend/ml_predictor_data")
    out_dir.mkdir(parents=True, exist_ok=True)
    base_cases = [
        ("meta_on", "meta", "on"),
        ("meta_off", "meta", "off"),
        ("direct_on", "direct", "on"),
        ("direct_off", "direct", "off"),
    ]
    if args.prob_mode:
        cases = [c for c in base_cases if c[1] == args.prob_mode]
    else:
        cases = base_cases

    results: List[Dict[str, Any]] = []
    for case_id, prob_mode, temp_scale in cases:
        cmd = [
            sys.executable,
            str(script),
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
            prob_mode,
            "--temp-scale",
            temp_scale,
            "--meta-band-mode",
            args.meta_band_mode,
            "--meta-band-q",
            str(args.meta_band_q),
            "--meta-band-min-pct",
            str(args.meta_band_min_pct),
            "--meta-band-max-pct",
            str(args.meta_band_max_pct),
            "--label-mode",
            args.label_mode,
            "--tbm-vol-span",
            str(args.tbm_vol_span),
            "--tbm-k",
            str(args.tbm_k),
            "--no-auto-fill-missing",
        ]
        cmd.append("--db-check" if args.db_check else "--no-db-check")
        cmd.append("--fast" if args.fast else "--no-fast")

        print(f"\n[RUN] {case_id}: {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip())

        base_json = _extract_json_path(proc.stdout, args.asof)
        payload = _safe_load_json(base_json)
        case_json = out_dir / f"backtest_nvda_{args.asof.replace('-', '')}_{case_id}.json"
        if payload is not None and base_json.exists():
            shutil.copyfile(base_json, case_json)
        summary = _case_summary(payload) if payload is not None else {}
        results.append(
            {
                "case_id": case_id,
                "prob_mode": prob_mode,
                "temp_scale": temp_scale,
                "status": "OK" if proc.returncode == 0 and payload is not None else "FAILED",
                "return_code": int(proc.returncode),
                "json_path": str(case_json if case_json.exists() else base_json),
                "summary": summary,
            }
        )

    _print_table(results)
    diagnosis = _diagnose_first_cause(results)
    print("\nFIRST_CANDIDATE: " + diagnosis)

    diag_payload = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "asof": args.asof,
            "start": args.start,
            "end": args.end,
            "horizon": args.horizon,
            "flat_band": args.flat_band,
            "calibration": args.calibration,
            "db_check": args.db_check,
            "fast": args.fast,
            "prob_mode_filter": args.prob_mode,
            "meta_band_mode": args.meta_band_mode,
            "meta_band_q": args.meta_band_q,
            "meta_band_min_pct": args.meta_band_min_pct,
            "meta_band_max_pct": args.meta_band_max_pct,
            "label_mode": args.label_mode,
            "tbm_vol_span": args.tbm_vol_span,
            "tbm_k": args.tbm_k,
        },
        "cases": results,
        "first_candidate": diagnosis,
    }
    diag_path = out_dir / f"diagnose_nvda_{args.asof.replace('-', '')}.json"
    diag_path.write_text(json.dumps(diag_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"diagnose_json={diag_path}")
    failed = [r for r in results if r["status"] != "OK"]
    if failed:
        print("failed_cases=" + ",".join([f["case_id"] for f in failed]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
