import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts import run_backtest_nvda as rb


class _FakePredictionService:
    def backtest(self, **kwargs):
        return {
            "backtest": {
                "accuracy": 0.6,
                "macro_f1": 0.55,
                "logloss": 0.5,
                "brier": 0.15,
                "ece": 0.04,
                "interval_target_coverage": 0.8,
                "interval_q": None,
                "interval_width_mean": None,
                "interval_coverage": None,
                "bias": 0.0,
                "bias_abs": 0.0,
                "confusion_matrix": [[8, 2], [3, 7]],
                "class_metrics": {
                    "no_trade": {"precision": 0.72, "recall": 0.80, "f1": 0.76},
                    "buy": {"precision": 0.78, "recall": 0.70, "f1": 0.74},
                },
                "trading_metrics": {"trade_count": 9},
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
                "threshold_selection": {
                    "best": {
                        "t_buy": 0.8,
                        "win_rate": 0.72,
                        "coverage": 0.07,
                        "n_trades": 120,
                        "avg_return": 0.031,
                        "median_return": 0.015,
                        "confusion_2x2": [[10, 2], [3, 7]],
                    },
                    "candidates": [],
                    "fallback_unconstrained": False,
                    "constraints": {"min_coverage": 0.05, "min_trades": 50},
                },
                "warnings": [],
                "thresholds_applied": {"t_buy": 0.8},
                "label_distribution": {"negative_0": 0.8, "positive_1": 0.2},
                "prediction_distribution": {"buy_1": 0.07, "no_trade_0": 0.93},
                "prob_up5_summary": {"min": 0.01, "max": 0.95, "p50": 0.2, "p90": 0.8, "p95": 0.9, "p99": 0.94, "mean": 0.3, "std": 0.2},
                "prob_calibration_audit": {
                    "reliability_bins": [],
                    "high_confidence_miss": {"threshold": 0.8, "count": 1, "base_count": 5, "rate": 0.2},
                    "low_confidence_miss": {"threshold": 0.2, "count": 2, "base_count": 10, "rate": 0.2},
                },
                "win_rate_at_best": 0.72,
                "coverage_at_best": 0.07,
                "n_trades_at_best": 120,
                "avg_return_at_best": 0.031,
                "median_return_at_best": 0.015,
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


class TestUp5JsonKeys(unittest.TestCase):
    def test_run_params_and_diagnostics_keys_exist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_name = "tmp_up5_json_test.json"
            out_path = Path(__file__).resolve().parents[1] / "ml_predictor_data" / out_name
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
            with patch.object(sys, "argv", argv), patch.object(rb, "PredictionService", _FakePredictionService):
                code = rb.main()
                self.assertEqual(code, 0)
            data = json.loads(out_path.read_text(encoding="utf-8"))
            rp = data["meta"]["run_params"]
            self.assertEqual(rp["label_mode"], "up5_2w")
            self.assertEqual(rp["target_return"], 0.05)
            self.assertEqual(rp["horizon_trading_days"], 10)
            self.assertIn("t_buy_grid", rp)
            self.assertIn("thresholds_applied", rp)
            diag = data["diagnostics"]
            self.assertIn("threshold_selection", diag)
            self.assertIn("prob_up5_summary", diag)
            self.assertIn("prob_calibration_audit", diag)
            self.assertIn("confusion_2x2", diag)
            out_path.unlink()


if __name__ == "__main__":
    unittest.main()
