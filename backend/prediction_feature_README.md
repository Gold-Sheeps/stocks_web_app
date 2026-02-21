# Prediction Feature (Isolated Add-on)

This feature is added as new endpoints and does not replace existing APIs.

## Install (minimal new deps)

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-ml.txt
```

Added dependencies (only for this feature):
- `scikit-learn` (calibration + metrics)
- `xgboost` (3-class classification + regression)

## Endpoints

- `POST /api/v1/prediction/forecast`
- `POST /api/v1/prediction/backtest`

Request body:

```json
{
  "ticker": "NVDA",
  "as_of_date": "2026-02-16",
  "flat_band_pct": 2.0,
  "horizon_trading_days": 15,
  "calibration": "sigmoid"
}
```

Optional:
- `sample_csv_path` (offline fallback CSV path)

## Smoke test (NVDA)

```powershell
.\.venv\Scripts\python.exe backend\scripts\smoke_prediction_nvda.py
```

Offline fallback sample:
- `backend/ml_predictor_data/nvda_sample.csv`

## Backtest CLI example (DB-only)

```powershell
python backend/scripts/run_backtest_nvda.py --asof 2026-02-16 --start 2019-01-01 --end 2026-02-16 --horizon 15 --flat_band 2.0 --calibration sigmoid --db-check --fast --prob-mode meta --temp-scale on
```
