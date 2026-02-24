from __future__ import annotations

import argparse
import json
import pickle
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.prediction_service import PredictionService, PredictorConfig


DEFAULT_THRESHOLDS = [0.7]


@dataclass
class FoldConfig:
    horizon: int = 10
    test_window: int = 15
    min_train_rows: int = 280
    fold_step: int = 30
    train_window: Optional[int] = None
    purge_days: Optional[int] = None
    embargo_mode: str = "pct"
    embargo_days: int = 5
    embargo_pct: float = 0.01
    calibration_method: str = "platt"


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value}") from exc


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


def _load_tickers_file(path: str) -> List[str]:
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


def _resolve_train_holdout_tickers(
    all_tickers: List[str],
    holdout_tickers: List[str],
) -> Tuple[List[str], List[str]]:
    holdout_set = {str(t).upper() for t in holdout_tickers}
    all_unique = [str(t).upper() for t in all_tickers]
    holdout = [t for t in all_unique if t in holdout_set]
    train = [t for t in all_unique if t not in holdout_set]
    return train, holdout


def _parse_thresholds(raw: str | None, fixed_threshold: float | None = None) -> List[float]:
    if fixed_threshold is not None:
        vals = [float(fixed_threshold)]
    elif raw is None:
        vals = list(DEFAULT_THRESHOLDS)
    else:
        vals = [float(v.strip()) for v in str(raw).split(",") if v.strip()]
        if not vals:
            raise ValueError("fixed-thresholds must not be empty.")
    vals.extend([0.5, 0.6, 0.7])
    uniq = sorted({float(np.clip(v, 0.0, 1.0)) for v in vals})
    return uniq


def _collect_ticker_frame(
    svc: PredictionService,
    ticker: str,
    asof: date,
    start: date,
    end: date,
    feature_set: str,
    horizon: int,
    regime_symbols: List[str],
    relative_symbols: List[str],
    sector_symbols: List[str],
    target_return: float,
) -> Tuple[pd.DataFrame, List[str]]:
    cfg = PredictorConfig(
        flat_band_pct=2.0,
        horizon_trading_days=int(horizon),
        calibration="sigmoid",
        target_interval_coverage=0.80,
        feature_version=feature_set,
    )
    raw = svc._load_prices(ticker, as_of=asof)
    feat = svc._build_feature_frame(
        ticker=ticker,
        price_df=raw,
        as_of=asof,
        regime_symbols=regime_symbols,
        relative_symbols=relative_symbols,
        sector_symbols=sector_symbols,
        feature_version=feature_set,
    )
    prepared = svc._prepare_dataset(feat, asof, cfg)
    feature_cols = list(prepared["feature_cols"])
    model_df = prepared["model_df"].copy()
    model_df = model_df[(model_df.index >= pd.Timestamp(start)) & (model_df.index <= pd.Timestamp(end))]
    model_df["ticker"] = ticker
    model_df["y_up5"] = (
        (model_df["target_return_pct_h"].astype(float) / 100.0) >= float(target_return)
    ).astype(int)
    model_df["future_return"] = model_df["target_return_pct_h"].astype(float) / 100.0
    return model_df, feature_cols


def _build_pooled_dataset(
    frames: List[pd.DataFrame],
    feature_cols: List[str],
    use_ticker_dummies: bool = True,
) -> Tuple[pd.DataFrame, List[str], List[str]]:
    if not frames:
        raise ValueError("No frames to pool.")
    base_cols = list(feature_cols)
    for f in frames:
        for col in base_cols:
            if col not in f.columns:
                raise ValueError(f"Feature column missing in pooled frame: {col}")
    pooled = pd.concat(frames, axis=0).sort_index()

    if use_ticker_dummies:
        pooled["ticker_id"] = pooled["ticker"].astype(str)
        dummies = pd.get_dummies(pooled["ticker_id"], prefix="tid", dtype=float)
        dummy_cols = list(dummies.columns)
        X_df = pd.concat([pooled[base_cols].astype(float), dummies], axis=1)
    else:
        dummy_cols = []
        X_df = pooled[base_cols].astype(float)

    pooled = pooled.join(X_df, rsuffix="_x")
    model_feature_columns = list(X_df.columns)
    return pooled, model_feature_columns, dummy_cols


