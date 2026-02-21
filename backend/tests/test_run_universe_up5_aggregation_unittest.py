import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts import run_universe_up5 as ru


class TestRunUniverseUp5Aggregation(unittest.TestCase):
    def test_micro_aggregation_matches_weighted_definition(self) -> None:
        payload_a = {
            "metrics": {
                "precision_at_p70": 0.8,
                "coverage_at_p70": 0.2,
                "n_trades_at_p70": 20,
                "avg_return_at_p70": 0.03,
            },
            "diagnostics": {"fixed_threshold_metrics": {"0.7": {"n_samples": 100}}},
        }
        payload_b = {
            "metrics": {
                "precision_at_p70": 0.5,
                "coverage_at_p70": 0.1,
                "n_trades_at_p70": 10,
                "avg_return_at_p70": 0.01,
            },
            "diagnostics": {"fixed_threshold_metrics": {"0.7": {"n_samples": 100}}},
        }
        rows = [ru._p70_row(payload_a), ru._p70_row(payload_b)]
        micro = ru._micro_metrics(rows)

        self.assertEqual(micro["n_trades_at_p70_micro"], 30)
        self.assertAlmostEqual(float(micro["precision_at_p70_micro"]), (0.8 * 20 + 0.5 * 10) / 30)
        self.assertAlmostEqual(float(micro["coverage_at_p70_micro"]), 30 / 200)
        self.assertAlmostEqual(float(micro["avg_return_at_p70_micro"]), (0.03 * 20 + 0.01 * 10) / 30)


if __name__ == "__main__":
    unittest.main()
