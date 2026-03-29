"""AI prediction service: DB lookup + on-demand inference."""
from __future__ import annotations

import datetime
import json
import logging
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

# backend/ をパスに追加（scripts.predict_up5 を遅延 import するため）
_BACKEND_DIR = str(Path(__file__).resolve().parents[2])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.db.database import Database

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

SELECT_LATEST_SQL = """
    SELECT symbol_key, asof, p_up5, threshold_buy, decision, cal_method, artifact_path, updated_at
    FROM ai_predictions
    WHERE symbol_key = %s
      AND COALESCE(cal_method, 'none') != 'class15'
    ORDER BY
        asof DESC,
        CASE LOWER(COALESCE(cal_method, ''))
            WHEN 'none' THEN 0
            WHEN 'platt' THEN 1
            WHEN 'artifact' THEN 2
            WHEN 'isotonic' THEN 3
            ELSE 9
        END,
        updated_at DESC
    LIMIT 1
"""

# ── class15: 15営業日3クラス予測 ──────────────────────────────────────────────
# prediction_type='class15' として cal_method='class15' で ai_predictions に共存保存
# predicted_class: 0=Down, 1=Flat, 2=Up
# prob_up + prob_flat + prob_down = 1.0
CLASS15_HORIZON_DAYS  = 15
CLASS15_FLAT_BAND_PCT = 3.0  # ±3% で Flat 判定（対称）

UPSERT_CLASS15_SQL = """
    INSERT INTO ai_predictions
        (symbol_key, asof, cal_method, horizon_days, flat_band_pct,
         prob_up, prob_flat, prob_down, predicted_class,
         p_up5, threshold_buy, decision, updated_at)
    VALUES (%s, %s, 'class15', %s, %s, %s, %s, %s, %s, %s, 0.5, 'class15', CURRENT_TIMESTAMP)
    ON CONFLICT (symbol_key, asof, cal_method)
    DO UPDATE SET
        horizon_days   = EXCLUDED.horizon_days,
        flat_band_pct  = EXCLUDED.flat_band_pct,
        prob_up        = EXCLUDED.prob_up,
        prob_flat      = EXCLUDED.prob_flat,
        prob_down      = EXCLUDED.prob_down,
        predicted_class= EXCLUDED.predicted_class,
        p_up5          = EXCLUDED.p_up5,
        updated_at     = CURRENT_TIMESTAMP
"""

SELECT_LATEST_CLASS15_SQL = """
    SELECT symbol_key, asof, prob_up, prob_flat, prob_down, predicted_class,
           horizon_days, flat_band_pct, updated_at
    FROM ai_predictions
    WHERE symbol_key = %s
      AND cal_method = 'class15'
    ORDER BY asof DESC, updated_at DESC
    LIMIT 1
"""

logger = logging.getLogger(__name__)

# ── 予測ホライゾン定義 ────────────────────────────────────────────────────────
# Phase 1: 5営業日予測 (既存 up5 モデルを再利用)
#   - prob_up = p_up5: 「5日以内に+5%超」の確率
#   - FLAT_BAND_HIGH = +5.0: up5 モデルの正クラス閾値に合わせた意図的な非対称設計
#     （「+5%未満は上昇確定とみなさない」という保守的判定）
#   - FLAT_BAND_LOW = -3.0: -3%超の下落はフラット扱い（ノイズ許容）
#   → 非対称は仕様。対称化は Phase 3 での3クラスモデル再訓練時に検討
# Phase 3（将来）: 15営業日3クラス予測に切り替え予定
#   - HORIZON_DAYS=15, FLAT_BAND_LOW=-3.0, FLAT_BAND_HIGH=+3.0（対称）に変更
#   - モデル再訓練が必要。DB スキーマはそのまま使える
# ─────────────────────────────────────────────────────────────────────────────
HORIZON_DAYS   = 5
FLAT_BAND_LOW  = -3.0   # -3% 未満 → class 0 (下落)
FLAT_BAND_HIGH = +5.0   # +5% 超   → class 2 (上昇)  ※up5モデル正クラス閾値に合わせた非対称設計
FLAT_BAND_PCT  = 3.0    # DB保存用（対称代表値として記録）


def _compute_action_label(decision: str | None, gate_reason: str | None) -> str:
    """
    DB に保存せず、API 返却直前に呼ぶ。
    decision + gate_reason から最終的な action_label を返す。
    戻り値: "BUY" | "HOLD" | "SELL" | "WATCH" | "BLOCKED"
    """
    if decision == "GATE_BLOCKED" or (gate_reason and "停止" in gate_reason):
        return "BLOCKED"
    if decision == "CAUTION":
        return "WATCH"
    if decision == "BUY":
        return "BUY"
    if decision == "SELL":
        return "SELL"
    if decision in ("HOLD", "WAIT", "NO TRADE", "NO_TRADE"):
        return "HOLD"
    return "WATCH"


def _compute_ai_signal_strength(
    p_up5: float | None,
    action_label: str,
    gate_reasons: list[str],
) -> float:
    """
    Stock Detail 用の AI シグナル強度 (0-100)。
    final_rank_score から tech/CANSLIM 成分を除いたAI純粋シグナル。
    - BLOCKED → 0
    - WATCH (ゲート起因) → p * 0.5
    - それ以外 → p * 1.0
    """
    if action_label == "BLOCKED":
        return 0.0
    p = float(p_up5 or 0) * 100
    # ゲート起因 WATCH（market_env_unavailable はゲートなしと同扱い）
    is_gate_caused = bool(gate_reasons) and any(
        r in ("market_regime_blocked", "market_regime_caution", "prob_below_caution_threshold")
        for r in gate_reasons
    )
    if action_label == "WATCH" and is_gate_caused:
        return round(p * 0.5, 1)
    return round(p, 1)


