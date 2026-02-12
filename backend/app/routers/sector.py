from fastapi import APIRouter
from app.services.sector_detail_service import SectorDetailService

router = APIRouter(prefix="/sector", tags=["sector"])


@router.get("/{sector_symbol}")
def get_sector_detail(sector_symbol: str):
    """Get sector detail data"""
    service = SectorDetailService()
    return service.get_sector_detail(sector_symbol)
