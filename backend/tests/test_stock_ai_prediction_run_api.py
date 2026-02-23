import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.routers import stock_detail


class TestStockAiPredictionRunApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = FastAPI()
        app.include_router(stock_detail.router, prefix="/api/v1")
        cls.client = TestClient(app)

    def test_run_ai_prediction_price_data_missing_returns_non_200(self) -> None:
        err = RuntimeError("Price data missing in DB: US:CRWV for asof=2026-02-21")
        with patch.object(stock_detail.AiPredictionService, "run_on_demand", side_effect=err):
            resp = self.client.post("/api/v1/stock/US:CRWV/ai-prediction/run")

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertIn("error", body)
        self.assertIn("message", body)
        self.assertIn("symbol", body)
        self.assertEqual(body["error"], "price_data_missing")
        self.assertEqual(body["symbol"], "US:CRWV")
        self.assertIn("データ欠損", body["message"])

    def test_run_ai_prediction_success_keeps_200_and_response_shape(self) -> None:
        payload = {
            "has_prediction": True,
            "symbol_key": "US:AAPL",
            "asof": "2026-02-22",
            "p_up5": 0.61,
            "threshold_buy": 0.55,
            "decision": "BUY",
            "cal_method": "sigmoid",
            "artifact_path": "artifact.pkl",
            "updated_at": "2026-02-22 10:00:00",
            "db_saved": True,
        }
        with patch.object(stock_detail.AiPredictionService, "run_on_demand", return_value=payload):
            resp = self.client.post("/api/v1/stock/US:AAPL/ai-prediction/run")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["has_prediction"])
        self.assertEqual(body["symbol_key"], "US:AAPL")
        self.assertIn("p_up5", body)
        self.assertIn("decision", body)


if __name__ == "__main__":
    unittest.main()
