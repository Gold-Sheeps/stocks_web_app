"""
validate_class15.py — Phase 3-2: 暫定 class15 事後検証スクリプト

目的:
  - ai_predictions の up5 履歴から class15 方向を導出
  - price_daily で 15 営業日後の実績リターンを取得
  - direction 別成績、calibration、up5 との関係を定量評価
  - final_rank_score 組み込み可否の判断材料を出力

使い方:
  uv run python backend/scripts/validate_class15.py
  uv run python backend/scripts/validate_class15.py --cutoff 2026-03-04 --flat-band 3.0
  uv run python backend/scripts/validate_class15.py --output results/class15_validation.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from scripts.run_class15_batch import _derive_class15_probs, CLASS15_FLAT_BAND_PCT


# ── 定数 ─────────────────────────────────────────────────────────────────────
HORIZON_DAYS  = 15   # 何営業日後を見るか
DEFAULT_FLAT_BAND = CLASS15_FLAT_BAND_PCT  # 実績 Up/Flat/Down 判定の閾値 (%)


# ── データ取得 ────────────────────────────────────────────────────────────────

def fetch_predictions(db: Database, cutoff: date) -> list[dict]:
    """up5 予測レコードを取得 (asof <= cutoff)。"""
    rows = db.execute_query(
        """
        SELECT DISTINCT ON (symbol_key, asof)
            symbol_key, asof, p_up5, threshold_buy, decision
        FROM ai_predictions
        WHERE COALESCE(cal_method, 'none') != 'class15'
          AND p_up5 IS NOT NULL
          AND asof <= %s
        ORDER BY symbol_key, asof, updated_at DESC
        """,
        (cutoff,),
    ) or []
    return [
        {
            "symbol_key": str(r[0]),
            "asof": r[1],
            "p_up5": float(r[2]),
            "threshold_buy": float(r[3]) if r[3] is not None else 0.7,
            "decision": str(r[4]) if r[4] else "NO_TRADE",
        }
        for r in rows
    ]


def fetch_price_15d(db: Database, symbol_key: str, asof: date) -> tuple[float | None, float | None, date | None]:
    """asof 時点の終値と、15 営業日後の終値を返す。"""
    entry_rows = db.execute_query(
        """
        SELECT close FROM price_daily
        WHERE symbol_key = %s AND trading_date <= %s AND close IS NOT NULL
        ORDER BY trading_date DESC LIMIT 1
        """,
        (symbol_key, asof),
    )
    if not entry_rows or entry_rows[0][0] is None:
        return None, None, None
    entry_close = float(entry_rows[0][0])

    exit_rows = db.execute_query(
        """
        SELECT trading_date, close FROM price_daily
        WHERE symbol_key = %s AND trading_date > %s AND close IS NOT NULL
        ORDER BY trading_date
        LIMIT 1 OFFSET %s
        """,
        (symbol_key, asof, HORIZON_DAYS - 1),
    )
    if not exit_rows or exit_rows[0][1] is None:
        return entry_close, None, None
    exit_date  = exit_rows[0][0]
    exit_close = float(exit_rows[0][1])
    return entry_close, exit_close, exit_date


def classify_actual(return_pct: float, flat_band: float) -> int:
    """実績リターン → 0=Down / 1=Flat / 2=Up。"""
    if return_pct > flat_band:
        return 2
    if return_pct < -flat_band:
        return 0
    return 1


# ── 一括取得クエリ（高速化） ─────────────────────────────────────────────────

def fetch_outcomes_bulk(db: Database, cutoff: date) -> list[dict]:
    """
    price_daily を LATERAL JOIN して 15 営業日後 close を一括取得。
    1レコード = 1 (symbol, asof) の予測 + 実績。
    """
    rows = db.execute_query(
        """
        WITH preds AS (
            SELECT DISTINCT ON (symbol_key, asof)
                symbol_key, asof, p_up5, threshold_buy, decision
            FROM ai_predictions
            WHERE COALESCE(cal_method, 'none') != 'class15'
              AND p_up5 IS NOT NULL
              AND asof <= %s
            ORDER BY symbol_key, asof, updated_at DESC
        ),
        entry_prices AS (
            SELECT p.symbol_key, p.asof, p.p_up5, p.threshold_buy, p.decision,
                   pd_e.close AS entry_close
            FROM preds p
            JOIN LATERAL (
                SELECT close FROM price_daily
                WHERE symbol_key = p.symbol_key
                  AND trading_date <= p.asof
                  AND close IS NOT NULL
                ORDER BY trading_date DESC
                LIMIT 1
            ) pd_e ON TRUE
        ),
        exit_prices AS (
            SELECT e.symbol_key, e.asof, e.p_up5, e.threshold_buy, e.decision,
                   e.entry_close,
                   pd_x.trading_date AS exit_date,
                   pd_x.close        AS exit_close
            FROM entry_prices e
            JOIN LATERAL (
                SELECT trading_date, close FROM price_daily
                WHERE symbol_key = e.symbol_key
                  AND trading_date > e.asof
                  AND close IS NOT NULL
                ORDER BY trading_date
                LIMIT 1 OFFSET %s
            ) pd_x ON TRUE
        )
        SELECT symbol_key, asof, p_up5, threshold_buy, decision,
               entry_close, exit_date, exit_close
        FROM exit_prices
        ORDER BY symbol_key, asof
        """,
        (cutoff, HORIZON_DAYS - 1),
    ) or []
    return [
        {
            "symbol_key": str(r[0]),
            "asof": r[1],
            "p_up5": float(r[2]),
            "threshold_buy": float(r[3]) if r[3] is not None else 0.7,
            "decision": str(r[4]) if r[4] else "NO_TRADE",
            "entry_close": float(r[5]),
            "exit_date": r[6],
            "exit_close": float(r[7]),
        }
        for r in rows
        if r[5] is not None and r[7] is not None
    ]


# ── 分析ロジック ──────────────────────────────────────────────────────────────

def analyse(records: list[dict], flat_band: float, cutoff: "date | None" = None) -> dict[str, Any]:
    """
    各レコードに predicted_class / actual_class / return_pct を付与し、
    各種集計を返す。
    """
    enriched = []
    for rec in records:
        ret_pct = (rec["exit_close"] / rec["entry_close"] - 1.0) * 100.0
        prob_up, prob_flat, prob_down, pred_class = _derive_class15_probs(rec["p_up5"])
        actual_class = classify_actual(ret_pct, flat_band)
        enriched.append({
            **rec,
            "return_pct": ret_pct,
            "prob_up": prob_up,
            "prob_flat": prob_flat,
            "prob_down": prob_down,
            "pred_class": pred_class,
            "actual_class": actual_class,
            "pred_direction": {2: "Up", 1: "Flat", 0: "Down"}[pred_class],
            "actual_direction": {2: "Up", 1: "Flat", 0: "Down"}[actual_class],
            "correct": pred_class == actual_class,
        })

    n = len(enriched)
    if n == 0:
        return {"error": "no_data", "n": 0}

    def _safe_mean(vals):
        vals = [v for v in vals if v == v and abs(v) < 1e9]  # NaN/inf filter
        return round(sum(vals) / len(vals), 3) if vals else None

    # ── 1. Direction 別成績 ───────────────────────────────────────────────────
    by_pred: dict[str, list] = defaultdict(list)
    for e in enriched:
        by_pred[e["pred_direction"]].append(e)

    direction_stats: dict[str, Any] = {}
    for d in ("Up", "Flat", "Down"):
        grp = by_pred[d]
        if not grp:
            direction_stats[d] = {"n": 0, "note": "predicted 0 times (formula issue)"}
            continue
        returns = [e["return_pct"] for e in grp]
        correct = [e for e in grp if e["correct"]]
        actual_up   = sum(1 for e in grp if e["actual_direction"] == "Up")
        actual_flat = sum(1 for e in grp if e["actual_direction"] == "Flat")
        actual_down = sum(1 for e in grp if e["actual_direction"] == "Down")
        direction_stats[d] = {
            "n": len(grp),
            "pct_of_total": round(100 * len(grp) / n, 1),
            "accuracy": round(100 * len(correct) / len(grp), 1),
            "mean_return_pct": _safe_mean(returns),
            "median_return_pct": round(sorted([r for r in returns if r==r and abs(r)<1e9])[len([r for r in returns if r==r and abs(r)<1e9])//2], 3) if returns else None,
            "positive_rate_pct": round(100 * sum(1 for r in returns if r == r and r > 0) / max(len(returns), 1), 1),
            "actual_dist": {
                "Up": actual_up,
                "Flat": actual_flat,
                "Down": actual_down,
            },
            "win_rate_pct": round(100 * actual_up / len(grp), 1),   # 実際 Up だった割合
        }

    # ── 2. 全体 accuracy ─────────────────────────────────────────────────────
    overall_accuracy = round(100 * sum(1 for e in enriched if e["correct"]) / n, 1)
    # Baseline: 最多クラスに全部当てた場合
    actual_dist = defaultdict(int)
    for e in enriched:
        actual_dist[e["actual_direction"]] += 1
    majority_class = max(actual_dist, key=lambda k: actual_dist[k])
    baseline_accuracy = round(100 * actual_dist[majority_class] / n, 1)

    # ── 3. 実績 direction 分布 ────────────────────────────────────────────────
    actual_direction_dist = {
        "Up": actual_dist["Up"],
        "Flat": actual_dist["Flat"],
        "Down": actual_dist["Down"],
    }

    # ── 4. Calibration (p_up5 分位 vs 実際 Up 率) ─────────────────────────────
    # p_up5 を 5 分位に分けて各 bin の実際 Up 率を計算
    sorted_by_p = sorted(enriched, key=lambda e: e["p_up5"])
    bin_size = n // 5
    calibration_bins = []
    for i in range(5):
        start = i * bin_size
        end = (i + 1) * bin_size if i < 4 else n
        grp = sorted_by_p[start:end]
        p_vals = [e["p_up5"] for e in grp]
        actual_up_15d = sum(1 for e in grp if e["actual_direction"] == "Up")
        calibration_bins.append({
            "bin": i + 1,
            "p_range": f"{min(p_vals):.3f}-{max(p_vals):.3f}",
            "mean_p_up5": round(sum(p_vals) / len(p_vals), 3),
            "n": len(grp),
            "actual_up15_rate_pct": round(100 * actual_up_15d / len(grp), 1),
            "mean_return_pct": _safe_mean([e["return_pct"] for e in grp]),
        })

    # ── 5. up5 BUY vs NO_TRADE の 15 日成績 (矛盾分析) ────────────────────────
    # "矛盾" の定義: up5=BUY だが 15 日実績は Down、または up5=NO_TRADE だが 15 日実績は Up
    buy_recs   = [e for e in enriched if e["decision"] == "BUY"]
    notrade_recs = [e for e in enriched if e["decision"] != "BUY"]

    def _dir_dist(grp):
        total = len(grp) or 1
        return {
            "n": len(grp),
            "Up_pct":   round(100 * sum(1 for e in grp if e["actual_direction"] == "Up")   / total, 1),
            "Flat_pct": round(100 * sum(1 for e in grp if e["actual_direction"] == "Flat") / total, 1),
            "Down_pct": round(100 * sum(1 for e in grp if e["actual_direction"] == "Down") / total, 1),
            "mean_return_pct": _safe_mean([e["return_pct"] for e in grp]),
        }

    contradiction_analysis = {
        "up5_BUY_15d_outcome":     _dir_dist(buy_recs),
        "up5_NOTRADE_15d_outcome": _dir_dist(notrade_recs),
        "buy_but_down_15d": sum(1 for e in buy_recs    if e["actual_direction"] == "Down"),
        "notrade_but_up_15d": sum(1 for e in notrade_recs if e["actual_direction"] == "Up"),
        "buy_but_down_15d_pct": round(100 * sum(1 for e in buy_recs if e["actual_direction"] == "Down") / max(len(buy_recs), 1), 1),
        "notrade_but_up_15d_pct": round(100 * sum(1 for e in notrade_recs if e["actual_direction"] == "Up") / max(len(notrade_recs), 1), 1),
    }

    # ── 6. 重要発見: Flat が 0 ─────────────────────────────────────────────────
    formula_issues = []
    if direction_stats.get("Flat", {}).get("n", 0) == 0:
        formula_issues.append(
            "CRITICAL: Flat (class 1) は一度も予測されない。"
            "変換式で prob_flat < prob_down が常成立するため。"
            "Up/Down の 2値分類になっている。"
        )

    # ── 7. 相関: p_up5 vs return_pct (外れ値除外: ±50%超をクリップ) ──────────
    try:
        xs = [e["p_up5"] for e in enriched]
        ys = [max(-50.0, min(50.0, e["return_pct"])) for e in enriched]  # clip outliers
        xm = sum(xs) / n
        ym = sum(ys) / n
        cov = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / n
        sx  = (sum((x - xm) ** 2 for x in xs) / n) ** 0.5
        sy  = (sum((y - ym) ** 2 for y in ys) / n) ** 0.5
        pearson_r = round(cov / (sx * sy), 4) if sx > 1e-9 and sy > 1e-9 else None
    except Exception:
        pearson_r = None

    return {
        "meta": {
            "n_records": n,
            "horizon_days": HORIZON_DAYS,
            "flat_band_pct": flat_band,
            "cutoff": str(cutoff),
        },
        "overall": {
            "accuracy_pct": overall_accuracy,
            "baseline_accuracy_pct": baseline_accuracy,
            "majority_class": majority_class,
            "lift_over_baseline_ppt": round(overall_accuracy - baseline_accuracy, 1),
            "pearson_r_p_up5_vs_return15d": pearson_r,
        },
        "actual_direction_dist": actual_direction_dist,
        "direction_stats": direction_stats,
        "calibration_bins": calibration_bins,
        "contradiction_analysis": contradiction_analysis,
        "formula_issues": formula_issues,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def run(cutoff: date, flat_band: float, output: str | None) -> dict:
    db = Database()
    if not db.connect():
        return {"ok": False, "error": "db_connect_failed"}
    try:
        print(f"[validate_class15] Fetching predictions (cutoff={cutoff}, horizon={HORIZON_DAYS}d)...")
        records = fetch_outcomes_bulk(db, cutoff)
        print(f"[validate_class15] Fetched {len(records)} records with realized prices.")
        if not records:
            return {"ok": False, "error": "no_records", "cutoff": str(cutoff)}

        result = analyse(records, flat_band)
        result["ok"] = True

        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            print(f"[validate_class15] Results written to {output}")

        return result
    finally:
        db.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate class15 predictions against actual 15-day outcomes.")
    parser.add_argument("--cutoff", default="2026-03-04", help="Max asof date for validation (YYYY-MM-DD).")
    parser.add_argument("--flat-band", type=float, default=DEFAULT_FLAT_BAND, help=f"Flat band %% (default {DEFAULT_FLAT_BAND}).")
    parser.add_argument("--output", default=None, help="JSON output path.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    cutoff = date.fromisoformat(args.cutoff)
    result = run(cutoff=cutoff, flat_band=args.flat_band, output=args.output)
    # 結果サマリーを stdout に出力
    if result.get("ok"):
        meta = result["meta"]
        ov   = result["overall"]
        print(f"\n{'='*60}")
        print(f"Phase 3-2: class15 Validation Report")
        print(f"{'='*60}")
        print(f"N={meta['n_records']}  horizon={meta['horizon_days']}d  flat_band=±{meta['flat_band_pct']}%  cutoff={meta['cutoff']}")
        print(f"\n[Overall]")
        print(f"  accuracy     : {ov['accuracy_pct']}%  (baseline={ov['baseline_accuracy_pct']}%  lift={ov['lift_over_baseline_ppt']:+.1f}pp)")
        print(f"  Pearson r    : {ov['pearson_r_p_up5_vs_return15d']}  (p_up5 vs 15d return)")
        print(f"  majority cls : {ov['majority_class']}")

        dist = result["actual_direction_dist"]
        n = meta['n_records']
        print(f"\n[Actual direction dist]")
        for d in ("Up", "Flat", "Down"):
            print(f"  {d}: {dist[d]} ({100*dist[d]/n:.1f}%)")

        print(f"\n[Direction stats (predicted → actual)]")
        ds = result["direction_stats"]
        for d in ("Up", "Flat", "Down"):
            s = ds.get(d, {})
            if s.get("n", 0) == 0:
                print(f"  {d}: NOT PREDICTED ({s.get('note','')})")
            else:
                print(f"  {d}: n={s['n']}({s['pct_of_total']}%) acc={s['accuracy']}% win={s['win_rate_pct']}% mean_ret={s['mean_return_pct']:+.2f}%")

        print(f"\n[Calibration: p_up5 bins vs actual Up-15d rate]")
        for b in result["calibration_bins"]:
            print(f"  Bin{b['bin']} p={b['p_range']} mean_p={b['mean_p_up5']:.3f} -> actual_Up15={b['actual_up15_rate_pct']}%  mean_ret={b['mean_return_pct']:+.2f}%")

        ca = result["contradiction_analysis"]
        print(f"\n[up5 BUY vs NO_TRADE: 15d actual outcomes]")
        b  = ca["up5_BUY_15d_outcome"]
        nt = ca["up5_NOTRADE_15d_outcome"]
        print(f"  BUY   (n={b['n']}): Up={b['Up_pct']}% Flat={b['Flat_pct']}% Down={b['Down_pct']}%  mean_ret={b['mean_return_pct']:+.2f}%")
        print(f"  !BUY  (n={nt['n']}): Up={nt['Up_pct']}% Flat={nt['Flat_pct']}% Down={nt['Down_pct']}%  mean_ret={nt['mean_return_pct']:+.2f}%")
        print(f"  BUY だが15d Down: {ca['buy_but_down_15d']} ({ca['buy_but_down_15d_pct']}%)")
        print(f"  !BUY だが15d Up:  {ca['notrade_but_up_15d']} ({ca['notrade_but_up_15d_pct']}%)")

        if result.get("formula_issues"):
            print(f"\n[!!! Formula Issues !!!]")
            for issue in result["formula_issues"]:
                print(f"  {issue}")
        print(f"{'='*60}")
    else:
        print(f"Error: {result}")
    sys.exit(0 if result.get("ok") else 1)
