from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


EPS_DEFAULT = 1e-12


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Print latest 3-class probabilities (UP/DOWN/FLAT) from backtest JSON."
    )
    p.add_argument("--path", required=True, help="Path to backtest JSON.")
    p.add_argument(
        "--mode",
        default="meta",
        choices=["meta", "direct"],
        help="Probability mode hint (default: meta).",
    )
    p.add_argument("--out", default=None, help="Optional output path for latest probs JSON.")
    p.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Print raw (pre-rounding) probabilities on an extra line.",
    )
    p.add_argument(
        "--recompute-if-missing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="If keys are missing, recompute latest 1-point probs via DB-only prediction.",
    )
    return p.parse_args()


def _as_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
        return v if math.isfinite(v) else None
    return None


def _iter_nodes(x: Any, path: str = "") -> Iterable[Tuple[str, Any]]:
    yield path, x
    if isinstance(x, dict):
        for k, v in x.items():
            p = f"{path}.{k}" if path else k
            yield from _iter_nodes(v, p)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            p = f"{path}[{i}]"
            yield from _iter_nodes(v, p)


def _normalize_probs(p_up: float, p_down: float, p_flat: float, eps: float) -> Tuple[float, float, float, bool]:
    vals = [p_up, p_down, p_flat]
    clipped = []
    clip_before_norm = False
    for v in vals:
        c = min(1.0 - eps, max(eps, v))
        if c != v:
            clip_before_norm = True
        clipped.append(c)
    s = sum(clipped)
    if s <= 0:
        raise ValueError("Invalid probabilities: sum after clipping is not positive.")
    return clipped[0] / s, clipped[1] / s, clipped[2] / s, clip_before_norm


def _round_pct_sum_100(p_up: float, p_down: float, p_flat: float) -> Tuple[int, int, int]:
    raw = [100.0 * p_up, 100.0 * p_down, 100.0 * p_flat]
    out = [int(math.floor(v + 0.5)) for v in raw]
    diff = 100 - sum(out)
    if diff == 0:
        return out[0], out[1], out[2]

    if diff > 0:
        order = sorted(range(3), key=lambda i: (raw[i] - out[i], raw[i]), reverse=True)
        for i in range(diff):
            out[order[i % 3]] += 1
    else:
        order = sorted(range(3), key=lambda i: (out[i] - raw[i], out[i]), reverse=True)
        need = -diff
        idx = 0
        while need > 0 and idx < 1000:
            j = order[idx % 3]
            if out[j] > 0:
                out[j] -= 1
                need -= 1
            idx += 1
        if need != 0:
            raise ValueError("Failed to adjust rounded percentages to 100.")
    return out[0], out[1], out[2]


