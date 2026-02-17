from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.prediction_service import PredictionService

router = APIRouter(prefix="/prediction", tags=["prediction"])


class ForecastRequest(BaseModel):
    ticker: str
    as_of_date: str
    flat_band_pct: float = 2.0
    horizon_trading_days: int = 15
    calibration: str = Field(default="sigmoid", pattern="^(sigmoid|isotonic)$")
    sample_csv_path: Optional[str] = None
    include_band_sweep: bool = True


@router.post("/forecast")
def forecast(req: ForecastRequest):
    try:
        svc = PredictionService()
        return svc.predict(
            ticker=req.ticker,
            as_of_date=req.as_of_date,
            flat_band_pct=req.flat_band_pct,
            horizon_trading_days=req.horizon_trading_days,
            calibration=req.calibration,
            sample_csv_path=req.sample_csv_path,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/backtest")
def backtest(req: ForecastRequest):
    try:
        svc = PredictionService()
        return svc.backtest(
            ticker=req.ticker,
            as_of_date=req.as_of_date,
            flat_band_pct=req.flat_band_pct,
            horizon_trading_days=req.horizon_trading_days,
            calibration=req.calibration,
            sample_csv_path=req.sample_csv_path,
            include_band_sweep=req.include_band_sweep,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
