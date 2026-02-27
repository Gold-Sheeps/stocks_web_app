from __future__ import annotations

import argparse
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.prediction_service import PredictionService, PredictorConfig


def _parse_iso_date(value: str, label: str) -> datetime.date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value}") from exc


def _load_artifact(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    with p.open("rb") as f:
        return pickle.load(f)


def _parse_symbol_list(raw: str | None) -> List[str]:
    if raw is None:
        return []
    out: List[str] = []
    for tok in str(raw).split(","):
        s = tok.strip().upper()
        if not s:
            continue
        if ":" not in s:
            s = f"US:{s}"
        if s not in out:
            out.append(s)
    return out


def _load_tickers_file(path: str | None) -> List[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"tickers file not found: {path}")
    out: List[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip().upper()
        if not s:
            continue
        if ":" not in s:
            s = f"US:{s}"
        if s not in out:
            out.append(s)
    return out


def _latest_artifact_path() -> str:
    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    cands = list(out_dir.glob("pooled_up5_*_artifact.pkl"))
    if not cands:
        raise FileNotFoundError("No pooled artifact found under backend/ml_predictor_data.")

    def _llm_rank(p: Path) -> int:
        # Prefer artifacts that actually include LLM feature columns.
        try:
            with p.open("rb") as f:
                art = pickle.load(f)
            llm_cols = art.get("llm_feature_columns") or []
            return 1 if len(llm_cols) > 0 else 0
        except Exception:
            return 0

    def _rank(p: Path) -> tuple[int, float]:
        # Prefer richer feature sets first (v4 > v3 > older), then recency.
        name = p.name.lower()
        rank = 0
        if "_v4_" in name or name.startswith("pooled_up5_v4"):
            rank = 2
        elif "_v3_" in name or name.startswith("pooled_up5_v3"):
            rank = 1
        return (_llm_rank(p), rank, p.stat().st_mtime)

    best = max(cands, key=_rank)
    return str(best)


def _build_inference_row(
    svc: PredictionService,
    ticker: str,
    asof,
    artifact: Dict[str, Any],
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    cfg = PredictorConfig(
        flat_band_pct=2.0,
        horizon_trading_days=int(artifact.get("horizon", 10)),
        calibration="sigmoid",
        target_interval_coverage=0.80,
        feature_version=str(artifact.get("feature_set", "v2_ohlcv_ta_relregime_event")),
    )
    raw = svc._load_prices(ticker, as_of=asof)
    feat_ver = str(artifact.get("feature_set", "v2_ohlcv_ta_relregime_event"))
    feat = svc._build_feature_frame(
        ticker=ticker,
        price_df=raw,
        as_of=asof,
        regime_symbols=artifact.get("regime_symbols", []),
        relative_symbols=artifact.get("relative_symbols", []),
        sector_symbols=artifact.get("sector_symbols", []),
        feature_version=feat_ver,
    )
    prepared = svc._prepare_dataset(feat, asof, cfg)
    pred_row = prepared["pred_row"]
    base_cols: List[str] = list(artifact["base_feature_columns"])
    # Hybrid artifacts may include LLM columns in base_feature_columns; inference row
    # starts from price-derived features only, so missing columns are zero-filled here.
    x_base = pred_row.reindex(base_cols, fill_value=0.0).astype(float).to_dict()

    row = dict(x_base)
    for c in artifact.get("ticker_dummy_columns", []):
        row[c] = 0.0
    tid = f"tid_{str(ticker).upper()}"
    if tid in row:
        row[tid] = 1.0

    pre_cols = list(row.keys())
    model_cols: List[str] = list(artifact["model_feature_columns"])
    missing = sorted([c for c in model_cols if c not in pre_cols])
    extra = sorted([c for c in pre_cols if c not in model_cols])
    X_df = pd.DataFrame([row])
    X_df = X_df.reindex(columns=model_cols, fill_value=0.0).astype(float)
    series = X_df.iloc[0]
    nonzero = series[series != 0.0]
    top_nonzero = nonzero.abs().sort_values(ascending=False).head(10).index.tolist()
    active_onehot = tid if tid in X_df.columns and float(X_df.iloc[0][tid]) == 1.0 else None
    audit = {
        "n_feature_mismatch": int(len(missing) + len(extra)),
        "missing_feature_columns": missing,
        "extra_feature_columns": extra,
        "active_ticker_onehot_column": active_onehot,
        "top_nonzero_features": top_nonzero,
    }
    return X_df, audit


def _apply_artifact_calibration(artifact: Dict[str, Any], probs: np.ndarray) -> np.ndarray:
    p = np.asarray(probs, dtype=float).reshape(-1)
    cal = artifact.get("calibration", {"type": "none"})
    ctype = str(cal.get("type", "none")).lower()

    if ctype == "platt" and cal.get("model") is not None:
        lr = cal["model"]
        eps = 1e-6
        p_clip = np.clip(p, eps, 1.0 - eps)
        logits = np.log(p_clip / (1.0 - p_clip)).reshape(-1, 1)
        return np.clip(lr.predict_proba(logits)[:, 1], eps, 1.0 - eps)

    if ctype == "isotonic" and cal.get("model") is not None:
        iso = cal["model"]
        return np.clip(np.asarray(iso.predict(p), dtype=float), 1e-6, 1.0 - 1e-6)

    if ctype == "temp":
        t = float(cal.get("temperature", 1.0))
        z = np.log(np.clip(p, 1e-6, 1.0 - 1e-6) / np.clip(1.0 - p, 1e-6, 1.0 - 1e-6))
        out = 1.0 / (1.0 + np.exp(-(z / max(1e-6, t))))
        return np.clip(np.asarray(out, dtype=float), 1e-6, 1.0 - 1e-6)

    return np.clip(p, 1e-6, 1.0 - 1e-6)


def predict_with_artifact(
    artifact: Dict[str, Any],
    x_df: pd.DataFrame,
    calibration_method: str | None = "artifact",
) -> Dict[str, Any]:
    model = artifact["model"]
    p_raw = float(model.predict_proba(x_df.to_numpy(dtype=float))[0][1])
    cal_mode = str(calibration_method or "artifact").strip().lower()
    if cal_mode in ("artifact", "auto", "platt", "isotonic"):
        p = float(_apply_artifact_calibration(artifact, np.asarray([p_raw], dtype=float))[0])
        cal_used = str((artifact.get("calibration") or {}).get("type", "none"))
    elif cal_mode == "none":
        p = float(np.clip(p_raw, 1e-6, 1.0 - 1e-6))
        cal_used = "none"
    else:
        raise ValueError(f"Unsupported calibration_method: {calibration_method}")
    threshold = float(artifact.get("threshold_buy", 0.7))
    action = "BUY" if p >= threshold else "NO_TRADE"
    return {"p_up5": p, "action": action, "threshold_buy": threshold, "cal_method_used": cal_used}


def _rank_predictions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda x: float(x.get("p_up5", 0.0)), reverse=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pooled up5 prediction for one ticker or watchlist.")
    parser.add_argument("--ticker", required=False, help="Single ticker (US:AAPL or AAPL).")
    parser.add_argument("--tickers", default=None, help='Comma-separated tickers. Example: "US:AAPL,US:MSFT".')
    parser.add_argument("--tickers-file", default=None, help="Path to ticker file.")
    parser.add_argument("--asof", required=True, help="As-of date YYYY-MM-DD.")
    parser.add_argument("--artifact", default=None, help="Artifact path. Default: latest pooled artifact.")
    parser.add_argument(
        "--calibration-method",
        default="none",
        choices=["none", "artifact", "platt", "isotonic"],
        help="Probability calibration for inference output. Default: none (raw model probability).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    tickers = []
    if args.ticker:
        tickers.extend(_parse_symbol_list(args.ticker))
    tickers.extend(_parse_symbol_list(args.tickers))
    tickers.extend(_load_tickers_file(args.tickers_file))
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        raise ValueError("No ticker provided. Use --ticker or --tickers or --tickers-file.")
    asof = _parse_iso_date(args.asof, "asof")
    artifact_path = args.artifact or _latest_artifact_path()
    artifact = _load_artifact(artifact_path)

    svc = PredictionService()
    rows: List[Dict[str, Any]] = []
    for ticker in tickers:
        x_df, feat_audit = _build_inference_row(svc, ticker, asof, artifact)
        pred = predict_with_artifact(artifact, x_df, calibration_method=args.calibration_method)
        rows.append(
            {
                "ticker": ticker,
                "asof": asof.isoformat(),
                "p_up5": float(pred["p_up5"]),
                "decision": str(pred["action"]),
                "cal_method_used": str(pred.get("cal_method_used", "none")),
                "feature_audit": feat_audit,
            }
        )
    ranked = _rank_predictions(rows)
    for r in ranked:
        print(
            "ticker={t} asof={d} p_up5={p:.6f} decision={a}".format(
                t=r["ticker"], d=r["asof"], p=float(r["p_up5"]), a=r["decision"]
            )
        )
        print(f"calibration ticker={r['ticker']} method={r.get('cal_method_used','none')}")
        print(
            "feature_audit ticker={t} n_feature_mismatch={n} active_ticker_onehot_column={c} top_nonzero_features={f}".format(
                t=r["ticker"],
                n=int(r["feature_audit"]["n_feature_mismatch"]),
                c=r["feature_audit"]["active_ticker_onehot_column"],
                f=",".join(r["feature_audit"]["top_nonzero_features"][:5]),
            )
        )
        if r["feature_audit"]["missing_feature_columns"] or r["feature_audit"]["extra_feature_columns"]:
            print(
                "feature_mismatch_detail ticker={t} missing={m} extra={e}".format(
                    t=r["ticker"],
                    m=",".join(r["feature_audit"]["missing_feature_columns"][:10]),
                    e=",".join(r["feature_audit"]["extra_feature_columns"][:10]),
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
