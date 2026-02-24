from fastapi import APIRouter

from app.services.market_environment_service import MarketEnvironmentService

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/environment")
async def get_market_environment():
    service = MarketEnvironmentService()
    return service.get_dashboard()

