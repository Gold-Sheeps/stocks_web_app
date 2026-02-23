
from fastapi import APIRouter, Header, HTTPException, Body
from typing import List, Optional
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel
import sys
import os

from app.services.data_service import DataService
from app.db.database import Database

router = APIRouter(prefix="/system", tags=["System"])

class DataUpdateRequest(BaseModel):
    range_days: int = 14
    targets: List[str] # Indices, FX, Metal, Crypto, Sector, Stocks
    
    class Config:
        json_schema_extra = {
            "example": {
                "range_days": 14,
                "targets": ["Indices", "FX"]
            }
        }

@router.post("/update-data")
def update_data(request: DataUpdateRequest):
    """データ更新を実行"""
    service = DataService()
    try:
        result = service.update_all_data(request.range_days, request.targets)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/logs")
def get_system_logs(limit: int = 50):
    """システムログを取得"""
    db = Database()
    db.connect()
    try:
        query = """
            SELECT log_id, job_name, status, message, details, created_at 
            FROM system_logs 
            ORDER BY created_at DESC 
            LIMIT %s
        """
        rows = db.execute_query(query, (limit,))
        logs = []
        if rows:
            for r in rows:
                logs.append({
                    "log_id": r[0],
                    "job_name": r[1],
                    "status": r[2],
                    "message": r[3],
                    "details": r[4], # JSONB -> dict locally
                    "created_at": r[5]
                })
        return logs
    finally:
        db.disconnect()

def _table_exists(db, table_name: str) -> bool:
    """テーブルが存在するかチェック"""
    try:
        rows = db.execute_query(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table_name,)
        )
        return bool(rows)
    except Exception:
        return False


@router.get("/ai-prediction/status")
def get_ai_prediction_status():
    """AI推論バッチの実行可否を判定。対象銘柄のデータ鮮度をチェック。"""
    from datetime import date, timedelta
    db = Database()
    db.connect()
    try:
        today = date.today()
        row = db.execute_query(
            "SELECT MAX(trading_date) FROM price_daily"
        )
        latest_date = row[0][0] if row and row[0][0] else None

        if latest_date is None:
            return {
                "can_run": False,
                "reason": "No price data in DB",
                "latest_data_date": None,
                "today": today.isoformat(),
            }

        day_diff = (today - latest_date).days
        can_run = day_diff <= 3  # 3日以内（金曜→月曜のケースも許容）

        ticker_count_row = db.execute_query("""
            SELECT COUNT(DISTINCT symbol_key) FROM (
                SELECT DISTINCT symbol_key FROM watchlist
                UNION
                SELECT DISTINCT symbol_key FROM price_daily
                WHERE trading_date >= CURRENT_DATE - INTERVAL '30 days'
                LIMIT 500
            ) t
        """)
        estimated_tickers = ticker_count_row[0][0] if ticker_count_row and ticker_count_row[0][0] else 0

        last_run_row = db.execute_query(
            "SELECT MAX(asof) FROM ai_predictions"
        ) if _table_exists(db, 'ai_predictions') else None
        last_run_date = last_run_row[0][0] if last_run_row and last_run_row[0] and last_run_row[0][0] else None

        return {
            "can_run": can_run,
            "reason": None if can_run else f"Price data is {day_diff} days old (max 3 allowed). Update market data first.",
            "latest_data_date": latest_date.isoformat() if latest_date else None,
            "today": today.isoformat(),
            "day_diff": day_diff,
            "estimated_tickers": estimated_tickers,
            "last_run_date": last_run_date.isoformat() if last_run_date else None,
        }
    finally:
        db.disconnect()


class AiPredictionRunRequest(BaseModel):
    max_tickers: int = 500

    class Config:
        json_schema_extra = {
            "example": {"max_tickers": 500}
        }


@router.post("/ai-prediction/run")
def run_ai_prediction_batch(request: AiPredictionRunRequest):
    """AI推論バッチを実行。対象銘柄のデータ鮮度をチェックしてから実行。"""
    import subprocess
    from datetime import date, timedelta

    db = Database()
    db.connect()
    try:
        today = date.today()
        row = db.execute_query("SELECT MAX(trading_date) FROM price_daily")
        latest_date = row[0][0] if row and row[0][0] else None
        if latest_date is None:
            return {"status": "error", "message": "No price data in DB"}
        day_diff = (today - latest_date).days
        if day_diff > 3:
            return {
                "status": "error",
                "message": f"Price data is {day_diff} days old. Update market data first."
            }
        asof_date = latest_date.isoformat()
    finally:
        db.disconnect()

    script_path = str(Path(__file__).resolve().parents[2] / "scripts" / "run_daily_predictions.py")
    cmd = [
        sys.executable,
        script_path,
        "--asof", asof_date,
        "--max-tickers", str(request.max_tickers),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)

        db2 = Database()
        db2.connect()
        try:
            status = "success" if proc.returncode == 0 else "failed"
            message = f"AI Prediction batch: {request.max_tickers} tickers, asof={asof_date}"
            details = {
                "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
                "stderr_tail": proc.stderr[-500:] if proc.stderr else "",
                "returncode": proc.returncode,
            }
            import json as _json
            db2.execute_query(
                """INSERT INTO system_logs (job_name, status, message, details)
                   VALUES (%s, %s, %s, %s::jsonb)""",
                ("ai_prediction_batch", status, message, _json.dumps(details, ensure_ascii=False)),
            )
        except Exception:
            pass
        finally:
            db2.disconnect()

        if proc.returncode == 0:
            return {
                "status": "success",
                "message": f"AI Prediction batch completed for asof={asof_date}",
                "asof": asof_date,
                "max_tickers": request.max_tickers,
                "output": proc.stdout[-1000:] if proc.stdout else "",
            }
        else:
            return {
                "status": "error",
                "message": f"Batch failed (exit={proc.returncode})",
                "stderr": proc.stderr[-500:] if proc.stderr else "",
            }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Batch timed out after 2 hours"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


from fastapi import Request
@router.get("/diagnostics/screener")
def get_screener_diagnostics(request: Request):
    """スクリーナーの診断情報を取得"""
    from app.services.screener_service import ScreenerService
    service = ScreenerService()
    return service.get_diagnostics(dict(request.query_params))
