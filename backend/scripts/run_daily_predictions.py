"""Batch daily AI prediction runner.

Usage:
    python backend/scripts/run_daily_predictions.py --asof 2026-02-22
    python backend/scripts/run_daily_predictions.py --asof 2026-02-22 --max-tickers 10 --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List

# backend/ をパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.prediction_service import PredictionService
from scripts.predict_up5 import (
    _build_inference_row,
    _latest_artifact_path,
    _load_artifact,
    predict_with_artifact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso_date(value: str) -> "datetime.date":
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"--asof must be YYYY-MM-DD, got: {value}") from exc


def _collect_tickers_from_db(db: Database, max_tickers: int) -> List[str]:
    """watchlist の symbol_key を返す。"""
    rows = db.execute_query("SELECT DISTINCT symbol_key FROM watchlist")
    if not rows:
        return []
    return [r[0] for r in rows if r[0]]


def _collect_tickers_from_canslim() -> List[str]:
    """canslim_breakout_report.csv があれば読む。なければ空リストを返す。"""
    search_dirs = [
        Path(__file__).resolve().parents[1],                      # backend/
        Path(__file__).resolve().parents[1] / "ml_predictor_data",
        Path(__file__).resolve().parents[2],                      # project root
    ]
    for d in search_dirs:
        csv_path = d / "canslim_breakout_report.csv"
        if csv_path.exists():
            try:
                import pandas as pd
                df = pd.read_csv(csv_path)
                # symbol_key または symbol カラムを探す
                col = None
                for candidate in ("symbol_key", "symbol", "ticker"):
                    if candidate in df.columns:
                        col = candidate
                        break
                if col is None:
                    return []
                tickers = [str(v).strip().upper() for v in df[col].dropna().unique()]
                tickers = [f"US:{t}" if ":" not in t else t for t in tickers if t]
                print(f"[INFO] canslim CSV loaded from {csv_path} ({len(tickers)} tickers)")
                return tickers
            except Exception as e:
                print(f"[WARN] Failed to read canslim CSV at {csv_path}: {e}")
    return []


def _collect_tickers_from_volume(db: Database, max_tickers: int) -> List[str]:
    """price_daily の出来高上位銘柄を返す（フォールバック）。"""
    sql = """
        SELECT symbol_key, AVG(volume) AS avg_vol
        FROM price_daily
        WHERE volume > 0
        GROUP BY symbol_key
        HAVING COUNT(*) >= 20
        ORDER BY avg_vol DESC
        LIMIT %s
    """
    rows = db.execute_query(sql, (max_tickers,))
    if not rows:
        return []
    return [r[0] for r in rows if r[0]]


def _build_ticker_list(db: Database, max_tickers: int) -> List[str]:
    """重複排除・最大 max_tickers 件のティッカーリストを組み立てる。"""
    seen: set = set()
    out: List[str] = []

    def add(tickers: List[str]) -> None:
        for t in tickers:
            if t not in seen:
                seen.add(t)
                out.append(t)

    # 1. watchlist
    add(_collect_tickers_from_db(db, max_tickers))

    # 2. canslim CSV
    add(_collect_tickers_from_canslim())

    # 3. 出来高上位（フォールバック）
    if len(out) < max_tickers:
        add(_collect_tickers_from_volume(db, max_tickers))

    return out[:max_tickers]


# ---------------------------------------------------------------------------
# UPSERT SQL
# ---------------------------------------------------------------------------

UPSERT_SQL = """
    INSERT INTO ai_predictions
        (symbol_key, asof, p_up5, threshold_buy, decision, cal_method, artifact_path, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
    ON CONFLICT (symbol_key, asof, cal_method)
    DO UPDATE SET
        p_up5          = EXCLUDED.p_up5,
        threshold_buy  = EXCLUDED.threshold_buy,
        decision       = EXCLUDED.decision,
        cal_method     = EXCLUDED.cal_method,
        artifact_path  = EXCLUDED.artifact_path,
        updated_at     = CURRENT_TIMESTAMP
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily batch AI predictions.")
    parser.add_argument("--asof", required=True, help="As-of date YYYY-MM-DD.")
    parser.add_argument("--max-tickers", type=int, default=500, help="Max tickers to process (default 500).")
    parser.add_argument("--artifact", default=None, help="Artifact path. Default: latest pooled artifact.")
    parser.add_argument(
        "--calibration-method",
        default="none",
        choices=["none", "artifact", "platt", "isotonic"],
        help="Probability calibration for stored p_up5. Default: none (raw model probability).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip DB writes.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    asof = _parse_iso_date(args.asof)
    artifact_path = args.artifact or _latest_artifact_path()
    print(f"[INFO] artifact: {artifact_path}")
    print(f"[INFO] asof:     {asof}")
    print(f"[INFO] dry-run:  {args.dry_run}")

    artifact = _load_artifact(artifact_path)
    cal_method = str(args.calibration_method)

    db = Database()
    if not db.connect():
        print("[ERROR] DB connection failed.")
        return 1

    tickers = _build_ticker_list(db, args.max_tickers)
    total = len(tickers)
    print(f"[INFO] {total} tickers to process.")

    if total == 0:
        print("[WARN] No tickers found. Exiting.")
        db.disconnect()
        return 0

    svc = PredictionService()
    succeeded: List[str] = []
    failed: List[tuple] = []

    for i, ticker in enumerate(tickers):
        try:
            x_df, _ = _build_inference_row(svc, ticker, asof, artifact)
            pred = predict_with_artifact(artifact, x_df, calibration_method=args.calibration_method)
            p_up5 = float(pred["p_up5"])
            threshold = float(pred["threshold_buy"])
            decision = str(pred["action"])
            cal_method = str(pred.get("cal_method_used", cal_method))

            if args.dry_run:
                print(f"[DRY-RUN] {ticker}: p_up5={p_up5:.4f} decision={decision}")
            else:
                db.execute_command(
                    UPSERT_SQL,
                    (ticker, asof, p_up5, threshold, decision, cal_method, artifact_path),
                )
            succeeded.append(ticker)
        except Exception as e:
            failed.append((ticker, str(e)))
            print(f"[FAIL] {ticker}: {e}")

        if (i + 1) % 50 == 0:
            print(f"[PROGRESS] {i+1}/{total} processed ...")

    db.disconnect()

    print(f"\n[DONE] succeeded={len(succeeded)} failed={len(failed)} total={total}")
    if failed:
        print("[FAILED TICKERS]")
        for t, err in failed[:20]:
            print(f"  {t}: {err}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
