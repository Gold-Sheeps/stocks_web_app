"""train_class15.py — Phase 3-3b: 真の15営業日3クラス予測モデルを訓練する。

ラベル定義:
  - 2 = Up   : 15営業日後リターン > +flat_band_pct
  - 1 = Flat : -flat_band_pct <= リターン <= +flat_band_pct
  - 0 = Down : 15営業日後リターン < -flat_band_pct

特徴量: v3_ohlcv_ta_relregime_event_fundflow (既存 up5 と同一セット)
モデル: XGBoost multi:softprob (num_class=3)
不均衡対応: クラス逆頻度 sample_weight
評価: walk-forward OOS (direction accuracy, 3x3 confusion matrix, calibration)

使い方:
  uv run python backend/scripts/train_class15.py \\
      --tickers-file backend/config/universe_100.txt \\
      --asof 2026-03-21  --start 2022-01-01  --end 2026-03-21

  uv run python backend/scripts/train_class15.py \\
      --tickers-file backend/config/pool_smoke.txt \\
      --asof 2026-03-21  --start 2023-01-01  --end 2026-03-21 \\
      --flat-band 3.0  --horizon 15  --feature-set v3_ohlcv_ta_relregime_event_fundflow
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.prediction_service import PredictionService, PredictorConfig
from app.db.database import Database
from scripts.train_pooled_up5 import (
    _collect_ticker_frame,
    _build_pooled_dataset,
    _build_inference_matrix_for_pooled,
    _parse_symbol_list,
    _load_tickers_file,
    _resolve_train_holdout_tickers,
    FoldConfig,
)


# ── 定数 ─────────────────────────────────────────────────────────────────────

DEFAULT_FLAT_BAND_PCT = 3.0
DEFAULT_HORIZON = 15
DEFAULT_FEATURE_SET = "v3_ohlcv_ta_relregime_event_fundflow"
TENTATIVE_ACCURACY_PCT = 33.8   # Phase 3-2 で計測した暫定 class15 の accuracy


# ── ラベル生成 ────────────────────────────────────────────────────────────────

def _label_class15(ret_pct: float, flat_band: float) -> int:
    """15営業日リターン (%) → 0=Down / 1=Flat / 2=Up"""
    if ret_pct > flat_band:
        return 2
    if ret_pct < -flat_band:
        return 0
    return 1


# ── フレーム収集 ──────────────────────────────────────────────────────────────

def _collect_frame_class15(
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
    flat_band: float,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    特徴量フレームを取得して y_class15 ラベルを付与する。
    _collect_ticker_frame を再利用し、target_return_pct_h から独自ラベルを生成。
    """
    # 既存の up5 収集関数を呼ぶ (horizon=15, target_return は二値ラベル計算用だが使わない)
    model_df, feature_cols = _collect_ticker_frame(
        svc=svc,
        ticker=ticker,
        asof=asof,
        start=start,
        end=end,
        feature_set=feature_set,
        horizon=horizon,
        regime_symbols=regime_symbols,
        relative_symbols=relative_symbols,
        sector_symbols=sector_symbols,
        target_return=0.03,          # y_up5 は使わない; ここは形式上必要
        include_llm_features=False,
    )
    # target_return_pct_h を使って 3クラスラベルを生成
    if "target_return_pct_h" not in model_df.columns:
        raise RuntimeError(f"target_return_pct_h missing for {ticker}")

    model_df["y_class15"] = model_df["target_return_pct_h"].apply(
        lambda r: _label_class15(float(r), flat_band) if pd.notna(r) else np.nan
    )
    model_df = model_df.dropna(subset=["y_class15"])
    model_df["y_class15"] = model_df["y_class15"].astype(int)
    return model_df, feature_cols


# ── クラス不均衡 sample_weight ────────────────────────────────────────────────

def _compute_sample_weights(y: np.ndarray) -> np.ndarray:
    """逆頻度重み: weight_i = n / (n_classes * count(class_i))"""
    y = np.asarray(y, dtype=int)
    n = len(y)
    classes = np.unique(y)
    n_classes = len(classes)
    w = np.ones(n, dtype=float)
    for c in classes:
        mask = y == c
        count = int(np.sum(mask))
        if count > 0:
            w[mask] = float(n) / (float(n_classes) * float(count))
    return w


