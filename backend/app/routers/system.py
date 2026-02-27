
from fastapi import APIRouter, Header, HTTPException, Body, BackgroundTasks
from typing import List, Optional
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel
import sys
import os
import time
import threading
import uuid
import json

from app.services.data_service import DataService
from app.services.freshness_service import FreshnessService
from app.db.database import Database

router = APIRouter(prefix="/system", tags=["System"])
_UPDATE_JOBS: dict[str, dict] = {}
_UPDATE_JOBS_LOCK = threading.Lock()

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


def _plan_update_steps(targets: List[str]) -> list[str]:
    selected = set(targets or [])
    steps = ["main_refresh"]
    if "Sector" in selected:
        steps.append("sector_rotation")
    if "Fundamentals" in selected:
        steps.append("fetch_fundamentals_watchlist_portfolio")
    if "MarketEnvironment" in selected:
        steps.append("fetch_market_environment")
    if ("AI" in selected) or ("AI Prediction" in selected):
        steps.append("llm_signal_extraction_batch")
        steps.append("ai_prediction_batch")
    return steps


def _read_job_logs(job_id: str) -> list[dict]:
    db = Database()
    db.connect()
    try:
        rows = db.execute_query(
            """
            SELECT status, message, details, created_at
            FROM system_logs
            WHERE job_name = 'Data Update'
              AND details->>'job_id' = %s
            ORDER BY created_at ASC
            """,
            (job_id,),
        )
    finally:
        db.disconnect()
    logs = []
    for r in rows or []:
        details = r[2]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}
        logs.append(
            {
                "status": str(r[0] or "").upper(),
                "message": r[1],
                "details": details if isinstance(details, dict) else {},
                "created_at": r[3],
            }
        )
    return logs


def _compute_job_progress(job_id: str) -> dict:
    with _UPDATE_JOBS_LOCK:
        meta = dict(_UPDATE_JOBS.get(job_id) or {})

    if not meta:
        return {"status": "not_found", "job_id": job_id}

    planned_steps: list[str] = list(meta.get("planned_steps") or [])
    step_status = {s: "pending" for s in planned_steps}
    step_messages = {s: None for s in planned_steps}
    logs = _read_job_logs(job_id)

    terminal = None
    for e in logs:
        details = e.get("details") or {}
        step = details.get("step")
        status = e.get("status")
        if step in step_status:
            if status == "RUNNING":
                step_status[step] = "running"
            elif status == "SUCCESS":
                step_status[step] = "success"
            elif status in ("FAILED", "ERROR"):
                step_status[step] = "failed"
            step_messages[step] = e.get("message")
        elif step is None and status in ("SUCCESS", "FAILED", "ERROR"):
            terminal = status

    done_count = sum(1 for s in step_status.values() if s in ("success", "failed"))
    total = len(planned_steps) or 1
    progress_pct = int((done_count / total) * 100)
    running_steps = [k for k, v in step_status.items() if v == "running"]
    current_step = running_steps[-1] if running_steps else None

    job_status = meta.get("status", "running")
    if terminal in ("SUCCESS", "FAILED", "ERROR"):
        job_status = "completed" if terminal == "SUCCESS" else "failed"
        progress_pct = 100

    if job_status in ("completed", "failed"):
        progress_pct = 100

    return {
        "status": job_status,
        "job_id": job_id,
        "range_days": meta.get("range_days"),
        "targets": meta.get("targets", []),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "current_step": current_step,
        "progress_pct": progress_pct,
        "steps": [
            {"step": s, "status": step_status[s], "message": step_messages[s]}
            for s in planned_steps
        ],
        "result": meta.get("result"),
    }


def _run_update_job(job_id: str, range_days: int, targets: List[str]) -> None:
    service = DataService()
    try:
        result = service.update_all_data(range_days, targets, job_id=job_id)
        with _UPDATE_JOBS_LOCK:
            entry = _UPDATE_JOBS.get(job_id, {})
            entry["status"] = "completed" if result.get("status") == "success" else "failed"
            entry["finished_at"] = datetime.utcnow().isoformat() + "Z"
            entry["result"] = result
            _UPDATE_JOBS[job_id] = entry
    except Exception as e:
        with _UPDATE_JOBS_LOCK:
            entry = _UPDATE_JOBS.get(job_id, {})
            entry["status"] = "failed"
            entry["finished_at"] = datetime.utcnow().isoformat() + "Z"
            entry["result"] = {"status": "error", "message": str(e)}
            _UPDATE_JOBS[job_id] = entry

@router.post("/update-data")
def update_data(request: DataUpdateRequest):
    """データ更新を実行"""
    service = DataService()
    try:
        result = service.update_all_data(request.range_days, request.targets)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-data/start")
