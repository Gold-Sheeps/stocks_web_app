from fastapi import APIRouter
from app.services.stock_detail_service import StockDetailService
from app.models.schemas import StockDetailResponse

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("/{symbol}", response_model=StockDetailResponse)
async def get_stock_detail(symbol: str):
    """(Deprecated) Legacy Endpoint - Alias for summary"""
    service = StockDetailService()
    return service.get_stock_detail(symbol)


@router.get("/{symbol}/summary", response_model=StockDetailResponse)
async def get_stock_summary(symbol: str):
    """Phase 5-1: Lightweight Summary (Use this for initial load)"""
    service = StockDetailService()
    return service.get_stock_summary(symbol)


@router.get("/{symbol}/signals")
async def get_stock_signals(symbol: str):
    """(Deprecated in Phase 5) Signals are now in summary"""
    service = StockDetailService()
    return service.get_signals(symbol)
