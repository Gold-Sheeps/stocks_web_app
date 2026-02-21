import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.services.prediction_service import PredictionService


class TestUp5Label(unittest.TestCase):
    def test_up5_labels_boundary_and_tail_exclusion(self) -> None:
        svc = PredictionService.__new__(PredictionService)
        close = np.array([100.0, 104.0, 105.0, 110.25, 120.0], dtype=float)
        # horizon=2:
        # t=0 -> 105/100-1 = +0.05 => 1 (boundary inclusive)
        # t=1 -> 110.25/104-1 = +0.060096... => 1
        # t=2 -> 120/105-1 = +0.142857... => 1
        # tail (t=3,4) => NaN
        y = svc._up5_labels_from_close(close, horizon=2, target_return=0.05)
        self.assertEqual(y.tolist()[:3], [1.0, 1.0, 1.0])
        self.assertTrue(np.isnan(y[3]))
        self.assertTrue(np.isnan(y[4]))

    def test_up5_labels_negative_case(self) -> None:
        svc = PredictionService.__new__(PredictionService)
        close = np.array([100.0, 101.0, 102.0, 103.0], dtype=float)
        # horizon=2:
        # t=0 -> 102/100-1 = 0.02 => 0
        # t=1 -> 103/101-1 ~= 0.0198 => 0
        y = svc._up5_labels_from_close(close, horizon=2, target_return=0.05)
        self.assertEqual(y.tolist()[:2], [0.0, 0.0])
        self.assertTrue(np.isnan(y[2]))
        self.assertTrue(np.isnan(y[3]))


if __name__ == "__main__":
    unittest.main()
