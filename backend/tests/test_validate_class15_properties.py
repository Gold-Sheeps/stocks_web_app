"""
性質ベーステスト: validate_class15.py の純粋関数群

DB 接続不要。fetch_outcomes_bulk / fetch_predictions は対象外。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from scripts.run_class15_batch import _derive_class15_probs
from scripts.validate_class15 import classify_actual, analyse


# ─────────────────────────────────────────────────────────────────────────────
# classify_actual
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyActual:
    @pytest.mark.parametrize("ret, flat_band, expected", [
        (5.0,   3.0, 2),   # strictly above +3% → Up
        (3.1,   3.0, 2),   # just above +3% → Up
        (3.0,   3.0, 1),   # exactly at +3% → Flat (not strictly greater)
        (0.0,   3.0, 1),   # zero → Flat
        (-2.9,  3.0, 1),   # inside band → Flat
        (-3.0,  3.0, 1),   # exactly -3% → Flat (not strictly less)
        (-3.1,  3.0, 0),   # just below -3% → Down
        (-10.0, 3.0, 0),   # well below → Down
    ])
    def test_boundaries(self, ret, flat_band, expected):
        assert classify_actual(ret, flat_band) == expected

    def test_output_in_valid_set(self):
        for ret in [-100.0, -3.1, -3.0, 0.0, 3.0, 3.1, 100.0]:
            result = classify_actual(ret, 3.0)
            assert result in (0, 1, 2)

    @pytest.mark.parametrize("flat_band", [1.0, 2.0, 5.0, 10.0])
    def test_symmetric_around_zero(self, flat_band):
        """±flat_band は対称：+band → Flat、-band → Flat"""
        assert classify_actual(flat_band, flat_band) == 1
        assert classify_actual(-flat_band, flat_band) == 1
        assert classify_actual(flat_band + 0.001, flat_band) == 2
        assert classify_actual(-flat_band - 0.001, flat_band) == 0


# ─────────────────────────────────────────────────────────────────────────────
# _derive_class15_probs: Flat は一度も予測されない (formula issue)
# ─────────────────────────────────────────────────────────────────────────────

class TestFlatNeverPredicted:
    @pytest.mark.parametrize("p", [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    def test_prob_flat_always_lt_prob_down(self, p):
        """prob_flat = 0.45*(1-p) < 0.55*(1-p) = prob_down なので Flat は予測されない"""
        _, prob_flat, prob_down, _ = _derive_class15_probs(p)
        # p < 1 の場合、prob_flat < prob_down が常に成立
        if p < 1.0:
            assert prob_flat < prob_down, (
                f"p={p}: expected prob_flat({prob_flat}) < prob_down({prob_down})"
            )

    @pytest.mark.parametrize("p", [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    def test_predicted_class_never_1(self, p):
        """予測クラス 1 (Flat) は一度も選ばれない"""
        _, _, _, predicted_class = _derive_class15_probs(p)
        assert predicted_class != 1, f"p={p} yielded predicted_class=1 (Flat), unexpected"


# ─────────────────────────────────────────────────────────────────────────────
# analyse(): 出力 shape と不変条件
# ─────────────────────────────────────────────────────────────────────────────

def _make_record(p_up5: float, entry: float, exit_: float, decision: str = "NO_TRADE") -> dict:
    from datetime import date
    return {
        "symbol_key": "US:TEST",
        "asof": date(2026, 2, 1),
        "p_up5": p_up5,
        "threshold_buy": 0.7,
        "decision": decision,
        "entry_close": entry,
        "exit_close": exit_,
        "exit_date": date(2026, 2, 22),
    }


def _make_records_n(n: int = 10) -> list[dict]:
    """n >= 5 のサンプルレコード群を生成。calibration_bins が 5 bins になる。"""
    return [_make_record(i / (n - 1), 100.0, 100.0 + (i - n // 2) * 2) for i in range(n)]


class TestAnalyseOutputShape:
    def test_empty_records_returns_error(self):
        result = analyse([], flat_band=3.0)
        assert result.get("error") == "no_data"
        assert result["n"] == 0

    def test_required_top_level_keys(self):
        records = _make_records_n(10)
        result = analyse(records, flat_band=3.0)
        for key in ("meta", "overall", "actual_direction_dist", "direction_stats",
                    "calibration_bins", "contradiction_analysis", "formula_issues"):
            assert key in result, f"missing key: {key}"

    def test_meta_fields(self):
        records = _make_records_n(10)
        result = analyse(records, flat_band=3.0)
        meta = result["meta"]
        assert meta["n_records"] == 10
        assert meta["horizon_days"] == 15
        assert meta["flat_band_pct"] == 3.0

    def test_direction_stats_keys(self):
        records = _make_records_n(10)
        result = analyse(records, flat_band=3.0)
        ds = result["direction_stats"]
        for d in ("Up", "Flat", "Down"):
            assert d in ds

    def test_calibration_bins_count(self):
        """5 bins が生成される (n >= 5 のとき)"""
        records = _make_records_n(10)
        result = analyse(records, flat_band=3.0)
        assert len(result["calibration_bins"]) == 5

    def test_contradiction_keys(self):
        records = [_make_record(0.8, 100.0, 105.0, decision="BUY")] * 3 + \
                  [_make_record(0.3, 100.0, 95.0, decision="NO_TRADE")] * 7
        result = analyse(records, flat_band=3.0)
        ca = result["contradiction_analysis"]
        for key in ("up5_BUY_15d_outcome", "up5_NOTRADE_15d_outcome",
                    "buy_but_down_15d", "notrade_but_up_15d"):
            assert key in ca

    def test_formula_issues_detected(self):
        """変換式バグで Flat が一度も予測されない → formula_issues に CRITICAL が含まれる"""
        records = _make_records_n(10)
        result = analyse(records, flat_band=3.0)
        issues = result["formula_issues"]
        assert isinstance(issues, list)
        assert len(issues) >= 1
        assert any("Flat" in issue or "CRITICAL" in issue for issue in issues)

    def test_overall_accuracy_between_0_and_100(self):
        records = _make_records_n(10)
        result = analyse(records, flat_band=3.0)
        acc = result["overall"]["accuracy_pct"]
        assert 0.0 <= acc <= 100.0

    def test_pearson_r_in_range_or_none(self):
        records = _make_records_n(10)
        result = analyse(records, flat_band=3.0)
        r = result["overall"]["pearson_r_p_up5_vs_return15d"]
        if r is not None:
            assert -1.0 <= r <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# analyse(): _safe_mean の NaN/Inf フィルタ (間接テスト)
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeMeanFiltering:
    def test_extreme_return_does_not_crash(self):
        """exit/entry が極端に大きくても analyse が例外を出さないこと"""
        records = [
            _make_record(0.8, 1.0, 1_000_000.0),   # 99999900% return
            _make_record(0.2, 100.0, 0.001),         # -99.999% return
            _make_record(0.5, 100.0, 103.0),
            _make_record(0.6, 100.0, 97.0),
            _make_record(0.4, 100.0, 101.0),
        ]
        result = analyse(records, flat_band=3.0)
        assert "meta" in result

    def test_pearson_r_with_extreme_values_is_none_or_bounded(self):
        """極端なリターンが含まれても Pearson r は None または [-1, 1] 範囲内"""
        records = [_make_record(0.9, 1.0, 1_000_000.0) for _ in range(5)] + \
                  [_make_record(0.1, 100.0, 1.0) for _ in range(5)]
        result = analyse(records, flat_band=3.0)
        r = result["overall"]["pearson_r_p_up5_vs_return15d"]
        if r is not None:
            assert -1.0 <= r <= 1.0
