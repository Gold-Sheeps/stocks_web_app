"""Override API Router (Phase 1)."""
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.services.override_service import OverrideService

router = APIRouter(prefix="/override", tags=["override"])


@router.get("/fields")
def get_fields():
    """List available override fields."""
    svc = OverrideService()
    return svc.get_fields()


@router.get("/symbols")
def get_symbols():
    """List symbols from instruments."""
    svc = OverrideService()
    return svc.get_symbols()


@router.get("/list")
def list_overrides(
    scope: Optional[str] = None,
    scope_key: Optional[str] = None,
    category: Optional[str] = None,
    enabled: Optional[bool] = None,
):
    """List overrides with optional filters."""
    svc = OverrideService()
    items = svc.list_overrides(scope, scope_key, category, enabled)
    return {"overrides": items, "total": len(items)}


@router.post("")
def create_override(data: dict):
    """Create a new override."""
    required = ["scope", "scope_key", "category", "field_name", "override_value", "reason"]
    missing = [k for k in required if k not in data]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required fields: {missing}")
    svc = OverrideService()
    try:
        return svc.create_override(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
def preview_override(data: dict):
    """Preview override diff."""
    svc = OverrideService()
    return svc.preview(data)


@router.post("/{override_id}/activate")
def activate(override_id: int):
    """Activate an override."""
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
    """Deactivate an override."""
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
    """Rollback an override (deactivate + audit)."""
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
    """Get recent audit logs."""
    svc = OverrideService()
    return svc.get_audit_log(limit)
