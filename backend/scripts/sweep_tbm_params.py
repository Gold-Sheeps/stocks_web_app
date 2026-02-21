from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sweep TBM params for meta_off backtest (DB-only).")
    p.add_argument("--ticker", default="US:NVDA")
    p.add_argument("--asof", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizon", type=int, required=True)
    p.add_argument("--flat_band", "--flat-band", type=float, required=True, dest="flat_band")
    p.add_argument("--calibration", default="sigmoid", choices=["sigmoid", "isotonic"])
    p.add_argument("--db-check", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--no-fast", action="store_true")
    p.add_argument("--no-auto-fill-missing", action="store_true", default=False)
    p.add_argument("--k-grid", default="1.0,1.25,1.5,1.75,2.0,2.5,3.0,3.5,4.0")
    p.add_argument("--span-grid", default="10,20,40,80")
    p.add_argument("--out", default=None)
    return p.parse_args()


def _parse_float_grid(raw: str, name: str) -> List[float]:
    out: List[float] = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(float(token))
        except ValueError as exc:
            raise ValueError(f"{name} includes non-float value: {token}") from exc
    if not out:
        raise ValueError(f"{name} must include at least one value")
    return out


def _parse_int_grid(raw: str, name: str) -> List[int]:
    out: List[int] = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError as exc:
            raise ValueError(f"{name} includes non-int value: {token}") from exc
    if not out:
        raise ValueError(f"{name} must include at least one value")
    return out


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


def _extract_json_path(stdout: str, ticker: str, asof: str) -> Path:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if "json:" in ln.lower()]
    for ln in reversed(lines):
        if "json:" in ln:
            return Path(ln.split("json:", 1)[1].strip())
    slug = "".join(ch for ch in ticker.split(":", 1)[-1].lower() if ch.isalnum()) or "ticker"
    return Path("backend/ml_predictor_data") / f"backtest_{slug}_{asof.replace('-', '')}.json"


def _extract_preflight(stdout: str) -> Dict[str, Any]:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and "missing_symbols" in ln and "missing_ranges" in ln:
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    return {"missing_symbols": [], "missing_ranges": {}}


def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _base_cmd(args: argparse.Namespace) -> List[str]:
    cmd = [
        sys.executable,
        "backend/scripts/run_backtest_nvda.py",
        "--ticker", args.ticker,
        "--asof", args.asof,
        "--start", args.start,
        "--end", args.end,
        "--horizon", str(args.horizon),
        "--flat_band", str(args.flat_band),
        "--calibration", args.calibration,
        "--prob-mode", "meta",
        "--temp-scale", "off",
        "--label-mode", "tbm",
    ]
    cmd.append("--db-check" if args.db_check else "--no-db-check")
    cmd.append("--no-fast" if args.no_fast else "--fast")
    cmd.append("--no-auto-fill-missing" if args.no_auto_fill_missing else "--auto-fill-missing")
    return cmd


def _run_preflight(args: argparse.Namespace) -> Tuple[bool, Dict[str, Any], int]:
    cmd = _base_cmd(args) + ["--preflight-only"]
    print("[PREFLIGHT] " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip())
    pf = _extract_preflight(proc.stdout)
    ok = proc.returncode == 0 and not (pf.get("missing_symbols") or [])
    return ok, pf, int(proc.returncode)


def _flat_stats(fold_stats: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    vals: List[float] = []
    for f in fold_stats:
        v = _as_float(f.get("flat_ratio_test"))
        if v is None:
            v = _as_float(((f.get("class_ratio_test") or {}).get("flat")))
        if v is not None:
            vals.append(v)
    if not vals:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": float(statistics.mean(vals)),
        "median": float(statistics.median(vals)),
        "min": float(min(vals)),
        "max": float(max(vals)),
    }


def _extract_c1_c2(fold_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    folds = len(fold_stats)
    extreme = 0
    fallback = 0
    for f in fold_stats:
        rates = [
            _as_float(f.get("meta_pos_rate_train")),
            _as_float(f.get("meta_pos_rate_calib")),
            _as_float(f.get("meta_pos_rate_test")),
        ]
        if any(v is not None and (v < 0.01 or v > 0.99) for v in rates):
            extreme += 1
        if bool(f.get("side_fallback_used")):
            fallback += 1
    return {
        "folds": folds,
        "extreme_folds": extreme,
        "fallback_folds": fallback,
        "extreme_rate": (float(extreme / folds) if folds > 0 else None),
        "fallback_rate": (float(fallback / folds) if folds > 0 else None),
    }


def _score(delta_ll: Optional[float], delta_br: Optional[float], mean_flat: Optional[float]) -> Tuple[float, int, int]:
    p_low = 1 if (mean_flat is not None and mean_flat < 0.15) else 0
    p_high = 1 if (mean_flat is not None and mean_flat > 0.85) else 0
    s = 0.0
    s += 1000.0 * max(0.0, delta_ll or 0.0)
    s += 2000.0 * max(0.0, delta_br or 0.0)
    s += 200.0 * p_low
    s += 200.0 * p_high
    return float(s), p_low, p_high


def _evaluate(payload: Dict[str, Any], k: float, span: int, json_path: str) -> Dict[str, Any]:
    metrics = payload.get("metrics") or {}
    prior = ((payload.get("baselines") or {}).get("baseline_prior") or {})
    mm = payload.get("meta_metrics") or {}
    fold_stats = mm.get("fold_stats") or []

    model_ll = _as_float(metrics.get("logloss"))
    model_br = _as_float(metrics.get("brier"))
    prior_ll = _as_float(prior.get("logloss"))
    prior_br = _as_float(prior.get("brier"))
    d_ll = (model_ll - prior_ll) if (model_ll is not None and prior_ll is not None) else None
    d_br = (model_br - prior_br) if (model_br is not None and prior_br is not None) else None

    flat = _flat_stats(fold_stats)
    c12 = _extract_c1_c2(fold_stats)
    s, p_low, p_high = _score(d_ll, d_br, flat.get("mean"))

    return {
        "tbm_k": float(k),
        "tbm_vol_span": int(span),
        "metrics": {"logloss": model_ll, "brier": model_br},
        "baseline_prior": {"logloss": prior_ll, "brier": prior_br},
        "deltas": {"delta_logloss": d_ll, "delta_brier": d_br},
        "flat_ratio_test": flat,
        "p_trade": {
            "mean": _as_float(mm.get("p_trade_mean")),
            "std": _as_float(mm.get("p_trade_std")),
        },
        "c1_c2": c12,
        "warnings_count": len(mm.get("warnings") or []),
        "penalty_flat_low": p_low,
        "penalty_flat_high": p_high,
        "score": s,
        "wins_prior_both": bool((d_ll is not None and d_ll < 0.0) and (d_br is not None and d_br < 0.0)),
        "backtest_json_path": json_path,
    }


def _print_top10(rows: List[Dict[str, Any]]) -> None:
    print("\n[TOP 10]")
    print("rank | k | span | mean_flat | median_flat | min_flat | max_flat | delta_logloss | delta_brier | score")
    for i, r in enumerate(rows[:10], start=1):
        f = r.get("flat_ratio_test") or {}
        d = r.get("deltas") or {}
        print(
            "{rank} | {k} | {s} | {mf} | {med} | {mn} | {mx} | {dll} | {dbr} | {sc}".format(
                rank=i,
                k=_fmt(r.get("tbm_k"), 2),
                s=r.get("tbm_vol_span"),
                mf=_fmt(f.get("mean")),
                med=_fmt(f.get("median")),
                mn=_fmt(f.get("min")),
                mx=_fmt(f.get("max")),
                dll=_fmt(d.get("delta_logloss")),
                dbr=_fmt(d.get("delta_brier")),
                sc=_fmt(r.get("score")),
            )
        )


def main() -> int:
    args = _parse_args()
    if not args.no_auto_fill_missing:
        raise ValueError("This sweep must run DB-only. Use --no-auto-fill-missing.")

    k_grid = _parse_float_grid(args.k_grid, "k-grid")
    span_grid = _parse_int_grid(args.span_grid, "span-grid")

    ok, pf, pre_rc = _run_preflight(args)
    if not ok:
        print("[ERROR] Missing DB data detected. Stop (DB-only, no auto-fill).")
        print(json.dumps({
            "return_code": pre_rc,
            "missing_symbols": pf.get("missing_symbols", []),
            "missing_ranges": pf.get("missing_ranges", {}),
        }, ensure_ascii=False, indent=2))
        return 1

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    total = len(k_grid) * len(span_grid)
    idx = 0

    for k in k_grid:
        for span in span_grid:
            idx += 1
            cmd = _base_cmd(args) + ["--tbm-k", str(k), "--tbm-vol-span", str(span)]
            print(f"\n[RUN {idx}/{total}] " + " ".join(cmd))
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.stdout:
                print(proc.stdout.rstrip())
            if proc.stderr:
                print(proc.stderr.rstrip())

            jpath = _extract_json_path(proc.stdout or "", args.ticker, args.asof)
            payload = _safe_load_json(jpath)
            if proc.returncode != 0 or payload is None:
                failures.append({
                    "tbm_k": float(k),
                    "tbm_vol_span": int(span),
                    "return_code": int(proc.returncode),
                    "json_path": str(jpath),
                })
                continue

            results.append(_evaluate(payload, k, span, str(jpath)))

    results.sort(key=lambda r: float(r.get("score") or 0.0))
    _print_top10(results)

    winners = [r for r in results if r.get("wins_prior_both")]
    if winners:
        best = sorted(winners, key=lambda r: float(r.get("score") or 0.0))[0]
        print(
            "\nFIRST_CANDIDATE: k={k}, span={s}, mean_flat={mf}, delta_logloss={dll}, delta_brier={dbr}, score={sc}".format(
                k=_fmt(best.get("tbm_k"), 2),
                s=best.get("tbm_vol_span"),
                mf=_fmt((best.get("flat_ratio_test") or {}).get("mean")),
                dll=_fmt((best.get("deltas") or {}).get("delta_logloss")),
                dbr=_fmt((best.get("deltas") or {}).get("delta_brier")),
                sc=_fmt(best.get("score")),
            )
        )
    else:
        top3 = results[:3]
        print("\nFAIL: no point beats prior on both logloss and brier.")
        if top3:
            fvals = [((r.get("flat_ratio_test") or {}).get("mean")) for r in top3 if (r.get("flat_ratio_test") or {}).get("mean") is not None]
            if fvals:
                print("Top3 mean_flat range: {}..{}".format(_fmt(min(fvals)), _fmt(max(fvals))))
            print("Top3 candidates:")
            for r in top3:
                d = r.get("deltas") or {}
                print(
                    "  k={k}, span={s}, mean_flat={mf}, d_logloss={dll}, d_brier={dbr}, score={sc}".format(
                        k=_fmt(r.get("tbm_k"), 2),
                        s=r.get("tbm_vol_span"),
                        mf=_fmt((r.get("flat_ratio_test") or {}).get("mean")),
                        dll=_fmt(d.get("delta_logloss")),
                        dbr=_fmt(d.get("delta_brier")),
                        sc=_fmt(r.get("score")),
                    )
                )
        print("Hypothesis: TBM k/span grid still leaves p_trade too high and flat ratio band misaligned, so probabilities remain over-confident.")

    summary = {
        "total": total,
        "success": len(results),
        "failed": len(failures),
        "wins_prior_both_count": len(winners),
        "best_score": (results[0].get("score") if results else None),
    }
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
                "no_fast": args.no_fast,
                "no_auto_fill_missing": args.no_auto_fill_missing,
                "k_grid": k_grid,
                "span_grid": span_grid,
            },
        },
        "grid_results": results,
        "best_candidates": results[:10],
        "summary": summary,
        "failures": failures,
    }

    out_path = Path(args.out) if args.out else (Path("backend/ml_predictor_data") / f"sweep_tbm_{args.asof.replace('-', '')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"sweep_json={out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
