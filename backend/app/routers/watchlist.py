
from fastapi import APIRouter, HTTPException
from typing import List
from app.services.watchlist_service import WatchlistService
from app.models.schemas import WatchlistRequest, WatchlistItem

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

@router.get("", response_model=List[WatchlistItem])
def get_watchlist():
    """ウォッチリストを取得"""
    print("DEBUG: Router get_watchlist called", flush=True)
    service = WatchlistService()
    try:
        return service.get_watchlist()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("")
def add_to_watchlist(req: WatchlistRequest):
    """ウォッチリストに追加または更新"""
    service = WatchlistService()
    try:
        success = service.add_to_watchlist(req)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to add to watchlist")
        return {"status": "success", "message": f"Added {req.symbol} to watchlist"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{symbol}")
def update_watchlist(symbol: str, req: WatchlistRequest):
    """ウォッチリスト項目を更新"""
    service = WatchlistService()
    try:
        req.symbol = symbol # Ensure symbol matches path
        success = service.update_watchlist_item(req)
        if not success:
            raise HTTPException(status_code=404, detail="Watchlist item not found or failed to update")
        return {"status": "success", "message": f"Updated {symbol}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{symbol}")
def delete_from_watchlist(symbol: str):
    """ウォッチリストから削除"""
    service = WatchlistService()
    try:
        success = service.delete_from_watchlist(symbol)
        if not success:
            raise HTTPException(status_code=404, detail="Watchlist item not found")
        return {"status": "success", "message": f"Deleted {symbol} from watchlist"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
