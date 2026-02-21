from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List
import statistics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize backtest JSON for quick human review."
    )
    parser.add_argument("--path", required=True, help="Path to backtest JSON.")
    parser.add_argument(
        "--audit-n",
        type=int,
        default=5,
        help="How many rows from interval_audit_sample to print (default: 5).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output path to save summary JSON.",
    )
    return parser.parse_args()


def _fmt(x: Any, nd: int = 4) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _to_summary(payload: Dict[str, Any], audit_n: int) -> Dict[str, Any]:
    metrics = payload.get("metrics", {}) or {}
    backtest = payload.get("backtest", {}) or {}
    baselines = payload.get("baselines", {}) or {}
    diagnostics = payload.get("diagnostics", {}) or {}
    meta = payload.get("meta", {}) or {}
    interval_audit = payload.get("interval_audit", {}) or {}
    meta_metrics = payload.get("meta_metrics", {}) or {}
    temperature_scaling = payload.get("temperature_scaling", {}) or {}

    metric_summary = {
        "accuracy": metrics.get("accuracy"),
        "macro_f1": metrics.get("macro_f1"),
        "logloss": metrics.get("logloss"),
        "brier": metrics.get("brier"),
        "ece": metrics.get("ece"),
        "interval_target_coverage": metrics.get("interval_target_coverage"),
        "interval_coverage": metrics.get("interval_coverage"),
        "interval_width_mean": metrics.get("interval_width_mean"),
        "best_flat_band_pct": payload.get("best_flat_band_pct"),
        "interval_q": metrics.get("interval_q"),
        "bias": metrics.get("bias"),
        "bias_abs": metrics.get("bias_abs"),
    }

    prior = baselines.get("baseline_prior", {}) or {}
    momentum = baselines.get("baseline_momentum", {}) or {}
    mvb = baselines.get("model_vs_baseline", {}) or {}
    baseline_summary = {
        "baseline_prior": {
            "logloss": prior.get("logloss"),
            "brier": prior.get("brier"),
        },
        "baseline_momentum": {
            "logloss": momentum.get("logloss"),
            "brier": momentum.get("brier"),
        },
        "model_vs_baseline": mvb,
    }

    class_perf = {
        "confusion_matrix": diagnostics.get("confusion_matrix"),
        "class_metrics": diagnostics.get("class_metrics"),
        "class_ratio": diagnostics.get("class_ratio"),
    }

    audit_rows: List[Dict[str, Any]] = (interval_audit.get("sample") or [])[: max(0, int(audit_n))]
    audit_summary = {
        "interval_target_mismatch_warn": interval_audit.get("target_mismatch_warn", False),
        "interval_target_name": interval_audit.get("target_name", meta.get("target_name")),
        "interval_target_unit": interval_audit.get("target_unit", meta.get("target_unit")),
        "rows_head": audit_rows,
    }

    return {
        "metrics": metric_summary,
        "baselines": baseline_summary,
        "class_performance": class_perf,
        "interval_audit": audit_summary,
        "meta_metrics": meta_metrics,
        "temperature_scaling": temperature_scaling,
    }


def _diagnostic_comment(summary: Dict[str, Any]) -> str:
    m = summary["metrics"]
    b = summary["baselines"]
    logloss = m.get("logloss")
    prior_ll = (b.get("baseline_prior") or {}).get("logloss")
    target_cov = m.get("interval_target_coverage")
    actual_cov = m.get("interval_coverage")
    mismatch = summary["interval_audit"].get("interval_target_mismatch_warn")
    mm = summary.get("meta_metrics") or {}
    ts = summary.get("temperature_scaling") or {}
    p_trade_std = mm.get("p_trade_std")
    side_train = mm.get("side_samples_train")
    warns = mm.get("warnings") or []
    temp_enabled = ts.get("enabled")
    best_t = ts.get("best_T")

    if isinstance(p_trade_std, (int, float)) and p_trade_std < 0.02:
        return "Meta p_trade is almost constant (std<0.02) -> meta model may be non-informative."
    if isinstance(side_train, (int, float)) and side_train < 200:
        return "Side samples are too few -> band/label setting likely causing weak side model."
    if len(warns) > 0:
        return "Side fallback warnings detected -> check label balance and flat_band setting."
    if temp_enabled and isinstance(best_t, (int, float)) and (best_t <= 0.55 or best_t >= 2.95):
        return "Best temperature is at grid edge -> current temperature range/model calibration may be unstable."

    if isinstance(logloss, (int, float)) and isinstance(prior_ll, (int, float)) and logloss > prior_ll:
        return "Model logloss worse than baseline_prior -> probability model may not add value."
    if (
        isinstance(target_cov, (int, float))
        and isinstance(actual_cov, (int, float))
        and abs(actual_cov - target_cov) >= 0.15
    ):
        return "Interval coverage far from target -> check q calibration and target alignment."
    if mismatch:
        return "Interval target mismatch flag is ON -> verify y_true/y_pred target/unit consistency."
    return "No immediate red flag from headline metrics; inspect class-wise errors and interval rows."