# ── モデル訓練 ────────────────────────────────────────────────────────────────

def _fit_multiclass_model(
    svc: PredictionService,
    X: np.ndarray,
    y: np.ndarray,
) -> Any:
    """XGBoost multi:softprob で3クラス分類を訓練する。"""
    import xgboost as xgb  # type: ignore
    X = np.where(np.isinf(X), np.nan, X)
    y = np.asarray(y, dtype=int)
    sw = _compute_sample_weights(y)
    n_unique = len(np.unique(y))
    num_class = max(3, n_unique)

    clf = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=num_class,
        n_estimators=300,
        max_depth=5,
        learning_rate=0.03,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_weight=3,
        gamma=0.05,
        reg_alpha=0.1,
        reg_lambda=2.0,
        eval_metric="mlogloss",
        tree_method="hist",
        random_state=11,
    )
    clf.fit(X, y, sample_weight=sw)
    return clf


# ── Walk-forward OOS 評価 ─────────────────────────────────────────────────────

def _walk_forward_eval_class15(
    svc: PredictionService,
    pooled: pd.DataFrame,
    model_feature_columns: List[str],
    fold_cfg: FoldConfig,
) -> Dict[str, Any]:
    """
    時系列 walk-forward で OOS 予測を収集する。
    y_true (int 0/1/2) と probs (n × 3: [P(Down), P(Flat), P(Up)]) を返す。
    """
    df = pooled.sort_index()
    X_all = df[model_feature_columns].to_numpy(dtype=float)
    y_all = df["y_class15"].astype(int).to_numpy()
    ret_all = df["future_return"].to_numpy(dtype=float)
    dates = [str(d.date()) if hasattr(d, "date") else str(d) for d in df.index]
    tickers = df["ticker"].astype(str).tolist()

    n = len(df)
    min_train = int(max(fold_cfg.min_train_rows, 280))
    step = int(max(1, fold_cfg.fold_step))
    test_window = int(max(1, fold_cfg.test_window))
    purge_n = int(fold_cfg.horizon if fold_cfg.purge_days is None else max(0, int(fold_cfg.purge_days)))
    if str(fold_cfg.embargo_mode).lower() == "days":
        embargo_n = max(0, int(fold_cfg.embargo_days))
    else:
        embargo_n = max(1, int(np.ceil(n * float(max(0.0, fold_cfg.embargo_pct)))))

    y_true_list: List[int] = []
    probs_list: List[np.ndarray] = []   # each entry: shape (test_window, 3)
    rets_list: List[float] = []
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
        if fold_cfg.train_window is not None and int(fold_cfg.train_window) > 0:
            train_from = max(0, train_end - int(fold_cfg.train_window))

        X_train = X_all[train_from:train_end]
        y_train = y_all[train_from:train_end]
        X_test = X_all[start:end]
        y_test = y_all[start:end]
        r_test = ret_all[start:end]
        d_test = dates[start:end]
        t_test = tickers[start:end]

        # 3クラス全部が訓練データに存在しないと multi:softprob が失敗する
        if len(np.unique(y_train)) < 3:
            continue

        try:
            clf = _fit_multiclass_model(svc, X_train, y_train)
            p_test = clf.predict_proba(np.where(np.isinf(X_test), np.nan, X_test))
            # shape: (n_test, 3) = [P(Down), P(Flat), P(Up)]
        except Exception as e:
            print(f"[WARN] fold skip: {e}", flush=True)
            continue

        y_true_list.extend(y_test.tolist())
        probs_list.append(p_test)
        rets_list.extend(r_test.tolist())
        out_dates.extend(d_test)
        out_tickers.extend(t_test)
        fold_count += 1
        next_eligible_start = end + embargo_n
        if fold_count % 5 == 0:
            print(f"  fold {fold_count} done, OOS samples so far: {len(y_true_list)}", flush=True)

    if not y_true_list:
        raise RuntimeError("No valid folds for class15 walk-forward eval.")

    y_true = np.asarray(y_true_list, dtype=int)
    probs = np.vstack(probs_list)  # (N, 3)
    future_returns = np.asarray(rets_list, dtype=float)
    return {
        "y_true": y_true,
        "probs": probs,           # columns: [P(Down), P(Flat), P(Up)]
        "future_returns": future_returns,
        "dates": out_dates,
        "tickers": out_tickers,
        "folds_used": fold_count,
    }


