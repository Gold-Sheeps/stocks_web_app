from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sweep quantile meta-band params and summarize C1/C2 vs logloss/brier tradeoff."
    )
    p.add_argument("--ticker", default="US:NVDA")
    p.add_argument("--asof", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizon", type=int, required=True)
    p.add_argument("--flat-band", type=float, required=True, dest="flat_band")
    p.add_argument("--calibration", default="sigmoid", choices=["sigmoid", "isotonic"])
    p.add_argument(
        "--db-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --db-check/--no-db-check to run_backtest_nvda.py",
    )
    p.add_argument(
        "--no-fast",
        action="store_true",
        help="Do not pass --fast (default behavior passes --fast).",
    )
    p.add_argument("--q-grid", default="0.50,0.55,0.60,0.65,0.70")
    p.add_argument("--band-min-grid", default="0.5")
    p.add_argument("--band-max-grid", default="5,6,8,10")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--out", default=None)
    return p.parse_args()


def _parse_float_grid(raw: str, name: str) -> List[float]:
    vals: List[float] = []
    for x in str(raw).split(","):
        x = x.strip()
        if not x:
            continue
        try:
            vals.append(float(x))
        except ValueError as exc:
            raise ValueError(f"{name} includes non-float value: {x}") from exc
    if not vals:
        raise ValueError(f"{name} must include at least one value")
    return vals


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _ticker_slug(ticker: str) -> str:
    raw = str(ticker).split(":", 1)[-1].lower()
    return "".join(ch for ch in raw if ch.isalnum()) or "ticker"


def _extract_json_path(stdout: str, ticker: str, asof: str) -> Path:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if "json:" in ln.lower()]
    for ln in reversed(lines):
        if "json:" in ln:
            return Path(ln.split("json:", 1)[1].strip())
    return Path("backend/ml_predictor_data") / f"backtest_{_ticker_slug(ticker)}_{asof.replace('-', '')}.json"


def _extract_preflight(stdout: str) -> Dict[str, Any]:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and "missing_symbols" in ln and "missing_ranges" in ln:
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    return {"missing_symbols": [], "missing_ranges": {}}


def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_base_cmd(args: argparse.Namespace) -> List[str]:
    cmd = [
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
        "meta",
        "--meta-band-mode",
        "quantile",
        "--no-auto-fill-missing",
    ]
    cmd.append("--db-check" if args.db_check else "--no-db-check")
    cmd.append("--no-fast" if args.no_fast else "--fast")
    return cmd


def _run_preflight(args: argparse.Namespace) -> Tuple[bool, Dict[str, Any], int]:
    cmd = _build_base_cmd(args) + ["--preflight-only"]
    print("[PREFLIGHT] " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip())
    pf = _extract_preflight(proc.stdout)
    ok = proc.returncode == 0 and not (pf.get("missing_symbols") or [])
    return ok, pf, int(proc.returncode)


def _evaluate_payload(
    payload: Dict[str, Any],
    q: float,
    band_min: float,
    band_max: float,
    json_path: str,
) -> Dict[str, Any]:
    metrics = payload.get("metrics") or {}
    baselines = payload.get("baselines") or {}
    prior = baselines.get("baseline_prior") or {}
    meta_metrics = payload.get("meta_metrics") or {}
    fold_stats = meta_metrics.get("fold_stats") or []

    folds = len(fold_stats)
    extreme = 0
    fallback = 0
    hit_min = 0
    hit_max = 0

    for f in fold_stats:
        vals = [
            _as_float(f.get("meta_pos_rate_train")),
            _as_float(f.get("meta_pos_rate_calib")),
            _as_float(f.get("meta_pos_rate_test")),
        ]
        if any(v is not None and (v < 0.01 or v > 0.99) for v in vals):
            extreme += 1
        if bool(f.get("side_fallback_used")):
            fallback += 1

        bu = _as_float(f.get("band_used_pct"))
        if bu is not None and abs(bu - band_min) < 1e-9:
            hit_min += 1
        if bu is not None and abs(bu - band_max) < 1e-9:
            hit_max += 1

    model_ll = _as_float(metrics.get("logloss"))
    model_br = _as_float(metrics.get("brier"))
    prior_ll = _as_float(prior.get("logloss"))
    prior_br = _as_float(prior.get("brier"))
    d_ll = (model_ll - prior_ll) if (model_ll is not None and prior_ll is not None) else None
    d_br = (model_br - prior_br) if (model_br is not None and prior_br is not None) else None

    c1_ok = (extreme / folds) < 0.05 if folds > 0 else False
    c2_ok = (fallback / folds) < 0.10 if folds > 0 else False
    a_ok = bool((d_ll is not None and d_ll < 0.0) or (d_br is not None and d_br < 0.0))
    overall_ok = bool(c1_ok and c2_ok and a_ok)

    score = 0.0
    score += 1000.0 * max(0.0, d_ll or 0.0)
    score += 2000.0 * max(0.0, d_br or 0.0)
    score += 20.0 * extreme
    score += 10.0 * fallback
    score += 5.0 * hit_max
    score += 5.0 * hit_min

    return {
        "q": float(q),
        "band_min_pct": float(band_min),
        "band_max_pct": float(band_max),
        "metrics": {
            "logloss": model_ll,
            "brier": model_br,
        },
        "baselines": {
            "prior_logloss": prior_ll,
            "prior_brier": prior_br,
        },
        "deltas": {
            "delta_logloss": d_ll,
            "delta_brier": d_br,
        },
        "folds": int(folds),
        "extreme_folds": int(extreme),
        "fallback_folds": int(fallback),
        "band_hit_min_folds": int(hit_min),
        "band_hit_max_folds": int(hit_max),
        "rates": {
            "extreme_rate": float(extreme / folds) if folds > 0 else None,
            "fallback_rate": float(fallback / folds) if folds > 0 else None,
        },
        "ok_flags": {
            "A_ok": a_ok,
            "C1_ok": c1_ok,
            "C2_ok": c2_ok,
            "overall_ok": overall_ok,
        },
        "score": float(score),
        "backtest_json_path": json_path,
    }


