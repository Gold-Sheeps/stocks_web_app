from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rank validation-suite failures and show root-cause culprits."
    )
    p.add_argument("--path", required=True, help="Path to validation_suite_YYYYMMDD.json")
    p.add_argument("--top", type=int, default=10, help="Top N rows to display (default: 10)")
    p.add_argument("--out", default=None, help="Optional output JSON path")
    return p.parse_args()


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


def _symbol_slug(symbol: str) -> str:
    raw = str(symbol).split(":", 1)[-1].lower()
    out = "".join(ch for ch in raw if ch.isalnum())
    return out or "ticker"


def _derive_candidate_paths(case_json_path: Optional[str], symbol: str, asof: str) -> List[Path]:
    out: List[Path] = []
    ymd = asof.replace("-", "")
    slug = _symbol_slug(symbol)

    if case_json_path:
        base = Path(case_json_path)
        out.append(base)
        if base.suffix == ".json":
            out.append(base.with_name(f"{base.stem}_meta_off.json"))
        if base.parent:
            out.append(base.parent / f"backtest_{slug}_{ymd}_meta_off.json")
            out.append(base.parent / f"backtest_{slug}_{ymd}.json")

    local_dir = Path("backend/ml_predictor_data")
    out.append(local_dir / f"backtest_{slug}_{ymd}_meta_off.json")
    out.append(local_dir / f"backtest_{slug}_{ymd}.json")

    uniq: List[Path] = []
    seen = set()
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _extract_fold_stats_from_payload(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    meta_metrics = payload.get("meta_metrics") or {}
    fold_stats = meta_metrics.get("fold_stats") or []
    return fold_stats if isinstance(fold_stats, list) else []


def _choose_best_fold_stats(symbol: str, asof: str, preferred_json_path: Optional[str]) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
    probes: List[str] = []
    for cand in _derive_candidate_paths(preferred_json_path, symbol, asof):
        probes.append(str(cand))
        payload = _safe_load_json(cand)
        fold_stats = _extract_fold_stats_from_payload(payload)
        if fold_stats:
            return fold_stats, probes, str(cand)
    return [], probes, None


def _find_meta_off_rows(cases: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in cases:
        if c.get("case_id") != "meta_off":
            continue
        key = (str(c.get("symbol")), str(c.get("asof")))
        out[key] = c
    return out


def _build_case_path_map(cases: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, str]]:
    out: Dict[Tuple[str, str], Dict[str, str]] = {}
    for c in cases:
        key = (str(c.get("symbol")), str(c.get("asof")))
        out.setdefault(key, {})[str(c.get("case_id"))] = str(c.get("json_path") or "")
    return out


def _culprit_analysis(fold_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    c1_ids: List[Any] = []
    c1_values: List[Dict[str, Any]] = []
    c2_ids: List[Any] = []
    pstd_small_ids: List[Any] = []

    for i, f in enumerate(fold_stats):
        fid = f.get("fold_id", i)
        t = _as_float(f.get("meta_pos_rate_train"))
        c = _as_float(f.get("meta_pos_rate_calib"))
        e = _as_float(f.get("meta_pos_rate_test"))

        extreme_keys = {}
        for k, v in (("train", t), ("calib", c), ("test", e)):
            if v is not None and (v < 0.01 or v > 0.99):
                extreme_keys[k] = v
        if extreme_keys:
            c1_ids.append(fid)
            c1_values.append({"fold_id": fid, "extreme_meta_pos_rate": extreme_keys})

        if bool(f.get("side_fallback_used")):
            c2_ids.append(fid)

        pstd = _as_float(f.get("p_trade_std_test"))
        if pstd is not None and pstd < 0.01:
            pstd_small_ids.append(fid)

    return {
        "c1_fold_ids": c1_ids,
        "c1_values": c1_values,
        "c2_fold_ids": c2_ids,
        "p_trade_std_small_fold_ids": pstd_small_ids,
    }


def _make_note(a_fail: bool, c1_cnt: int, c2_cnt: int, pstd_cnt: int, d_ll: Optional[float], d_br: Optional[float], fold_stats_found: bool) -> str:
    if a_fail and (d_ll is not None and d_br is not None):
        return f"A_fail: loses to prior (delta_ll={d_ll:.4f}, delta_br={d_br:.4f})"
    if not fold_stats_found:
        return "fold_stats missing: check meta_off backtest JSON path"
    if c1_cnt > 0:
        return f"C1_fail: extreme meta_pos_rate folds={c1_cnt}"
    if c2_cnt > 0:
        return f"C2_fail: side_fallback folds={c2_cnt}"
    if pstd_cnt > 0:
        return f"collapse signal: p_trade_std<0.01 folds={pstd_cnt}"
    return "no major collapse flag"


def _aggregate(top_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    eligible = [r for r in top_rows if r.get("delta_logloss") is not None and r.get("delta_brier") is not None]
    pair_count = len(eligible)
    a_win_count = sum(1 for r in eligible if not r.get("A_fail"))
    a_win_rate = (a_win_count / pair_count) if pair_count > 0 else 0.0

    model_lls = [r["model_logloss"] for r in eligible if r.get("model_logloss") is not None]
    prior_lls = [r["prior_logloss"] for r in eligible if r.get("prior_logloss") is not None]
    model_brs = [r["model_brier"] for r in eligible if r.get("model_brier") is not None]
    prior_brs = [r["prior_brier"] for r in eligible if r.get("prior_brier") is not None]

    mean_model_ll = mean(model_lls) if model_lls else None
    mean_prior_ll = mean(prior_lls) if prior_lls else None
    mean_model_br = mean(model_brs) if model_brs else None
    mean_prior_br = mean(prior_brs) if prior_brs else None

    total_folds = sum(int(r.get("fold_count") or 0) for r in top_rows)
    c1_folds = sum(int(r.get("c1_fold_count") or 0) for r in top_rows)
    c2_folds = sum(int(r.get("c2_fold_count") or 0) for r in top_rows)
    c1_rate = (c1_folds / total_folds) if total_folds > 0 else 1.0
    c2_rate = (c2_folds / total_folds) if total_folds > 0 else 1.0

    cond_a = a_win_rate >= 0.60
    cond_b = bool(mean_model_ll is not None and mean_prior_ll is not None and mean_model_ll < mean_prior_ll)
    cond_c1 = c1_rate < 0.05
    cond_c2 = c2_rate < 0.10
    final = "PASS" if (cond_a and cond_b and cond_c1 and cond_c2) else "FAIL"

    return {
        "A_win_rate": a_win_rate,
        "A_win_count": a_win_count,
        "A_pair_count": pair_count,
        "mean_logloss": {
            "meta_off": mean_model_ll,
            "prior": mean_prior_ll,
            "delta_meta_minus_prior": (mean_model_ll - mean_prior_ll) if (mean_model_ll is not None and mean_prior_ll is not None) else None,
        },
        "mean_brier": {
            "meta_off": mean_model_br,
            "prior": mean_prior_br,
            "delta_meta_minus_prior": (mean_model_br - mean_prior_br) if (mean_model_br is not None and mean_prior_br is not None) else None,
        },
        "C1_rate": c1_rate,
        "C1_fold_count": c1_folds,
        "C2_rate": c2_rate,
        "C2_fold_count": c2_folds,
        "total_fold_count": total_folds,
        "conditions": {
            "A": cond_a,
            "B": cond_b,
            "C1": cond_c1,
            "C2": cond_c2,
        },
        "final_pass_fail": final,
    }


def _print_top(rows: List[Dict[str, Any]], top_n: int) -> None:
    print("[TOP FAILURES]")
    print("rank | symbol | asof | score | delta_ll | delta_br | flags(A/C1/C2) | c1_folds | c2_folds | note")
    for i, r in enumerate(rows[: max(0, top_n)], start=1):
        print(
            "{rank} | {sym} | {asof} | {score} | {dll} | {dbr} | {a}/{c1}/{c2} | {c1ids} | {c2ids} | {note}".format(
                rank=i,
                sym=r.get("symbol"),
                asof=r.get("asof"),
                score=_fmt(r.get("severity_score")),
                dll=_fmt(r.get("delta_logloss")),
                dbr=_fmt(r.get("delta_brier")),
                a=r.get("A_fail"),
                c1=r.get("C1_fail"),
                c2=r.get("C2_fail"),
                c1ids=r.get("c1_fold_ids"),
                c2ids=r.get("c2_fold_ids"),
                note=r.get("note"),
            )
        )


def _print_aggregate(agg: Dict[str, Any]) -> None:
    print("\n[AGGREGATE]")
    print(
        "A_win_rate={wr} ({w}/{n})".format(
            wr=_fmt(agg.get("A_win_rate")),
            w=agg.get("A_win_count"),
            n=agg.get("A_pair_count"),
        )
    )
    ml = agg.get("mean_logloss") or {}
    mb = agg.get("mean_brier") or {}
    print(
        "mean_logloss(meta_off/prior/delta)={m}/{p}/{d}".format(
            m=_fmt(ml.get("meta_off")),
            p=_fmt(ml.get("prior")),
            d=_fmt(ml.get("delta_meta_minus_prior")),
        )
    )
    print(
        "mean_brier(meta_off/prior/delta)={m}/{p}/{d}".format(
            m=_fmt(mb.get("meta_off")),
            p=_fmt(mb.get("prior")),
            d=_fmt(mb.get("delta_meta_minus_prior")),
        )
    )
    print(
        "C1_rate={r} ({c}/{t})".format(
            r=_fmt(agg.get("C1_rate")),
            c=agg.get("C1_fold_count"),
            t=agg.get("total_fold_count"),
        )
    )
    print(
        "C2_rate={r} ({c}/{t})".format(
            r=_fmt(agg.get("C2_rate")),
            c=agg.get("C2_fold_count"),
            t=agg.get("total_fold_count"),
        )
    )
    c = agg.get("conditions") or {}
    print(f"conditions: A={c.get('A')} B={c.get('B')} C1={c.get('C1')} C2={c.get('C2')}")
    print(f"FINAL: {agg.get('final_pass_fail')}")


def main() -> int:
    args = _parse_args()
    in_path = Path(args.path)
    suite = _safe_load_json(in_path)
    if suite is None:
        raise FileNotFoundError(f"Could not load JSON: {in_path}")

    cases = suite.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError("validation_suite JSON: cases must be a list")

    meta_off_map = _find_meta_off_rows(cases)
    case_paths_map = _build_case_path_map(cases)

    ranked_rows: List[Dict[str, Any]] = []
    for (symbol, asof), row in sorted(meta_off_map.items()):
        summary = row.get("summary") or {}
        model_ll = _as_float(summary.get("logloss"))
        prior_ll = _as_float(summary.get("baseline_prior_logloss"))
        model_br = _as_float(summary.get("brier"))
        prior_br = _as_float(summary.get("baseline_prior_brier"))
        d_ll = (model_ll - prior_ll) if (model_ll is not None and prior_ll is not None) else None
        d_br = (model_br - prior_br) if (model_br is not None and prior_br is not None) else None

        a_fail = bool(d_ll is not None and d_br is not None and d_ll >= 0 and d_br >= 0)

        fold_stats, probes, selected = _choose_best_fold_stats(symbol, asof, row.get("json_path"))
        culprits = _culprit_analysis(fold_stats)
        c1_cnt = len(culprits["c1_fold_ids"])
        c2_cnt = len(culprits["c2_fold_ids"])
        pstd_cnt = len(culprits["p_trade_std_small_fold_ids"])

        c1_fail = c1_cnt > 0
        c2_fail = c2_cnt > 0

        severity = 0.0
        severity += 100.0 * max(0.0, d_ll or 0.0)
        severity += 200.0 * max(0.0, d_br or 0.0)
        severity += 20.0 * c1_cnt
        severity += 10.0 * c2_cnt
        severity += 5.0 * pstd_cnt

        note = _make_note(a_fail, c1_cnt, c2_cnt, pstd_cnt, d_ll, d_br, bool(fold_stats))

        ranked_rows.append(
            {
                "symbol": symbol,
                "asof": asof,
                "severity_score": severity,
                "model_logloss": model_ll,
                "prior_logloss": prior_ll,
                "delta_logloss": d_ll,
                "model_brier": model_br,
                "prior_brier": prior_br,
                "delta_brier": d_br,
                "A_fail": a_fail,
                "C1_fail": c1_fail,
                "C2_fail": c2_fail,
                "fold_count": len(fold_stats) if fold_stats else int(summary.get("fold_count") or 0),
                "c1_fold_count": c1_cnt if fold_stats else int(summary.get("extreme_fold_count") or 0),
                "c2_fold_count": c2_cnt if fold_stats else int(summary.get("fallback_fold_count") or 0),
                "c1_fold_ids": culprits["c1_fold_ids"],
                "c2_fold_ids": culprits["c2_fold_ids"],
                "p_trade_std_small_fold_ids": culprits["p_trade_std_small_fold_ids"],
                "culprit_folds": {
                    "c1": culprits["c1_values"],
                    "c2": [{"fold_id": x} for x in culprits["c2_fold_ids"]],
                    "p_trade_std_small": [{"fold_id": x} for x in culprits["p_trade_std_small_fold_ids"]],
                },
                "note": note,
                "case_json_paths": case_paths_map.get((symbol, asof), {}),
                "meta_off_json_path_selected": selected,
                "meta_off_json_path_probed": probes,
            }
        )

    ranked_rows.sort(key=lambda r: float(r.get("severity_score") or 0.0), reverse=True)
    agg = _aggregate(ranked_rows)

    _print_top(ranked_rows, args.top)
    _print_aggregate(agg)

    out_payload = {
        "metadata": {
            "input_path": str(in_path),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "top": args.top,
        },
        "top_failures": ranked_rows[: max(0, int(args.top))],
        "aggregate": agg,
    }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nranked_json={out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
