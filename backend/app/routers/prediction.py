from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.prediction_service import PredictionService
from app.services.vix_prediction_service import VixPredictionService
from app.services.ai_prediction_service import AiPredictionService

router = APIRouter(prefix="/prediction", tags=["prediction"])


class ForecastRequest(BaseModel):
    ticker: str
    as_of_date: str
    flat_band_pct: float = 2.0
    horizon_trading_days: int = 15
    calibration: str = Field(default="sigmoid", pattern="^(sigmoid|isotonic)$")
    sample_csv_path: Optional[str] = None
    include_band_sweep: bool = True


class VixPredictionRunRequest(BaseModel):
    asof_date: Optional[str] = None
    horizon_days: int = Field(default=5, ge=1, le=10)
    backtest_years: int = Field(default=3, ge=1, le=10)
    refresh_news: bool = True


class VixBackfillActualsRequest(BaseModel):
    run_limit: int = Field(default=500, ge=1, le=5000)


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


@router.post("/vix/run")
def run_vix_prediction(req: VixPredictionRunRequest):
    try:
        svc = VixPredictionService()
        return svc.run_and_save(
            asof_date=req.asof_date,
            horizon_days=req.horizon_days,
            backtest_years=req.backtest_years,
            refresh_news=req.refresh_news,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/vix/latest")
def get_latest_vix_prediction():
    try:
        svc = VixPredictionService()
        payload = svc.get_latest_run()
        return payload or {"status": "empty", "message": "No VIX prediction runs found"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/vix/runs")
def list_vix_prediction_runs(limit: int = 20):
    try:
        svc = VixPredictionService()
        return {"items": svc.list_runs(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/vix/run/{run_id}")
def get_vix_prediction_run(run_id: int):
    try:
        svc = VixPredictionService()
        payload = svc.get_run(run_id)
        return payload or {"status": "empty", "message": f"Run not found: {run_id}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/vix/backfill-actuals")
def backfill_vix_prediction_actuals(req: VixBackfillActualsRequest):
    try:
        svc = VixPredictionService()
        return svc.backfill_actuals(run_limit=req.run_limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/stock-ai/error-ranking")
def get_stock_ai_error_ranking(limit: int = 30, min_realized: int = 3):
    try:
        svc = AiPredictionService()
        return svc.get_symbol_error_ranking(limit=limit, min_realized=min_realized)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/stock-ai/source-report")
def get_stock_ai_source_report(
    symbol_key: Optional[str] = None,
    limit: int = 20,
    min_obs: int = 3,
    days_back: int = 365 * 3,
):
    try:
        svc = AiPredictionService()
        return svc.get_news_source_performance_report(
            symbol_key=symbol_key,
            limit=limit,
            min_obs=min_obs,
            days_back=days_back,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
