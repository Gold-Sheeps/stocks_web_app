import unittest
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from app.services.prediction_service import PredictionService


class ProbRoundingTests(unittest.TestCase):
    def setUp(self) -> None:
        # Avoid heavy dependency initialization in __init__.
        self.svc = PredictionService.__new__(PredictionService)

    def test_sum_is_always_100(self) -> None:
        out = self.svc._round_to_100({"up": 33.4, "down": 33.3, "flat": 33.3})
        self.assertEqual(out["up"] + out["down"] + out["flat"], 100)

    def test_bounds_are_enforced(self) -> None:
        out = self.svc._round_to_100({"up": -5.0, "down": 20.0, "flat": 200.0})
        for k in ("up", "down", "flat"):
            self.assertGreaterEqual(out[k], 0)
            self.assertLessEqual(out[k], 100)
        self.assertEqual(out["up"] + out["down"] + out["flat"], 100)

    def test_diff_adjusts_largest_element(self) -> None:
        # Half-up rounding gives [34, 34, 34] => diff=-2 should be taken from largest element.
        out = self.svc._round_to_100({"up": 33.5, "down": 33.5, "flat": 33.5})
        self.assertEqual(out["up"] + out["down"] + out["flat"], 100)
        self.assertEqual(out["up"], 32)
        self.assertEqual(out["down"], 34)
        self.assertEqual(out["flat"], 34)


if __name__ == "__main__":
    unittest.main()
