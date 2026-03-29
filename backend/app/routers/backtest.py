from datetime import date
from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from app.services.backtest_service import BacktestService

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("")
def run_backtest(
    symbol: str = Query(..., description="Symbol key e.g. US:AAPL, JP:7203"),
    strategy: str = Query("ai_signals", enum=["ai_signals", "ma_cross", "breakout_52w"]),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    initial_capital: float = Query(10000, ge=100),
):
    try:
        sd = date.fromisoformat(start_date) if start_date else None
        ed = date.fromisoformat(end_date) if end_date else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")

    result = BacktestService().run_backtest(
        symbol_key=symbol.strip().upper(),
        strategy=strategy,
        start_date=sd,
        end_date=ed,
        initial_capital=initial_capital,
    )
    if "error" in result and "symbol_key" not in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
