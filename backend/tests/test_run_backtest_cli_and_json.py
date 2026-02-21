import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts import run_backtest_nvda as rb


def _run_main_with_args(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["run_backtest_nvda.py", *args])
    return rb.main()


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_backtest_nvda.py", "--asof", "2026-02-16"])
    args = rb._parse_args()
    assert args.class_weight_mode == "off", "Default class_weight_mode must remain off for compatibility."
    assert args.threshold_mode == "argmax", "Default threshold_mode must remain argmax for compatibility."
    assert args.threshold_search is False, "Default threshold_search must remain disabled."
    assert args.down_recall_min == 0.10, "Default down_recall_min must be 0.10."
    assert args.pred_down_min_pct == 0.05, "Default pred_down_min_pct must be 0.05."


def test_parse_class_weight_map_and_threshold_grid():
    cw = rb._parse_class_weight_map("down=3.0,flat=1.0,up=1.0")
    assert cw == {"down": 3.0, "flat": 1.0, "up": 1.0}, "Custom class-weight parsing failed."

    grid = rb._parse_threshold_grid("down=0.2,0.3;up=0.4,0.5")
    assert grid == {"down": [0.2, 0.3], "up": [0.4, 0.5]}, "Threshold grid parsing failed."

    with pytest.raises(ValueError, match="down\\|flat\\|up"):
        rb._parse_class_weight_map("bad=1.0")
    with pytest.raises(ValueError, match="down\\|up"):
        rb._parse_threshold_grid("bad=0.2")


def test_main_requires_class_weight_when_custom(monkeypatch):
    with pytest.raises(ValueError, match="requires --class-weight"):
        _run_main_with_args(
            monkeypatch,
            [
                "--asof",
                "2026-02-16",
                "--no-db-check",
                "--class-weight-mode",
                "custom",
            ],
        )


