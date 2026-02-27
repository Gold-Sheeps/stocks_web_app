import logging
import re
from typing import List, Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.services.stock_detail_service import StockDetailService
from app.services.ai_prediction_service import AiPredictionService
from app.models.schemas import StockDetailResponse

router = APIRouter(prefix="/stock", tags=["stock"])
logger = logging.getLogger(__name__)
_SYMBOL_KEY_PATTERN = re.compile(r"^[A-Z]+:[A-Z0-9._-]+$")


def _build_ai_prediction_error_response(symbol: str, exc: Exception) -> JSONResponse:
    symbol_key = (symbol or "").upper()
    message = str(exc)
    message_lower = message.lower()

    if "price data missing in db" in message_lower:
        # Missing history for the requested symbol is treated as a not-found resource.
        status_code = 404
        error = "price_data_missing"
        user_message = f"価格データ欠損: {message}"
    elif isinstance(exc, ValueError) or (
        "ticker" in message_lower and ("invalid" in message_lower or "format" in message_lower)
    ):
        status_code = 400
        error = "invalid_input"
        user_message = message or "Invalid ticker format"
    else:
        status_code = 500
        error = "internal_error"
        user_message = "AI prediction run failed due to an internal server error"

    logger.exception("AI prediction run failed for symbol=%s", symbol_key)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "message": user_message,
            "symbol": symbol_key or None,
        },
    )


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


@router.get("/{symbol}/price")
async def get_stock_price_history(
    symbol: str,
    limit: Optional[int] = None,
    timeframe: Optional[str] = "1d",
):
    """Fetch full historical OHLCV data for charts"""
    service = StockDetailService()
    return service.get_price_history_all(symbol, limit=limit, timeframe=timeframe)


@router.get("/{symbol}/fundamentals")
async def get_stock_fundamentals(
    symbol: str,
    limit: Optional[int] = None,
):
    """Fetch fundamentals from DB (latest periods)"""
    service = StockDetailService()
    return service.get_fundamentals(symbol, limit=limit)


@router.get("/{symbol}/ratios")
async def get_stock_ratios(symbol: str):
    """Fetch calculated ratios from DB-backed data"""
    service = StockDetailService()
    return service.get_ratios(symbol)


@router.get("/{symbol}/ai-prediction")
async def get_ai_prediction(symbol: str):
    """DB から最新の AI 推論結果を返す。"""
    service = AiPredictionService()
    return service.get_latest(symbol)


@router.post("/{symbol}/ai-prediction/run")
async def run_ai_prediction(symbol: str):
    """オンデマンドで AI 推論を実行し、DB に保存して結果を返す。"""
    symbol_key = symbol.upper()
    if not _SYMBOL_KEY_PATTERN.match(symbol_key):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_input",
                "message": "Invalid ticker format. Expected format like US:AAPL",
                "symbol": symbol_key,
            },
        )

    service = AiPredictionService()
    try:
        return service.run_on_demand(symbol)
    except Exception as exc:
        return _build_ai_prediction_error_response(symbol, exc)


@router.post("/{symbol}/ai-prediction/backfill-actuals")
async def backfill_ai_prediction_actuals(symbol: str, horizon_days: int = 10, limit: int = 500):
    symbol_key = symbol.upper()
    if not _SYMBOL_KEY_PATTERN.match(symbol_key):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_input",
                "message": "Invalid ticker format. Expected format like US:AAPL",
                "symbol": symbol_key,
            },
        )
    service = AiPredictionService()
    result = service.backfill_actuals(symbol_key=symbol_key, horizon_days=horizon_days, limit=limit)
    status = 200 if result.get("ok") else 500
    return JSONResponse(status_code=status, content=result)

