import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.services.prediction_service import PredictionService


def _confusion_matrix_2(y_true, y_pred, labels):
    assert labels == [0, 1]
    cm = np.zeros((2, 2), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[int(t), int(p)] += 1
    return cm


class TestThresholdSweep(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = PredictionService.__new__(PredictionService)
        self.svc._sk_metrics = {
            "confusion_matrix": _confusion_matrix_2,
        }

    def test_threshold_candidates_metrics(self) -> None:
        y_true = np.array([1, 0, 1, 0, 1, 0], dtype=int)
        prob = np.array([0.90, 0.80, 0.70, 0.40, 0.30, 0.20], dtype=float)
        rets = np.array([0.10, -0.02, 0.06, -0.01, 0.01, -0.03], dtype=float)
        cands = self.svc._threshold_candidates_up5(
            y_true_arr=y_true,
            prob_up_arr=prob,
            realized_returns=rets,
            t_buy_values=[0.50, 0.80],
        )
        c50 = next(c for c in cands if abs(c["t_buy"] - 0.50) < 1e-12)
        self.assertEqual(c50["n_trades"], 3)
        self.assertAlmostEqual(c50["coverage"], 0.5)
        self.assertAlmostEqual(c50["win_rate"], 2.0 / 3.0)

        c80 = next(c for c in cands if abs(c["t_buy"] - 0.80) < 1e-12)
        self.assertEqual(c80["n_trades"], 2)
        self.assertAlmostEqual(c80["coverage"], 2.0 / 6.0)
        self.assertAlmostEqual(c80["win_rate"], 0.5)

    def test_threshold_selection_with_and_without_fallback(self) -> None:
        candidates = [
            {"t_buy": 0.50, "win_rate": 0.60, "coverage": 0.10, "n_trades": 100},
            {"t_buy": 0.70, "win_rate": 0.72, "coverage": 0.07, "n_trades": 70},
            {"t_buy": 0.90, "win_rate": 0.90, "coverage": 0.01, "n_trades": 10},
        ]
        sel = self.svc._select_threshold_candidate_up5(
            candidates=candidates,
            min_coverage=0.05,
            min_trades=50,
        )
        self.assertFalse(sel["fallback_unconstrained"])
        self.assertAlmostEqual(sel["best"]["t_buy"], 0.70)

        sel_fb = self.svc._select_threshold_candidate_up5(
            candidates=candidates,
            min_coverage=0.20,
            min_trades=200,
        )
        self.assertTrue(sel_fb["fallback_unconstrained"])
        self.assertAlmostEqual(sel_fb["best"]["t_buy"], 0.90)

    def test_auto_t_buy_grid_from_probs(self) -> None:
        prob = np.array([0.01, 0.02, 0.05, 0.10, 0.20, 0.40, 0.60, 0.80, 0.95], dtype=float)
        grid = self.svc._auto_t_buy_grid_from_probs(prob)
        self.assertTrue(len(grid) >= 3)
        self.assertTrue(all(0.0 <= g <= 1.0 for g in grid))


if __name__ == "__main__":
    unittest.main()
