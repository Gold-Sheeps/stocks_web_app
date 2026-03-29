"""
性質ベーステスト: class15 (15営業日3クラス予測)

絶対値ではなく「確率の合計が1」「クラスが0/1/2のみ」「fallback」「up5共存」を検証。
DB接続不要。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from scripts.run_class15_batch import _derive_class15_probs
from app.services.ai_prediction_service import (
    AiPredictionService,
    CLASS15_HORIZON_DAYS,
    CLASS15_FLAT_BAND_PCT,
    _compute_action_label,
)


# ─────────────────────────────────────────────────────────────
# _derive_class15_probs
# ─────────────────────────────────────────────────────────────

class TestDeriveClass15Probs:
    @pytest.mark.parametrize("p_up5", [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0])
    def test_probs_sum_to_1(self, p_up5):
        prob_up, prob_flat, prob_down, _ = _derive_class15_probs(p_up5)
        total = prob_up + prob_flat + prob_down
        assert abs(total - 1.0) < 1e-6, f"total={total} for p_up5={p_up5}"

    @pytest.mark.parametrize("p_up5", [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0])
    def test_predicted_class_in_valid_range(self, p_up5):
        _, _, _, predicted_class = _derive_class15_probs(p_up5)
        assert predicted_class in (0, 1, 2), f"predicted_class={predicted_class}"

    @pytest.mark.parametrize("p_up5", [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0])
    def test_all_probs_nonnegative(self, p_up5):
        prob_up, prob_flat, prob_down, _ = _derive_class15_probs(p_up5)
        assert prob_up >= 0.0
        assert prob_flat >= 0.0
        assert prob_down >= 0.0

    def test_high_p_up5_gives_class2(self):
        _, _, _, pc = _derive_class15_probs(0.9)
        assert pc == 2, "高い p_up5 は Up (class 2) になるべき"

    def test_low_p_up5_gives_class0(self):
        _, _, _, pc = _derive_class15_probs(0.05)
        assert pc == 0, "低い p_up5 は Down (class 0) になるべき"

    def test_extreme_values_do_not_raise(self):
        for v in (-100.0, 0.0, 1.0, 999.0):
            _derive_class15_probs(v)  # 例外が出ないこと


# ─────────────────────────────────────────────────────────────
# class15 定数
# ─────────────────────────────────────────────────────────────

class TestClass15Constants:
    def test_horizon_days_is_15(self):
        assert CLASS15_HORIZON_DAYS == 15

    def test_flat_band_pct_is_symmetric(self):
        assert CLASS15_FLAT_BAND_PCT == 3.0


# ─────────────────────────────────────────────────────────────
# AiPredictionService._load_latest_class15 (DB なしのユニット)
# ─────────────────────────────────────────────────────────────

class TestLoadLatestClass15NoDb:
    def _make_svc(self):
        return AiPredictionService.__new__(AiPredictionService)

    def test_returns_none_on_empty_rows(self):
        svc = self._make_svc()

        class _FakeDb:
            def execute_query(self, sql, params):
                return []

        result = svc._load_latest_class15(_FakeDb(), "US:NVDA")
        assert result is None

    def test_returns_dict_with_correct_shape(self):
        svc = self._make_svc()
        import datetime

        class _FakeDb:
            def execute_query(self, sql, params):
                # (symbol_key, asof, prob_up, prob_flat, prob_down, predicted_class, horizon_days, flat_band_pct, updated_at)
                return [("US:NVDA", datetime.date(2026, 3, 24), 0.558, 0.199, 0.243, 2, 15, 3.0, datetime.datetime.now())]

        result = svc._load_latest_class15(_FakeDb(), "US:NVDA")
        assert result is not None
        assert result["prediction_type"] == "class15"
        assert result["horizon_days"] == 15
        assert result["prob_up"] == pytest.approx(0.558)
        assert result["prob_flat"] == pytest.approx(0.199)
        assert result["prob_down"] == pytest.approx(0.243)
        assert result["predicted_class"] == 2
        assert result["direction"] == "Up"

    def test_direction_labels(self):
        svc = self._make_svc()
        import datetime

        for pc, expected_dir in [(0, "Down"), (1, "Flat"), (2, "Up")]:
            class _FakeDb:
                def execute_query(self, sql, params):
                    return [("US:NVDA", datetime.date(2026, 3, 24), 0.4, 0.3, 0.3, pc, 15, 3.0, datetime.datetime.now())]

            result = svc._load_latest_class15(_FakeDb(), "US:NVDA")
            assert result["direction"] == expected_dir, f"pc={pc}"

    def test_returns_none_on_exception(self):
        svc = self._make_svc()

        class _FakeDb:
            def execute_query(self, sql, params):
                raise RuntimeError("DB error")

        result = svc._load_latest_class15(_FakeDb(), "US:NVDA")
        assert result is None


# ─────────────────────────────────────────────────────────────
# up5 と class15 共存: get_latest の返却形状チェック (DB なし)
# ─────────────────────────────────────────────────────────────

class TestGetLatestCoexistence:
    def _make_fake_db(self, dt):
        """SELECT_LATEST_SQL (!=class15) と SELECT_LATEST_CLASS15_SQL (=class15) を区別する FakeDb。"""
        class _FakeDb:
            def connect(self):
                return True
            def execute_query(self, sql, params):
                # class15 専用クエリ: 'cal_method = %s' かつ params に 'class15' が含まれる
                if params and len(params) >= 2 and params[1] == 'class15' if isinstance(params, (list, tuple)) else False:
                    return []
                if "cal_method = 'class15'" in sql:
                    return []
                # up5 クエリ: レコードを返す
                return [("US:NVDA", dt.date(2026, 3, 24), 0.558, 0.7, "BUY", "none", None, dt.datetime.now())]
            def disconnect(self):
                pass
        return _FakeDb()

    def test_class15_key_present_even_when_no_class15(self):
        """DB に class15 がなくても 'class15' キーが返り、None である"""
        import datetime as dt
        svc = AiPredictionService.__new__(AiPredictionService)

        import unittest.mock as mock
        with mock.patch("app.services.ai_prediction_service.Database", return_value=self._make_fake_db(dt)):
            with mock.patch.object(svc, "_get_latest_market_environment", return_value=None):
                with mock.patch.object(svc, "_get_latest_run_meta", return_value=None):
                    result = svc.get_latest("US:NVDA")

        assert "class15" in result
        assert result["class15"] is None

    def test_action_label_not_broken_by_class15(self):
        """class15 の追加で action_label が壊れないこと"""
        import datetime as dt
        svc = AiPredictionService.__new__(AiPredictionService)

        import unittest.mock as mock
        with mock.patch("app.services.ai_prediction_service.Database", return_value=self._make_fake_db(dt)):
            with mock.patch.object(svc, "_get_latest_market_environment", return_value=None):
                with mock.patch.object(svc, "_get_latest_run_meta", return_value=None):
                    result = svc.get_latest("US:NVDA")

        assert result.get("action_label") in ("BUY", "HOLD", "SELL", "WATCH", "BLOCKED")
        assert "gate_reasons" in result
        assert "ai_signal_strength" in result


# ─────────────────────────────────────────────────────────────
# ScreenerService._get_latest_class15_predictions (DB なし)
# ─────────────────────────────────────────────────────────────

class TestScreenerClass15Bulk:
    def test_empty_symbols_returns_empty(self):
        from app.services.screener_service import ScreenerService
        svc = ScreenerService.__new__(ScreenerService)
        result = svc._get_latest_class15_predictions([])
        assert result == {}

    def test_exception_returns_empty(self):
        from app.services.screener_service import ScreenerService
        svc = ScreenerService.__new__(ScreenerService)

        class _FakeDb:
            def execute_query(self, *a, **kw):
                raise RuntimeError("DB error")
            def execute_command(self, *a, **kw):
                pass

        svc.db = _FakeDb()
        result = svc._get_latest_class15_predictions(["US:NVDA"])
        assert result == {}