class AiPredictionService:
    STOCK_AI_NEWS_LOOKBACK_DAYS = 75
    STOCK_AI_NEWS_DOC_LIMIT = 75
    STOCK_QUERY_VOCAB_OVERRIDES: Dict[str, List[str]] = {
        "NVDA": ["datacenter", "gpu", "ai chips", "h100", "blackwell", "export controls", "hyperscaler capex"],
        "AMD": ["datacenter", "ai gpu", "mi300", "cpu share", "server demand", "export controls"],
        "TSLA": ["deliveries", "ev demand", "price cuts", "fsd", "robotaxi", "china sales", "gross margin"],
        "META": ["ad revenue", "reels", "ai capex", "engagement", "ad pricing"],
        "AMZN": ["aws", "cloud growth", "retail margin", "prime", "advertising"],
        "MSFT": ["azure", "copilot", "enterprise spending", "cloud growth", "openai"],
        "GOOGL": ["search ads", "cloud", "youtube ads", "antitrust", "ai overview"],
        "AAPL": ["iphone demand", "services growth", "china sales", "app store", "supply chain"],
    }
    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_latest(self, symbol_key: str) -> Dict[str, Any]:
        """DB から最新の推論結果を返す。なければ has_prediction=False。class15 も追加で返す。"""
        symbol_key = symbol_key.upper()
        try:
            db = Database()
            if not db.connect():
                return {"has_prediction": False, "symbol_key": symbol_key, "error": "DB connection failed"}
            try:
                rows = db.execute_query(SELECT_LATEST_SQL, (symbol_key,))
                if not rows:
                    result = {"has_prediction": False, "symbol_key": symbol_key}
                else:
                    row = rows[0]
                    result = {
                        "has_prediction": True,
                        "symbol_key": row[0],
                        "asof": str(row[1]),
                        "p_up5": float(row[2]),
                        "threshold_buy": float(row[3]),
                        "decision": str(row[4]),
                        "cal_method": row[5],
                        "artifact_path": row[6],
                        "updated_at": str(row[7]),
                    }
                    meta = self._get_latest_run_meta(db, str(row[0]), row[1], str(row[5] or "none"))
                    if meta:
                        result.update(meta)
                    # ゲート適用 + action_label 付与（DB には保存しない）
                    result = self._apply_gate_and_action_label(result)

                # class15 を追加（無くても落ちない）
                result["class15"] = self._load_latest_class15(db, symbol_key)
                return result
            finally:
                db.disconnect()
        except Exception as e:
            return {"has_prediction": False, "symbol_key": symbol_key, "error": str(e)}

    def _load_latest_class15(self, db: Database, symbol_key: str) -> Dict[str, Any] | None:
        """ai_predictions から最新の class15 レコードを返す。なければ None。"""
        try:
            rows = db.execute_query(SELECT_LATEST_CLASS15_SQL, (symbol_key,))
            if not rows:
                return None
            row = rows[0]
            prob_up   = float(row[2]) if row[2] is not None else None
            prob_flat = float(row[3]) if row[3] is not None else None
            prob_down = float(row[4]) if row[4] is not None else None
            predicted_class = int(row[5]) if row[5] is not None else None
            # 方向ラベル: 2=Up, 1=Flat, 0=Down
            _labels = {2: "Up", 1: "Flat", 0: "Down"}
            direction = _labels.get(predicted_class) if predicted_class is not None else None
            return {
                "prediction_type": "class15",
                "horizon_days": int(row[6]) if row[6] is not None else CLASS15_HORIZON_DAYS,
                "flat_band_pct": float(row[7]) if row[7] is not None else CLASS15_FLAT_BAND_PCT,
                "prob_up": prob_up,
                "prob_flat": prob_flat,
                "prob_down": prob_down,
                "predicted_class": predicted_class,
                "direction": direction,
                "asof": str(row[1]),
                "updated_at": str(row[8]),
            }
        except Exception as e:
            logger.warning("_load_latest_class15 failed for %s: %s", symbol_key, e)
            return None

    def _apply_gate_and_action_label(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """market_environment からゲートを適用し action_label / gate_reasons を付与する。DB には保存しない。"""
        market_env = self._get_latest_market_environment()
        decision = result.get("decision", "") or ""
        p_up5 = float(result.get("p_up5") or 0.0)
        gate_reason: str | None = None
        gate_reasons: list[str] = []  # 構造化された理由リスト（UI説明用）

        if market_env:
            market_regime = market_env.get("regime", "")
            position_limit_pct = market_env.get("position_limit_pct")

            if market_regime == "新規買い停止" and decision == "BUY":
                decision = "GATE_BLOCKED"
                gate_reason = "新規買い停止中（市場環境）"
                gate_reasons.append("market_regime_blocked")
            elif market_regime == "注意" and p_up5 < 0.6 and decision == "BUY":
                decision = "CAUTION"
                gate_reason = "市場環境「注意」のため閾値引き上げ（p<0.6）"
                gate_reasons.append("market_regime_caution")
                gate_reasons.append("prob_below_caution_threshold")

            result["market_regime"] = market_regime
            if position_limit_pct is not None:
                result["position_limit_pct"] = position_limit_pct
        else:
            # market_environment テーブルが空 / DB 接続失敗 → ゲートなし（素通し）
            gate_reasons.append("market_env_unavailable")

        if gate_reason:
            result["gate_reason"] = gate_reason
            result["original_decision"] = result.get("decision")
            result["decision"] = decision

        result["action_label"] = _compute_action_label(decision, gate_reason)
        result["gate_reasons"] = gate_reasons  # [] = ゲート発動なし
        # AI信号強度: p_up5ベース + ゲート調整 (tech/CANSLIM なしの純粋AIシグナル, 0-100)
        result["ai_signal_strength"] = _compute_ai_signal_strength(
            result.get("p_up5"), result["action_label"], gate_reasons
        )
        return result

    def run_on_demand(self, symbol_key: str) -> Dict[str, Any]:
        """オンデマンドで推論を実行し、DB に保存して結果を返す。

        注: 例外を絶対に外に漏らさない。
        ServerErrorMiddleware が CORS middleware の外側にあるため、
        uncaught exception が発生すると CORS ヘッダーなしの 500 になる。

        Price data missing の RuntimeError が発生した場合は、yfinance からデータを
        自動取得してリトライする。
        """
        symbol_key = symbol_key.upper()
        asof = self._resolve_inference_asof(symbol_key)
        try:
            # 遅延 import（サーバー起動時の重いMLライブラリ読み込みを回避）
            from scripts.predict_up5 import (
                _build_inference_row,
                _latest_artifact_path,
                _load_artifact,
                predict_with_artifact,
            )
            from app.services.prediction_service import PredictionService
            from app.services.feature_fusion_service import FeatureFusionService
            from app.services.llm_signal_extraction_service import LlmSignalExtractionService
            from app.services.explanation_summary_service import ExplanationSummaryService
            from app.services.rag_retrieval_service import RagRetrievalService

            market = "JP" if symbol_key.startswith("JP:") else "US"
            artifact_path = _latest_artifact_path(market=market)
            artifact = _load_artifact(artifact_path)
            cal_method = "none"

            svc = PredictionService()
            fusion_svc = FeatureFusionService()
            llm_extract_svc = LlmSignalExtractionService()
            expl_svc = ExplanationSummaryService()
            rag_retrieval_svc = RagRetrievalService()
            external_news_refresh = self._best_effort_refresh_external_news_and_llm(
                symbol_key=symbol_key,
                asof=asof,
                llm_extract_svc=llm_extract_svc,
                rag_retrieval_svc=rag_retrieval_svc,
            )

            llm_payload: Dict[str, Any] | None = None
            llm_features: Dict[str, Any] = {}
            llm_degraded_mode = False
            llm_features_used = False
            feature_set_version = "xgb_only_v1"
            evidence_set_id: str | None = None
            explanation_summary: Dict[str, Any] | None = None
            llm_error_message: str | None = None
            llm_degraded_reason: str | None = None
            llm_feature_usage: Dict[str, Any] = {}

            try:
                llm_payload = llm_extract_svc.get_latest_structured_features(symbol_key, asof)
                if llm_payload and llm_payload.get("point_in_time_ok", True):
                    llm_features = dict(llm_payload.get("llm_features") or {})
                    evidence_set_id = llm_payload.get("evidence_set_id")
                    feature_set_version = str(llm_payload.get("feature_set_version") or "xgb_base_plus_llm_v1")
                    explanation_summary = expl_svc.summarize_evidence(llm_payload.get("evidence_items") or [])
                else:
                    feature_set_version = "xgb_only_v1"
                    if llm_payload and not llm_payload.get("point_in_time_ok", True):
                        llm_degraded_mode = True
                        llm_degraded_reason = "point_in_time_violation"
            except Exception as llm_exc:
                llm_degraded_mode = True
                llm_error_message = str(llm_exc)
                llm_degraded_reason = "llm_feature_lookup_error"
                logger.warning("LLM feature retrieval degraded for %s: %s", symbol_key, llm_error_message)

            max_retries = 2
            for attempt in range(max_retries):
                try:
                    x_df, _ = _build_inference_row(svc, symbol_key, asof, artifact)
                    x_df, llm_feature_usage = fusion_svc.merge_inference_features(
                        x_df,
                        list(artifact.get("model_feature_columns", [])),
                        llm_features,
                    )
                    llm_features_used = bool(llm_feature_usage.get("llm_features_used"))
                    try:
                        pred_feature_usage = svc.build_feature_usage_breakdown(
                            list(artifact.get("model_feature_columns", [])),
                            extra_features=llm_features,
                        )
                    except Exception:
                        pred_feature_usage = {}
                    if pred_feature_usage:
                        llm_feature_usage = {**llm_feature_usage, "prediction_service": pred_feature_usage}
                    if not llm_features_used and llm_features:
                        # LLM payload exists but artifact/model does not consume these columns.
                        feature_set_version = feature_set_version or "xgb_only_v1"
                    pred = predict_with_artifact(artifact, x_df, calibration_method="none")

                    p_up5 = float(pred["p_up5"])
                    threshold = float(pred["threshold_buy"])
                    decision = str(pred["action"])
                    cal_method = str(pred.get("cal_method_used", "none"))

                    db = Database()
                    db_saved = False
                    if db.connect():
                        try:
                            db.execute_command(
                                UPSERT_SQL,
                                (symbol_key, asof, p_up5, threshold, decision, cal_method, artifact_path),
                            )
                            self._ensure_prediction_aux_tables(db)
                            self._save_prediction_run_meta(
                                db=db,
                                symbol_key=symbol_key,
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
                                    }
                                    if llm_payload else None
                                ),
                            )
                            if llm_payload and evidence_set_id:
                                self._save_prediction_evidence_snapshot(
                                    db=db,
                                    symbol_key=symbol_key,
                                    asof=asof,
                                    cal_method=cal_method,
                                    evidence_set_id=evidence_set_id,
                                    evidence_items=list(llm_payload.get("evidence_items") or []),
                                )
                            db_saved = True
                        finally:
                            db.disconnect()

                    # ★ ハードゲート: 市場環境による最終判定調整
                    market_env = self._get_latest_market_environment()
                    market_regime = None
                    position_limit_pct = None
                    gate_reason = None
                    original_decision = decision

                    if market_env:
                        market_regime = market_env.get("regime", "")
                        position_limit_pct = market_env.get("position_limit_pct")

                        if market_regime == "新規買い停止" and decision == "BUY":
                            decision = "GATE_BLOCKED"
                            gate_reason = "新規買い停止中（市場環境）"
                        elif market_regime == "注意" and p_up5 < 0.6 and decision == "BUY":
                            decision = "CAUTION"
                            gate_reason = "市場環境「注意」のため閾値引き上げ（p<0.6）"

                    result: Dict[str, Any] = {
                        "has_prediction": True,
                        "symbol_key": symbol_key,
                        "asof": str(asof),
                        "p_up5": p_up5,
                        "threshold_buy": threshold,
                        "decision": decision,
                        "cal_method": cal_method,
                        "artifact_path": artifact_path,
                        "updated_at": str(datetime.datetime.now()),
                        "db_saved": db_saved,
                        "llm_features_used": bool(llm_features_used),
                        "llm_degraded_mode": bool(llm_degraded_mode),
                        "feature_set_version": feature_set_version,
                    }
                    if llm_degraded_reason:
                        result["llm_degraded_reason"] = llm_degraded_reason
                    if evidence_set_id:
                        result["evidence_set_id"] = evidence_set_id
                    if llm_feature_usage:
                        result["feature_usage"] = llm_feature_usage
                    if external_news_refresh:
                        result["external_news_refresh"] = external_news_refresh
                    news_usage = self._load_news_usage_details(symbol_key, asof)
                    if external_news_refresh and external_news_refresh.get("docs_for_asof") is not None:
                        news_usage["docs_for_asof_count"] = int(external_news_refresh.get("docs_for_asof") or 0)
                    result["news_usage"] = news_usage
                    if explanation_summary:
                        result["explanation_summary"] = explanation_summary
                    if market_regime is not None:
                        result["market_regime"] = market_regime
                    if position_limit_pct is not None:
                        result["position_limit_pct"] = position_limit_pct
                    if gate_reason is not None:
                        result["gate_reason"] = gate_reason
                        result["original_decision"] = original_decision
                    return result

                except RuntimeError as e:
                    error_msg = str(e)
                    print(
                        f"[AiPredictionService] RuntimeError attempt={attempt}: {error_msg[:120]}",
                        flush=True,
                    )
                    if ("Price data missing" in error_msg or "No prediction row available for as_of_date" in error_msg) and attempt == 0:
                        print(
                            f"[AiPredictionService] Calling _fetch_missing_data for {symbol_key}",
                            flush=True,
                        )
                        self._fetch_missing_data(symbol_key)
                        asof = self._resolve_inference_asof(symbol_key)
                        continue  # リトライ
                    else:
                        if attempt == 1:
                            print(f"[AiPredictionService] Retry failed: {error_msg}", flush=True)
                        return {
                            "symbol_key": symbol_key,
                            "has_prediction": False,
                            "error": True,
                            "message": error_msg,
                        }
                except Exception as e:
                    print(
                        f"[AiPredictionService] Exception attempt={attempt} type={type(e).__name__}: {str(e)[:120]}",
                        flush=True,
                    )
                    return {
                        "symbol_key": symbol_key,
                        "has_prediction": False,
                        "error": True,
                        "message": f"Prediction failed: {str(e)}",
                    }

            # ループを抜けたが結果が返らなかった場合（理論上到達しない）
            return {
                "symbol_key": symbol_key,
                "has_prediction": False,
                "error": True,
                "message": "Prediction failed after data fetch retry",
            }

        except Exception:
            logger.exception("AiPredictionService.run_on_demand failed for symbol=%s", symbol_key)
            raise

    def _get_latest_market_environment(self) -> Dict[str, Any] | None:
        """market_environment テーブルから最新レコードの data dict を返す。"""
        import json as _json
        db = Database()
        try:
            if not db.connect():
                return None
            rows = db.execute_query(
                "SELECT data FROM market_environment ORDER BY check_date DESC LIMIT 1"
            )
            if rows and rows[0] and rows[0][0]:
                raw = rows[0][0]
                return raw if isinstance(raw, dict) else _json.loads(raw)
        except Exception as e:
            print(f"[AiPredictionService] _get_latest_market_environment error: {e}", flush=True)
        finally:
            db.disconnect()
        return None

    def _ensure_prediction_aux_tables(self, db: Database) -> None:
        db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS ai_prediction_run_meta (
                symbol_key TEXT NOT NULL,
                asof DATE NOT NULL,
                cal_method TEXT NOT NULL,
                llm_features_used BOOLEAN NOT NULL DEFAULT FALSE,
                llm_degraded_mode BOOLEAN NOT NULL DEFAULT FALSE,
                feature_set_version TEXT NULL,
                evidence_set_id TEXT NULL,
                feature_usage_json TEXT NULL,
                explanation_summary_json TEXT NULL,
                extraction_meta_json TEXT NULL,
                llm_error_message TEXT NULL,
                llm_degraded_reason TEXT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol_key, asof, cal_method)
            )
            """
        )
        # Backward-compatible schema upgrades for existing tables.
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS llm_degraded_reason TEXT NULL"
        )
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS realized_horizon_days INTEGER NULL"
        )
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS actual_close_h REAL NULL"
        )
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS actual_return_pct_h REAL NULL"
        )
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS actual_up5 BOOLEAN NULL"
        )
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS prob_error_up5 REAL NULL"
        )
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS brier_component REAL NULL"
        )
        db.execute_command(
            "ALTER TABLE ai_prediction_run_meta ADD COLUMN IF NOT EXISTS realized_at TIMESTAMP NULL"
        )
        db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS ai_prediction_evidence_snapshot (
                symbol_key TEXT NOT NULL,
                asof DATE NOT NULL,
                cal_method TEXT NOT NULL,
                evidence_set_id TEXT NOT NULL,
                evidence_rank INTEGER NOT NULL,
                source_id TEXT NULL,
                source_type TEXT NULL,
                source_ref TEXT NULL,
                published_at TIMESTAMP NULL,
                claim_type TEXT NULL,
                direction TEXT NULL,
                strength REAL NULL,
                relevance REAL NULL,
                short_rationale TEXT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol_key, asof, cal_method, evidence_rank)
            )
            """
        )

    def _save_prediction_run_meta(
        self,
        db: Database,
        symbol_key: str,
        asof: datetime.date,
        cal_method: str,
        llm_features_used: bool,
        llm_degraded_mode: bool,
        feature_set_version: str,
        evidence_set_id: str | None,
        feature_usage: Dict[str, Any] | None,
        explanation_summary: Dict[str, Any] | None,
        llm_error_message: str | None,
        llm_degraded_reason: str | None,
        extraction_meta: Dict[str, Any] | None,
    ) -> None:
        db.execute_command(
            """
            INSERT INTO ai_prediction_run_meta
                (symbol_key, asof, cal_method, llm_features_used, llm_degraded_mode, feature_set_version,
                 evidence_set_id, feature_usage_json, explanation_summary_json, extraction_meta_json,
                 llm_error_message, llm_degraded_reason, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT (symbol_key, asof, cal_method)
            DO UPDATE SET
                llm_features_used = EXCLUDED.llm_features_used,
                llm_degraded_mode = EXCLUDED.llm_degraded_mode,
                feature_set_version = EXCLUDED.feature_set_version,
                evidence_set_id = EXCLUDED.evidence_set_id,
                feature_usage_json = EXCLUDED.feature_usage_json,
                explanation_summary_json = EXCLUDED.explanation_summary_json,
                extraction_meta_json = EXCLUDED.extraction_meta_json,
                llm_error_message = EXCLUDED.llm_error_message,
                llm_degraded_reason = EXCLUDED.llm_degraded_reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                symbol_key,
                asof,
                str(cal_method or "none"),
                bool(llm_features_used),
                bool(llm_degraded_mode),
                feature_set_version,
                evidence_set_id,
                self._safe_json(feature_usage),
                self._safe_json(explanation_summary),
                self._safe_json(extraction_meta),
                llm_error_message,
                llm_degraded_reason,
            ),
        )

    def _save_prediction_evidence_snapshot(
        self,
        db: Database,
        symbol_key: str,
        asof: datetime.date,
        cal_method: str,
        evidence_set_id: str,
        evidence_items: List[Dict[str, Any]],
    ) -> None:
        for idx, ev in enumerate((evidence_items or [])[:10], start=1):
            db.execute_command(
                """
                INSERT INTO ai_prediction_evidence_snapshot
                    (symbol_key, asof, cal_method, evidence_set_id, evidence_rank,
                     source_id, source_type, source_ref, published_at, claim_type, direction,
                     strength, relevance, short_rationale, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT (symbol_key, asof, cal_method, evidence_rank)
                DO UPDATE SET
                    evidence_set_id = EXCLUDED.evidence_set_id,
                    source_id = EXCLUDED.source_id,
                    source_type = EXCLUDED.source_type,
                    source_ref = EXCLUDED.source_ref,
                    published_at = EXCLUDED.published_at,
                    claim_type = EXCLUDED.claim_type,
                    direction = EXCLUDED.direction,
                    strength = EXCLUDED.strength,
                    relevance = EXCLUDED.relevance,
                    short_rationale = EXCLUDED.short_rationale,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    symbol_key,
                    asof,
                    str(cal_method or "none"),
                    evidence_set_id,
                    idx,
                    ev.get("source_id"),
                    ev.get("source_type"),
                    ev.get("source_ref"),
                    self._parse_ts(ev.get("published_at")),
                    ev.get("claim_type"),
                    ev.get("direction"),
                    self._safe_float(ev.get("strength")),
                    self._safe_float(ev.get("relevance")),
                    self._safe_text(ev.get("short_rationale"), limit=240),
                ),
            )

    def _get_latest_run_meta(
        self,
        db: Database,
        symbol_key: str,
        asof: Any,
        cal_method: str,
    ) -> Dict[str, Any] | None:
        rows = db.execute_query(
            """
            SELECT llm_features_used, llm_degraded_mode, feature_set_version, evidence_set_id,
                   llm_degraded_reason,
                   feature_usage_json, explanation_summary_json,
                   realized_horizon_days, actual_close_h, actual_return_pct_h, actual_up5,
                   prob_error_up5, brier_component, realized_at
            FROM ai_prediction_run_meta
            WHERE symbol_key = %s AND asof = %s AND cal_method = %s
            LIMIT 1
            """,
            (symbol_key, asof, str(cal_method or "none")),
        )
        if not rows:
            return None
        row = rows[0]
        out: Dict[str, Any] = {
            "llm_features_used": bool(row[0]),
            "llm_degraded_mode": bool(row[1]),
            "feature_set_version": row[2],
            "evidence_set_id": row[3],
        }
        if row[4]:
            out["llm_degraded_reason"] = row[4]
        feature_usage = self._json_loads(row[5])
        explanation_summary = self._json_loads(row[6])
        if feature_usage is not None:
            out["feature_usage"] = feature_usage
        if explanation_summary is not None:
            out["explanation_summary"] = explanation_summary
        if row[7] is not None:
            out["realized_horizon_days"] = int(row[7])
        if row[8] is not None:
            out["actual_close_h"] = float(row[8])
        if row[9] is not None:
            out["actual_return_pct_h"] = float(row[9])
        if row[10] is not None:
            out["actual_up5"] = bool(row[10])
        if row[11] is not None:
            out["prob_error_up5"] = float(row[11])
        if row[12] is not None:
            out["brier_component"] = float(row[12])
        if row[13] is not None:
            out["realized_at"] = str(row[13])
        return out

    def backfill_actuals(self, symbol_key: str | None = None, horizon_days: int = 10, limit: int = 500) -> Dict[str, Any]:
        horizon_days = max(1, int(horizon_days))
        limit = max(1, min(int(limit), 5000))
        db = Database()
        if not db.connect():
            return {"ok": False, "error": "db_connect_failed"}
        try:
            self._ensure_prediction_aux_tables(db)
            params: List[Any] = []
            sql = """
                SELECT p.symbol_key, p.asof, p.cal_method, p.p_up5
                FROM ai_predictions p
                LEFT JOIN ai_prediction_run_meta m
                  ON m.symbol_key = p.symbol_key AND m.asof = p.asof AND m.cal_method = p.cal_method
                WHERE 1=1
            """
            if symbol_key:
                sql += " AND p.symbol_key = %s"
                params.append(symbol_key.upper())
            sql += """
                ORDER BY p.asof DESC, p.updated_at DESC
                LIMIT %s
            """
            params.append(limit)
            rows = db.execute_query(sql, tuple(params)) or []
            updated = 0
            skipped_unrealized = 0
            for sym, asof, cal_method, p_up5 in rows:
                realized = self._compute_realized_outcome(db, str(sym), asof, horizon_days=horizon_days)
                if not realized.get("ready"):
                    skipped_unrealized += 1
                    continue
                actual_up5 = bool(realized.get("actual_up5"))
                p = float(p_up5 or 0.0)
                prob_error = float((1.0 if actual_up5 else 0.0) - p)
                brier = float(prob_error * prob_error)
                db.execute_command(
                    """
                    INSERT INTO ai_prediction_run_meta
                        (symbol_key, asof, cal_method, llm_features_used, llm_degraded_mode, updated_at,
                         realized_horizon_days, actual_close_h, actual_return_pct_h, actual_up5, prob_error_up5, brier_component, realized_at)
                    VALUES (%s,%s,%s,FALSE,FALSE,CURRENT_TIMESTAMP,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                    ON CONFLICT (symbol_key, asof, cal_method)
                    DO UPDATE SET
                        realized_horizon_days = EXCLUDED.realized_horizon_days,
                        actual_close_h = EXCLUDED.actual_close_h,
                        actual_return_pct_h = EXCLUDED.actual_return_pct_h,
                        actual_up5 = EXCLUDED.actual_up5,
                        prob_error_up5 = EXCLUDED.prob_error_up5,
                        brier_component = EXCLUDED.brier_component,
                        realized_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        str(sym),
                        asof,
                        str(cal_method or "none"),
                        horizon_days,
                        realized.get("actual_close_h"),
                        realized.get("actual_return_pct_h"),
                        actual_up5,
                        prob_error,
                        brier,
                    ),
                )
                updated += 1

            summary = self._get_realized_error_summary(db, symbol_key=symbol_key)
            return {
                "ok": True,
                "symbol_key": symbol_key.upper() if symbol_key else None,
                "horizon_days": horizon_days,
                "scanned": len(rows),
                "updated": updated,
                "skipped_unrealized": skipped_unrealized,
                "summary": summary,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            db.disconnect()

    def get_symbol_error_ranking(self, limit: int = 30, min_realized: int = 3) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        min_realized = max(1, int(min_realized))
        db = Database()
        if not db.connect():
            return {"items": [], "error": "db_connect_failed"}
        try:
            self._ensure_prediction_aux_tables(db)
            rows = db.execute_query(
                """
                SELECT m.symbol_key,
                       COUNT(*) AS realized_count,
                       AVG(m.brier_component) AS avg_brier,
                       AVG(ABS(m.prob_error_up5)) AS mae_prob,
                       AVG(CASE WHEN m.actual_up5 THEN 1.0 ELSE 0.0 END) AS actual_up5_rate,
                       AVG(p.p_up5) AS avg_pred_prob,
                       MAX(m.asof) AS latest_asof
                FROM ai_prediction_run_meta m
                JOIN ai_predictions p
                  ON p.symbol_key = m.symbol_key AND p.asof = m.asof AND p.cal_method = m.cal_method
                WHERE m.actual_up5 IS NOT NULL
                GROUP BY m.symbol_key
                HAVING COUNT(*) >= %s
                ORDER BY AVG(m.brier_component) DESC NULLS LAST, COUNT(*) DESC, m.symbol_key ASC
                LIMIT %s
                """,
                (min_realized, limit),
            ) or []
            items = []
            for r in rows:
                avg_pred = float(r[5]) if r[5] is not None else None
                actual_rate = float(r[4]) if r[4] is not None else None
                items.append(
                    {
                        "symbol_key": str(r[0]),
                        "realized_count": int(r[1] or 0),
                        "avg_brier": (float(r[2]) if r[2] is not None else None),
                        "mae_prob": (float(r[3]) if r[3] is not None else None),
                        "actual_up5_rate": actual_rate,
                        "avg_pred_prob": avg_pred,
                        "calibration_gap": ((actual_rate - avg_pred) if (actual_rate is not None and avg_pred is not None) else None),
                        "latest_asof": (str(r[6]) if r[6] is not None else None),
                    }
                )
            return {"items": items, "min_realized": min_realized, "limit": limit}
        except Exception as e:
            return {"items": [], "error": str(e)}
        finally:
            db.disconnect()

    def get_news_source_performance_report(
        self,
        symbol_key: str | None = None,
        limit: int = 20,
        min_obs: int = 3,
        days_back: int = 365 * 3,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        min_obs = max(1, int(min_obs))
        days_back = max(30, min(int(days_back), 3650))
        db = Database()
        if not db.connect():
            return {"items": [], "error": "db_connect_failed"}
        try:
            self._ensure_prediction_aux_tables(db)
            params: List[Any] = [datetime.date.today() - datetime.timedelta(days=days_back)]
            sql = """
                SELECT e.symbol_key, e.source_type, e.source_ref, e.published_at,
                       m.asof, m.actual_up5, m.prob_error_up5, m.brier_component,
                       p.p_up5
                FROM ai_prediction_evidence_snapshot e
                JOIN ai_prediction_run_meta m
                  ON m.symbol_key = e.symbol_key AND m.asof = e.asof AND m.cal_method = e.cal_method
                JOIN ai_predictions p
                  ON p.symbol_key = e.symbol_key AND p.asof = e.asof AND p.cal_method = e.cal_method
                WHERE m.actual_up5 IS NOT NULL
                  AND m.asof >= %s
            """
            if symbol_key:
                sql += " AND e.symbol_key = %s"
                params.append(symbol_key.upper())
            sql += " ORDER BY m.asof DESC"
            rows = db.execute_query(sql, tuple(params)) or []

            agg: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                sym = str(r[0] or "")
                src_type = str(r[1] or "")
                src_ref = str(r[2] or "")
                src_dom = self._source_domain_from_ref_or_title(src_ref, "")
                key = src_dom or src_type or "unknown"
                rec = agg.setdefault(
                    key,
                    {
                        "source_domain": key,
                        "source_type": src_type or "unknown",
                        "mentions": 0,
                        "runs": set(),
                        "symbols": set(),
                        "sum_brier": 0.0,
                        "sum_abs_prob_error": 0.0,
                        "sum_prob_error": 0.0,
                        "sum_pred_prob": 0.0,
                        "sum_actual": 0.0,
                        "published_min": None,
                        "published_max": None,
                    },
                )
                rec["mentions"] += 1
                run_key = f"{sym}|{r[4]}"
                rec["runs"].add(run_key)
                rec["symbols"].add(sym)
                actual = 1.0 if bool(r[5]) else 0.0
                prob_error = float(r[6] or 0.0)
                brier = float(r[7] or 0.0)
                pred_prob = float(r[8] or 0.0)
                rec["sum_brier"] += brier
                rec["sum_abs_prob_error"] += abs(prob_error)
                rec["sum_prob_error"] += prob_error
                rec["sum_pred_prob"] += pred_prob
                rec["sum_actual"] += actual
                pub = self._parse_ts(r[3])
                if pub is not None:
                    rec["published_min"] = pub if rec["published_min"] is None else min(rec["published_min"], pub)
                    rec["published_max"] = pub if rec["published_max"] is None else max(rec["published_max"], pub)

            items: List[Dict[str, Any]] = []
            for _, rec in agg.items():
                n = int(rec["mentions"])
                if n < min_obs:
                    continue
                run_n = max(1, len(rec["runs"]))
                avg_pred = rec["sum_pred_prob"] / n
                actual_rate = rec["sum_actual"] / n
                items.append(
                    {
                        "source_domain": rec["source_domain"],
                        "source_type": rec["source_type"],
                        "mentions": n,
                        "unique_runs": len(rec["runs"]),
                        "symbol_count": len(rec["symbols"]),
                        "avg_brier": rec["sum_brier"] / n,
                        "avg_abs_prob_error": rec["sum_abs_prob_error"] / n,
                        "avg_prob_error": rec["sum_prob_error"] / n,
                        "avg_pred_prob": avg_pred,
                        "actual_up5_rate": actual_rate,
                        "calibration_gap": actual_rate - avg_pred,
                        "avg_mentions_per_run": n / run_n,
                        "published_min": rec["published_min"].isoformat() if rec["published_min"] else None,
                        "published_max": rec["published_max"].isoformat() if rec["published_max"] else None,
                    }
                )
            items.sort(key=lambda x: (-(x.get("mentions") or 0), -(x.get("avg_abs_prob_error") or 0.0)))
            return {
                "items": items[:limit],
                "symbol_key": symbol_key.upper() if symbol_key else None,
                "min_obs": min_obs,
                "days_back": days_back,
                "limit": limit,
            }
        except Exception as e:
            return {"items": [], "error": str(e)}
        finally:
            db.disconnect()

    def _compute_realized_outcome(self, db: Database, symbol_key: str, asof: Any, horizon_days: int = 10) -> Dict[str, Any]:
        rows = db.execute_query(
            """
            SELECT trading_date, close
            FROM price_daily
            WHERE symbol_key = %s AND trading_date >= %s
            ORDER BY trading_date ASC
            LIMIT %s
            """,
            (symbol_key.upper(), asof, int(horizon_days + 1)),
        ) or []
        if len(rows) < int(horizon_days + 1):
            return {"ready": False}
        try:
            base_close = float(rows[0][1])
            fut_close = float(rows[horizon_days][1])
        except Exception:
            return {"ready": False}
        ret_pct = (fut_close / max(1e-9, base_close) - 1.0) * 100.0
        return {
            "ready": True,
            "actual_close_h": fut_close,
            "actual_return_pct_h": float(ret_pct),
            "actual_up5": bool(ret_pct >= 5.0),
        }

    def _get_realized_error_summary(self, db: Database, symbol_key: str | None = None) -> Dict[str, Any]:
        params: List[Any] = []
        sql = """
            SELECT COUNT(*) AS n,
                   AVG(CASE WHEN actual_up5 IS NOT NULL THEN brier_component END) AS avg_brier,
                   AVG(CASE WHEN actual_up5 IS NOT NULL THEN ABS(prob_error_up5) END) AS mae_prob,
                   AVG(CASE WHEN actual_up5 IS TRUE THEN 1.0 ELSE 0.0 END) AS actual_up5_rate
            FROM ai_prediction_run_meta
            WHERE actual_up5 IS NOT NULL
        """
        if symbol_key:
            sql += " AND symbol_key = %s"
            params.append(symbol_key.upper())
        rows = db.execute_query(sql, tuple(params)) or []
        if not rows or not rows[0]:
            return {"realized_count": 0}
        r = rows[0]
        return {
            "realized_count": int(r[0] or 0),
            "avg_brier": (float(r[1]) if r[1] is not None else None),
            "mae_prob": (float(r[2]) if r[2] is not None else None),
            "actual_up5_rate": (float(r[3]) if r[3] is not None else None),
        }

    def _safe_json(self, data: Any) -> str | None:
        if data is None:
            return None
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return None

    def _json_loads(self, raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _parse_ts(self, value: Any) -> datetime.datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.date):
            return datetime.datetime.combine(value, datetime.time.min)
        raw = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
        return None

    def _safe_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _safe_text(self, value: Any, limit: int = 200) -> str | None:
        if value is None:
            return None
        txt = str(value).replace("\n", " ").strip()
        return txt[:limit] if txt else None

    def _resolve_inference_asof(self, symbol_key: str) -> datetime.date:
        """Use latest available DB trading date to avoid timezone/trading-day gaps."""
        today = datetime.date.today()
        db = Database()
        try:
            if not db.connect():
                return today
            rows = db.execute_query(
                """
                SELECT MAX(trading_date)
                FROM price_daily
                WHERE symbol_key = %s AND trading_date <= %s
                """,
                (symbol_key, today),
            )
            if rows and rows[0] and rows[0][0]:
                return rows[0][0]
            rows = db.execute_query(
                "SELECT MAX(trading_date) FROM price_daily WHERE trading_date <= %s",
                (today,),
            )
            if rows and rows[0] and rows[0][0]:
                return rows[0][0]
        except Exception as e:
            print(f"[AiPredictionService] _resolve_inference_asof error: {e}", flush=True)
        finally:
            db.disconnect()
        return today

    def _best_effort_refresh_external_news_and_llm(
        self,
        symbol_key: str,
        asof: datetime.date,
        llm_extract_svc: Any,
        rag_retrieval_svc: Any,
    ) -> Dict[str, Any]:
        """
        Run-now pre-step: fetch external RSS news (12 months) and refresh structured features.
        Failures are warning-only; prediction must continue with XGBoost fallback.
        """
        result: Dict[str, Any] = {
            "attempted": True,
            "months_back": 12,
            "news_ingest": "skipped",
            "llm_extract": "skipped",
        }
        queries = self._build_external_news_refresh_queries(symbol_key)
        result["queries"] = queries
        try:
            script_path = Path(__file__).resolve().parents[2] / "scripts" / "ingest_external_news_rss_backfill.py"
            if script_path.exists():
                feeds_file = Path(__file__).resolve().parents[2] / "ml_predictor_data" / f"_stock_news_feeds_{symbol_key.replace(':','_')}.json"
                cmd = [
                    sys.executable,
                    str(script_path),
                    "--asof",
                    asof.isoformat(),
                    "--months-back",
                    "12",
                    "--feeds-file",
                    str(feeds_file),
                    "--timeout",
                    "8",
                ]
                feeds_spec = []
                for q in queries:
                    feeds_spec.append(
                        {
                            "symbol_key": symbol_key.upper(),
                            "url": q.get("url"),
                            "source_type": "google_news_rss",
                            "metadata": {
                                "provider": "google-news-rss",
                                "stock_query_label": q.get("label"),
                                "query": q.get("query"),
                            },
                        }
                    )
                feeds_file.parent.mkdir(parents=True, exist_ok=True)
                feeds_file.write_text(json.dumps(feeds_spec, ensure_ascii=False), encoding="utf-8")
                proc = subprocess.run(
                    cmd,
                    cwd=str(Path(__file__).resolve().parents[3]),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                result["news_ingest"] = "success" if proc.returncode == 0 else "warning"
                tail = ((proc.stdout or "").splitlines() + (proc.stderr or "").splitlines())[-6:]
                if tail:
                    result["news_ingest_log_tail"] = tail
            else:
                result["news_ingest"] = "warning"
                result["news_ingest_error"] = f"script_not_found:{script_path}"
        except Exception as e:
            result["news_ingest"] = "warning"
            result["news_ingest_error"] = f"{type(e).__name__}: {e}"
        finally:
            try:
                if 'feeds_file' in locals() and feeds_file.exists():
                    feeds_file.unlink()
            except Exception:
                pass

        try:
            docs = rag_retrieval_svc.get_documents_for_asof(
                symbol_key,
                asof=asof,
                limit=self.STOCK_AI_NEWS_DOC_LIMIT,
            ) or []
            result["docs_for_asof"] = len(docs)
            if not docs:
                return result
            ext = llm_extract_svc.extract_structured_features(symbol_key, asof, docs)
            save = llm_extract_svc.save_structured_features(
                symbol_key=symbol_key,
                asof=asof,
                llm_features=dict(ext.get("llm_features") or {}),
                evidence_items=list(ext.get("evidence_items") or []),
                quality_metrics=dict(ext.get("quality_metrics") or {}),
                extraction_status="success",
                point_in_time_ok=bool(ext.get("point_in_time_ok", True)),
            )
            result["llm_extract"] = "success" if bool(save.get("ok")) else "warning"
            if save.get("evidence_set_id"):
                result["evidence_set_id"] = save.get("evidence_set_id")
        except Exception as e:
            result["llm_extract"] = "warning"
            result["llm_extract_error"] = f"{type(e).__name__}: {e}"
        return result

    def _build_external_news_refresh_queries(self, symbol_key: str) -> List[Dict[str, Any]]:
        ticker = symbol_key.split(":", 1)[1] if ":" in symbol_key else symbol_key
        company_name = self._lookup_company_name(symbol_key)
        name_part = f' "{company_name}"' if company_name else ""
        base = f"{ticker}{name_part}"
        extra_terms = self._symbol_specific_query_terms(symbol_key, company_name)
        extra_clause = f" {' '.join(extra_terms)}" if extra_terms else ""
        query_specs = [
            ("Earnings / Guidance", f"{base} earnings guidance revenue outlook"),
            ("Analyst / Target", f"{base} analyst upgrade downgrade price target"),
            ("Product / Demand", f"{base} datacenter AI demand supply chain export controls{extra_clause}"),
            ("Options / Vol", f"{base} options flow put call implied volatility gamma short interest"),
        ]
        out: List[Dict[str, Any]] = []
        for label, query in query_specs:
            out.append(
                {
                    "symbol_key": symbol_key.upper(),
                    "label": label,
                    "query": query,
                    "url": f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en",
                }
            )
        return out

    def _symbol_specific_query_terms(self, symbol_key: str, company_name: str | None = None) -> List[str]:
        ticker = (symbol_key.split(":", 1)[1] if ":" in symbol_key else symbol_key).upper()
        if ticker in self.STOCK_QUERY_VOCAB_OVERRIDES:
            return list(self.STOCK_QUERY_VOCAB_OVERRIDES[ticker])
        name_l = str(company_name or "").lower()
        if any(tok in name_l for tok in ("semiconductor", "micro devices", "nvidia", "chip", "semi")):
            return ["datacenter", "wafer supply", "foundry", "ai demand"]
        if any(tok in name_l for tok in ("biotech", "pharma", "therapeutics")):
            return ["trial", "fda", "guidance", "pipeline"]
        if any(tok in name_l for tok in ("bank", "financial", "capital", "holdings")):
            return ["net interest margin", "credit quality", "deposit flows", "regulatory capital"]
        return []

    def _lookup_company_name(self, symbol_key: str) -> str | None:
        db = Database()
        try:
            if not db.connect():
                return None
            rows = db.execute_query(
                "SELECT name FROM instruments WHERE symbol_key = %s LIMIT 1",
                (symbol_key.upper(),),
            ) or []
            if rows and rows[0] and rows[0][0]:
                name = str(rows[0][0]).strip()
                if name and not name.lower().startswith(("index ", "future ", "sector etf ")):
                    return name
        except Exception:
            return None
        finally:
            db.disconnect()
        return None

    def _load_news_usage_details(self, symbol_key: str, asof: datetime.date) -> Dict[str, Any]:
        today = datetime.date.today()
        start_date = asof - datetime.timedelta(days=self.STOCK_AI_NEWS_LOOKBACK_DAYS)
        # UI visibility: when asof is previous trading day, newly fetched headlines may be dated today (UTC/local skew).
        display_end_date = max(asof, today)
        db = Database()
        try:
            if not db.connect():
                return {"window_days": 7, "docs_used_count": 0}
            rows = db.execute_query(
                """
                SELECT symbol_key, source_type, source_ref, published_at, title
                FROM rag_documents
                WHERE symbol_key = %s
                  AND (published_at IS NULL OR DATE(published_at) BETWEEN %s AND %s)
                ORDER BY published_at DESC NULLS LAST
                LIMIT %s
                """,
                (symbol_key.upper(), start_date, display_end_date, int(max(self.STOCK_AI_NEWS_DOC_LIMIT * 4, self.STOCK_AI_NEWS_DOC_LIMIT))),
            ) or []
            items: List[Dict[str, Any]] = []
            pit_count = 0
            by_source: Dict[str, int] = {}
            by_symbol: Dict[str, int] = {}
            for r in rows:
                sym = str(r[0] or "")
                src_type = str(r[1] or "")
                by_source[src_type] = by_source.get(src_type, 0) + 1
                by_symbol[sym] = by_symbol.get(sym, 0) + 1
                pub_dt = self._parse_ts(r[3])
                if pub_dt is None or pub_dt.date() <= asof:
                    pit_count += 1
                items.append(
                    {
                        "symbol_key": sym,
                        "source_type": src_type,
                        "source_ref": self._safe_text(r[2], 300) or "",
                        "published_at": (pub_dt.isoformat() if pub_dt else None),
                        "title": self._safe_text(r[4], 300) or "",
                    }
                )
            items = self._dedupe_news_items(items, limit=int(self.STOCK_AI_NEWS_DOC_LIMIT))
            by_source = {}
            by_symbol = {}
            pit_count = 0
            for item in items:
                sym = str(item.get("symbol_key") or "")
                src_type = str(item.get("source_type") or "")
                by_source[src_type] = by_source.get(src_type, 0) + 1
                by_symbol[sym] = by_symbol.get(sym, 0) + 1
                pub_dt = self._parse_ts(item.get("published_at"))
                if pub_dt is None or pub_dt.date() <= asof:
                    pit_count += 1
            return {
                "window_days": int(self.STOCK_AI_NEWS_LOOKBACK_DAYS),
                "asof_date": asof.isoformat(),
                "display_window_end_date": display_end_date.isoformat(),
                "docs_used_count": len(items),
                "docs_for_asof_count": pit_count,
                "docs_by_symbol": by_symbol,
                "docs_by_source_type": by_source,
                "docs_sample": items[:25],
                "doc_limit": int(self.STOCK_AI_NEWS_DOC_LIMIT),
            }
        except Exception as e:
            return {
                "window_days": int(self.STOCK_AI_NEWS_LOOKBACK_DAYS),
                "docs_used_count": 0,
                "doc_limit": int(self.STOCK_AI_NEWS_DOC_LIMIT),
                "error": str(e),
            }
        finally:
            db.disconnect()

    def _normalize_news_text(self, value: Any) -> str:
        txt = str(value or "").lower().strip()
        txt = re.sub(r"https?://\S+", " ", txt)
        txt = re.sub(r"[^a-z0-9]+", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def _dedupe_news_items(self, items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        out: List[Dict[str, Any]] = []
        for item in items:
            title_key = self._normalize_news_text(item.get("title"))
            ref_key = self._normalize_news_text(item.get("source_ref"))
            key = f"title:{title_key}" if len(title_key) >= 12 else f"ref:{ref_key}"
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= int(limit):
                break
        return out

    def _source_domain_from_ref_or_title(self, source_ref: str, title: str) -> str:
        ref = str(source_ref or "").strip()
        if ref:
            try:
                parsed = urllib.parse.urlparse(ref)
                host = (parsed.netloc or "").lower()
                if host.startswith("www."):
                    host = host[4:]
                if host:
                    return host
            except Exception:
                pass
        ttl = str(title or "")
        if " - " in ttl:
            suffix = ttl.rsplit(" - ", 1)[-1].strip().lower()
            suffix = re.sub(r"[^a-z0-9. ]+", "", suffix).replace(" ", "")
            return suffix
        return ""

    def _fetch_missing_data(self, symbol_key: str) -> None:
        """銘柄のデータがDBに無い場合、yfinanceからデータを取得する。"""
        import subprocess

        print(
            f"[AiPredictionService] _fetch_missing_data called for {symbol_key}",
            flush=True,
        )

        # regime_symbols も一緒に取得（QQQ, SPY は推論に必要）
        symbols_to_fetch = [symbol_key, "US:QQQ", "US:SPY", "US:^SOX", "US:SMH"]
        symbols_str = ",".join(symbols_to_fetch)
        print(f"[AiPredictionService] symbols_to_fetch={symbols_to_fetch}", flush=True)

        script_path = str(
            Path(__file__).resolve().parents[2] / "scripts" / "refresh_market_data.py"
        )

        cmd = [
            sys.executable,
            script_path,
            "--symbols", symbols_str,
            "--source", "yfinance",
        ]

        print(f"[AiPredictionService] script_path={script_path}", flush=True)
        print(f"[AiPredictionService] cmd={cmd}", flush=True)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                print(
                    f"[AiPredictionService] Data fetch failed for {symbol_key}: {proc.stderr[-300:]}",
                    flush=True,
                )
                logger.warning(
                    "[AiPredictionService] Data fetch failed for %s: %s",
                    symbol_key,
                    proc.stderr[-300:],
                )
            else:
                print(f"[AiPredictionService] Data fetched for {symbol_key}", flush=True)
                logger.info("[AiPredictionService] Data fetched for %s", symbol_key)

            db = Database()
            if db.connect():
                try:
                    row = db.execute_query(
                        "SELECT COUNT(*) FROM price_daily WHERE symbol_key = %s",
                        (symbol_key,),
                    )
                    count = row[0][0] if row else 0
                    print(
                        f"[AiPredictionService] After fetch: {symbol_key} has {count} price rows",
                        flush=True,
                    )
                finally:
                    db.disconnect()
        except subprocess.TimeoutExpired:
            print(f"[AiPredictionService] Data fetch timeout for {symbol_key}", flush=True)
            logger.warning("[AiPredictionService] Data fetch timeout for %s", symbol_key)
        except Exception as e:
            print(f"[AiPredictionService] Data fetch error for {symbol_key}: {e}", flush=True)
            logger.warning("[AiPredictionService] Data fetch error for %s: %s", symbol_key, e)