def _extract_latest_triplet(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    prob_sets = [
        ("p_up", "p_down", "p_flat"),
        ("prob_up", "prob_down", "prob_flat"),
        ("up_prob", "down_prob", "flat_prob"),
    ]
    pct_sets = [
        ("p_up_pct", "p_down_pct", "p_flat_pct"),
        ("up_pct", "down_pct", "flat_pct"),
        ("pct_up", "pct_down", "pct_flat"),
    ]
    cands: List[Dict[str, Any]] = []
    touched: List[str] = []
    for path, node in _iter_nodes(payload):
        if not isinstance(node, dict):
            continue
        for ku, kd, kf in prob_sets:
            if ku in node and kd in node and kf in node:
                touched.extend([f"{path}.{ku}", f"{path}.{kd}", f"{path}.{kf}"])
                vu = _as_float(node.get(ku))
                vd = _as_float(node.get(kd))
                vf = _as_float(node.get(kf))
                if None in (vu, vd, vf):
                    continue
                scale = 1.0
                if max(vu, vd, vf) > 1.0 and max(vu, vd, vf) <= 100.0:
                    scale = 100.0
                cands.append(
                    {
                        "p_up": vu / scale,
                        "p_down": vd / scale,
                        "p_flat": vf / scale,
                        "extracted_from_keys": [f"{path}.{ku}", f"{path}.{kd}", f"{path}.{kf}"],
                    }
                )
        for ku, kd, kf in pct_sets:
            if ku in node and kd in node and kf in node:
                touched.extend([f"{path}.{ku}", f"{path}.{kd}", f"{path}.{kf}"])
                vu = _as_float(node.get(ku))
                vd = _as_float(node.get(kd))
                vf = _as_float(node.get(kf))
                if None in (vu, vd, vf):
                    continue
                cands.append(
                    {
                        "p_up": vu / 100.0,
                        "p_down": vd / 100.0,
                        "p_flat": vf / 100.0,
                        "extracted_from_keys": [f"{path}.{ku}", f"{path}.{kd}", f"{path}.{kf}"],
                    }
                )
    return (cands[-1] if cands else None), {"touched": touched[-60:]}


def _extract_latest_trade_cond(payload: Dict[str, Any], mode: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    trade_keys = [
        "p_trade",
        "p_trade_latest",
        "p_trade_test",
        "p_trade_mean_test",
        "p_trade_mean",
    ]
    up_cond_keys = [
        "p_up_cond",
        "p_up_cond_latest",
        "p_up_cond_test",
        "p_up_cond_mean_test",
        "p_up_cond_mean",
        "p_up_given_trade",
    ]
    cands: List[Dict[str, Any]] = []
    touched: List[str] = []
    for path, node in _iter_nodes(payload):
        if not isinstance(node, dict):
            continue
        tk = next((k for k in trade_keys if k in node), None)
        uk = next((k for k in up_cond_keys if k in node), None)
        if tk is None or uk is None:
            continue
        touched.extend([f"{path}.{tk}", f"{path}.{uk}"])
        pt = _as_float(node.get(tk))
        pu = _as_float(node.get(uk))
        if pt is None or pu is None:
            continue
        cands.append(
            {
                "p_trade": pt,
                "p_up_cond": pu,
                "extracted_from_keys": [f"{path}.{tk}", f"{path}.{uk}"],
                "mode_hint": mode,
            }
        )
    return (cands[-1] if cands else None), {"touched": touched[-60:]}


def _infer_args_from_backtest_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = payload.get("meta") or {}
    run_params = meta.get("run_params") or {}
    period = payload.get("period") or {}
    inferred = {
        "ticker": payload.get("ticker") or run_params.get("ticker") or "US:NVDA",
        "as_of_date": period.get("as_of") or meta.get("as_of_date"),
        "flat_band_pct": run_params.get("flat_band", 2.0),
        "horizon_trading_days": run_params.get("horizon", 15),
        "calibration": run_params.get("calibration", "sigmoid"),
        "target_interval_coverage": run_params.get(
            "target_coverage", run_params.get("target_interval_coverage", 0.80)
        ),
        "prob_mode": run_params.get("prob_mode"),
        "label_mode": run_params.get("label_mode"),
        "tbm_vol_span": run_params.get("tbm_vol_span"),
        "tbm_k": run_params.get("tbm_k"),
    }
    return inferred


def _saved_run_args_from_json(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    md = payload.get("metadata") or {}
    candidates = [
        md.get("run_args_full"),
        md.get("args_full"),
        md.get("args"),
        (payload.get("meta") or {}).get("run_args_full"),
    ]
    for c in candidates:
        if isinstance(c, dict):
            return c
    return None


def _args_from_saved_run_args(payload: Dict[str, Any], saved: Dict[str, Any]) -> Dict[str, Any]:
    cli = saved.get("cli_args_raw") if isinstance(saved.get("cli_args_raw"), dict) else {}
    eff = saved.get("effective_args") if isinstance(saved.get("effective_args"), dict) else {}
    period = payload.get("period") or {}
    meta = payload.get("meta") or {}

    ticker = (
        eff.get("target_symbol")
        or eff.get("service_ticker")
        or payload.get("ticker")
        or cli.get("ticker")
        or "US:NVDA"
    )
    if isinstance(ticker, str) and ":" not in ticker:
        ticker = f"US:{ticker.upper()}"

    return {
        "ticker": ticker,
        "as_of_date": period.get("as_of") or eff.get("as_of") or cli.get("asof") or meta.get("as_of_date"),
        "flat_band_pct": cli.get("flat_band", 2.0),
        "horizon_trading_days": cli.get("horizon", 15),
        "calibration": cli.get("calibration", "sigmoid"),
        "target_interval_coverage": cli.get("target_coverage", 0.80),
        "prob_mode": cli.get("prob_mode"),
        "label_mode": cli.get("label_mode"),
        "tbm_vol_span": cli.get("tbm_vol_span"),
        "tbm_k": cli.get("tbm_k"),
        "temp_scale": cli.get("temp_scale"),
    }


def _recompute_latest_probs(payload: Dict[str, Any]) -> Dict[str, Any]:
    saved = _saved_run_args_from_json(payload)
    recompute_source = "saved_args" if isinstance(saved, dict) else "inferred_args"
    used_args = _args_from_saved_run_args(payload, saved) if isinstance(saved, dict) else _infer_args_from_backtest_json(payload)
    as_of = used_args.get("as_of_date")
    if not isinstance(as_of, str) or not as_of:
        raise ValueError("Cannot recompute: as_of_date missing in JSON (period.as_of/meta.as_of_date).")

    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.append(str(backend_root))

    from app.services.prediction_service import PredictionService  # local import by design

    svc = PredictionService()
    pred = svc.predict(
        ticker=str(used_args["ticker"]),
        as_of_date=as_of,
        flat_band_pct=float(used_args["flat_band_pct"]),
        horizon_trading_days=int(used_args["horizon_trading_days"]),
        calibration=str(used_args["calibration"]),
        target_interval_coverage=float(used_args["target_interval_coverage"]),
    )

    probs = pred.get("probs") or {}
    up = _as_float(probs.get("up"))
    down = _as_float(probs.get("down"))
    flat = _as_float(probs.get("flat"))
    if None in (up, down, flat):
        raise ValueError("Recompute failed: prediction output does not contain probs.up/down/flat.")
    scale = 100.0 if max(float(up), float(down), float(flat)) > 1.0 else 1.0
    return {
        "p_up": float(up) / scale,
        "p_down": float(down) / scale,
        "p_flat": float(flat) / scale,
        "extracted_from_keys": ["predict.probs.up", "predict.probs.down", "predict.probs.flat"],
        "source_payload": pred,
        "used_args": used_args,
        "recompute_source": recompute_source,
    }


def _main() -> int:
    args = _parse_args()
    path = Path(args.path)
    if not path.exists():
        raise FileNotFoundError(f"JSON not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))

    extracted, triplet_scan = _extract_latest_triplet(payload)
    method = "direct"
    source = "three_class_keys"
    p_trade = None
    p_up_cond = None
    inferred_args = _infer_args_from_backtest_json(payload)
    recompute_source = None
    source_keys = []
    scan_paths = []
    scan_paths.extend(triplet_scan.get("touched", []))
    search_specs = {
        "direct_triplet_keys": [
            "p_up,p_down,p_flat",
            "prob_up,prob_down,prob_flat",
            "up_prob,down_prob,flat_prob",
            "p_up_pct,p_down_pct,p_flat_pct",
            "up_pct,down_pct,flat_pct",
            "pct_up,pct_down,pct_flat",
        ],
        "reconstruct_pair_keys_same_object": [
            "p_trade|p_trade_latest|p_trade_test|p_trade_mean_test|p_trade_mean",
            "p_up_cond|p_up_cond_latest|p_up_cond_test|p_up_cond_mean_test|p_up_cond_mean|p_up_given_trade",
        ],
    }

    if extracted is None:
        tc, trade_scan = _extract_latest_trade_cond(payload, args.mode)
        scan_paths.extend(trade_scan.get("touched", []))
        if tc is None:
            if not args.recompute_if_missing:
                missing_keys = [
                    "latest 3-class keys: (p_up,p_down,p_flat) or *_pct variants",
                    "fallback pair: (p_trade,p_up_cond) in same object",
                ]
                print("ERROR: probability materials are missing in JSON.")
                print("missing_keys=" + json.dumps(missing_keys, ensure_ascii=False))
                print("tried_search_specs=" + json.dumps(search_specs, ensure_ascii=False))
                print("tried_paths_hit_candidates=" + json.dumps(scan_paths[-80:], ensure_ascii=False))
                return 2
            rec = _recompute_latest_probs(payload)
            extracted = {
                "p_up": rec["p_up"],
                "p_down": rec["p_down"],
                "p_flat": rec["p_flat"],
                "extracted_from_keys": rec["extracted_from_keys"],
            }
            inferred_args = rec["used_args"]
            recompute_source = rec["recompute_source"]
            method = "recompute"
            source = "db_only_predict_recompute"
        else:
            p_trade = tc["p_trade"]
            p_up_cond = tc["p_up_cond"]
            p_flat = 1.0 - p_trade
            p_up = p_trade * p_up_cond
            p_down = p_trade * (1.0 - p_up_cond)
            extracted = {
                "p_up": p_up,
                "p_down": p_down,
                "p_flat": p_flat,
                "extracted_from_keys": tc["extracted_from_keys"],
            }
            method = "reconstruct"
            source = "reconstructed_from_p_trade_p_up_cond"

    source_keys = list(extracted.get("extracted_from_keys", []))

    p_up, p_down, p_flat, clip_before_norm = _normalize_probs(
        float(extracted["p_up"]),
        float(extracted["p_down"]),
        float(extracted["p_flat"]),
        EPS_DEFAULT,
    )
    up_pct, down_pct, flat_pct = _round_pct_sum_100(p_up, p_down, p_flat)
    sum_pct = up_pct + down_pct + flat_pct

    head = f"UP:{up_pct}% DOWN:{down_pct}% FLAT:{flat_pct}% (SUM={sum_pct}) method={method}"
    if method == "recompute":
        head += f" source={recompute_source}"
    print(head)
    if args.verbose:
        print(
            "RAW: p_up={:.8f} p_down={:.8f} p_flat={:.8f} source={}".format(
                p_up, p_down, p_flat, source
            )
        )
        print("EXTRACTED_FROM: " + json.dumps(source_keys, ensure_ascii=False))
        if method == "recompute":
            print(f"method=recompute_source={recompute_source}")
            print("USED_ARGS: " + json.dumps(inferred_args, ensure_ascii=False))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_payload = {
            "latest_probs": {
                "p_up": p_up,
                "p_down": p_down,
                "p_flat": p_flat,
                "p_up_pct": up_pct,
                "p_down_pct": down_pct,
                "p_flat_pct": flat_pct,
                "sum_pct": sum_pct,
                "method": method,
            },
            "raw_inputs": {
                "p_trade": p_trade,
                "p_up_cond": p_up_cond,
                "eps": EPS_DEFAULT,
                "clip_before_norm": clip_before_norm,
            },
            "metadata": {
                "source_path": str(path),
                "extracted_from_keys": source_keys,
                "inferred_args": inferred_args,
                "recompute_source": recompute_source,
                "used_args": inferred_args,
                "method_detail": {
                    "method": method,
                    "source_keys": source_keys if method in ("direct", "reconstruct") else None,
                    "recompute_source": recompute_source if method == "recompute" else None,
                    "used_args": inferred_args if method == "recompute" else None,
                },
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        warnings = []
        if method in ("direct", "reconstruct") and any(
            str(k).startswith("predict.probs.") for k in source_keys
        ):
            warnings.append("method/source_keys mismatch: recompute-like keys with non-recompute method")
        if method == "recompute" and any(
            not str(k).startswith("predict.probs.") for k in source_keys
        ):
            warnings.append("method/source_keys mismatch: non-recompute keys with recompute method")
        if warnings:
            out_payload["metadata"]["warnings"] = warnings
        out_path.write_text(json.dumps(out_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
