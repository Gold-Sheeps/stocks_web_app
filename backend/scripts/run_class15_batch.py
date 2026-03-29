"""run_class15_batch.py: 15営業日3クラス予測(class15)を生成して DB に保存するバッチ。

モード:
  [真モデルモード]  --model-path または自動検出で class15_*_artifact.pkl が使える場合
                    XGBoost multi:softprob で直接3クラス確率を計算して保存
  [暫定モード]      true モデルが見つからない場合は up5 確率から変換（Phase 3-2 時点の式）

使い方:
  uv run python backend/scripts/run_class15_batch.py
  uv run python backend/scripts/run_class15_batch.py --limit 50
  uv run python backend/scripts/run_class15_batch.py --symbols US:NVDA,US:AMD
  uv run python backend/scripts/run_class15_batch.py --model-path backend/ml_predictor_data/class15_v1_us_20260321_artifact.pkl
  uv run python backend/scripts/run_class15_batch.py --dry-run --force-tentative

保存先:
  ai_predictions (cal_method='class15', horizon_days=15, prediction_type 相当)
  PK: (symbol_key, asof, cal_method='class15')
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.ai_prediction_service import UPSERT_CLASS15_SQL, CLASS15_HORIZON_DAYS, CLASS15_FLAT_BAND_PCT
from app.services.prediction_service import PredictionService, PredictorConfig


# ── 真モデル: アーティファクト自動検出 ─────────────────────────────────────────

def _find_latest_class15_artifact() -> Optional[Path]:
    """backend/ml_predictor_data/ から最新の class15 アーティファクトを返す。"""
    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    cands = sorted(out_dir.glob("class15_*_artifact.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    # smoke_test は除外
    cands = [p for p in cands if "smoke_test" not in p.name]
    return cands[0] if cands else None


def _load_class15_artifact(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"class15 artifact not found: {p}")
    with p.open("rb") as f:
        art = pickle.load(f)
    if art.get("artifact_type") != "class15_multiclass":
        raise ValueError(f"Not a class15 artifact: {path}")
    return art


def _build_inference_row_class15(
    svc: PredictionService,
    ticker: str,
    asof: date,
    artifact: Dict[str, Any],
) -> pd.DataFrame:
    """特徴量ベクトルを構築して model_feature_columns に揃えた DataFrame を返す。"""
    feature_set = str(artifact.get("feature_set", "v3_ohlcv_ta_relregime_event_fundflow"))
    horizon = int(artifact.get("horizon_days", 15))
    cfg = PredictorConfig(
        flat_band_pct=3.0,
        horizon_trading_days=horizon,
        calibration="sigmoid",
        feature_version=feature_set,
    )
    raw = svc._load_prices(ticker, as_of=asof)
    feat = svc._build_feature_frame(
        ticker=ticker,
        price_df=raw,
        as_of=asof,
        regime_symbols=artifact.get("regime_symbols", ["US:QQQ"]),
        relative_symbols=artifact.get("relative_symbols", ["US:QQQ"]),
        sector_symbols=artifact.get("sector_symbols", []),
        feature_version=feature_set,
    )
    prepared = svc._prepare_dataset(feat, asof, cfg)
    pred_row = prepared["pred_row"]
    base_cols: list[str] = list(artifact["base_feature_columns"])
    row = pred_row.reindex(base_cols, fill_value=0.0).astype(float).to_dict()

    # ticker dummy columns (all 0 since no ticker dummies in class15 model)
    for c in artifact.get("ticker_dummy_columns", []):
        row[c] = 0.0

    model_cols: list[str] = list(artifact["model_feature_columns"])
    X_df = pd.DataFrame([row]).reindex(columns=model_cols, fill_value=0.0).astype(float)
    return X_df


def _predict_class15_true(
    svc: PredictionService,
    ticker: str,
    asof: date,
    artifact: Dict[str, Any],
) -> tuple[float, float, float, int]:
    """真の class15 モデルで (prob_down, prob_flat, prob_up, predicted_class) を返す。"""
    X_df = _build_inference_row_class15(svc, ticker, asof, artifact)
    probs = artifact["model"].predict_proba(X_df.to_numpy(dtype=float))[0]
    # probs = [P(Down), P(Flat), P(Up)] (XGBoost multi:softprob の class 順)
    prob_down = float(probs[0])
    prob_flat = float(probs[1])
    prob_up   = float(probs[2])
    predicted_class = int(np.argmax(probs))
    return prob_up, prob_flat, prob_down, predicted_class


# ── 暫定モード ────────────────────────────────────────────────────────────────

def _derive_class15_probs(p_up5: float) -> tuple[float, float, float, int]:
    """up5 バイナリ確率から class15 の 3クラス確率と predicted_class を導出する。

    - prob_up = p_up5 (上昇優勢)
    - prob_down = (1 - p_up5) * 0.55  (下落基準率)
    - prob_flat = 1 - prob_up - prob_down
    - predicted_class: 2=Up, 1=Flat, 0=Down

    NOTE: これは暫定変換。真の15日3クラスモデルは Phase 3 で別途訓練する。
    """
    p = max(0.0, min(1.0, float(p_up5)))
    prob_up = p
    prob_down = round((1.0 - p) * 0.55, 6)
    prob_flat = round(1.0 - prob_up - prob_down, 6)
    # 丸め誤差補正
    total = prob_up + prob_flat + prob_down
    if abs(total - 1.0) > 1e-9:
        prob_flat += 1.0 - total

    if prob_up >= prob_flat and prob_up >= prob_down:
        predicted_class = 2
    elif prob_down >= prob_up and prob_down >= prob_flat:
        predicted_class = 0
    else:
        predicted_class = 1

    return prob_up, prob_flat, prob_down, predicted_class


def _get_symbols_from_db(db: Database, limit: int) -> list[tuple[str, date, float]]:
    """ai_predictions から既存 up5 レコードの (symbol_key, asof, p_up5) を返す。"""
    rows = db.execute_query(
        """
        SELECT DISTINCT ON (symbol_key) symbol_key, asof, p_up5
        FROM ai_predictions
        WHERE COALESCE(cal_method, 'none') != 'class15'
          AND p_up5 IS NOT NULL
        ORDER BY symbol_key, asof DESC, updated_at DESC
        LIMIT %s
        """,
        (limit,),
    ) or []
    return [(str(r[0]), r[1], float(r[2])) for r in rows]


def _get_symbols_from_list(db: Database, symbols: list[str]) -> list[tuple[str, date, float]]:
    """指定銘柄の最新 up5 レコードを返す。"""
    rows = db.execute_query(
        """
        SELECT DISTINCT ON (symbol_key) symbol_key, asof, p_up5
        FROM ai_predictions
        WHERE symbol_key = ANY(%s)
          AND COALESCE(cal_method, 'none') != 'class15'
          AND p_up5 IS NOT NULL
        ORDER BY symbol_key, asof DESC, updated_at DESC
        """,
        (symbols,),
    ) or []
    return [(str(r[0]), r[1], float(r[2])) for r in rows]


def run_batch(
    symbols: list[str] | None = None,
    limit: int = 200,
    dry_run: bool = False,
    model_path: str | None = None,
    force_tentative: bool = False,
    asof_override: date | None = None,
) -> dict:
    """
    Args:
        model_path: 真モデルアーティファクトのパス。None の場合は自動検出。
        force_tentative: True の場合は真モデルが存在しても暫定式を使う。
        asof_override: 真モデルモード時の asof 日付 (None なら今日)。
    """
    db = Database()
    if not db.connect():
        return {"ok": False, "error": "db_connect_failed"}

    # 真モデル読み込み
    artifact = None
    svc = None
    if not force_tentative:
        try:
            p = Path(model_path) if model_path else _find_latest_class15_artifact()
            if p:
                artifact = _load_class15_artifact(p)
                svc = PredictionService()
                print(f"[class15_batch] TRUE MODEL: {p.name}", flush=True)
        except Exception as e:
            print(f"[class15_batch] True model load failed ({e}), falling back to tentative.", flush=True)
            artifact = None
            svc = None

    if artifact is None:
        print("[class15_batch] TENTATIVE MODE: using up5→class15 derivation.", flush=True)

    try:
        if symbols:
            targets = _get_symbols_from_list(db, symbols)
        else:
            targets = _get_symbols_from_db(db, limit)

        saved = 0
        skipped = 0
        errors = 0

        # 真モデルモードでは asof = 今日 (または override)
        asof_true = asof_override or datetime.now().date()

        for symbol_key, asof, p_up5 in targets:
            try:
                if artifact is not None and svc is not None:
                    # 真モデルモード: asof は今日
                    try:
                        prob_up, prob_flat, prob_down, predicted_class = _predict_class15_true(
                            svc, symbol_key, asof_true, artifact
                        )
                        asof = asof_true
                    except Exception as e:
                        # 価格データなし等 → 暫定にフォールバック
                        print(f"[WARN] true model failed for {symbol_key}: {e}, using tentative", flush=True)
                        prob_up, prob_flat, prob_down, predicted_class = _derive_class15_probs(p_up5)
                else:
                    # 暫定モード
                    prob_up, prob_flat, prob_down, predicted_class = _derive_class15_probs(p_up5)

                if dry_run:
                    mode = "TRUE" if artifact is not None else "TENT"
                    print(
                        f"[DRY/{mode}] {symbol_key} asof={asof} "
                        f"→ Up={prob_up:.3f} Flat={prob_flat:.3f} Down={prob_down:.3f} class={predicted_class}"
                    )
                    saved += 1
                    continue
                db.execute_command(
                    UPSERT_CLASS15_SQL,
                    (
                        symbol_key,
                        asof,
                        CLASS15_HORIZON_DAYS,
                        CLASS15_FLAT_BAND_PCT,
                        prob_up,
                        prob_flat,
                        prob_down,
                        predicted_class,
                        prob_up,  # p_up5 列にも prob_up を格納
                    ),
                )
                saved += 1
            except Exception as e:
                print(f"[WARN] {symbol_key}: {e}", flush=True)
                errors += 1
                continue

        action = "dry-run" if dry_run else "saved"
        mode_str = "true_model" if artifact is not None else "tentative"
        print(
            f"[class15_batch] mode={mode_str} {action}={saved} skipped={skipped} errors={errors} "
            f"total_targets={len(targets)}",
            flush=True,
        )
        return {"ok": True, "saved": saved, "skipped": skipped, "errors": errors, "total": len(targets), "mode": mode_str}
    finally:
        db.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and save class15 predictions.")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol keys (e.g. US:NVDA,US:AMD).")
    parser.add_argument("--limit", type=int, default=200, help="Max symbols to process (default: 200).")
    parser.add_argument("--dry-run", action="store_true", help="Print results without saving.")
    parser.add_argument(
        "--model-path", default=None,
        help="Path to class15 artifact (.pkl). If omitted, auto-detects latest class15_*_artifact.pkl.",
    )
    parser.add_argument(
        "--force-tentative", action="store_true",
        help="Use tentative up5→class15 derivation even if a true model exists.",
    )
    parser.add_argument(
        "--asof", default=None,
        help="As-of date for true model inference (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    asof_override = None
    if args.asof:
        asof_override = datetime.strptime(args.asof, "%Y-%m-%d").date()
    result = run_batch(
        symbols=symbols,
        limit=args.limit,
        dry_run=args.dry_run,
        model_path=args.model_path,
        force_tentative=args.force_tentative,
        asof_override=asof_override,
    )
    sys.exit(0 if result.get("ok") else 1)
