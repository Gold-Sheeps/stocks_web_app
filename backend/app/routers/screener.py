from fastapi import APIRouter, Query
from typing import Optional
from app.services.screener_service import ScreenerService

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("/scan")
async def scan_stocks(
    mode: str = Query(default="all", description="Scan mode: 'all', 'sector', 'theme'"),
    sector: Optional[str] = Query(default=None, description="Sector name (for mode='sector')"),
    limit: int = Query(default=20, description="Number of results to return"),
    offset: int = Query(default=0, description="Offset for pagination"),
    min_rs: int = Query(default=85, description="Minimum RS Rating"),
    min_total_score: int = Query(default=70, description="Minimum Total Score"),
    min_price: Optional[float] = Query(default=None, description="Minimum close price"),
    max_price: Optional[float] = Query(default=None, description="Maximum close price"),
    volume_min: Optional[int] = Query(default=None, description="Minimum volume"),
    rsi_filter: Optional[str] = Query(default=None, description="RSI filter: oversold, neutral, overbought"),
    symbol: Optional[str] = Query(default=None, description="Symbol search query")
):
    """
    銘柄スクリーニング
    """
    service = ScreenerService()
    return service.scan_stocks(
        mode=mode, sector=sector, limit=limit, offset=offset, 
        min_rs=min_rs, min_total_score=min_total_score,
        min_price=min_price, max_price=max_price,
        volume_min=volume_min, rsi_filter=rsi_filter, symbol=symbol
    )