# ── 評価指標 ──────────────────────────────────────────────────────────────────

def _metrics_class15(
    y_true: np.ndarray,
    probs: np.ndarray,       # (N, 3): [P(Down), P(Flat), P(Up)]
    future_returns: np.ndarray,
    flat_band: float,
) -> Dict[str, Any]:
    """direction accuracy, confusion matrix, calibration bins を計算する。"""
    y = np.asarray(y_true, dtype=int)
    p = probs  # (N, 3)
    ret = np.asarray(future_returns, dtype=float)
    n = len(y)

    pred = np.argmax(p, axis=1)   # 0/1/2

    _label_map = {0: "Down", 1: "Flat", 2: "Up"}

    # 1. Overall accuracy
    accuracy = float(np.mean(pred == y))
    counts = np.bincount(y, minlength=3)
    baseline_class = int(np.argmax(counts))
    baseline_accuracy = float(counts[baseline_class]) / n

    # 2. Confusion matrix (rows=actual, cols=predicted)
    cm = np.zeros((3, 3), dtype=int)
    for a, p_ in zip(y.tolist(), pred.tolist()):
        cm[a, p_] += 1
    cm_list = cm.tolist()

    # 3. Direction stats
    direction_stats: Dict[str, Any] = {}
    for c in range(3):
        label = _label_map[c]
        pred_mask = pred == c
        actual_mask = y == c
        n_pred = int(np.sum(pred_mask))
        n_actual = int(np.sum(actual_mask))
        if n_pred == 0:
            direction_stats[label] = {
                "n_pred": 0,
                "n_actual": int(n_actual),
                "precision": None,
                "recall": None,
                "f1": None,
                "mean_return_pct": None,
                "note": "never predicted",
            }
            continue
        tp = int(np.sum(pred_mask & actual_mask))
        precision = tp / n_pred
        recall = float(tp / max(1, n_actual))
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        mean_ret = float(np.mean(ret[pred_mask])) * 100.0
        direction_stats[label] = {
            "n_pred": n_pred,
            "pct_of_total": round(100 * n_pred / n, 1),
            "n_actual": n_actual,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "mean_return_pct": round(mean_ret, 3),
        }

    # 4. Calibration: P(Up) quintiles vs actual Up rate
    prob_up = p[:, 2]
    sorted_idx = np.argsort(prob_up)
    bin_size = n // 5
    calibration_bins = []
    for i in range(5):
        start = i * bin_size
        end = (i + 1) * bin_size if i < 4 else n
        idx = sorted_idx[start:end]
        pv = prob_up[idx]
        actual_up_mask = y[idx] == 2
        actual_up_rate = float(np.mean(actual_up_mask)) if len(idx) > 0 else None
        rets_bin = ret[idx]
        rets_clean = rets_bin[np.isfinite(rets_bin) & (np.abs(rets_bin) < 0.5)]
        mean_ret = float(np.mean(rets_clean)) * 100.0 if len(rets_clean) > 0 else None
        calibration_bins.append({
            "bin": i + 1,
            "p_up_range": f"{float(min(pv)):.3f}-{float(max(pv)):.3f}",
            "mean_p_up": round(float(np.mean(pv)), 3),
            "n": len(idx),
            "actual_up_rate_pct": round(actual_up_rate * 100.0, 1) if actual_up_rate is not None else None,
            "mean_return_pct": round(mean_ret, 3) if mean_ret is not None else None,
        })

    # 5. Pearson r: P(Up) vs return (clipped)
    try:
        xs = prob_up.tolist()
        ys = [max(-0.5, min(0.5, float(r))) for r in future_returns]
        xm = float(np.mean(xs))
        ym = float(np.mean(ys))
        cov = float(np.mean([(x - xm) * (y_ - ym) for x, y_ in zip(xs, ys)]))
        sx = float(np.std(xs))
        sy = float(np.std(ys))
        pearson_r = round(cov / (sx * sy), 4) if sx > 1e-9 and sy > 1e-9 else None
    except Exception:
        pearson_r = None

    return {
        "n": n,
        "accuracy_pct": round(accuracy * 100.0, 1),
        "baseline_accuracy_pct": round(baseline_accuracy * 100.0, 1),
        "baseline_class": _label_map[baseline_class],
        "lift_ppt": round((accuracy - baseline_accuracy) * 100.0, 1),
        "folds_used": None,   # filled in by caller
        "confusion_matrix": {
            "rows": "actual (Down/Flat/Up)",
            "cols": "predicted (Down/Flat/Up)",
            "matrix": cm_list,
        },
        "direction_stats": direction_stats,
        "calibration_bins": calibration_bins,
        "pearson_r_prob_up_vs_return15d": pearson_r,
    }