def start_update_data(request: DataUpdateRequest, background_tasks: BackgroundTasks):
    """Start Data Update as background job and return job_id immediately."""
    with _UPDATE_JOBS_LOCK:
        running = [
            j for j in _UPDATE_JOBS.values()
            if j.get("status") == "running"
        ]
        if running:
            return {
                "status": "already_running",
                "message": "Another Data Update job is running",
                "job_id": running[-1].get("job_id"),
            }

    job_id = f"update_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    planned_steps = _plan_update_steps(request.targets)
    with _UPDATE_JOBS_LOCK:
        _UPDATE_JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "finished_at": None,
            "range_days": int(request.range_days),
            "targets": list(request.targets or []),
            "planned_steps": planned_steps,
            "result": None,
        }

    background_tasks.add_task(_run_update_job, job_id, int(request.range_days), list(request.targets or []))
    return {
        "status": "started",
        "job_id": job_id,
        "planned_steps": planned_steps,
    }


@router.get("/update-data/status/{job_id}")
def get_update_data_status(job_id: str):
    """Get current progress for a Data Update job."""
    return _compute_job_progress(job_id)

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


def _safe_scalar(db: Database, query: str):
    """Run a scalar query and return the first column, swallowing errors."""
    try:
        rows = db.execute_query(query)
        if rows and rows[0]:
            return rows[0][0]
    except Exception:
        return None
    return None


def _to_iso(value):
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


@router.get("/data-freshness")
def get_data_freshness():
    """Return latest dates/timestamps for key datasets used by the app."""
    db = Database()
    db.connect()
    try:
        datasets = {
            "price_daily": {
                "latest_trading_date": _to_iso(_safe_scalar(db, "SELECT MAX(trading_date) FROM price_daily")),
                "latest_updated_at": _to_iso(_safe_scalar(db, "SELECT MAX(updated_at) FROM price_daily")),
            },
            "indicator_daily": {
                "latest_trading_date": _to_iso(_safe_scalar(db, "SELECT MAX(trading_date) FROM indicator_daily")),
                "latest_updated_at": _to_iso(_safe_scalar(db, "SELECT MAX(updated_at) FROM indicator_daily")),
            },
            "rs_ratings": {
                "latest_updated_at": _to_iso(_safe_scalar(db, "SELECT MAX(updated_at) FROM rs_ratings")),
            },
            "sector_rotation": {
                "latest_trading_date": _to_iso(_safe_scalar(db, "SELECT MAX(trading_date) FROM sector_rotation")),
                "latest_updated_at": _to_iso(_safe_scalar(db, "SELECT MAX(updated_at) FROM sector_rotation")),
            },
            "market_environment": {
                "latest_trading_date": _to_iso(
                    _safe_scalar(db, "SELECT MAX(trading_date) FROM market_environment")
                ) or _to_iso(
                    _safe_scalar(db, "SELECT MAX(check_date) FROM market_environment")
                ),
                "latest_updated_at": _to_iso(_safe_scalar(db, "SELECT MAX(updated_at) FROM market_environment")),
            },
            "fundamentals": {
                "latest_updated_at": _to_iso(_safe_scalar(db, "SELECT MAX(updated_at) FROM fundamentals")),
            },
            "ai_predictions": {
                "latest_asof": _to_iso(_safe_scalar(db, "SELECT MAX(asof) FROM ai_predictions")),
                "latest_updated_at": _to_iso(_safe_scalar(db, "SELECT MAX(updated_at) FROM ai_predictions")),
            },
            "system_logs": {
                "last_data_update_success_at": _to_iso(
                    _safe_scalar(
                        db,
                        """
                        SELECT MAX(created_at)
                        FROM system_logs
                        WHERE job_name = 'Data Update' AND UPPER(status) = 'SUCCESS'
                        """,
                    )
                ),
                "last_ai_batch_success_at": _to_iso(
                    _safe_scalar(
                        db,
                        """
                        SELECT MAX(created_at)
                        FROM system_logs
                        WHERE job_name = 'ai_prediction_batch' AND UPPER(status) = 'SUCCESS'
                        """,
                    )
                ),
            },
        }
    finally:
        db.disconnect()

    freshness = None
    try:
        freshness = FreshnessService().price_freshness_for_active_instruments(
            scope="all_active_instruments"
        ).model_dump(mode="json")
    except Exception as e:
        freshness = {"error": str(e)}

    return {
        "server_time": datetime.utcnow().isoformat() + "Z",
        "datasets": datasets,
        "price_freshness_active": freshness,
    }


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
