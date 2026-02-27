import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.ai_prediction_service import AiPredictionService


class _FakeDb:
    def connect(self):
        return True

    def disconnect(self):
        return True

    def execute_query(self, query, params=None):
        q = str(query or "").lower()
        if "from market_environment" in q:
            return []
        if "from ai_prediction_run_meta" in q:
            return []
        return []

    def execute_command(self, query, params=None):
        return True


class _DummyPredictionService:
    def build_feature_usage_breakdown(self, model_feature_columns, extra_features=None):
        supplied = sorted(list((extra_features or {}).keys()))
        applied = [c for c in supplied if c in list(model_feature_columns or [])]
        ignored = [c for c in supplied if c not in list(model_feature_columns or [])]
        return {
            "base_feature_count": len(list(model_feature_columns or [])),
            "extra_feature_columns_applied": applied,
            "extra_feature_columns_ignored": ignored,
        }


class TestAiPredictionServiceLlmIntegration(unittest.TestCase):
    def _run_case(self, llm_payload=None, llm_side_effect=None):
        svc = AiPredictionService()
        base_x = pd.DataFrame([{"base_a": 1.0}])
        artifact = {"model_feature_columns": ["base_a", "llm_earnings_tone_score"]}

        def _fake_build_inference_row(_svc, _ticker, _asof, _artifact):
            return base_x.copy(), {"n_feature_mismatch": 0}

        def _fake_predict_with_artifact(_artifact, x_df, calibration_method="none"):
            self.assertIn("base_a", x_df.columns)
            self.assertIn("llm_earnings_tone_score", x_df.columns)
            return {
                "p_up5": 0.62,
                "threshold_buy": 0.55,
                "action": "BUY",
                "cal_method_used": "none",
            }

        patches = [
            patch("app.services.ai_prediction_service.Database", _FakeDb),
            patch.object(AiPredictionService, "_resolve_inference_asof", return_value=date(2026, 2, 24)),
            patch.object(AiPredictionService, "_best_effort_refresh_external_news_and_llm", return_value={"attempted": True, "news_ingest": "warning", "llm_extract": "skipped"}),
            patch.object(AiPredictionService, "_get_latest_market_environment", return_value=None),
            patch("scripts.predict_up5._latest_artifact_path", return_value="dummy_artifact.pkl"),
            patch("scripts.predict_up5._load_artifact", return_value=artifact),
            patch("scripts.predict_up5._build_inference_row", side_effect=_fake_build_inference_row),
            patch("scripts.predict_up5.predict_with_artifact", side_effect=_fake_predict_with_artifact),
            patch("app.services.prediction_service.PredictionService", _DummyPredictionService),
        ]
        if llm_side_effect is not None:
            patches.append(
                patch(
                    "app.services.llm_signal_extraction_service.LlmSignalExtractionService.get_latest_structured_features",
                    side_effect=llm_side_effect,
                )
            )
        else:
            patches.append(
                patch(
                    "app.services.llm_signal_extraction_service.LlmSignalExtractionService.get_latest_structured_features",
                    return_value=llm_payload,
                )
            )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
            return svc.run_on_demand("US:AAPL")

    def test_run_on_demand_with_llm_features(self):
        payload = {
            "evidence_set_id": "ev123",
            "feature_set_version": "xgb_base_plus_llm_v1",
            "llm_features": {"llm_earnings_tone_score": 1.25},
            "evidence_items": [
                {
                    "source_id": "doc1",
                    "source_type": "news",
                    "source_ref": "n1",
                    "claim_type": "news_event",
                    "direction": "positive",
                    "short_rationale": "earnings beat and raised guidance",
                }
            ],
            "quality_metrics": {"coverage_ratio": 1.0},
            "point_in_time_ok": True,
        }
        out = self._run_case(llm_payload=payload)
        self.assertTrue(out["has_prediction"])
        self.assertEqual(out["decision"], "BUY")
        self.assertIn("p_up5", out)
        self.assertTrue(out["llm_features_used"])
        self.assertFalse(out["llm_degraded_mode"])
        self.assertEqual(out["feature_set_version"], "xgb_base_plus_llm_v1")
        self.assertEqual(out["evidence_set_id"], "ev123")
        self.assertIn("feature_usage", out)

    def test_run_on_demand_without_llm_features_keeps_success(self):
        out = self._run_case(llm_payload=None)
        self.assertTrue(out["has_prediction"])
        self.assertIn("p_up5", out)
        self.assertFalse(out["llm_features_used"])
        self.assertFalse(out["llm_degraded_mode"])
        self.assertEqual(out["feature_set_version"], "xgb_only_v1")

    def test_run_on_demand_llm_failure_degraded_mode(self):
        out = self._run_case(llm_side_effect=RuntimeError("llm backend unavailable"))
        self.assertTrue(out["has_prediction"])
        self.assertIn("p_up5", out)
        self.assertFalse(out["llm_features_used"])
        self.assertTrue(out["llm_degraded_mode"])
        self.assertIn("decision", out)


if __name__ == "__main__":
    unittest.main()
