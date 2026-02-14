"""
Override API Router (Phase 1)
パターン: routers/watchlist.py, routers/stock_detail.py と同じ構成。
prefix=/override, tags=["override"]
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from app.services.override_service import OverrideService

router = APIRouter(prefix="/override", tags=["override"])


@router.get("/fields")
def get_fields():
    """Override 可能フィールド定義一覧"""
    svc = OverrideService()
    return svc.get_fields()


@router.get("/symbols")
def get_symbols():
    """instruments テーブルから銘柄一覧を取得"""
    svc = OverrideService()
    return svc.get_symbols()


@router.get("/list")
def list_overrides(
    scope: Optional[str] = None,
    scope_key: Optional[str] = None,
    category: Optional[str] = None,
    enabled: Optional[bool] = None,
):
    """Override 一覧取得（フィルタ可）"""
    svc = OverrideService()
    items = svc.list_overrides(scope, scope_key, category, enabled)
    return {"overrides": items, "total": len(items)}


@router.post("")
def create_override(data: dict):
    """Override を新規登録"""
    required = ["scope", "scope_key", "category", "field_name", "override_value", "reason"]
    missing = [k for k in required if k not in data]
    if missing:
        raise HTTPException(status_code=422, detail=f"必須フィールド不足: {missing}")
    svc = OverrideService()
    try:
        return svc.create_override(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
def preview_override(data: dict):
    """保存せずにプレビュー（Phase 1: diff のみ）"""
    svc = OverrideService()
    return svc.preview(data)


@router.post("/{override_id}/activate")
def activate(override_id: int):
    """Override を有効化"""
    svc = OverrideService()
    try:
        result = svc.set_enabled(override_id, True)
        if "error" in result:
            raise HTTPException(status_code=404, detail="Override not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{override_id}/deactivate")
def deactivate(override_id: int):
    """Override を無効化"""
    svc = OverrideService()
    try:
        result = svc.set_enabled(override_id, False)
        if "error" in result:
            raise HTTPException(status_code=404, detail="Override not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{override_id}/rollback")
def rollback(override_id: int):
    """Override をロールバック（無効化 + audit 記録）"""
    svc = OverrideService()
    try:
        result = svc.rollback(override_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail="Override not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit")
def get_audit(limit: int = 20):
    """監査ログ取得"""
    svc = OverrideService()
    return svc.get_audit_log(limit)
