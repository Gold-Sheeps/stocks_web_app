from fastapi import APIRouter, Query
from app.services.sector_detail_service import SectorDetailService

router = APIRouter(prefix="/sector", tags=["sector"])


@router.get("/{sector_symbol}")
def get_sector_detail(sector_symbol: str):
    """Get sector detail data"""
    service = SectorDetailService()
    return service.get_sector_detail(sector_symbol)


@router.get("/US/{sector_name}/top")
def get_sector_top_stocks(sector_name: str, limit: int = Query(default=10, ge=1, le=50)):
    """Get Top N stocks for a sector by leader/laggard style ranking."""
    service = SectorDetailService()
    return service.get_sector_top_stocks(sector_name=sector_name, limit=limit)
