"""
test_class15_pipeline.py: run_class15_pipeline の統合ロジックテスト

DB 不要。run_pipeline() に mock 関数を注入して挙動を検証する。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from run_class15_pipeline import run_pipeline, _step_batch, _step_monitor, _step_report


# ── テスト用ヘルパー ──────────────────────────────────────────────────────────

def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        asof=None, limit=200, weeks=4, top_n=30,
        append_log=None, skip_batch=False, skip_report=False,
        force_tentative=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _ok_batch(**kw):
    return {"ok": True, "saved": 10, "errors": 0, "mode": "true_model", "total": 10}


def _ok_monitor(**kw):
    return {
        "ok": True,
        "run_date": "2026-03-25",
        "asof": "2026-03-21",
        "since": "2026-02-21",
        "alerts": [],
        "distribution_by_date": [{
            "asof": "2026-03-21",
            "total": 302,
            "up_n": 246, "up_pct": 81.5,
            "flat_n": 32, "flat_pct": 10.6,
            "down_n": 24, "down_pct": 7.9,
            "flat_down_pct": 18.5,
            "avg_prob_up": 0.462,
            "avg_prob_flat": 0.247,
            "avg_prob_down": 0.291,
            "std_prob_up": 0.103,
            "alerts": [],
        }],
        "screener_top_class15": {"top_n": 30, "coverage": 30,
                                  "direction_dist": {"Up": 18, "Flat": 6, "Down": 6, "N/A": 0}},
        "divergence_analysis": {
            "asof": "2026-03-21", "total_matched": 279,
            "summary": {"buy_and_up_aligned": 194, "buy_but_class15_flat": 7,
                        "buy_but_class15_down": 5, "notrade_but_class15_up": 33,
                        "alignment_rate_pct": 69.5},
            "buy_but_class15_down": [], "notrade_but_class15_up": [],
            "aligned_buy_up_top10": [],
        },
    }


def _ok_report(log_path):
    return {
        "ok": True,
        "n": 1,
        "stability": {"n": 1, "flat_down_mean": 18.5, "flat_down_min": 18.5,
                      "flat_down_max": 18.5, "up_mean": 81.5, "up_max": 81.5,
                      "alert_total": 0, "recent_flat_down_pct": 18.5,
                      "recent_up_pct": 81.5, "recent_alerts": 0,
                      "recent_buy_down_rate_pct": 1.8, "flat_down_trend": "stable"},
        "recommendation": {
            "verdict": "WAIT",
            "recommended_w_c15": None,
            "reason": "observations 1/3 -- not enough yet",
            "next_action": "2 more observations needed",
        },
        "entries": [],
    }


def _noop_print(result):
    pass  # suppress monitor output in tests


def _make_fns(batch=None, monitor=None, report=None, print_monitor=None):
    return {
        "batch":         batch or _ok_batch,
        "monitor":       monitor or _ok_monitor,
        "report":        report or _ok_report,
        "print_monitor": print_monitor or _noop_print,
    }


# ── 通常実行: 3ステップ全て呼ばれる ──────────────────────────────────────────

class TestNormalRun:
    def test_all_three_steps_called(self):
        calls = []
        def track_batch(**kw): calls.append("batch"); return _ok_batch(**kw)
        def track_monitor(**kw): calls.append("monitor"); return _ok_monitor(**kw)
        def track_report(log_path): calls.append("report"); return _ok_report(log_path)

        result = run_pipeline(_args(), _fns=_make_fns(
            batch=track_batch, monitor=track_monitor, report=track_report,
        ))
        assert result["ok"] is True
        assert calls == ["batch", "monitor", "report"]

    def test_returns_all_step_results(self):
        result = run_pipeline(_args(), _fns=_make_fns())
        assert "batch" in result
        assert "monitor" in result
        assert "report" in result


# ── --skip-batch ─────────────────────────────────────────────────────────────

class TestSkipBatch:
    def test_batch_not_called(self):
        def should_not_be_called(**kw):
            pytest.fail("batch should not be called with --skip-batch")

        result = run_pipeline(
            _args(skip_batch=True),
            _fns=_make_fns(batch=should_not_be_called),
        )
        assert result["ok"] is True
        assert result["batch"].get("skipped") is True

    def test_monitor_and_report_still_run(self):
        calls = []
        def track_monitor(**kw): calls.append("monitor"); return _ok_monitor(**kw)
        def track_report(log_path): calls.append("report"); return _ok_report(log_path)

        run_pipeline(
            _args(skip_batch=True),
            _fns=_make_fns(monitor=track_monitor, report=track_report),
        )
        assert "monitor" in calls
        assert "report" in calls


# ── --skip-report ────────────────────────────────────────────────────────────

class TestSkipReport:
    def test_report_not_called(self):
        def should_not_be_called(log_path):
            pytest.fail("report should not be called with --skip-report")

        result = run_pipeline(
            _args(skip_report=True),
            _fns=_make_fns(report=should_not_be_called),
        )
        assert result["ok"] is True
        assert result["report"].get("skipped") is True

    def test_batch_and_monitor_still_run(self):
        calls = []
        def track_batch(**kw): calls.append("batch"); return _ok_batch(**kw)
        def track_monitor(**kw): calls.append("monitor"); return _ok_monitor(**kw)

        run_pipeline(
            _args(skip_report=True),
            _fns=_make_fns(batch=track_batch, monitor=track_monitor),
        )
        assert "batch" in calls
        assert "monitor" in calls


# ── 失敗ステップの検出 ────────────────────────────────────────────────────────

class TestStepFailure:
    def test_batch_failure_stops_pipeline(self):
        calls = []
        def fail_batch(**kw): return {"ok": False, "error": "db_connect_failed"}
        def track_monitor(**kw): calls.append("monitor"); return _ok_monitor(**kw)

        result = run_pipeline(
            _args(),
            _fns=_make_fns(batch=fail_batch, monitor=track_monitor),
        )
        assert result["ok"] is False
        assert result["failed_step"] == "batch"
        assert "monitor" not in calls  # monitor は呼ばれない

    def test_monitor_failure_stops_pipeline(self):
        calls = []
        def fail_monitor(**kw): return {"ok": False, "error": "db_error"}
        def track_report(log_path): calls.append("report"); return _ok_report(log_path)

        result = run_pipeline(
            _args(),
            _fns=_make_fns(monitor=fail_monitor, report=track_report),
        )
        assert result["ok"] is False
        assert result["failed_step"] == "monitor"
        assert "report" not in calls  # report は呼ばれない

    def test_failed_step_name_in_result(self):
        def fail_batch(**kw): return {"ok": False, "error": "timeout"}
        result = run_pipeline(_args(), _fns=_make_fns(batch=fail_batch))
        assert result.get("failed_step") == "batch"


# ── _step_* 単体 ──────────────────────────────────────────────────────────────

class TestStepFunctions:
    def test_step_batch_skip(self):
        called = []
        def track(**kw): called.append(True); return _ok_batch(**kw)
        result = _step_batch(track, _args(skip_batch=True))
        assert result["skipped"] is True
        assert len(called) == 0

    def test_step_report_skip(self):
        called = []
        def track(log_path): called.append(True); return _ok_report(log_path)
        result = _step_report(track, _args(skip_report=True))
        assert result["skipped"] is True
        assert len(called) == 0

    def test_step_batch_passes_limit(self):
        received = {}
        def capture_batch(**kw): received.update(kw); return _ok_batch(**kw)
        _step_batch(capture_batch, _args(limit=50))
        assert received.get("limit") == 50

    def test_step_batch_passes_asof(self):
        from datetime import date
        received = {}
        def capture_batch(**kw): received.update(kw); return _ok_batch(**kw)
        _step_batch(capture_batch, _args(asof="2026-03-21"))
        assert received.get("asof_override") == date(2026, 3, 21)
