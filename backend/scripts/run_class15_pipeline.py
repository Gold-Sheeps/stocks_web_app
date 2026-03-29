"""run_class15_pipeline.py -- class15 更新->観察->レポートの統合実行スクリプト

実行ステップ:
  1. run_class15_batch.py  (--skip-batch で省略可)
  2. monitor_class15.py    (常に実行)
  3. report_class15_observe.py (--skip-report で省略可)

使い方:
  uv run python backend/scripts/run_class15_pipeline.py
  uv run python backend/scripts/run_class15_pipeline.py --asof 2026-03-25 --limit 300
  uv run python backend/scripts/run_class15_pipeline.py \\
    --asof 2026-03-25 --limit 300 \\
    --append-log backend/ml_predictor_data/class15_observe_log.jsonl
  uv run python backend/scripts/run_class15_pipeline.py --skip-batch
  uv run python backend/scripts/run_class15_pipeline.py --skip-report
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

_scripts_dir = Path(__file__).resolve().parent
_backend_dir = _scripts_dir.parent
for _p in [str(_scripts_dir), str(_backend_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_LOG_PATH = str(_backend_dir / "ml_predictor_data" / "class15_observe_log.jsonl")


# ── 依存関数ローダー (テスト時は差し替え可能) ─────────────────────────────────

def _load_real_fns() -> dict[str, Callable]:
    """本番で使う関数を遅延ロードして返す。テストでは差し替え用 dict を渡す。"""
    from run_class15_batch import run_batch
    from monitor_class15 import run as run_monitor, _print_report as print_monitor_report
    from report_class15_observe import run_report
    return {
        "batch":         run_batch,
        "monitor":       run_monitor,
        "report":        run_report,
        "print_monitor": print_monitor_report,
    }


# ── ステップ実装 ──────────────────────────────────────────────────────────────

def _step_batch(fn: Callable, args) -> dict[str, Any]:
    if getattr(args, "skip_batch", False):
        print("[pipeline] Step 1/3 batch  -- SKIPPED (--skip-batch)", flush=True)
        return {"ok": True, "skipped": True}

    print("[pipeline] Step 1/3 batch  -- running ...", flush=True)
    asof_override = None
    if getattr(args, "asof", None):
        asof_override = datetime.strptime(args.asof, "%Y-%m-%d").date()

    result = fn(
        limit=getattr(args, "limit", 200),
        asof_override=asof_override,
        force_tentative=getattr(args, "force_tentative", False),
    )

    if result.get("ok"):
        print(
            f"[pipeline] batch OK: saved={result.get('saved')}  "
            f"errors={result.get('errors')}  mode={result.get('mode')}",
            flush=True,
        )
    else:
        print(f"[pipeline] batch FAILED: {result.get('error')}", flush=True)
    return result


def _step_monitor(fn: Callable, print_fn: Callable, args) -> dict[str, Any]:
    print("[pipeline] Step 2/3 monitor -- running ...", flush=True)
    asof_override = None
    if getattr(args, "asof", None):
        asof_override = datetime.strptime(args.asof, "%Y-%m-%d").date()

    log_path = getattr(args, "append_log", None) or DEFAULT_LOG_PATH
    result = fn(
        weeks=getattr(args, "weeks", 4),
        top_n=getattr(args, "top_n", 30),
        output=None,
        asof_override=asof_override,
        append_log=log_path,
    )

    if result.get("ok"):
        print_fn(result)
    else:
        print(f"[pipeline] monitor FAILED: {result.get('error')}", flush=True)
    return result


def _step_report(fn: Callable, args) -> dict[str, Any]:
    if getattr(args, "skip_report", False):
        print("[pipeline] Step 3/3 report -- SKIPPED (--skip-report)", flush=True)
        return {"ok": True, "skipped": True}

    print("[pipeline] Step 3/3 report -- running ...", flush=True)
    log_path = getattr(args, "append_log", None) or DEFAULT_LOG_PATH
    result = fn(log_path=log_path)
    return result


# ── 最終サマリー表示 ──────────────────────────────────────────────────────────

def _print_pipeline_summary(
    asof: str | None,
    batch_result: dict,
    monitor_result: dict,
    report_result: dict,
) -> None:
    sep = "=" * 60
    print(f"\n{sep}")
    print("class15 pipeline -- summary")
    print(sep)

    # asof
    asof_str = asof or monitor_result.get("asof") or "?"
    print(f"  asof           : {asof_str}")

    # batch
    if batch_result.get("skipped"):
        print("  batch          : SKIPPED")
    elif batch_result.get("ok"):
        print(
            f"  batch          : OK  saved={batch_result.get('saved')}  "
            f"errors={batch_result.get('errors')}  mode={batch_result.get('mode')}"
        )
    else:
        print(f"  batch          : FAILED  ({batch_result.get('error')})")

    # distribution (from monitor)
    dist_list = monitor_result.get("distribution_by_date", [])
    asof_str_mon = monitor_result.get("asof", "?")
    dist0 = next(
        (d for d in dist_list if d.get("asof") == asof_str_mon),
        dist_list[0] if dist_list else {},
    )
    if dist0:
        print(
            f"  distribution   : Up={dist0.get('up_n',0)} ({dist0.get('up_pct',0):.1f}%)  "
            f"Flat={dist0.get('flat_n',0)} ({dist0.get('flat_pct',0):.1f}%)  "
            f"Down={dist0.get('down_n',0)} ({dist0.get('down_pct',0):.1f}%)"
        )
        print(f"  avg_prob_up    : {dist0.get('avg_prob_up') or 0:.3f}  "
              f"std={dist0.get('std_prob_up') or 0:.3f}")
        alerts = dist0.get("alerts", [])
        print(f"  alerts         : {len(alerts)}" + (" (!)" if alerts else ""))
        for a in alerts:
            print(f"    - {a}")
    else:
        print("  distribution   : (no data)")

    # report recommendation
    if report_result.get("skipped"):
        print("  recommend w    : SKIPPED")
        print("  next action    : --")
    elif not report_result.get("ok"):
        print("  recommend w    : FAILED")
        print("  next action    : check report errors")
    else:
        rec = report_result.get("recommendation") or {}
        verdict = rec.get("verdict", "?")
        next_action = rec.get("next_action", rec.get("reason", ""))

        # current _W_C15 from screener_service
        try:
            import importlib
            _svc_path = str(_backend_dir / "app" / "services")
            if _svc_path not in sys.path:
                sys.path.insert(0, _svc_path)
            _mod = importlib.import_module("screener_service")
            current_w = _mod.ScreenerService._W_C15
        except Exception:
            current_w = 0.07  # fallback

        if verdict in ("WAIT", "NO_DATA"):
            print(f"  recommend w    : {verdict}")
            print(f"  next action    : {next_action}")
        else:
            try:
                rec_w = float(verdict)
                if abs(rec_w - current_w) < 0.001:
                    action_str = f"keep _W_C15 = {current_w}"
                else:
                    action_str = f"consider changing _W_C15 from {current_w} to {rec_w}"
            except (ValueError, TypeError):
                action_str = next_action
            print(f"  recommend w    : {verdict}  (current: {current_w})")
            print(f"  next action    : {action_str}")
            print("  (edit screener_service.py _W_C15 to apply)")

    print(f"{sep}\n")


# ── コアパイプライン ───────────────────────────────────────────────────────────

def run_pipeline(args, _fns: dict[str, Callable] | None = None) -> dict[str, Any]:
    """
    パイプラインを実行して結果 dict を返す。

    Args:
        args:  argparse.Namespace (or any object with the expected attributes)
        _fns:  テスト用依存関数差し替え dict。None のとき実際の関数をロードする。
               keys: "batch", "monitor", "report", "print_monitor"

    Returns:
        {
            "ok": bool,
            "batch":   dict,
            "monitor": dict,
            "report":  dict,
        }
    """
    fns = _fns if _fns is not None else _load_real_fns()

    # Step 1: batch
    batch_result = _step_batch(fns["batch"], args)
    if not batch_result.get("ok"):
        print("[pipeline] Stopping: batch step failed.", flush=True)
        return {"ok": False, "failed_step": "batch", "batch": batch_result,
                "monitor": {}, "report": {}}

    # Step 2: monitor
    monitor_result = _step_monitor(fns["monitor"], fns["print_monitor"], args)
    if not monitor_result.get("ok"):
        print("[pipeline] Stopping: monitor step failed.", flush=True)
        return {"ok": False, "failed_step": "monitor", "batch": batch_result,
                "monitor": monitor_result, "report": {}}

    # Step 3: report
    report_result = _step_report(fns["report"], args)
    if not report_result.get("ok"):
        print("[pipeline] Stopping: report step failed.", flush=True)
        return {"ok": False, "failed_step": "report", "batch": batch_result,
                "monitor": monitor_result, "report": report_result}

    _print_pipeline_summary(
        asof=getattr(args, "asof", None),
        batch_result=batch_result,
        monitor_result=monitor_result,
        report_result=report_result,
    )
    return {"ok": True, "batch": batch_result, "monitor": monitor_result, "report": report_result}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="class15 batch->monitor->report 統合実行パイプライン。"
    )
    parser.add_argument("--asof",   default=None, help="基準日 YYYY-MM-DD (batch/monitor 共通)")
    parser.add_argument("--limit",  type=int, default=200, help="batch の最大処理銘柄数 (default: 200)")
    parser.add_argument("--weeks",  type=int, default=4,   help="monitor の表示週数 (default: 4)")
    parser.add_argument("--top-n",  type=int, default=30,  help="monitor の Screener 上位 N (default: 30)")
    parser.add_argument(
        "--append-log", default=DEFAULT_LOG_PATH,
        help=f"JSONL 観察ログ追記パス (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument("--skip-batch",  action="store_true", help="batch を省略して monitor/report のみ実行")
    parser.add_argument("--skip-report", action="store_true", help="report を省略して batch/monitor のみ実行")
    parser.add_argument(
        "--force-tentative", action="store_true",
        help="真モデルが存在しても暫定式を使う (batch に渡す)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    # fix Windows console encoding
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    _args = _parse_args()
    _result = run_pipeline(_args)
    sys.exit(0 if _result.get("ok") else 1)
