"""Override API Router (Phase 1 + Phase 2 direct edit)."""
import io
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

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


# ======================================================================
# Phase 2: Direct edit endpoints
# ======================================================================

@router.get("/symbols/search")
def search_symbols(q: str = "", limit: int = 20):
    """Incremental symbol search by symbol_key or name."""
    svc = OverrideService()
    return svc.search_symbols(q, limit)


@router.get("/data/{symbol}")
def get_symbol_data(symbol: str, category: str = "all"):
    """Get all data for a symbol with missing detection.
    category: all / price / indicator / fundamental / ratio
    """
    svc = OverrideService()
    try:
        return svc.get_symbol_data(symbol, category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/data/{symbol}/update")
def update_symbol_data(symbol: str, data: dict):
    """Directly update a row in a source table.
    body: { table, updates: {col: val}, row_id: {trading_date: ...} }
    """
    svc = OverrideService()
    result = svc.direct_update(symbol, data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/market")
def get_market_data():
    """Get Market Environment data with missing detection."""
    svc = OverrideService()
    return svc.get_market_environment_data()


@router.post("/market/update")
def update_market_data(data: dict):
    """Update Market Environment JSONB fields."""
    svc = OverrideService()
    result = svc.update_market_environment(data)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/export/csv/{symbol}")
def export_csv(symbol: str, category: str = "all"):
    """Export symbol data as UTF-8 BOM CSV template."""
    svc = OverrideService()
    try:
        csv_text = svc.export_csv_data(symbol, category)
        filename = f"override_{symbol.replace(':', '_')}.csv"
        return StreamingResponse(
            iter([csv_text.encode("utf-8-sig")]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/csv")
async def import_csv(file: UploadFile = File(...)):
    """Bulk import from CSV template (new_value column applied)."""
    content = await file.read()
    svc = OverrideService()
    try:
        result = svc.import_from_csv(content.decode("utf-8", errors="replace"))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
