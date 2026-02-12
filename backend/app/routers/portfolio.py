from fastapi import APIRouter, HTTPException
from app.services.portfolio_service import PortfolioService
from app.models.schemas import PortfolioDetailResponse, TradeCreate

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/", response_model=PortfolioDetailResponse)
def get_portfolio():
    """ポートフォリオ詳細を取得"""
    service = PortfolioService()
    return service.get_portfolio_detail()

@router.post("/trade")
def add_trade(trade: TradeCreate):
    """取引を追加"""
    service = PortfolioService()
    try:
        service.add_trade(trade)
        return {"message": "Trade added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/trade/{trade_id}")
def delete_trade(trade_id: int):
    """取引を削除"""
    service = PortfolioService()
    if not service.delete_trade(trade_id):
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"message": "Trade deleted successfully"}

@router.get("/symbols")
def get_symbols():
    """銘柄リストを取得（Autocomplete用）"""
    service = PortfolioService()
    # This method needs to be implemented in service
    return service.get_all_symbols()