def _print_table(rows: List[Dict[str, Any]], top: int) -> None:
    print("\n[TRADEOFF TABLE]")
    print(
        "rank | q | bmax | ll | prior_ll | d_ll | br | prior_br | d_br | extreme/folds | fallback/folds | hit_min | hit_max | ok"
    )
    for i, r in enumerate(rows[: max(0, int(top))], start=1):
        m = r.get("metrics") or {}
        b = r.get("baselines") or {}
        d = r.get("deltas") or {}
        print(
            "{rank} | {q} | {bmax} | {ll} | {pll} | {dll} | {br} | {pbr} | {dbr} | {ex}/{fd} | {fb}/{fd} | {hmin} | {hmax} | {ok}".format(
                rank=i,
                q=_fmt(r.get("q"), 2),
                bmax=_fmt(r.get("band_max_pct"), 2),
                ll=_fmt(m.get("logloss")),
                pll=_fmt(b.get("prior_logloss")),
                dll=_fmt(d.get("delta_logloss")),
                br=_fmt(m.get("brier")),
                pbr=_fmt(b.get("prior_brier")),
                dbr=_fmt(d.get("delta_brier")),
                ex=r.get("extreme_folds"),
                fb=r.get("fallback_folds"),
                fd=max(1, int(r.get("folds") or 0)),
                hmin=r.get("band_hit_min_folds"),
                hmax=r.get("band_hit_max_folds"),
                ok=(r.get("ok_flags") or {}).get("overall_ok"),
            )
        )


def main() -> int:
    args = _parse_args()
    q_grid = _parse_float_grid(args.q_grid, "q-grid")
    bmin_grid = _parse_float_grid(args.band_min_grid, "band-min-grid")
    bmax_grid = _parse_float_grid(args.band_max_grid, "band-max-grid")

    pre_ok, pf, pre_rc = _run_preflight(args)
    if not pre_ok:
        print("[ERROR] Missing DB data detected (DB-only mode, no auto-fill).")
        print(json.dumps({
            "return_code": pre_rc,
            "missing_symbols": pf.get("missing_symbols", []),
            "missing_ranges": pf.get("missing_ranges", {}),
        }, ensure_ascii=False, indent=2))
        return 1

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for q in q_grid:
        for bmin in bmin_grid:
            for bmax in bmax_grid:
                cmd = _build_base_cmd(args) + [
                    "--meta-band-q",
                    str(q),
                    "--meta-band-min-pct",
                    str(bmin),
                    "--meta-band-max-pct",
                    str(bmax),
                ]
                print("\n[RUN] " + " ".join(cmd))
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.stdout:
                    print(proc.stdout.rstrip())
                if proc.stderr:
                    print(proc.stderr.rstrip())

                json_path = _extract_json_path(proc.stdout or "", args.ticker, args.asof)
                payload = _safe_load_json(json_path)
                if proc.returncode != 0 or payload is None:
                    failures.append(
                        {
                            "q": q,
                            "band_min_pct": bmin,
                            "band_max_pct": bmax,
                            "return_code": int(proc.returncode),
                            "json_path": str(json_path),
                        }
                    )
                    continue

                row = _evaluate_payload(payload, q, bmin, bmax, str(json_path))
                results.append(row)

    results.sort(key=lambda r: float(r.get("score") or 0.0))
    _print_table(results, args.top)

    overall_ok_count = sum(1 for r in results if (r.get("ok_flags") or {}).get("overall_ok"))
    summary = {
        "total_runs": len(q_grid) * len(bmin_grid) * len(bmax_grid),
        "success_runs": len(results),
        "failed_runs": len(failures),
        "overall_ok_count": overall_ok_count,
        "best_score": (results[0].get("score") if results else None),
    }

    print("\n[SUMMARY]")
    print(
        "runs={tr} success={sr} failed={fr} overall_ok={ok} best_score={bs}".format(
            tr=summary["total_runs"],
            sr=summary["success_runs"],
            fr=summary["failed_runs"],
            ok=summary["overall_ok_count"],
            bs=_fmt(summary["best_score"]),
        )
    )

    out_payload = {
        "metadata": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "args": {
                "ticker": args.ticker,
                "asof": args.asof,
                "start": args.start,
                "end": args.end,
                "horizon": args.horizon,
                "flat_band": args.flat_band,
                "calibration": args.calibration,
                "db_check": args.db_check,
                "fast": (not args.no_fast),
                "q_grid": q_grid,
                "band_min_grid": bmin_grid,
                "band_max_grid": bmax_grid,
                "top": args.top,
            },
        },
        "grid_results": results,
        "best_candidates": results[: max(0, int(args.top))],
        "summary": summary,
        "failures": failures,
    }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"sweep_json={out_path}")

    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
