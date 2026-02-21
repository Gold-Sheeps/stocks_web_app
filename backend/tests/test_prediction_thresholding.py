import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.services.prediction_service import PredictionService


def _confusion_matrix_3(y_true, y_pred):
    cm = np.zeros((3, 3), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[int(t), int(p)] += 1
    return cm


def _macro_f1_3(y_true, y_pred, average="macro"):
    assert average == "macro"
    cm = _confusion_matrix_3(y_true, y_pred)
    f1s = []
    for i in range(3):
        tp = cm[i, i]
        pred_pos = np.sum(cm[:, i])
        true_pos = np.sum(cm[i, :])
        precision = tp / pred_pos if pred_pos > 0 else 0.0
        recall = tp / true_pos if true_pos > 0 else 0.0
        f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        f1s.append(f1)
    return float(np.mean(f1s))


def test_threshold_decision_boundary_and_priority():
    svc = PredictionService.__new__(PredictionService)
    probs = np.array(
        [
            [0.30, 0.20, 0.50],  # down at boundary -> down priority
            [0.29, 0.10, 0.40],  # down below, up at boundary -> up
            [0.29, 0.45, 0.39],  # both below -> flat
            [0.35, 0.10, 0.80],  # both above -> down priority
        ],
        dtype=float,
    )
    pred = svc._predict_classes_threshold(probs, t_down=0.30, t_up=0.40)
    assert pred.tolist() == [0, 2, 1, 0], (
        "Threshold decision must satisfy down-first rule with boundary-inclusive comparisons."
    )


def test_threshold_search_selects_feasible_candidate():
    svc = PredictionService.__new__(PredictionService)
    svc._sk_metrics = {
        "f1_score": _macro_f1_3,
        "confusion_matrix": lambda y_true, y_pred, labels: _confusion_matrix_3(y_true, y_pred),
    }
    y_true = np.array([0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2], dtype=int)
    probs = np.array(
        [
            [0.55, 0.25, 0.20],
            [0.45, 0.30, 0.25],
            [0.40, 0.35, 0.25],
            [0.20, 0.50, 0.30],
            [0.25, 0.55, 0.20],
            [0.20, 0.60, 0.20],
            [0.15, 0.70, 0.15],
            [0.20, 0.55, 0.25],
            [0.15, 0.20, 0.65],
            [0.20, 0.20, 0.60],
            [0.10, 0.25, 0.65],
            [0.15, 0.20, 0.65],
        ],
        dtype=float,
    )
    candidates = svc._threshold_candidates(
        y_true_arr=y_true,
        probs=probs,
        t_down_values=[0.30, 0.45],
        t_up_values=[0.45, 0.60],
    )
    selected = svc._select_threshold_candidate(
        candidates=candidates,
        down_recall_min=0.25,
        pred_down_min_pct=0.10,
    )
    best = selected["best"]
    assert best["down_recall"] >= 0.25, "Best threshold must satisfy down_recall_min when feasible candidates exist."
    assert best["pred_down_pct"] >= 0.10, "Best threshold must satisfy pred_down_min_pct when feasible candidates exist."
    assert selected["fallback_unconstrained"] is False, "Fallback must be false when at least one feasible candidate exists."
    assert len(selected["ranked"]) >= 1, "Ranked candidate list must not be empty."


def test_threshold_search_fallback_when_no_feasible():
    svc = PredictionService.__new__(PredictionService)
    cands = [
        {"t_down": 0.3, "t_up": 0.4, "macro_f1": 0.3, "down_recall": 0.02, "pred_down_pct": 0.02},
        {"t_down": 0.4, "t_up": 0.5, "macro_f1": 0.4, "down_recall": 0.01, "pred_down_pct": 0.01},
    ]
    selected = svc._select_threshold_candidate(cands, down_recall_min=0.2, pred_down_min_pct=0.1)
    assert selected["fallback_unconstrained"] is True, "Fallback flag must be true when no candidate satisfies constraints."
    assert selected["best"]["macro_f1"] == 0.4, "Fallback best should maximize macro_f1 among all candidates."


def test_class_weight_modes_and_sample_weight_mapping():
    svc = PredictionService.__new__(PredictionService)
    y = np.array([0, 0, 1, 2, 2, 2], dtype=int)

    off = svc._resolve_class_weight_map("off", None, y)
    assert off is None, "class_weight_mode=off must return None to preserve baseline weighting path."

    balanced = svc._resolve_class_weight_map("balanced", None, y)
    assert balanced is not None, "class_weight_mode=balanced must return a class weight map."
    assert abs(balanced[0] - 1.0) < 1e-9, "Balanced weight for class down should be n/(k*count)=1.0 in this fixture."
    assert abs(balanced[1] - 2.0) < 1e-9, "Balanced weight for class flat should be n/(k*count)=2.0 in this fixture."
    assert abs(balanced[2] - (2.0 / 3.0)) < 1e-9, "Balanced weight for class up should be n/(k*count)=2/3 in this fixture."

    custom = svc._resolve_class_weight_map("custom", {"down": 3.0, "flat": 1.0, "up": 1.0}, y)
    sw = svc._sample_weight_from_class_map(y, custom)
    assert sw.tolist() == [3.0, 3.0, 1.0, 1.0, 1.0, 1.0], (
        "Custom class weights must expand to per-sample weights by class label."
    )