# ── 暫定 class15 との比較 ─────────────────────────────────────────────────────

def _comparison_table(true_metrics: Dict[str, Any]) -> Dict[str, Any]:
    tentative = {
        "model": "tentative (up5→class15 derivation)",
        "accuracy_pct": TENTATIVE_ACCURACY_PCT,
        "baseline_accuracy_pct": 64.9,
        "lift_ppt": round(TENTATIVE_ACCURACY_PCT - 64.9, 1),
        "pearson_r_prob_up_vs_return15d": -0.0769,
        "flat_predicted": False,
    }
    true = {
        "model": "true (class15 XGBoost multi:softprob)",
        "accuracy_pct": true_metrics["accuracy_pct"],
        "baseline_accuracy_pct": true_metrics["baseline_accuracy_pct"],
        "lift_ppt": true_metrics["lift_ppt"],
        "pearson_r_prob_up_vs_return15d": true_metrics.get("pearson_r_prob_up_vs_return15d"),
        "flat_predicted": (true_metrics.get("direction_stats", {}).get("Flat", {}).get("n_pred", 0) or 0) > 0,
    }
    improvement_ppt = round(true["accuracy_pct"] - tentative["accuracy_pct"], 1)
    verdict = (
        "REPLACE_RECOMMENDED"
        if (improvement_ppt > 0 or not tentative["flat_predicted"])
        else "KEEP_TENTATIVE"
    )
    return {
        "tentative": tentative,
        "true_model": true,
        "improvement_ppt": improvement_ppt,
        "flat_now_predicted": true["flat_predicted"],
        "verdict": verdict,
    }


# ── 最終モデル訓練 ─────────────────────────────────────────────────────────────

def _fit_final_model_class15(
    svc: PredictionService,
    pooled: pd.DataFrame,
    model_feature_columns: List[str],
) -> Any:
    df = pooled.sort_index()
    X = df[model_feature_columns].to_numpy(dtype=float)
    y = df["y_class15"].astype(int).to_numpy()
    print(f"  Final model: X={X.shape}, y dist={dict(zip(*np.unique(y, return_counts=True)))}", flush=True)
    return _fit_multiclass_model(svc, X, y)


