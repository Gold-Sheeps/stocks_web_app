import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.services.prediction_service import PredictionService


class TestFixedThresholdMetrics(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = PredictionService.__new__(PredictionService)
        self.svc._sk_metrics = {"confusion_matrix": _confusion_matrix_2}

    def test_p70_precision_coverage_ntrades(self) -> None:
        probs = np.array([0.90, 0.80, 0.69, 0.70, 0.20], dtype=float)
        y_true = np.array([1, 0, 1, 1, 0], dtype=int)
        rets = np.array([0.08, -0.01, 0.03, 0.06, -0.02], dtype=float)

        rows = self.svc._eval_fixed_thresholds_up5(
            probs=probs,
            y_true=y_true,
            future_returns=rets,
            thresholds=[0.70, 0.80],
        )
        p70 = next(r for r in rows if abs(float(r["t_buy"]) - 0.70) < 1e-12)
        self.assertIn("precision", p70)
        self.assertIn("confusion_matrix", p70)

        self.assertEqual(p70["n_trades"], 3)
        self.assertAlmostEqual(float(p70["coverage"]), 3.0 / 5.0)
        self.assertAlmostEqual(float(p70["precision"]), 2.0 / 3.0)
        self.assertAlmostEqual(float(p70["avg_return"]), (0.08 - 0.01 + 0.06) / 3.0)


if __name__ == "__main__":
    unittest.main()


def _confusion_matrix_2(y_true, y_pred, labels):
    assert labels == [0, 1]
    cm = np.zeros((2, 2), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[int(t), int(p)] += 1
    return cm
