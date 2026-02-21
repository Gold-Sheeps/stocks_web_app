import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts import predict_up5 as pu


class _DummyModel:
    def predict_proba(self, x):
        _ = x
        return np.array([[0.2, 0.8]], dtype=float)


class TestPredictUp5(unittest.TestCase):
    def test_predict_with_artifact_returns_p_and_action(self) -> None:
        artifact = {
            "model": _DummyModel(),
            "calibration": {"type": "none"},
            "threshold_buy": 0.7,
        }
        x_df = pd.DataFrame([[1.0, 2.0]], columns=["a", "b"])
        out = pu.predict_with_artifact(artifact, x_df)
        self.assertIn("p_up5", out)
        self.assertIn("action", out)
        self.assertAlmostEqual(float(out["p_up5"]), 0.8)
        self.assertEqual(out["action"], "BUY")

    def test_watchlist_ranking(self) -> None:
        rows = [
            {"ticker": "US:AAPL", "p_up5": 0.4, "decision": "NO_TRADE"},
            {"ticker": "US:MSFT", "p_up5": 0.9, "decision": "BUY"},
            {"ticker": "US:GOOGL", "p_up5": 0.7, "decision": "BUY"},
        ]
        ranked = pu._rank_predictions(rows)
        self.assertEqual([r["ticker"] for r in ranked], ["US:MSFT", "US:GOOGL", "US:AAPL"])


if __name__ == "__main__":
    unittest.main()