# ── メイン ────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train true 15-day 3-class prediction model.")
    parser.add_argument("--tickers-file", required=True)
    parser.add_argument("--holdout-tickers", default=None)
    parser.add_argument("--asof", required=True, help="As-of date YYYY-MM-DD")
    parser.add_argument("--start", required=True, help="Training start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Training end date YYYY-MM-DD")
    parser.add_argument(
        "--feature-set",
        default=DEFAULT_FEATURE_SET,
        choices=[
            "v2_ohlcv_ta_relregime_event",
            "v3_ohlcv_ta_relregime_event_fundflow",
            "v4_ohlcv_ta_relregime_event_fundflow_signals",
        ],
    )
    parser.add_argument("--flat-band", type=float, default=DEFAULT_FLAT_BAND_PCT)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--regime-symbols", default="US:QQQ,US:SPY")
    parser.add_argument("--relative-symbols", default="US:QQQ")
    parser.add_argument("--sector-symbols", default="")
    parser.add_argument("--min-train-rows", type=int, default=280)
    parser.add_argument("--fold-step", type=int, default=10)
    parser.add_argument("--test-window", type=int, default=25)
    parser.add_argument("--train-window", type=int, default=None)
    parser.add_argument("--out-prefix", default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    asof = datetime.strptime(args.asof, "%Y-%m-%d").date()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    flat_band = float(args.flat_band)
    horizon = int(args.horizon)

    tickers = _load_tickers_file(args.tickers_file)
    holdout_requested = _parse_symbol_list(args.holdout_tickers) if args.holdout_tickers else []
    train_tickers, holdout_tickers = _resolve_train_holdout_tickers(tickers, holdout_requested)
    if not train_tickers:
        raise ValueError("Training tickers is empty.")

    regime_symbols = _parse_symbol_list(args.regime_symbols)
    relative_symbols = _parse_symbol_list(args.relative_symbols)
    sector_symbols = _parse_symbol_list(args.sector_symbols) if args.sector_symbols else []

    svc = PredictionService()
    frames: List[pd.DataFrame] = []
    feature_cols_ref: Optional[List[str]] = None
    skipped: List[str] = []
    for i, ticker in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] {ticker}...", flush=True)
        try:
            frame, feature_cols = _collect_frame_class15(
                svc=svc,
                ticker=ticker,
                asof=asof,
                start=start,
                end=end,
                feature_set=args.feature_set,
                horizon=horizon,
                regime_symbols=regime_symbols,
                relative_symbols=relative_symbols,
                sector_symbols=sector_symbols,
                flat_band=flat_band,
            )
            if len(frame) < 50:
                print(f"  -> skip (rows={len(frame)})", flush=True)
                skipped.append(ticker)
                continue
            if feature_cols_ref is None:
                feature_cols_ref = feature_cols
            frames.append(frame)
        except Exception as e:
            print(f"  -> skip ({e})", flush=True)
            skipped.append(ticker)

    print(f"\nCollected {len(frames)} frames, skipped {len(skipped)}", flush=True)
    if feature_cols_ref is None or not frames:
        raise RuntimeError("No frames collected.")

    train_frames = [f for f in frames if str(f["ticker"].iloc[0]).upper() in set(train_tickers)]
    holdout_frames = [f for f in frames if str(f["ticker"].iloc[0]).upper() in set(holdout_tickers)]

    # Pooled dataset (no ticker dummies for OOD generalization)
    pooled_train, model_feature_columns, dummy_cols = _build_pooled_dataset(
        train_frames, feature_cols_ref, use_ticker_dummies=False
    )
    print(f"Pooled train: {len(pooled_train)} rows, {len(model_feature_columns)} features", flush=True)
    # Label distribution
    dist = dict(zip(*np.unique(pooled_train["y_class15"].astype(int).to_numpy(), return_counts=True)))
    print(f"Label dist: {dist}  ({['Down','Flat','Up']})", flush=True)

    fold_cfg = FoldConfig(
        horizon=horizon,
        test_window=int(args.test_window),
        min_train_rows=int(args.min_train_rows),
        fold_step=int(args.fold_step),
        train_window=args.train_window,
    )

    print("\n[Walk-forward OOS evaluation]", flush=True)
    if holdout_frames:
        pooled_holdout, _, _ = _build_pooled_dataset(holdout_frames, feature_cols_ref, use_ticker_dummies=False)
        print(f"Using holdout: {len(pooled_holdout)} rows", flush=True)
        final_model = _fit_final_model_class15(svc, pooled_train, model_feature_columns)
        X_holdout = _build_inference_matrix_for_pooled(
            pooled=pooled_holdout,
            base_feature_cols=feature_cols_ref,
            train_dummy_cols=dummy_cols,
            model_feature_columns=model_feature_columns,
        )
        probs_holdout = final_model.predict_proba(np.where(np.isinf(X_holdout), np.nan, X_holdout))
        eval_out = {
            "y_true": pooled_holdout["y_class15"].astype(int).to_numpy(),
            "probs": probs_holdout,
            "future_returns": pooled_holdout["future_return"].to_numpy(dtype=float),
            "dates": [str(d.date()) if hasattr(d, "date") else str(d) for d in pooled_holdout.index],
            "tickers": pooled_holdout["ticker"].astype(str).tolist(),
            "folds_used": 0,
        }
    else:
        eval_out = _walk_forward_eval_class15(svc, pooled_train, model_feature_columns, fold_cfg)
        print(f"\nFitting final model...", flush=True)
        final_model = _fit_final_model_class15(svc, pooled_train, model_feature_columns)

    metrics = _metrics_class15(
        y_true=eval_out["y_true"],
        probs=eval_out["probs"],
        future_returns=eval_out["future_returns"],
        flat_band=flat_band,
    )
    metrics["folds_used"] = eval_out["folds_used"]
    comparison = _comparison_table(metrics)

    # ── 出力 ─────────────────────────────────────────────────────────────────
    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.out_prefix or f"class15_{asof.strftime('%Y%m%d')}"
    artifact_path = out_dir / f"{prefix}_artifact.pkl"
    eval_json_path = out_dir / f"{prefix}_eval.json"

    artifact = {
        "artifact_version": 1,
        "artifact_type": "class15_multiclass",
        "trained_at": datetime.now().isoformat(),
        "tickers": tickers,
        "train_tickers": train_tickers,
        "holdout_tickers": holdout_tickers,
        "feature_set": args.feature_set,
        "horizon_days": horizon,
        "flat_band_pct": flat_band,
        "num_class": 3,
        "class_labels": {0: "Down", 1: "Flat", 2: "Up"},
        "base_feature_columns": feature_cols_ref,
        "ticker_dummy_columns": dummy_cols,
        "model_feature_columns": model_feature_columns,
        "regime_symbols": regime_symbols,
        "relative_symbols": relative_symbols,
        "sector_symbols": sector_symbols,
        "model": final_model,
    }
    with artifact_path.open("wb") as f:
        pickle.dump(artifact, f)

    eval_payload = {
        "ok": True,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "asof": asof.isoformat()},
        "flat_band_pct": flat_band,
        "horizon_days": horizon,
        "feature_set": args.feature_set,
        "n_tickers": len(frames),
        "skipped_tickers": skipped,
        "metrics": metrics,
        "comparison_vs_tentative": comparison,
        "artifact_path": str(artifact_path),
    }
    eval_json_path.write_text(
        json.dumps(eval_payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # ── サマリー出力 ──────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("Phase 3-3b: class15 真モデル 訓練完了")
    print(f"{'='*65}")
    print(f"  OOS N={metrics['n']}  folds={metrics['folds_used']}  horizon={horizon}d  flat_band=±{flat_band}%")
    print(f"\n  [Overall accuracy]")
    print(f"    true model  : {metrics['accuracy_pct']}%")
    print(f"    baseline    : {metrics['baseline_accuracy_pct']}%  ({metrics['baseline_class']})")
    print(f"    lift        : {metrics['lift_ppt']:+.1f}pp")
    print(f"    Pearson r   : {metrics.get('pearson_r_prob_up_vs_return15d')}")
    print(f"\n  [Direction stats]")
    for d in ("Up", "Flat", "Down"):
        s = metrics["direction_stats"].get(d, {})
        if s.get("n_pred", 0) == 0:
            print(f"    {d}: NOT PREDICTED ({s.get('note', '')})")
        else:
            print(f"    {d}: n_pred={s['n_pred']}({s['pct_of_total']}%)  prec={s['precision']:.3f}  rec={s['recall']:.3f}  F1={s['f1']:.3f}  mean_ret={s['mean_return_pct']:+.2f}%")
    print(f"\n  [Confusion matrix (rows=actual, cols=predicted)]")
    print(f"    {'':12s} Pred:Down  Pred:Flat  Pred:Up")
    cm_m = metrics["confusion_matrix"]["matrix"]
    for i, lbl in enumerate(["Actual:Down", "Actual:Flat", "Actual:Up"]):
        print(f"    {lbl:12s}   {cm_m[i][0]:6d}     {cm_m[i][1]:6d}   {cm_m[i][2]:6d}")
    print(f"\n  [Calibration: P(Up) bins vs actual Up-15d rate]")
    for b in metrics["calibration_bins"]:
        print(f"    Bin{b['bin']} p_up={b['p_up_range']} -> actual_Up={b['actual_up_rate_pct']}%  mean_ret={b['mean_return_pct']:+.2f}%")
    print(f"\n  [vs 暫定 class15]")
    cmp = comparison
    print(f"    tentative accuracy : {cmp['tentative']['accuracy_pct']}%")
    print(f"    true model accuracy: {cmp['true_model']['accuracy_pct']}%")
    print(f"    improvement        : {cmp['improvement_ppt']:+.1f}pp")
    print(f"    Flat now predicted : {cmp['flat_now_predicted']}")
    print(f"    VERDICT            : {cmp['verdict']}")
    print(f"\n  artifact : {artifact_path}")
    print(f"  eval     : {eval_json_path}")
    print(f"{'='*65}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