def _build_inference_matrix_for_pooled(
    pooled: pd.DataFrame,
    base_feature_cols: List[str],
    train_dummy_cols: List[str],
    model_feature_columns: List[str],
) -> np.ndarray:
    base = pooled[base_feature_cols].astype(float).copy()
    for c in train_dummy_cols:
        if c not in base.columns:
            base[c] = 0.0
    x = base.reindex(columns=model_feature_columns, fill_value=0.0).to_numpy(dtype=float)
    return x


def _fit_binary_model(svc: PredictionService, X: np.ndarray, y: np.ndarray):
    # inf → nan に変換（XGBoost は nan を missing として扱える）
    X = np.where(np.isinf(X), np.nan, X)
    pos = int(np.sum(y == 1))
    neg = int(np.sum(y == 0))
    spw = float(neg / max(1, pos))
    clf = svc._xgb.XGBClassifier(
        objective="binary:logistic",
        n_estimators=180,
        max_depth=4,
        learning_rate=0.045,
        subsample=0.9,
        colsample_bytree=0.9,
        eval_metric="logloss",
        tree_method="hist",
        random_state=11,
        scale_pos_weight=spw,
    )
    sw = svc._binary_sample_weight(y)
    clf.fit(X, y, sample_weight=sw)
    return clf