def _print_summary(summary: Dict[str, Any], comment: str) -> None:
    m = summary["metrics"]
    b = summary["baselines"]
    c = summary["class_performance"]
    ia = summary["interval_audit"]
    mm = summary.get("meta_metrics") or {}
    ts = summary.get("temperature_scaling") or {}

    print("[1] METRICS")
    print(
        "accuracy={a} macro_f1={f1} logloss={ll} brier={br} ece={ece}".format(
            a=_fmt(m.get("accuracy")),
            f1=_fmt(m.get("macro_f1")),
            ll=_fmt(m.get("logloss")),
            br=_fmt(m.get("brier")),
            ece=_fmt(m.get("ece")),
        )
    )
    print(
        "interval_target_coverage={t} interval_coverage={c} interval_width_mean={w} interval_q={q} bias={b} bias_abs={ba}".format(
            t=_fmt(m.get("interval_target_coverage")),
            c=_fmt(m.get("interval_coverage")),
            w=_fmt(m.get("interval_width_mean")),
            q=_fmt(m.get("interval_q")),
            b=_fmt(m.get("bias")),
            ba=_fmt(m.get("bias_abs")),
        )
    )
    if m.get("best_flat_band_pct") is not None:
        print(f"best_flat_band_pct={_fmt(m.get('best_flat_band_pct'))}")

    print("\n[2] BASELINES")
    prior = b.get("baseline_prior", {})
    mom = b.get("baseline_momentum", {})
    mvb = b.get("model_vs_baseline", {})
    print(
        "prior: logloss={ll} brier={br}".format(
            ll=_fmt(prior.get("logloss")),
            br=_fmt(prior.get("brier")),
        )
    )
    print(
        "momentum: logloss={ll} brier={br}".format(
            ll=_fmt(mom.get("logloss")),
            br=_fmt(mom.get("brier")),
        )
    )
    print(
        "wins: prior(logloss={pll}, brier={pbr}) momentum(logloss={mll}, brier={mbr})".format(
            pll=mvb.get("beats_prior_logloss"),
            pbr=mvb.get("beats_prior_brier"),
            mll=mvb.get("beats_momentum_logloss"),
            mbr=mvb.get("beats_momentum_brier"),
        )
    )

    print("\n[3] CLASS PERFORMANCE")
    print(f"confusion_matrix={c.get('confusion_matrix')}")
    class_metrics = c.get("class_metrics") or {}
    if class_metrics:
        line = []
        for cls in ("down", "flat", "up"):
            met = class_metrics.get(cls) or {}
            line.append(
                "{k}(p={p},r={r},f1={f})".format(
                    k=cls,
                    p=_fmt(met.get("precision")),
                    r=_fmt(met.get("recall")),
                    f=_fmt(met.get("f1")),
                )
            )
        print("class_metrics=" + " ".join(line))
    else:
        print("class_metrics=-")
    print(f"class_ratio={c.get('class_ratio')}")

    print("\n[3.5] META/TEMP")
    if mm:
        warns = mm.get("warnings") or []
        fold_stats = mm.get("fold_stats") or []
        print(
            "meta_metrics: p_trade_mean={pm} p_trade_std={ps} side(train/calib/test)=({st}/{sc}/{se}) warnings={wc}".format(
                pm=_fmt(mm.get("p_trade_mean")),
                ps=_fmt(mm.get("p_trade_std")),
                st=mm.get("side_samples_train"),
                sc=mm.get("side_samples_calib"),
                se=mm.get("side_samples_test"),
                wc=len(warns),
            )
        )
        if warns:
            print("warnings_head=" + " | ".join([str(w) for w in warns[:3]]))
        if fold_stats:
            pstd_vals = [float(f.get("p_trade_std_test", 0.0)) for f in fold_stats if f.get("p_trade_std_test") is not None]
            mrate_vals = [float(f.get("meta_pos_rate_train", 0.0)) for f in fold_stats if f.get("meta_pos_rate_train") is not None]
            sf_vals = [1 if f.get("side_fallback_used") else 0 for f in fold_stats]
            if pstd_vals:
                print(
                    "fold_p_trade_std(min/med/max)={mn}/{md}/{mx}".format(
                        mn=_fmt(min(pstd_vals)),
                        md=_fmt(statistics.median(pstd_vals)),
                        mx=_fmt(max(pstd_vals)),
                    )
                )
            if mrate_vals:
                print(
                    "fold_meta_pos_rate_train(min/med/max)={mn}/{md}/{mx}".format(
                        mn=_fmt(min(mrate_vals)),
                        md=_fmt(statistics.median(mrate_vals)),
                        mx=_fmt(max(mrate_vals)),
                    )
                )
            if sf_vals:
                print(f"fold_side_fallback_count={sum(sf_vals)}/{len(sf_vals)}")
            collapse_flags = []
            if pstd_vals and statistics.median(pstd_vals) < 0.02:
                collapse_flags.append("p_trade almost constant")
            if sf_vals and (sum(sf_vals) / max(1, len(sf_vals))) > 0.3:
                collapse_flags.append("side fallback frequent")
            if mrate_vals and (min(mrate_vals) < 0.01 or max(mrate_vals) > 0.99):
                collapse_flags.append("meta label rate extreme")
            if collapse_flags:
                print("collapse_flags=" + ", ".join(collapse_flags))
    else:
        print("meta_metrics=-")
    if ts:
        print(
            "temperature_scaling: enabled={en} best_T={bt}".format(
                en=ts.get("enabled"),
                bt=_fmt(ts.get("best_T")),
            )
        )
    else:
        print("temperature_scaling=-")

    print("\n[4] INTERVAL AUDIT (HEAD N)")
    print(
        "mismatch_warn={mw} target={tn}/{tu}".format(
            mw=ia.get("interval_target_mismatch_warn"),
            tn=ia.get("interval_target_name"),
            tu=ia.get("interval_target_unit"),
        )
    )
    rows = ia.get("rows_head") or []
    if not rows:
        print("rows=-")
    else:
        for r in rows:
            idx = r.get("date", r.get("t_index"))
            print(
                "{idx} y_true={yt} y_pred={yp} y_true_raw={ytr} y_pred_raw={ypr} q={q} lower={lo} upper={up} covered={cv} raw_t={rt} raw_h={rh} bias={b}".format(
                    idx=idx,
                    yt=_fmt(r.get("y_true")),
                    yp=_fmt(r.get("y_pred")),
                    ytr=_fmt(r.get("y_true_raw")),
                    ypr=_fmt(r.get("y_pred_raw")),
                    q=_fmt(r.get("q")),
                    lo=_fmt(r.get("lower")),
                    up=_fmt(r.get("upper")),
                    cv=r.get("covered"),
                    rt=_fmt(r.get("raw_close_t")),
                    rh=_fmt(r.get("raw_close_future")),
                    b=_fmt(r.get("bias")),
                )
            )

    print("\nDIAGNOSTIC: " + comment)


def main() -> int:
    args = _parse_args()
    path = Path(args.path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = _to_summary(payload, audit_n=args.audit_n)
    comment = _diagnostic_comment(summary)
    _print_summary(summary, comment)

    if args.out:
        out_path = Path(args.out)
        out_payload = {"summary": summary, "diagnostic_comment": comment}
        out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nsummary_json={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
