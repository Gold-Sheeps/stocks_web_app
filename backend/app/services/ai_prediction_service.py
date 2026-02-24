"""AI prediction service: DB lookup + on-demand inference."""
from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path
from typing import Any, Dict

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

logger = logging.getLogger(__name__)


class AiPredictionService:
    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_latest(self, symbol_key: str) -> Dict[str, Any]:
        """DB から最新の推論結果を返す。なければ has_prediction=False。"""
        symbol_key = symbol_key.upper()
        try:
            db = Database()
            if not db.connect():
                return {"has_prediction": False, "symbol_key": symbol_key, "error": "DB connection failed"}
            try:
                rows = db.execute_query(SELECT_LATEST_SQL, (symbol_key,))
                if not rows:
                    return {"has_prediction": False, "symbol_key": symbol_key}
                row = rows[0]
                return {
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
            finally:
                db.disconnect()
        except Exception as e:
            return {"has_prediction": False, "symbol_key": symbol_key, "error": str(e)}

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

            artifact_path = _latest_artifact_path()
            artifact = _load_artifact(artifact_path)
            cal_method = "none"

            svc = PredictionService()

            max_retries = 2
            for attempt in range(max_retries):
                try:
                    x_df, _ = _build_inference_row(svc, symbol_key, asof, artifact)
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
                    }
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
