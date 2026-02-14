
from fastapi import APIRouter, Header, HTTPException, Body
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
import sys
import os

from app.services.data_service import DataService
from app.db.database import Database

router = APIRouter(prefix="/system", tags=["System"])

class DataUpdateRequest(BaseModel):
    range_days: int = 14
    targets: List[str] # Indices, FX, Metal, Sector, Stocks
    
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

from fastapi import Request
@router.get("/diagnostics/screener")
def get_screener_diagnostics(request: Request):
    """スクリーナーの診断情報を取得"""
    from app.services.screener_service import ScreenerService
    service = ScreenerService()
    return service.get_diagnostics(dict(request.query_params))