class _FakePredictionService:
    def backtest(self, **kwargs):
        threshold_mode = kwargs.get("threshold_mode", "argmax")
        class_weight_mode = kwargs.get("class_weight_mode", "off")
        class_weight = kwargs.get("class_weight")
        thresholds_applied = {"t_down": 0.3, "t_up": 0.5} if threshold_mode == "threshold" else None
        threshold_selection = (
            {
                "best": {
                    "t_down": 0.3,
                    "t_up": 0.5,
                    "macro_f1": 0.4,
                    "down_recall": 0.2,
                    "pred_down_pct": 0.1,
                    "confusion_matrix": [[4, 1, 0], [1, 3, 1], [0, 1, 4]],
                },
                "candidates": [
                    {
                        "t_down": 0.3,
                        "t_up": 0.5,
                        "macro_f1": 0.4,
                        "down_recall": 0.2,
                        "pred_down_pct": 0.1,
                        "confusion_matrix": [[4, 1, 0], [1, 3, 1], [0, 1, 4]],
                    }
                ],
            }
            if threshold_mode == "threshold"
            else {"best": None, "candidates": []}
        )
        return {
            "backtest": {
                "accuracy": 0.5,
                "macro_f1": 0.4,
                "logloss": 1.1,
                "brier": 0.22,
                "ece": 0.08,
                "interval_target_coverage": 0.8,
                "interval_q": 1.0,
                "interval_width_mean": 2.0,
                "interval_coverage": 0.7,
                "bias": 0.1,
                "bias_abs": 0.2,
                "class_ratio": {"down": 0.3, "flat": 0.3, "up": 0.4},
                "confusion_matrix": [[4, 1, 0], [1, 3, 1], [0, 1, 4]],
                "class_metrics": {
                    "down": {"precision": 0.8, "recall": 0.8, "f1": 0.8},
                    "flat": {"precision": 0.6, "recall": 0.6, "f1": 0.6},
                    "up": {"precision": 0.8, "recall": 0.8, "f1": 0.8},
                },
                "trading_metrics": {},
                "validation": {"purge_days": 15, "embargo_mode": "pct", "embargo_days": 5, "embargo_pct": 0.01, "folds_used": 3},
                "meta_metrics": {},
                "temperature_scaling": {"best_T": 1.0},
                "prob_calibration": {"temp_scale_mode": "off"},
                "baselines": {},
                "interval_target_name": "close_future",
                "interval_target_unit": "price",
                "target_transform": "raw_price",
                "target_scale_factor": 1.0,
                "interval_target_y_source": "target_close_h",
                "interval_target_mismatch_warn": False,
                "interval_audit_sample": [],
                "threshold_selection": threshold_selection,
                "warnings": ["threshold_search: example"] if kwargs.get("threshold_search", False) else [],
                "thresholds_applied": thresholds_applied,
                "class_weight": (
                    {"down": 3.0, "flat": 1.0, "up": 1.0}
                    if class_weight_mode != "off"
                    else None
                ),
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


def test_json_audit_keys_threshold_off_default(monkeypatch):
    monkeypatch.setattr(rb, "PredictionService", _FakePredictionService)
    out_name = f"unit_test_{uuid.uuid4().hex}.json"
    try:
        code = _run_main_with_args(
            monkeypatch,
            [
                "--asof",
                "2026-02-16",
                "--no-db-check",
                "--output-name",
                out_name,
            ],
        )
        assert code == 0, "main() should return 0 for successful run."
        out_path = Path("backend/ml_predictor_data") / out_name
        data = json.loads(out_path.read_text(encoding="utf-8"))
        rp = data["meta"]["run_params"]
        assert rp["class_weight_mode"] == "off", "Default JSON must keep class_weight_mode=off."
        assert rp["class_weight"] is None, "Default JSON must keep class_weight=null."
        assert rp["threshold_mode"] == "argmax", "Default JSON must keep threshold_mode=argmax."
        assert rp["threshold_search_enabled"] is False, "Default JSON must keep threshold_search disabled."
        assert rp["thresholds_applied"] is None, "Default JSON should set thresholds_applied to null in argmax mode."
    finally:
        p = Path("backend/ml_predictor_data") / out_name
        if p.exists():
            p.unlink()


def test_json_audit_keys_threshold_on(monkeypatch):
    monkeypatch.setattr(rb, "PredictionService", _FakePredictionService)
    out_name = f"unit_test_{uuid.uuid4().hex}.json"
    try:
        code = _run_main_with_args(
            monkeypatch,
            [
                "--asof",
                "2026-02-16",
                "--no-db-check",
                "--class-weight-mode",
                "custom",
                "--class-weight",
                "down=3.0,flat=1.0,up=1.0",
                "--threshold-mode",
                "threshold",
                "--threshold-search",
                "--output-name",
                out_name,
            ],
        )
        assert code == 0, "main() should return 0 for threshold mode run."
        out_path = Path("backend/ml_predictor_data") / out_name
        data = json.loads(out_path.read_text(encoding="utf-8"))
        rp = data["meta"]["run_params"]
        assert rp["class_weight_mode"] == "custom", "JSON must record class_weight_mode=custom."
        assert rp["class_weight"] == {"down": 3.0, "flat": 1.0, "up": 1.0}, "JSON must record effective class_weight map."
        assert rp["threshold_mode"] == "threshold", "JSON must record threshold_mode=threshold."
        assert rp["threshold_search_enabled"] is True, "JSON must record threshold_search_enabled=true."
        assert rp["thresholds_applied"] == {"t_down": 0.3, "t_up": 0.5}, "JSON must record applied thresholds."
        assert data["meta"]["threshold_selection"]["best"]["t_down"] == 0.3, "threshold_selection.best must exist for audit."
        assert len(data["meta"]["threshold_selection"]["candidates"]) >= 1, "threshold_selection.candidates must be non-empty."
    finally:
        p = Path("backend/ml_predictor_data") / out_name
        if p.exists():
            p.unlink()