def _apply_calibration(
    svc: PredictionService,
    p_cal_raw: np.ndarray,
    y_cal: np.ndarray,
    p_test_raw: np.ndarray,
    method: str = "platt",
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    キャリブレーション。デフォルトを Platt Scaling に変更。
    Platt = LogisticRegression on raw probabilities → sigmoid fit。
    Isotonic は確率を経験分布に圧縮するため、正例率が低い場合に
    高確信領域 (p>=0.7) が物理的に出なくなる問題がある。
    """
    y = np.asarray(y_cal, dtype=int)
    p_cal = np.asarray(p_cal_raw, dtype=float).reshape(-1)
    p_test = np.asarray(p_test_raw, dtype=float).reshape(-1)
    if len(y) == 0 or len(np.unique(y)) < 2:
        return p_cal, p_test, {"type": "none"}

    if method == "platt":
        try:
            from sklearn.linear_model import LogisticRegression
            eps = 1e-6
            p_cal_clip = np.clip(p_cal, eps, 1.0 - eps)
            logits_cal = np.log(p_cal_clip / (1.0 - p_cal_clip)).reshape(-1, 1)
            lr = LogisticRegression(C=1e10, solver="lbfgs", max_iter=1000)
            lr.fit(logits_cal, y)
            p_test_clip = np.clip(p_test, eps, 1.0 - eps)
            logits_test = np.log(p_test_clip / (1.0 - p_test_clip)).reshape(-1, 1)
            p_cal_out = np.clip(lr.predict_proba(logits_cal)[:, 1], eps, 1.0 - eps)
            p_test_out = np.clip(lr.predict_proba(logits_test)[:, 1], eps, 1.0 - eps)
            return p_cal_out, p_test_out, {
                "type": "platt",
                "model": lr,
                "coef": float(lr.coef_[0][0]),
                "intercept": float(lr.intercept_[0]),
            }
        except Exception:
            return p_cal, p_test, {"type": "none"}

    if method == "isotonic":
        try:
            from sklearn.isotonic import IsotonicRegression
            iso = IsotonicRegression(out_of_bounds="clip")
            iso.fit(p_cal, y)
            p_cal_iso = np.clip(np.asarray(iso.predict(p_cal), dtype=float), 1e-6, 1.0 - 1e-6)
            p_test_iso = np.clip(np.asarray(iso.predict(p_test), dtype=float), 1e-6, 1.0 - 1e-6)
            return p_cal_iso, p_test_iso, {"type": "isotonic", "model": iso}
        except Exception:
            pass

    # method == "none" or fallback
    return (
        np.clip(p_cal, 1e-6, 1.0 - 1e-6),
        np.clip(p_test, 1e-6, 1.0 - 1e-6),
        {"type": "none"},
    )


def _walk_forward_eval(
    svc: PredictionService,
    pooled: pd.DataFrame,
    model_feature_columns: List[str],
    cfg: FoldConfig,
) -> Dict[str, Any]:
    df = pooled.sort_index()
    X_all = df[model_feature_columns].to_numpy(dtype=float)
    y_all = df["y_up5"].astype(int).to_numpy()
    ret_all = df["future_return"].to_numpy(dtype=float)
    dates = [str(d.date()) if hasattr(d, "date") else str(d) for d in df.index]
    tickers = df["ticker"].astype(str).tolist()

    n = len(df)
    min_train = int(max(cfg.min_train_rows, 280))
    step = int(max(1, cfg.fold_step))
    test_window = int(max(1, cfg.test_window))
    purge_n = int(cfg.horizon if cfg.purge_days is None else max(0, int(cfg.purge_days)))
    if str(cfg.embargo_mode).lower() == "days":
        embargo_n = max(0, int(cfg.embargo_days))
    else:
        embargo_n = max(1, int(np.ceil(n * float(max(0.0, cfg.embargo_pct)))))

    y_true: List[int] = []
    probs_pre: List[float] = []
    probs_post: List[float] = []
    rets: List[float] = []
    out_dates: List[str] = []
    out_tickers: List[str] = []

    fold_count = 0
    next_eligible_start = min_train
    for start in range(min_train, n - test_window, step):
        if start < next_eligible_start:
            continue
        end = min(start + test_window, n)
        train_end = max(0, start - purge_n)
        if train_end <= 120:
            continue
        train_from = 0
        if cfg.train_window is not None and int(cfg.train_window) > 0:
            train_from = max(0, train_end - int(cfg.train_window))
        X_train = X_all[train_from:train_end]
        y_train = y_all[train_from:train_end]
        X_test = X_all[start:end]
        y_test = y_all[start:end]
        r_test = ret_all[start:end]
        d_test = dates[start:end]
        t_test = tickers[start:end]

        split = int(len(X_train) * 0.8)
        split = min(max(split, 120), len(X_train) - 30)
        if split <= 100:
            continue
        X_t, X_c = X_train[:split], X_train[split:]
        y_t, y_c = y_train[:split], y_train[split:]

        if len(np.unique(y_t)) < 2:
            p_prior = float(np.mean(y_t)) if len(y_t) else 0.0
            p_test_raw = np.full(len(X_test), p_prior, dtype=float)
            p_test = np.asarray(p_test_raw, dtype=float)
        else:
            clf = _fit_binary_model(svc, X_t, y_t)
            p_cal_raw = clf.predict_proba(np.where(np.isinf(X_c), np.nan, X_c))[:, 1]
            p_test_raw = clf.predict_proba(np.where(np.isinf(X_test), np.nan, X_test))[:, 1]
            _, p_test, _ = _apply_calibration(svc, p_cal_raw, y_c, p_test_raw, method=cfg.calibration_method)

        y_true.extend(y_test.tolist())
        probs_pre.extend(np.asarray(p_test_raw, dtype=float).tolist())
        probs_post.extend(np.asarray(p_test, dtype=float).tolist())
        rets.extend(np.asarray(r_test, dtype=float).tolist())
        out_dates.extend(d_test)
        out_tickers.extend(t_test)
        fold_count += 1
        next_eligible_start = end + embargo_n

    if not y_true:
        raise RuntimeError("No valid folds for pooled walk-forward.")
    return {
        "y_true": np.asarray(y_true, dtype=int),
        "probs_pre_calibration": np.clip(np.asarray(probs_pre, dtype=float), 1e-6, 1.0 - 1e-6),
        "probs": np.clip(np.asarray(probs_post, dtype=float), 1e-6, 1.0 - 1e-6),
        "future_returns": np.asarray(rets, dtype=float),
        "dates": out_dates,
        "tickers": out_tickers,
        "folds_used": int(fold_count),
    }


def _probability_summary(probs: np.ndarray) -> Dict[str, Any]:
    p = np.asarray(probs, dtype=float).reshape(-1)
    if p.size == 0:
        return {
            "n": 0,
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "quantiles": {"0.1": None, "0.25": None, "0.5": None, "0.75": None, "0.9": None, "0.95": None, "0.99": None},
        }
    q = np.quantile(p, [0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99])
    return {
        "n": int(p.size),
        "min": float(np.min(p)),
        "max": float(np.max(p)),
        "mean": float(np.mean(p)),
        "std": float(np.std(p)),
        "quantiles": {
            "0.1": float(q[0]),
            "0.25": float(q[1]),
            "0.5": float(q[2]),
            "0.75": float(q[3]),
            "0.9": float(q[4]),
            "0.95": float(q[5]),
            "0.99": float(q[6]),
        },
    }


def _fixed_threshold_metrics(
    y_true: np.ndarray,
    probs: np.ndarray,
    future_returns: np.ndarray,
    thresholds: List[float],
) -> Dict[str, Dict[str, Any]]:
    y = np.asarray(y_true, dtype=int).reshape(-1)
    p = np.asarray(probs, dtype=float).reshape(-1)
    r = np.asarray(future_returns, dtype=float).reshape(-1)
    out: Dict[str, Dict[str, Any]] = {}
    for t in sorted({float(np.clip(v, 0.0, 1.0)) for v in thresholds + [0.7]}):
        yhat = (p >= t).astype(int)
        tp = int(np.sum((yhat == 1) & (y == 1)))
        fp = int(np.sum((yhat == 1) & (y == 0)))
        tn = int(np.sum((yhat == 0) & (y == 0)))
        fn = int(np.sum((yhat == 0) & (y == 1)))
        n_trades = int(np.sum(yhat == 1))
        coverage = float(n_trades / max(1, len(y)))
        precision = float(tp / max(1, tp + fp))
        avg_return = float(np.mean(r[yhat == 1])) if n_trades else 0.0
        out[f"{t:g}"] = {
            "precision": precision,
            "coverage": coverage,
            "n_trades": n_trades,
            "avg_return": avg_return,
            "confusion_matrix": [[tn, fp], [fn, tp]],
        }
    return out


def _confidence_bins(
    y_true: np.ndarray,
    probs: np.ndarray,
    future_returns: np.ndarray,
) -> List[Dict[str, Any]]:
    y = np.asarray(y_true, dtype=int).reshape(-1)
    p = np.asarray(probs, dtype=float).reshape(-1)
    r = np.asarray(future_returns, dtype=float).reshape(-1)
    ranges = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    out: List[Dict[str, Any]] = []
    for lo, hi in ranges:
        if hi < 1.0:
            mask = (p >= lo) & (p < hi)
        else:
            mask = (p >= lo) & (p <= hi)
        n = int(np.sum(mask))
        win_rate = float(np.mean(y[mask] == 1)) if n > 0 else None
        avg_return = float(np.mean(r[mask])) if n > 0 else None
        out.append(
            {
                "bin": f"[{lo:.1f},{hi:.1f}{')' if hi < 1.0 else ']'}",
                "lo": float(lo),
                "hi": float(hi),
                "n": n,
                "win_rate": win_rate,
                "avg_return": avg_return,
            }
        )
    return out


def _high_low_miss_audit(
    y_true: np.ndarray,
    probs: np.ndarray,
    future_returns: np.ndarray,
    dates: List[str],
    tickers: List[str],
    top_n: int = 50,
) -> Dict[str, Any]:
    y = np.asarray(y_true, dtype=int).reshape(-1)
    p = np.asarray(probs, dtype=float).reshape(-1)
    r = np.asarray(future_returns, dtype=float).reshape(-1)
    hi_idx = np.where((p >= 0.7) & (y == 0))[0].tolist()
    lo_idx = np.where((p < 0.7) & (y == 1))[0].tolist()
    hi = [
        {
            "date": dates[i],
            "ticker": tickers[i],
            "probability": float(p[i]),
            "realized_return": float(r[i]),
            "actual": int(y[i]),
        }
        for i in hi_idx
    ]
    lo = [
        {
            "date": dates[i],
            "ticker": tickers[i],
            "probability": float(p[i]),
            "realized_return": float(r[i]),
            "actual": int(y[i]),
        }
        for i in lo_idx
    ]
    hi.sort(key=lambda x: x["probability"], reverse=True)
    lo.sort(key=lambda x: x["probability"])
    dedup_seen: set[tuple[Any, ...]] = set()
    lo_dedup: List[Dict[str, Any]] = []
    for item in lo:
        k = (item["date"], item["ticker"], item["probability"], item["realized_return"], item["actual"])
        if k in dedup_seen:
            continue
        dedup_seen.add(k)
        lo_dedup.append(item)
    return {
        "high_confidence_miss": {"threshold": 0.7, "count": len(hi), "top_misses": hi[: int(max(0, top_n))]},
        "low_confidence_miss": {
            "threshold": 0.7,
            "count": len(lo),
            "top_misses": lo[: int(max(0, top_n))],
            "top_misses_dedup": lo_dedup[: int(max(0, top_n))],
        },
    }


def _fit_final_model_and_calibrator(
    svc: PredictionService,
    pooled: pd.DataFrame,
    model_feature_columns: List[str],
    calibration_method: str = "platt",
) -> Dict[str, Any]:
    df = pooled.sort_index()
    X = df[model_feature_columns].to_numpy(dtype=float)
    y = df["y_up5"].astype(int).to_numpy()
    split = int(len(X) * 0.8)
    split = min(max(split, 120), len(X) - 30)
    if split <= 100:
        split = max(1, len(X) - 1)
    X_t, X_c = X[:split], X[split:]
    y_t, y_c = y[:split], y[split:]
    model = _fit_binary_model(svc, X_t, y_t)
    if len(X_c) > 0:
        p_cal_raw = model.predict_proba(X_c)[:, 1]
        _, _, cal = _apply_calibration(svc, p_cal_raw, y_c, p_cal_raw, method=calibration_method)
    else:
        cal = {"type": "none"}
    return {"model": model, "calibration": cal}


def _predict_with_model_and_calibration(
    calibration: Dict[str, Any],
    model,
    x: np.ndarray,
) -> np.ndarray:
    x = np.where(np.isinf(x), np.nan, x)
    p_raw = model.predict_proba(x)[:, 1]
    ctype = str((calibration or {}).get("type", "none")).lower()

    if ctype == "platt" and calibration.get("model") is not None:
        lr = calibration["model"]
        eps = 1e-6
        p_clip = np.clip(p_raw, eps, 1.0 - eps)
        logits = np.log(p_clip / (1.0 - p_clip)).reshape(-1, 1)
        return np.clip(lr.predict_proba(logits)[:, 1], eps, 1.0 - eps)

    if ctype == "isotonic" and calibration.get("model") is not None:
        iso = calibration["model"]
        p = np.asarray(iso.predict(p_raw), dtype=float)
        return np.clip(p, 1e-6, 1.0 - 1e-6)

    if ctype == "temp":
        t = float(calibration.get("temperature", 1.0))
        z = np.log(np.clip(p_raw, 1e-6, 1.0 - 1e-6) / np.clip(1.0 - p_raw, 1e-6, 1.0 - 1e-6))
        out = 1.0 / (1.0 + np.exp(-(z / max(1e-6, t))))
        return np.clip(np.asarray(out, dtype=float), 1e-6, 1.0 - 1e-6)

    return np.clip(np.asarray(p_raw, dtype=float), 1e-6, 1.0 - 1e-6)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train pooled up5_2w model on a small ticker universe.")
    parser.add_argument("--tickers-file", required=True, help="Ticker list file (20-30 recommended).")
    parser.add_argument("--holdout-tickers", default=None, help='Optional holdout tickers. Example: "US:AAPL,US:MSFT".')
    parser.add_argument("--holdout-tickers-file", default=None, help="Optional holdout ticker file.")
    parser.add_argument("--asof", required=True, help="As-of date YYYY-MM-DD.")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD.")
    parser.add_argument(
        "--feature-set",
        default="v2_ohlcv_ta_relregime_event",
        choices=[
            "v2_ohlcv_ta_relregime_event",
            "v3_ohlcv_ta_relregime_event_fundflow",
            "v4_ohlcv_ta_relregime_event_fundflow_signals",
        ],
    )
    parser.add_argument("--regime-symbols", default="US:QQQ,US:SPY")
    parser.add_argument("--relative-symbols", default="US:QQQ")
    parser.add_argument("--sector-symbols", default="")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--target-return", type=float, default=0.05)
    parser.add_argument("--fixed-threshold", type=float, default=None, help="Primary BUY threshold. Default depends on calibration method: 0.5 (platt), 0.7 (isotonic), 0.9 (none).")
    parser.add_argument("--fixed-thresholds", default="0.7")
    parser.add_argument("--min-train-rows", type=int, default=280)
    parser.add_argument("--fold-step", type=int, default=10)
    parser.add_argument("--test-window", type=int, default=25)
    parser.add_argument("--train-window", type=int, default=None)
    parser.add_argument("--purge-days", type=int, default=None)
    parser.add_argument("--embargo-mode", default="pct", choices=["pct", "days"])
    parser.add_argument("--embargo-days", type=int, default=5)
    parser.add_argument("--embargo-pct", type=float, default=0.01)
    parser.add_argument("--audit-top-n", type=int, default=50)
    parser.add_argument("--out-prefix", default=None)
    parser.add_argument(
        "--calibration-method",
        default="platt",
        choices=["platt", "isotonic", "none"],
        help="Calibration method for walk-forward and final model. Default: platt. "
             "isotonic was the previous default but compresses probabilities, "
             "preventing p>=0.7 from appearing.",
    )
    parser.add_argument(
        "--no-ticker-dummies",
        action="store_true",
        default=False,
        help="Disable ticker one-hot encoding. Improves OOD generalization for holdout tickers.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    asof = _parse_iso_date(args.asof, "asof")
    start = _parse_iso_date(args.start, "start")
    end = _parse_iso_date(args.end, "end")
    if start > end:
        raise ValueError(f"start must be <= end: {start} > {end}")

    tickers = _load_tickers_file(args.tickers_file)
    holdout_from_arg = _parse_symbol_list(args.holdout_tickers)
    holdout_from_file = _load_tickers_file(args.holdout_tickers_file) if args.holdout_tickers_file else []
    holdout_requested = list(dict.fromkeys(holdout_from_arg + holdout_from_file))
    train_tickers, holdout_tickers = _resolve_train_holdout_tickers(tickers, holdout_requested)
    if holdout_requested and not holdout_tickers:
        raise ValueError("No holdout tickers overlap with tickers-file universe.")
    if not train_tickers:
        raise ValueError("Training tickers is empty after holdout split.")
    thresholds = _parse_thresholds(args.fixed_thresholds, fixed_threshold=args.fixed_threshold)

    # calibration method に応じた threshold_buy デフォルト
    _CAL_DEFAULT_THRESHOLD: Dict[str, float] = {"platt": 0.5, "isotonic": 0.7, "none": 0.9}
    if args.fixed_threshold is not None:
        threshold_buy = float(np.clip(args.fixed_threshold, 0.0, 1.0))
    else:
        threshold_buy = _CAL_DEFAULT_THRESHOLD.get(args.calibration_method, 0.7)

    # threshold_buy が thresholds リストに含まれていなければ追加して評価対象にする
    if not any(abs(t - threshold_buy) < 1e-9 for t in thresholds):
        thresholds = sorted(thresholds + [threshold_buy])

    regime_symbols = _parse_symbol_list(args.regime_symbols)
    relative_symbols = _parse_symbol_list(args.relative_symbols)
    sector_symbols = _parse_symbol_list(args.sector_symbols)

    svc = PredictionService()
    frames: List[pd.DataFrame] = []
    feature_cols_ref: Optional[List[str]] = None
    for t in tickers:
        frame, feature_cols = _collect_ticker_frame(
            svc=svc,
            ticker=t,
            asof=asof,
            start=start,
            end=end,
            feature_set=args.feature_set,
            horizon=int(args.horizon),
            regime_symbols=regime_symbols,
            relative_symbols=relative_symbols,
            sector_symbols=sector_symbols,
            target_return=float(args.target_return),
        )
        if feature_cols_ref is None:
            feature_cols_ref = feature_cols
        elif feature_cols_ref != feature_cols:
            raise RuntimeError("Feature columns mismatch across tickers.")
        frames.append(frame)

    if feature_cols_ref is None:
        raise RuntimeError("No training frame collected.")
    train_frames = [f for f in frames if str(f["ticker"].iloc[0]).upper() in set(train_tickers)]
    holdout_frames = [f for f in frames if str(f["ticker"].iloc[0]).upper() in set(holdout_tickers)]
    use_dummies = not args.no_ticker_dummies
    pooled_train, model_feature_columns, dummy_cols = _build_pooled_dataset(train_frames, feature_cols_ref, use_ticker_dummies=use_dummies)

    fold_cfg = FoldConfig(
        horizon=int(args.horizon),
        test_window=int(args.test_window),
        min_train_rows=int(args.min_train_rows),
        fold_step=int(args.fold_step),
        train_window=args.train_window,
        purge_days=args.purge_days,
        embargo_mode=args.embargo_mode,
        embargo_days=int(args.embargo_days),
        embargo_pct=float(args.embargo_pct),
        calibration_method=args.calibration_method,
    )
    if holdout_frames:
        pooled_holdout, _, _ = _build_pooled_dataset(holdout_frames, feature_cols_ref, use_ticker_dummies=use_dummies)
        final_fit = _fit_final_model_and_calibrator(svc, pooled_train, model_feature_columns, calibration_method=args.calibration_method)
        x_holdout = _build_inference_matrix_for_pooled(
            pooled=pooled_holdout,
            base_feature_cols=feature_cols_ref,
            train_dummy_cols=dummy_cols,
            model_feature_columns=model_feature_columns,
        )
        p_holdout_raw = final_fit["model"].predict_proba(x_holdout)[:, 1]
        p_holdout = _predict_with_model_and_calibration(final_fit["calibration"], final_fit["model"], x_holdout)
        eval_out = {
            "y_true": pooled_holdout["y_up5"].astype(int).to_numpy(),
            "probs": np.asarray(p_holdout, dtype=float),
            "probs_pre_calibration": np.asarray(p_holdout_raw, dtype=float),
            "future_returns": pooled_holdout["future_return"].to_numpy(dtype=float),
            "dates": [str(d.date()) if hasattr(d, "date") else str(d) for d in pooled_holdout.index],
            "tickers": pooled_holdout["ticker"].astype(str).tolist(),
            "folds_used": 0,
        }
    else:
        eval_out = _walk_forward_eval(svc, pooled_train, model_feature_columns, fold_cfg)
        final_fit = _fit_final_model_and_calibrator(svc, pooled_train, model_feature_columns, calibration_method=args.calibration_method)

    fixed = _fixed_threshold_metrics(eval_out["y_true"], eval_out["probs"], eval_out["future_returns"], thresholds)
    p70 = fixed.get("0.7", {"precision": 0.0, "coverage": 0.0, "n_trades": 0, "avg_return": 0.0})
    _t_key = f"{threshold_buy:g}"
    p_at_threshold = fixed.get(_t_key, {"precision": 0.0, "coverage": 0.0, "n_trades": 0, "avg_return": 0.0})
    audit = _high_low_miss_audit(
        y_true=eval_out["y_true"],
        probs=eval_out["probs"],
        future_returns=eval_out["future_returns"],
        dates=eval_out["dates"],
        tickers=eval_out["tickers"],
        top_n=int(args.audit_top_n),
    )
    bins = _confidence_bins(eval_out["y_true"], eval_out["probs"], eval_out["future_returns"])
    probability_audit = {
        "pre_calibration_summary": _probability_summary(eval_out.get("probs_pre_calibration", eval_out["probs"])),
        "post_calibration_summary": _probability_summary(eval_out["probs"]),
    }

    per_ticker_rows: List[Dict[str, Any]] = []
    for t in sorted(set(eval_out["tickers"])):
        mask = np.asarray([x == t for x in eval_out["tickers"]], dtype=bool)
        per_t_thresholds = sorted({0.7, threshold_buy})
        per_t_fixed = _fixed_threshold_metrics(
            y_true=eval_out["y_true"][mask],
            probs=eval_out["probs"][mask],
            future_returns=eval_out["future_returns"][mask],
            thresholds=per_t_thresholds,
        )
        m70 = per_t_fixed.get("0.7", {"precision": 0.0, "coverage": 0.0, "n_trades": 0, "avg_return": 0.0})
        mt = per_t_fixed.get(_t_key, {"precision": 0.0, "coverage": 0.0, "n_trades": 0, "avg_return": 0.0})
        per_ticker_rows.append(
            {
                "ticker": t,
                # 後方互換: p70 系
                "precision_at_p70": m70["precision"],
                "coverage_at_p70": m70["coverage"],
                "n_trades_at_p70": m70["n_trades"],
                "avg_return_at_p70": m70["avg_return"],
                # threshold_buy 連動
                "effective_threshold_buy": threshold_buy,
                "precision_at_threshold": mt["precision"],
                "coverage_at_threshold": mt["coverage"],
                "n_trades_at_threshold": mt["n_trades"],
                "avg_return_at_threshold": mt["avg_return"],
            }
        )

    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.out_prefix or f"pooled_up5_{asof.strftime('%Y%m%d')}"
    artifact_path = out_dir / f"{prefix}_artifact.pkl"
    eval_json_path = out_dir / f"{prefix}_eval.json"
    eval_csv_path = out_dir / f"{prefix}_eval.csv"

    artifact = {
        "artifact_version": 1,
        "trained_at": datetime.now().isoformat(),
        "tickers": tickers,
        "train_tickers": train_tickers,
        "holdout_tickers": holdout_tickers,
        "feature_set": args.feature_set,
        "horizon": int(args.horizon),
        "target_return": float(args.target_return),
        "threshold_buy": threshold_buy,
        "base_feature_columns": feature_cols_ref,
        "ticker_dummy_columns": dummy_cols,
        "model_feature_columns": model_feature_columns,
        "regime_symbols": regime_symbols,
        "relative_symbols": relative_symbols,
        "sector_symbols": sector_symbols,
        "model": final_fit["model"],
        "calibration": final_fit["calibration"],
    }
    with artifact_path.open("wb") as f:
        pickle.dump(artifact, f)

    eval_payload = {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "asof": asof.isoformat()},
        "metrics": {
            # 後方互換: p70 系
            "precision_at_p70": p70["precision"],
            "coverage_at_p70": p70["coverage"],
            "n_trades_at_p70": p70["n_trades"],
            "avg_return_at_p70": p70["avg_return"],
            # threshold_buy 連動
            "effective_threshold_buy": threshold_buy,
            "precision_at_threshold": p_at_threshold["precision"],
            "coverage_at_threshold": p_at_threshold["coverage"],
            "n_trades_at_threshold": p_at_threshold["n_trades"],
            "avg_return_at_threshold": p_at_threshold["avg_return"],
            "folds_used": eval_out["folds_used"],
        },
        "diagnostics": {
            "fixed_threshold_metrics": fixed,
            "confidence_bins": bins,
            "probability_audit": probability_audit,
            "prob_calibration_audit": audit,
        },
        "meta": {
            "run_params": {
                "label_mode": "up5_2w",
                "horizon": int(args.horizon),
                "target_return": float(args.target_return),
                "fixed_thresholds": thresholds,
                "feature_set": args.feature_set,
                "tickers_file": args.tickers_file,
                "n_tickers": len(tickers),
                "train_tickers": train_tickers,
                "holdout_tickers": holdout_tickers,
            }
        },
        "per_ticker": per_ticker_rows,
        "artifact_path": str(artifact_path),
    }
    eval_json_path.write_text(json.dumps(eval_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(per_ticker_rows).to_csv(eval_csv_path, index=False, encoding="utf-8")

    print(f"artifact: {artifact_path}")
    print(f"eval_json: {eval_json_path}")
    print(f"eval_csv: {eval_csv_path}")
    print(
        "precision_at_p70={p:.4f} n_trades_at_p70={n} coverage_at_p70={c:.4f} avg_return_at_p70={r:+.4f}".format(
            p=float(p70["precision"]),
            n=int(p70["n_trades"]),
            c=float(p70["coverage"]),
            r=float(p70["avg_return"]),
        )
    )
    print(
        "effective_threshold_buy={tb} precision_at_threshold={p:.4f} n_trades_at_threshold={n} "
        "coverage_at_threshold={c:.4f} avg_return_at_threshold={r:+.4f}".format(
            tb=threshold_buy,
            p=float(p_at_threshold["precision"]),
            n=int(p_at_threshold["n_trades"]),
            c=float(p_at_threshold["coverage"]),
            r=float(p_at_threshold["avg_return"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
