from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare fixed vs tbm label-mode using validation suite.")
    p.add_argument("--symbols", required=True)
    p.add_argument("--asof-list", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizon", type=int, required=True)
    p.add_argument("--flat_band", type=float, required=True)
    p.add_argument("--calibration", default="sigmoid", choices=["sigmoid", "isotonic"])
    p.add_argument("--db-check", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--fast", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--tbm-vol-span", type=int, default=20)
    p.add_argument("--tbm-k", type=float, default=1.5)
    p.add_argument("--out", default=None)
    return p.parse_args()


def _run(label_mode: str, args: argparse.Namespace, out_path: Path) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "backend/scripts/validate_probabilities_suite.py",
        "--symbols",
        args.symbols,
        "--asof-list",
        args.asof_list,
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
        "--label-mode",
        label_mode,
        "--tbm-vol-span",
        str(args.tbm_vol_span),
        "--tbm-k",
        str(args.tbm_k),
        "--out",
        str(out_path),
    ]
    cmd.append("--db-check" if args.db_check else "--no-db-check")
    cmd.append("--fast" if args.fast else "--no-fast")

    print(f"\n[RUN {label_mode}] {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip())

    payload = {}
    if out_path.exists():
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    return {
        "label_mode": label_mode,
        "return_code": int(proc.returncode),
        "out_path": str(out_path),
        "payload": payload,
    }


def _summary(run: Dict[str, Any]) -> Dict[str, Any]:
    j = (run.get("payload") or {}).get("judgement") or {}
    return {
        "passed": j.get("passed"),
        "win_rate": j.get("win_rate"),
        "avg_meta_off_logloss": j.get("avg_meta_off_logloss"),
        "avg_prior_logloss": j.get("avg_prior_logloss"),
        "avg_logloss_delta": j.get("avg_logloss_delta_meta_minus_prior"),
        "extreme_fold_ratio": j.get("extreme_fold_ratio"),
        "side_fallback_fold_ratio": j.get("side_fallback_fold_ratio"),
        "condition_a": j.get("condition_a"),
        "condition_b": j.get("condition_b"),
        "condition_c1": j.get("condition_c1"),
        "condition_c2": j.get("condition_c2"),
    }


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def main() -> int:
    args = _parse_args()
    out_dir = Path("backend/ml_predictor_data")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fixed_path = out_dir / f"validation_suite_fixed_{stamp}.json"
    tbm_path = out_dir / f"validation_suite_tbm_{stamp}.json"

    fixed = _run("fixed", args, fixed_path)
    tbm = _run("tbm", args, tbm_path)

    s_fixed = _summary(fixed)
    s_tbm = _summary(tbm)

    print("\n[COMPARE label-mode]")
    print("mode | passed | win_rate | avg_ll(model/prior/delta) | C1_ratio | C2_ratio | A/B/C1/C2")
    print(
        "fixed | {p} | {wr} | {m}/{pr}/{d} | {c1} | {c2} | {a}/{b}/{c1ok}/{c2ok}".format(
            p=s_fixed.get("passed"), wr=_fmt(s_fixed.get("win_rate")),
            m=_fmt(s_fixed.get("avg_meta_off_logloss")), pr=_fmt(s_fixed.get("avg_prior_logloss")), d=_fmt(s_fixed.get("avg_logloss_delta")),
            c1=_fmt(s_fixed.get("extreme_fold_ratio")), c2=_fmt(s_fixed.get("side_fallback_fold_ratio")),
            a=s_fixed.get("condition_a"), b=s_fixed.get("condition_b"), c1ok=s_fixed.get("condition_c1"), c2ok=s_fixed.get("condition_c2"),
        )
    )
    print(
        "tbm   | {p} | {wr} | {m}/{pr}/{d} | {c1} | {c2} | {a}/{b}/{c1ok}/{c2ok}".format(
            p=s_tbm.get("passed"), wr=_fmt(s_tbm.get("win_rate")),
            m=_fmt(s_tbm.get("avg_meta_off_logloss")), pr=_fmt(s_tbm.get("avg_prior_logloss")), d=_fmt(s_tbm.get("avg_logloss_delta")),
            c1=_fmt(s_tbm.get("extreme_fold_ratio")), c2=_fmt(s_tbm.get("side_fallback_fold_ratio")),
            a=s_tbm.get("condition_a"), b=s_tbm.get("condition_b"), c1ok=s_tbm.get("condition_c1"), c2ok=s_tbm.get("condition_c2"),
        )
    )

    out_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "symbols": args.symbols,
            "asof_list": args.asof_list,
            "start": args.start,
            "end": args.end,
            "horizon": args.horizon,
            "flat_band": args.flat_band,
            "calibration": args.calibration,
            "db_check": args.db_check,
            "fast": args.fast,
            "tbm_vol_span": args.tbm_vol_span,
            "tbm_k": args.tbm_k,
        },
        "fixed": {"path": str(fixed_path), "summary": s_fixed, "return_code": fixed["return_code"]},
        "tbm": {"path": str(tbm_path), "summary": s_tbm, "return_code": tbm["return_code"]},
    }
    out_path = Path(args.out) if args.out else (out_dir / f"validation_suite_label_compare_{stamp}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"compare_json={out_path}")

    # non-zero only when both runs failed hard
    if fixed["return_code"] != 0 and tbm["return_code"] != 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
