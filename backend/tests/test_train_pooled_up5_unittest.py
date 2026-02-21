import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts import train_pooled_up5 as tp


class TestTrainPooledUp5(unittest.TestCase):
    def test_build_pooled_dataset_has_consistent_columns_and_ticker_id(self) -> None:
        idx = pd.to_datetime(["2020-01-01", "2020-01-02"])
        f1 = pd.DataFrame(
            {
                "f_a": [1.0, 2.0],
                "f_b": [3.0, 4.0],
                "target_return_pct_h": [6.0, -1.0],
                "ticker": ["US:AAA", "US:AAA"],
                "y_up5": [1, 0],
                "future_return": [0.06, -0.01],
            },
            index=idx,
        )
        f2 = pd.DataFrame(
            {
                "f_a": [1.5, 2.5],
                "f_b": [3.5, 4.5],
                "target_return_pct_h": [7.0, -2.0],
                "ticker": ["US:BBB", "US:BBB"],
                "y_up5": [1, 0],
                "future_return": [0.07, -0.02],
            },
            index=idx,
        )
        pooled, model_cols, dummy_cols = tp._build_pooled_dataset([f1, f2], ["f_a", "f_b"])
        self.assertIn("ticker_id", pooled.columns)
        self.assertIn("tid_US:AAA", dummy_cols)
        self.assertIn("tid_US:BBB", dummy_cols)
        self.assertEqual(len(model_cols), 4)

    def test_precision_metrics_are_subset_p_ge_07(self) -> None:
        y = np.array([1, 0, 1, 0], dtype=int)
        p = np.array([0.90, 0.80, 0.60, 0.20], dtype=float)
        r = np.array([0.08, -0.01, 0.02, -0.03], dtype=float)
        m = tp._fixed_threshold_metrics(y, p, r, [0.7])["0.7"]
        self.assertEqual(m["n_trades"], 2)
        self.assertAlmostEqual(float(m["coverage"]), 0.5)
        self.assertAlmostEqual(float(m["precision"]), 0.5)

    def test_holdout_tickers_not_mixed_into_train(self) -> None:
        train, holdout = tp._resolve_train_holdout_tickers(
            all_tickers=["US:AAPL", "US:MSFT", "US:GOOGL"],
            holdout_tickers=["US:MSFT"],
        )
        self.assertEqual(train, ["US:AAPL", "US:GOOGL"])
        self.assertEqual(holdout, ["US:MSFT"])

    def test_confidence_bins_are_computed_correctly(self) -> None:
        y = np.array([1, 0, 1, 1, 0], dtype=int)
        p = np.array([0.55, 0.65, 0.75, 0.85, 0.95], dtype=float)
        r = np.array([0.01, -0.02, 0.03, 0.04, -0.01], dtype=float)
        bins = tp._confidence_bins(y, p, r)
        self.assertEqual(len(bins), 5)
        b70 = next(b for b in bins if b["bin"] == "[0.7,0.8)")
        self.assertEqual(b70["n"], 1)
        self.assertAlmostEqual(float(b70["win_rate"]), 1.0)

    def test_low_confidence_miss_uses_p_lt_07(self) -> None:
        y = np.array([1, 1, 0], dtype=int)
        p = np.array([0.69, 0.29, 0.80], dtype=float)
        r = np.array([0.05, 0.01, -0.02], dtype=float)
        audit = tp._high_low_miss_audit(
            y_true=y,
            probs=p,
            future_returns=r,
            dates=["2020-01-01", "2020-01-02", "2020-01-03"],
            tickers=["US:AAA", "US:AAA", "US:AAA"],
            top_n=10,
        )
        self.assertEqual(audit["low_confidence_miss"]["threshold"], 0.7)
        self.assertEqual(audit["low_confidence_miss"]["count"], 2)

    def test_parse_thresholds_prefers_fixed_threshold(self) -> None:
        thresholds = tp._parse_thresholds("0.2,0.9", fixed_threshold=0.7)
        self.assertEqual(thresholds, [0.5, 0.6, 0.7])


if __name__ == "__main__":
    unittest.main()
