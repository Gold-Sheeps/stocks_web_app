from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SYMBOLS = [
    "US:NVDA",
    "US:AMD",
    "US:AVGO",
    "US:MSFT",
    "US:AAPL",
    "US:GOOGL",
    "US:AMZN",
    "US:META",
    "US:TSLA",
    "US:SPY",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run probability validation suite over symbols/as-of dates (DB-only)."
    )
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols.")
    p.add_argument("--asof-list", required=True, help="Comma-separated as-of dates (YYYY-MM-DD).")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--horizon", type=int, required=True)
    p.add_argument("--flat_band", type=float, required=True)
    p.add_argument("--calibration", required=True, choices=["sigmoid", "isotonic"])
    p.add_argument(
        "--db-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --db-check/--no-db-check to run_backtest_nvda.py",
    )
    p.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --fast/--no-fast to run_backtest_nvda.py",
    )
    p.add_argument(
        "--meta-band-mode",
        default="fixed",
        choices=["fixed", "quantile"],
        help="Pass-through meta band mode to run_backtest_nvda.py.",
    )
    p.add_argument(
        "--meta-band-q",
        type=float,
        default=0.65,
        help="Pass-through meta band quantile.",
    )
    p.add_argument(
        "--meta-band-min-pct",
        type=float,
        default=0.5,
        help="Pass-through meta band min pct.",
    )
    p.add_argument(
        "--meta-band-max-pct",
        type=float,
        default=10.0,
        help="Pass-through meta band max pct.",
    )
    p.add_argument(
        "--label-mode",
        default="fixed",
        choices=["fixed", "tbm"],
        help="Pass-through label mode to run_backtest_nvda.py.",
    )
    p.add_argument(
        "--tbm-vol-span",
        type=int,
        default=20,
        help="Pass-through TBM vol span.",
    )
    p.add_argument(
        "--tbm-k",
        type=float,
        default=1.5,
        help="Pass-through TBM barrier multiplier.",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Optional output JSON path (default: backend/ml_predictor_data/validation_suite_YYYYMMDD.json).",
    )
    return p.parse_args()


def _split_csv(raw: str) -> List[str]:
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def _extract_json_path(stdout: str, ticker: str, asof: str) -> Path:
    candidates = [ln.strip() for ln in (stdout or "").splitlines() if "json:" in ln.lower()]
    for ln in reversed(candidates):
        if "json:" in ln:
            return Path(ln.split("json:", 1)[1].strip())
    slug = str(ticker).split(":", 1)[-1].strip().lower()
    return Path("backend/ml_predictor_data") / f"backtest_{slug}_{asof.replace('-', '')}.json"


def _extract_preflight(stdout: str) -> Optional[Dict[str, Any]]:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("{") and "missing_symbols" in ln and "missing_ranges" in ln:
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
    return None


def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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


