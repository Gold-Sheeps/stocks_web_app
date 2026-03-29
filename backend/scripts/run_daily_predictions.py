"""Batch daily AI prediction runner.

Usage:
    python backend/scripts/run_daily_predictions.py --asof 2026-02-22
    python backend/scripts/run_daily_predictions.py --asof 2026-02-22 --max-tickers 10 --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List
import re

# backend/ をパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.ai_prediction_service import AiPredictionService
from app.services.explanation_summary_service import ExplanationSummaryService
from app.services.feature_fusion_service import FeatureFusionService
from app.services.llm_signal_extraction_service import LlmSignalExtractionService
from app.services.prediction_service import PredictionService
from app.services.rag_retrieval_service import RagRetrievalService
from scripts.predict_up5 import (
    _build_inference_row,
    _latest_artifact_path,
    _load_artifact,
    predict_with_artifact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso_date(value: str) -> "datetime.date":
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"--asof must be YYYY-MM-DD, got: {value}") from exc


def _normalize_symbol_key(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if not s:
        return s
    if ":" in s:
        return s
    if re.fullmatch(r"\d{4}", s):
        return f"JP:{s}"
    return f"US:{s}"


def _collect_tickers_from_db(db: Database, max_tickers: int) -> List[str]:
    """watchlist の symbol_key を返す。"""
    rows = db.execute_query("SELECT DISTINCT symbol_key FROM watchlist")
    if not rows:
        return []
    return [r[0] for r in rows if r[0]]


def _collect_tickers_from_canslim() -> List[str]:
    """canslim_breakout_report.csv があれば読む。なければ空リストを返す。"""
    search_dirs = [
        Path(__file__).resolve().parents[1],                      # backend/
        Path(__file__).resolve().parents[1] / "ml_predictor_data",
        Path(__file__).resolve().parents[2],                      # project root
    ]
    for d in search_dirs:
        csv_path = d / "canslim_breakout_report.csv"
        if csv_path.exists():
            try:
                import pandas as pd
                df = pd.read_csv(csv_path)
                # symbol_key または symbol カラムを探す
                col = None
                for candidate in ("symbol_key", "symbol", "ticker"):
                    if candidate in df.columns:
                        col = candidate
                        break
                if col is None:
                    return []
                tickers = [_normalize_symbol_key(v) for v in df[col].dropna().unique()]
                tickers = [t for t in tickers if t]
                print(f"[INFO] canslim CSV loaded from {csv_path} ({len(tickers)} tickers)")
                return tickers
            except Exception as e:
                print(f"[WARN] Failed to read canslim CSV at {csv_path}: {e}")
    return []


def _collect_tickers_from_volume(db: Database, max_tickers: int) -> List[str]:
    """price_daily の出来高上位銘柄を返す（フォールバック）。JP株はAIモデルが未対応なので除外。"""
    sql = """
        SELECT symbol_key, AVG(volume) AS avg_vol
        FROM price_daily
        WHERE volume > 0
          AND symbol_key NOT LIKE 'JP:%%'
        GROUP BY symbol_key
        HAVING COUNT(*) >= 20
        ORDER BY avg_vol DESC
        LIMIT %s
    """
    rows = db.execute_query(sql, (max_tickers,))
    if not rows:
        return []
    return [r[0] for r in rows if r[0]]


def _build_ticker_list(db: Database, max_tickers: int) -> List[str]:
    """重複排除・最大 max_tickers 件のティッカーリストを組み立てる。"""
    seen: set = set()
    out: List[str] = []

    def add(tickers: List[str]) -> None:
        for t in tickers:
            if t not in seen:
                seen.add(t)
                out.append(t)

    # 1. watchlist
    add(_collect_tickers_from_db(db, max_tickers))

    # 2. canslim CSV
    add(_collect_tickers_from_canslim())

    # 3. 出来高上位（フォールバック）
    if len(out) < max_tickers:
        add(_collect_tickers_from_volume(db, max_tickers))

    return out[:max_tickers]


def _best_effort_refresh_external_news_and_llm(
    ticker: str,
    asof,
    llm_extract_svc: LlmSignalExtractionService,
    rag_retrieval_svc: RagRetrievalService,
) -> dict:
    out = {"attempted": True, "news_ingest": "skipped", "llm_extract": "skipped", "months_back": 12}
    try:
        backend_dir = Path(__file__).resolve().parents[1]
        script_path = backend_dir / "scripts" / "ingest_external_news_rss_backfill.py"
        if script_path.exists():
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--asof",
                    asof.isoformat(),
                    "--months-back",
                    "12",
                    "--symbol",
                    ticker,
                    "--timeout",
                    "8",
                ],
                cwd=str(backend_dir.parent),
                capture_output=True,
                text=True,
                timeout=20,
            )
            out["news_ingest"] = "success" if proc.returncode == 0 else "warning"
    except Exception as e:
        out["news_ingest"] = "warning"
        out["news_ingest_error"] = f"{type(e).__name__}: {e}"
    try:
        docs = rag_retrieval_svc.get_documents_for_asof(ticker, asof=asof, limit=20) or []
        out["docs_for_asof"] = len(docs)
        if docs:
            ext = llm_extract_svc.extract_structured_features(ticker, asof, docs)
            save = llm_extract_svc.save_structured_features(
                symbol_key=ticker,
                asof=asof,
                llm_features=dict(ext.get("llm_features") or {}),
                evidence_items=list(ext.get("evidence_items") or []),
                quality_metrics=dict(ext.get("quality_metrics") or {}),
                extraction_status="success",
                point_in_time_ok=bool(ext.get("point_in_time_ok", True)),
            )
            out["llm_extract"] = "success" if bool(save.get("ok")) else "warning"
    except Exception as e:
        out["llm_extract"] = "warning"
        out["llm_extract_error"] = f"{type(e).__name__}: {e}"
    return out



# ---------------------------------------------------------------------------
# UPSERT SQL
# ---------------------------------------------------------------------------

UPSERT_SQL = """
    INSERT INTO ai_predictions
        (symbol_key, asof, p_up5, threshold_buy, decision, cal_method, artifact_path, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
    ON CONFLICT (symbol_key, asof, cal_method)
    DO UPDATE SET
        p_up5          = EXCLUDED.p_up5,
        threshold_buy  = EXCLUDED.threshold_buy,
        decision       = EXCLUDED.decision,
        cal_method     = EXCLUDED.cal_method,
        artifact_path  = EXCLUDED.artifact_path,
        updated_at     = CURRENT_TIMESTAMP
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily batch AI predictions.")
    parser.add_argument("--asof", required=True, help="As-of date YYYY-MM-DD.")
    parser.add_argument("--max-tickers", type=int, default=500, help="Max tickers to process (default 500).")
    parser.add_argument("--artifact", default=None, help="Artifact path. Default: latest pooled artifact.")
    parser.add_argument(
        "--calibration-method",
        default="none",
        choices=["none", "artifact", "platt", "isotonic"],
        help="Probability calibration for stored p_up5. Default: none (raw model probability).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip DB writes.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    asof = _parse_iso_date(args.asof)
    artifact_path = args.artifact or _latest_artifact_path()
    print(f"[INFO] artifact: {artifact_path}")
    print(f"[INFO] asof:     {asof}")
    print(f"[INFO] dry-run:  {args.dry_run}")

    artifact = _load_artifact(artifact_path)
    cal_method = str(args.calibration_method)

    db = Database()
    if not db.connect():
        print("[ERROR] DB connection failed.")
        return 1

    tickers = _build_ticker_list(db, args.max_tickers)
    total = len(tickers)
    print(f"[INFO] {total} tickers to process.")

    if total == 0:
        print("[WARN] No tickers found. Exiting.")
        db.disconnect()
        return 0

    svc = PredictionService()
    ai_meta_svc = AiPredictionService()
    llm_extract_svc = LlmSignalExtractionService()
    rag_retrieval_svc = RagRetrievalService()
    fusion_svc = FeatureFusionService()
    expl_svc = ExplanationSummaryService()
    succeeded: List[str] = []
    failed: List[tuple] = []

    for i, ticker in enumerate(tickers):
        try:
            external_news_refresh = _best_effort_refresh_external_news_and_llm(
                ticker=ticker,
                asof=asof,
                llm_extract_svc=llm_extract_svc,
                rag_retrieval_svc=rag_retrieval_svc,
            )
            llm_payload = None
            llm_features = {}
            llm_degraded_mode = False
            llm_features_used = False
            feature_set_version = "xgb_only_v1"
            evidence_set_id = None
            explanation_summary = None
            llm_error_message = None
            llm_degraded_reason = None
            llm_feature_usage = {}
            try:
                llm_payload = llm_extract_svc.get_latest_structured_features(ticker, asof)
                if llm_payload and llm_payload.get("point_in_time_ok", True):
                    llm_features = dict(llm_payload.get("llm_features") or {})
                    feature_set_version = str(llm_payload.get("feature_set_version") or "xgb_base_plus_llm_v1")
                    evidence_set_id = llm_payload.get("evidence_set_id")
                    explanation_summary = expl_svc.summarize_evidence(llm_payload.get("evidence_items") or [])
                elif llm_payload and not llm_payload.get("point_in_time_ok", True):
                    llm_degraded_mode = True
                    llm_degraded_reason = "point_in_time_violation"
            except Exception as llm_exc:
                llm_degraded_mode = True
                llm_error_message = str(llm_exc)
                llm_degraded_reason = "llm_feature_lookup_error"
                print(f"[WARN] LLM degraded for {ticker}: {llm_error_message}")

            x_df, _ = _build_inference_row(svc, ticker, asof, artifact)
            x_df, llm_feature_usage = fusion_svc.merge_inference_features(
                x_df,
                list(artifact.get("model_feature_columns", [])),
                llm_features,
            )
            llm_features_used = bool(llm_feature_usage.get("llm_features_used"))
            pred = predict_with_artifact(artifact, x_df, calibration_method=args.calibration_method)
            p_up5 = float(pred["p_up5"])
            threshold = float(pred["threshold_buy"])
            decision = str(pred["action"])
            cal_method = str(pred.get("cal_method_used", cal_method))

            if args.dry_run:
                print(f"[DRY-RUN] {ticker}: p_up5={p_up5:.4f} decision={decision}")
            else:
                db.execute_command(
                    UPSERT_SQL,
                    (ticker, asof, p_up5, threshold, decision, cal_method, artifact_path),
                )
                ai_meta_svc._ensure_prediction_aux_tables(db)
                ai_meta_svc._save_prediction_run_meta(
                    db=db,
                    symbol_key=ticker,
                    asof=asof,
                    cal_method=cal_method,
                    llm_features_used=bool(llm_features_used),
                    llm_degraded_mode=bool(llm_degraded_mode),
                    feature_set_version=feature_set_version,
                    evidence_set_id=evidence_set_id,
                    feature_usage=llm_feature_usage,
                    explanation_summary=explanation_summary,
                    llm_error_message=llm_error_message,
                    llm_degraded_reason=llm_degraded_reason,
                    extraction_meta=(
                        {
                            "extractor_version": llm_payload.get("extractor_version"),
                            "updated_at": llm_payload.get("updated_at"),
                            "quality_metrics": llm_payload.get("quality_metrics"),
                            "point_in_time_ok": llm_payload.get("point_in_time_ok"),
                            "extraction_status": llm_payload.get("extraction_status"),
                            "extraction_error": llm_payload.get("extraction_error"),
                            "external_news_refresh": external_news_refresh,
                        }
                        if llm_payload else None
                    ),
                )
                if llm_payload and evidence_set_id:
                    ai_meta_svc._save_prediction_evidence_snapshot(
                        db=db,
                        symbol_key=ticker,
                        asof=asof,
                        cal_method=cal_method,
                        evidence_set_id=evidence_set_id,
                        evidence_items=list(llm_payload.get("evidence_items") or []),
                    )
            succeeded.append(ticker)
            if external_news_refresh.get("news_ingest") != "success" or external_news_refresh.get("llm_extract") != "success":
                print(f"[WARN] pre-llm refresh {ticker}: {external_news_refresh}")
        except Exception as e:
            failed.append((ticker, str(e)))
            print(f"[FAIL] {ticker}: {e}")

        if (i + 1) % 50 == 0:
            print(f"[PROGRESS] {i+1}/{total} processed ...")

    db.disconnect()

    print(f"\n[DONE] succeeded={len(succeeded)} failed={len(failed)} total={total}")
    if failed:
        print("[FAILED TICKERS]")
        for t, err in failed[:20]:
            print(f"  {t}: {err}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
