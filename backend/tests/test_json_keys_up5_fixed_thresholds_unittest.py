import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts import run_backtest_nvda as rb


class _FakePredictionService:
    def backtest(self, **kwargs):
        return {
            "backtest": {
                "accuracy": 0.61,
                "macro_f1": 0.56,
                "logloss": 0.49,
                "brier": 0.14,
                "ece": 0.03,
                "interval_target_coverage": 0.8,
                "interval_q": None,
                "interval_width_mean": None,
                "interval_coverage": None,
                "bias": 0.0,
                "bias_abs": 0.0,
                "confusion_matrix": [[9, 1], [3, 7]],
                "class_metrics": {},
                "trading_metrics": {},
                "validation": {"purge_days": 10, "embargo_mode": "pct", "embargo_days": 5, "embargo_pct": 0.01, "folds_used": 3},
                "meta_metrics": {"label_mode": "up5_2w"},
                "temperature_scaling": {"best_T": 1.0},
                "prob_calibration": {"temp_scale_mode": "off"},
                "baselines": {},
                "interval_target_name": "return_up5_binary",
                "interval_target_unit": "fraction",
                "target_transform": "future_return_fraction",
                "target_scale_factor": 1.0,
                "interval_target_y_source": "target_return_pct_h",
                "interval_target_mismatch_warn": False,
                "interval_audit_sample": [],
                "threshold_selection": {"best": {"t_buy": 0.75}, "candidates": []},
                "fixed_threshold_metrics": [
                    {"t_buy": 0.5, "n_trades": 150, "coverage": 0.3, "win_rate": 0.62, "avg_return": 0.011},
                    {"t_buy": 0.7, "n_trades": 90, "coverage": 0.18, "win_rate": 0.74, "avg_return": 0.019},
                ],
                "warnings": [],
                "thresholds_applied": {"t_buy": 0.75},
                "label_distribution": {"negative_0": 0.8, "positive_1": 0.2},
                "prediction_distribution": {"buy_1": 0.1, "no_trade_0": 0.9},
                "prob_up5_summary": {},
                "prob_calibration_audit": {},
                "win_rate_at_best": 0.72,
                "coverage_at_best": 0.17,
                "n_trades_at_best": 85,
                "avg_return_at_best": 0.018,
                "median_return_at_best": 0.01,
                "precision_at_p70": 0.74,
                "coverage_at_p70": 0.18,
                "n_trades_at_p70": 90,
                "avg_return_at_p70": 0.019,
            },
            "meta": {
                "model_version": "fake_model",
                "feature_set_used": kwargs.get("feature_version", "v2_ohlcv_ta_relregime_event"),
                "feature_version": kwargs.get("feature_version", "v2_ohlcv_ta_relregime_event"),
                "used_feature_columns": ["f1", "f2"],
                "n_features": 2,
                "feature_hash": "fake_feature_hash",
                "sources": {},
                "origins": {},
                "db_freshness": {},
            },
            "best_flat_band_pct": 2.0,
            "band_sweep": [],
        }


class TestJsonKeysUp5FixedThresholds(unittest.TestCase):
    def test_fixed_threshold_keys_exist(self) -> None:
        out_name = "tmp_up5_fixed_thresholds.json"
        out_path = Path("backend/ml_predictor_data") / out_name
        if out_path.exists():
            out_path.unlink()
        argv = [
            "run_backtest_nvda.py",
            "--asof",
            "2026-02-18",
            "--ticker",
            "US:AAPL",
            "--label-mode",
            "up5_2w",
            "--horizon",
            "10",
            "--target-return",
            "0.05",
            "--threshold-search",
            "--no-db-check",
            "--output-name",
            out_name,
        ]
        try:
            with patch.object(sys, "argv", argv), patch.object(rb, "PredictionService", _FakePredictionService):
                code = rb.main()
                self.assertEqual(code, 0)
            data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn("fixed_threshold_metrics", data["diagnostics"])
            self.assertIn("0.7", data["diagnostics"]["fixed_threshold_metrics"])
            self.assertIn("precision_at_p70", data["metrics"])
            self.assertIn("coverage_at_p70", data["metrics"])
            self.assertIn("n_trades_at_p70", data["metrics"])
            self.assertIn("avg_return_at_p70", data["metrics"])
        finally:
            if out_path.exists():
                out_path.unlink()


if __name__ == "__main__":
    unittest.main()