def _build_common_cmd(args: argparse.Namespace, symbol: str, asof: str) -> List[str]:
    lookback = "5y"
    try:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").date()
        asof_dt = datetime.strptime(asof, "%Y-%m-%d").date()
        days = max(1, (asof_dt - start_dt).days)
        lookback = f"{days}d"
    except ValueError:
        pass

    cmd = [
        sys.executable,
        "backend/scripts/run_backtest_nvda.py",
        "--ticker",
        symbol,
        "--asof",
        asof,
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
        "--lookback",
        lookback,
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
    return cmd


def _run_preflight(args: argparse.Namespace, symbol: str, asof: str) -> Tuple[bool, Dict[str, Any], str, str, int]:
    cmd = _build_common_cmd(args, symbol, asof)
    cmd.append("--preflight-only")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    preflight = _extract_preflight(proc.stdout) or {"missing_symbols": [], "missing_ranges": {}}
    ok = proc.returncode == 0 and not preflight.get("missing_symbols")
    return ok, preflight, proc.stdout or "", proc.stderr or "", int(proc.returncode)


def _case_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    metrics = payload.get("metrics", {}) or {}
    baselines = payload.get("baselines", {}) or {}
    prior = baselines.get("baseline_prior", {}) or {}
    mvs = baselines.get("model_vs_baseline", {}) or {}
    meta_metrics = payload.get("meta_metrics", {}) or {}
    fold_stats = meta_metrics.get("fold_stats") or []

    def _extreme_rate(f: Dict[str, Any]) -> bool:
        for k in ("meta_pos_rate_train", "meta_pos_rate_calib", "meta_pos_rate_test"):
            v = _as_float(f.get(k))
            if v is not None and (v < 0.01 or v > 0.99):
                return True
        return False

    extreme_fold_count = sum(1 for f in fold_stats if _extreme_rate(f))
    fallback_fold_count = sum(1 for f in fold_stats if f.get("side_fallback_used"))

    return {
        "logloss": _as_float(metrics.get("logloss")),
        "brier": _as_float(metrics.get("brier")),
        "ece": _as_float(metrics.get("ece")),
        "accuracy": _as_float(metrics.get("accuracy")),
        "macro_f1": _as_float(metrics.get("macro_f1")),
        "baseline_prior_logloss": _as_float(prior.get("logloss")),
        "baseline_prior_brier": _as_float(prior.get("brier")),
        "beats_prior_logloss": bool(mvs.get("beats_prior_logloss")) if mvs.get("beats_prior_logloss") is not None else None,
        "beats_prior_brier": bool(mvs.get("beats_prior_brier")) if mvs.get("beats_prior_brier") is not None else None,
        "fold_count": len(fold_stats),
        "extreme_fold_count": extreme_fold_count,
        "fallback_fold_count": fallback_fold_count,
    }


def _print_case_table(rows: List[Dict[str, Any]]) -> None:
    print("\n[SUITE] CASE RESULTS")
    print("symbol | asof | case | status | logloss | brier | ece | acc | f1 | prior_ll | prior_br | beat_prior(ll/br)")
    for r in rows:
        s = r.get("summary") or {}
        print(
            "{sym} | {asof} | {cid} | {st} | {ll} | {br} | {ece} | {acc} | {f1} | {pll} | {pbr} | {bll}/{bbr}".format(
                sym=r.get("symbol"),
                asof=r.get("asof"),
                cid=r.get("case_id"),
                st=r.get("status"),
                ll=_fmt(s.get("logloss")),
                br=_fmt(s.get("brier")),
                ece=_fmt(s.get("ece")),
                acc=_fmt(s.get("accuracy")),
                f1=_fmt(s.get("macro_f1")),
                pll=_fmt(s.get("baseline_prior_logloss")),
                pbr=_fmt(s.get("baseline_prior_brier")),
                bll=s.get("beats_prior_logloss"),
                bbr=s.get("beats_prior_brier"),
            )
        )


def _evaluate(meta_off_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    eligible_pairs = {}
    pair_win_count = 0

    ll_model_vals: List[float] = []
    ll_prior_vals: List[float] = []

    total_folds = 0
    extreme_folds = 0
    fallback_folds = 0

    for r in meta_off_rows:
        s = r.get("summary") or {}
        key = (r.get("symbol"), r.get("asof"))
        ll = _as_float(s.get("logloss"))
        br = _as_float(s.get("brier"))
        pll = _as_float(s.get("baseline_prior_logloss"))
        pbr = _as_float(s.get("baseline_prior_brier"))
        if None not in (ll, br, pll, pbr):
            wins = bool(ll < pll or br < pbr)
            eligible_pairs[key] = {
                "win": wins,
                "logloss": ll,
                "brier": br,
                "prior_logloss": pll,
                "prior_brier": pbr,
            }
            ll_model_vals.append(ll)
            ll_prior_vals.append(pll)
        total_folds += int(s.get("fold_count") or 0)
        extreme_folds += int(s.get("extreme_fold_count") or 0)
        fallback_folds += int(s.get("fallback_fold_count") or 0)

    if eligible_pairs:
        pair_win_count = sum(1 for v in eligible_pairs.values() if v.get("win"))
    pair_count = len(eligible_pairs)
    win_rate = (pair_win_count / pair_count) if pair_count > 0 else 0.0

    avg_ll_model = mean(ll_model_vals) if ll_model_vals else None
    avg_ll_prior = mean(ll_prior_vals) if ll_prior_vals else None
    avg_ll_delta = (avg_ll_model - avg_ll_prior) if (avg_ll_model is not None and avg_ll_prior is not None) else None

    extreme_ratio = (extreme_folds / total_folds) if total_folds > 0 else 1.0
    fallback_ratio = (fallback_folds / total_folds) if total_folds > 0 else 1.0

    cond_a = win_rate >= 0.60
    cond_b = (avg_ll_model is not None and avg_ll_prior is not None and avg_ll_model < avg_ll_prior)
    cond_c1 = extreme_ratio < 0.05
    cond_c2 = fallback_ratio < 0.10

    return {
        "eligible_pair_count": pair_count,
        "pair_win_count": pair_win_count,
        "win_rate": win_rate,
        "avg_meta_off_logloss": avg_ll_model,
        "avg_prior_logloss": avg_ll_prior,
        "avg_logloss_delta_meta_minus_prior": avg_ll_delta,
        "total_fold_count": total_folds,
        "extreme_fold_count": extreme_folds,
        "extreme_fold_ratio": extreme_ratio,
        "side_fallback_fold_count": fallback_folds,
        "side_fallback_fold_ratio": fallback_ratio,
        "condition_a": cond_a,
        "condition_b": bool(cond_b),
        "condition_c1": cond_c1,
        "condition_c2": cond_c2,
        "passed": bool(cond_a and cond_b and cond_c1 and cond_c2),
        "per_pair": [
            {
                "symbol": k[0],
                "asof": k[1],
                **v,
            }
            for k, v in sorted(eligible_pairs.items())
        ],
    }


def main() -> int:
    args = _parse_args()
    symbols = _split_csv(args.symbols)
    asof_list = _split_csv(args.asof_list)
    if not symbols:
        raise ValueError("--symbols must include at least one symbol")
    if not asof_list:
        raise ValueError("--asof-list must include at least one date")

    out_dir = Path("backend/ml_predictor_data")
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = [
        ("meta_on", "meta", "on"),
        ("meta_off", "meta", "off"),
        ("direct_on", "direct", "on"),
        ("direct_off", "direct", "off"),
    ]

    missing_items: List[Dict[str, Any]] = []
    for sym in symbols:
        for asof in asof_list:
            print(f"\n[PREFLIGHT] {sym} @ {asof}")
            ok, pf, out, err, rc = _run_preflight(args, sym, asof)
            if out:
                print(out.rstrip())
            if err:
                print(err.rstrip())
            if not ok:
                miss = {
                    "symbol": sym,
                    "asof": asof,
                    "return_code": rc,
                    "missing_symbols": pf.get("missing_symbols") or [],
                    "missing_ranges": pf.get("missing_ranges") or {},
                }
                missing_items.append(miss)

    if missing_items:
        print("\n[ERROR] Missing DB data detected. No external fetch will be executed.")
        for m in missing_items:
            print(
                "symbol={sym} asof={asof} missing_symbols={ms} missing_ranges={mr}".format(
                    sym=m["symbol"],
                    asof=m["asof"],
                    ms=m["missing_symbols"],
                    mr=m["missing_ranges"],
                )
            )
        print("Run existing refresh workflow for missing ranges, then rerun this suite.")
        return 1

    all_rows: List[Dict[str, Any]] = []
    for sym in symbols:
        for asof in asof_list:
            for case_id, prob_mode, temp_scale in cases:
                cmd = _build_common_cmd(args, sym, asof)
                cmd.extend(["--prob-mode", prob_mode, "--temp-scale", temp_scale])

                print(f"\n[RUN] {sym} {asof} {case_id}: {' '.join(cmd)}")
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.stdout:
                    print(proc.stdout.rstrip())
                if proc.stderr:
                    print(proc.stderr.rstrip())

                json_path = _extract_json_path(proc.stdout or "", sym, asof)
                payload = _safe_load_json(json_path)
                row = {
                    "symbol": sym,
                    "asof": asof,
                    "case_id": case_id,
                    "prob_mode": prob_mode,
                    "temp_scale": temp_scale,
                    "status": "OK" if proc.returncode == 0 and payload is not None else "FAILED",
                    "return_code": int(proc.returncode),
                    "json_path": str(json_path),
                    "summary": _case_summary(payload) if payload is not None else {},
                }
                all_rows.append(row)

    _print_case_table(all_rows)

    meta_off_rows = [r for r in all_rows if r.get("case_id") == "meta_off" and r.get("status") == "OK"]
    judge = _evaluate(meta_off_rows)

    print("\n[SUITE] JUDGEMENT")
    print(
        "A win_rate(meta_off vs prior, ll or br)={wr} ({w}/{n}) >= 0.60 -> {ok}".format(
            wr=_fmt(judge.get("win_rate")),
            w=judge.get("pair_win_count"),
            n=judge.get("eligible_pair_count"),
            ok=judge.get("condition_a"),
        )
    )
    print(
        "B avg_logloss(meta_off)={mll} < avg_logloss(prior)={pll} delta={d} -> {ok}".format(
            mll=_fmt(judge.get("avg_meta_off_logloss")),
            pll=_fmt(judge.get("avg_prior_logloss")),
            d=_fmt(judge.get("avg_logloss_delta_meta_minus_prior")),
            ok=judge.get("condition_b"),
        )
    )
    print(
        "C1 extreme_meta_pos_rate_fold_ratio={r} ({e}/{t}) < 0.05 -> {ok}".format(
            r=_fmt(judge.get("extreme_fold_ratio")),
            e=judge.get("extreme_fold_count"),
            t=judge.get("total_fold_count"),
            ok=judge.get("condition_c1"),
        )
    )
    print(
        "C2 side_fallback_fold_ratio={r} ({f}/{t}) < 0.10 -> {ok}".format(
            r=_fmt(judge.get("side_fallback_fold_ratio")),
            f=judge.get("side_fallback_fold_count"),
            t=judge.get("total_fold_count"),
            ok=judge.get("condition_c2"),
        )
    )

    suite_payload = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "symbols": symbols,
            "asof_list": asof_list,
            "start": args.start,
            "end": args.end,
            "horizon": args.horizon,
            "flat_band": args.flat_band,
            "calibration": args.calibration,
            "db_check": args.db_check,
            "fast": args.fast,
            "meta_band_mode": args.meta_band_mode,
            "meta_band_q": args.meta_band_q,
            "meta_band_min_pct": args.meta_band_min_pct,
            "meta_band_max_pct": args.meta_band_max_pct,
            "label_mode": args.label_mode,
            "tbm_vol_span": args.tbm_vol_span,
            "tbm_k": args.tbm_k,
        },
        "cases": all_rows,
        "judgement": judge,
    }
    out_path = Path(args.out) if args.out else (out_dir / f"validation_suite_{datetime.now().strftime('%Y%m%d')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(suite_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nvalidation_suite_json={out_path}")

    verdict = "PASS" if judge.get("passed") else "FAIL"
    print(f"\nVALIDATION_SUITE_RESULT: {verdict}")
    return 0 if judge.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
