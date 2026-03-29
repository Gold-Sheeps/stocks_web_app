"""report_class15_observe.py — class15 運用観察ログのトレンド分析・重み推奨レポート

使い方:
  uv run python backend/scripts/report_class15_observe.py
  uv run python backend/scripts/report_class15_observe.py --log backend/ml_predictor_data/class15_observe_log.jsonl
  uv run python backend/scripts/report_class15_observe.py --log ... --output report.json

週次観察フロー:
  1. uv run python backend/scripts/run_class15_batch.py
  2. uv run python backend/scripts/monitor_class15.py --asof <今日> \\
       --append-log backend/ml_predictor_data/class15_observe_log.jsonl
  3. uv run python backend/scripts/report_class15_observe.py  ← このスクリプト
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_LOG = Path(__file__).resolve().parents[1] / "ml_predictor_data" / "class15_observe_log.jsonl"

# ── 推奨判断の閾値 ────────────────────────────────────────────────────────────
FLAT_DOWN_STABLE_THRESHOLD   = 15.0  # Flat+Down% がこれ以上なら「安定」
FLAT_DOWN_CAUTION_THRESHOLD  = 10.0  # Flat+Down% がこれ未満なら「警戒」
UP_HEALTHY_THRESHOLD         = 80.0  # Up% がこれ未満なら「健全」
UP_CAUTION_THRESHOLD         = 85.0  # Up% がこれ超えたら「警戒」
MIN_OBSERVATIONS_FOR_VERDICT = 3     # 判定に必要な最低観察回数


# ── ログ読み込み ──────────────────────────────────────────────────────────────

def load_log(log_path: str | Path) -> list[dict]:
    p = Path(log_path)
    if not p.exists():
        print(f"[report] ログファイルが見つかりません: {p}", flush=True)
        return []
    entries = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


# ── トレンドテーブル表示 ──────────────────────────────────────────────────────

def _print_trend_table(entries: list[dict]) -> None:
    print("\n[1] Direction 分布 推移 (全観察ログ)")
    print(f"  {'#':>3}  {'run_date':12s}  {'asof':12s}  {'total':>6}  "
          f"{'Up%':>6}  {'Flat%':>6}  {'Down%':>6}  {'F+D%':>6}  "
          f"{'avgP(Up)':>9}  {'std':>6}  アラート")
    print("  " + "-" * 102)
    for i, e in enumerate(entries, 1):
        alert_str = " (!)" if e.get("alerts") else ""
        print(
            f"  {i:3d}  {e.get('run_date','?'):12s}  {e.get('asof','?'):12s}  "
            f"{(e.get('total') or 0):6d}  "
            f"{(e.get('up_pct') or 0):6.1f}  "
            f"{(e.get('flat_pct') or 0):6.1f}  "
            f"{(e.get('down_pct') or 0):6.1f}  "
            f"{(e.get('flat_down_pct') or 0):6.1f}  "
            f"{(e.get('avg_prob_up') or 0):9.3f}  "
            f"{(e.get('std_prob_up') or 0):6.3f}"
            f"{alert_str}"
        )


def _print_screener_trend(entries: list[dict]) -> None:
    print("\n[2] Screener 上位 class15 分布 推移")
    print(f"  {'#':>3}  {'asof':12s}  {'top_n':>6}  {'coverage':>9}  "
          f"{'Up%':>6}  {'Flat%':>6}  {'Down%':>6}")
    print("  " + "-" * 65)
    for i, e in enumerate(entries, 1):
        cov = e.get("screener_coverage") or 0
        tn  = e.get("screener_top_n") or 0
        print(
            f"  {i:3d}  {e.get('asof','?'):12s}  {tn:6d}  {cov:9d}  "
            f"{(e.get('screener_up_pct') or 0):6.1f}  "
            f"{(e.get('screener_flat_pct') or 0):6.1f}  "
            f"{(e.get('screener_down_pct') or 0):6.1f}"
        )


def _print_divergence_trend(entries: list[dict]) -> None:
    print("\n[3] up5 vs class15 ズレ推移")
    print(f"  {'#':>3}  {'asof':12s}  {'matched':>8}  {'aligned%':>9}  "
          f"{'BUY+Dn':>8}  {'BUY+Fl':>8}  {'!BUY+Up':>9}")
    print("  " + "-" * 75)
    for i, e in enumerate(entries, 1):
        print(
            f"  {i:3d}  {e.get('asof','?'):12s}  "
            f"{(e.get('div_total_matched') or 0):8d}  "
            f"{(e.get('div_alignment_rate_pct') or 0):9.1f}  "
            f"{(e.get('div_buy_down') or 0):8d}  "
            f"{(e.get('div_buy_flat') or 0):8d}  "
            f"{(e.get('div_notrade_up') or 0):9d}"
        )


# ── 安定性評価 ────────────────────────────────────────────────────────────────

def _compute_stability(entries: list[dict]) -> dict[str, Any]:
    n = len(entries)
    if n == 0:
        return {"n": 0}

    flat_down_vals = [e.get("flat_down_pct") or 0.0 for e in entries]
    up_vals        = [e.get("up_pct") or 0.0 for e in entries]
    alert_counts   = [len(e.get("alerts", [])) for e in entries]
    buy_down_ns    = [e.get("div_buy_down") or 0 for e in entries]
    matched_ns     = [e.get("div_total_matched") or 1 for e in entries]
    buy_down_rates = [bd / max(t, 1) * 100 for bd, t in zip(buy_down_ns, matched_ns)]

    recent = min(n, 3)
    recent_fd        = sum(flat_down_vals[-recent:]) / recent
    recent_up        = sum(up_vals[-recent:]) / recent
    recent_alerts    = sum(alert_counts[-recent:])
    recent_bdr       = sum(buy_down_rates[-recent:]) / recent

    trend = "stable"
    if n >= 2:
        delta = flat_down_vals[-1] - flat_down_vals[0]
        if delta > 2.0:
            trend = "improving"
        elif delta < -2.0:
            trend = "declining"

    return {
        "n": n,
        "flat_down_mean": round(sum(flat_down_vals) / n, 2),
        "flat_down_min":  round(min(flat_down_vals), 2),
        "flat_down_max":  round(max(flat_down_vals), 2),
        "up_mean":        round(sum(up_vals) / n, 2),
        "up_max":         round(max(up_vals), 2),
        "alert_total":    sum(alert_counts),
        "recent_flat_down_pct":      round(recent_fd, 2),
        "recent_up_pct":             round(recent_up, 2),
        "recent_alerts":             recent_alerts,
        "recent_buy_down_rate_pct":  round(recent_bdr, 2),
        "flat_down_trend": trend,
    }


# ── 重み推奨ロジック ──────────────────────────────────────────────────────────

def _recommend_weight(stab: dict) -> dict[str, Any]:
    """
    安定性指標から w_c15 の推奨重みを判定する。

    class15_score = prob_up - prob_down  (-1〜+1)
    c15_component = (class15_score + 1) / 2 * 100  → [0, 100]
    final_rank 変化 = (c15_component - 50) * w_c15

    w=0.05: ±2.5pt (score=±1)  ← 小幅補正
    w=0.07: ±3.5pt              ← 標準
    w=0.10: ±5.0pt              ← 警告機能が明確に効く
    """
    n = stab.get("n", 0)
    if n < MIN_OBSERVATIONS_FOR_VERDICT:
        return {
            "verdict": "WAIT",
            "recommended_w_c15": None,
            "reason": f"観察回数 {n} / {MIN_OBSERVATIONS_FOR_VERDICT} 回 -- まだ判定できません",
            "next_action": f"あと {MIN_OBSERVATIONS_FOR_VERDICT - n} 回以上の観察が必要",
        }

    recent_fd = stab["recent_flat_down_pct"]
    recent_up = stab["recent_up_pct"]
    alerts    = stab["recent_alerts"]
    fd_min    = stab["flat_down_min"]
    up_max    = stab["up_max"]
    fd_trend  = stab["flat_down_trend"]

    reasons = []
    warn_fd_low    = fd_min < FLAT_DOWN_CAUTION_THRESHOLD
    warn_up_high   = up_max > UP_CAUTION_THRESHOLD
    warn_alerts    = alerts > 0
    warn_declining = fd_trend == "declining"
    good_fd_stable  = recent_fd >= FLAT_DOWN_STABLE_THRESHOLD
    good_up_healthy = recent_up < UP_HEALTHY_THRESHOLD

    if warn_fd_low:
        reasons.append(f"Flat+Down の最小値 {fd_min:.1f}% が閾値 {FLAT_DOWN_CAUTION_THRESHOLD}% を下回った回がある")
    if warn_up_high:
        reasons.append(f"Up の最大値 {up_max:.1f}% が閾値 {UP_CAUTION_THRESHOLD}% を超えた回がある")
    if warn_alerts:
        reasons.append(f"直近3回でアラートが {alerts} 件発生")
    if warn_declining:
        reasons.append("Flat+Down が減少傾向 (Up 偏りが強まっている)")
    if good_fd_stable:
        reasons.append(f"最近の Flat+Down={recent_fd:.1f}% ≥ {FLAT_DOWN_STABLE_THRESHOLD}% (安定)")
    if good_up_healthy:
        reasons.append(f"最近の Up={recent_up:.1f}% < {UP_HEALTHY_THRESHOLD}% (健全)")

    serious_warns = sum([warn_fd_low, warn_up_high, warn_declining])
    if warn_alerts and serious_warns >= 2:
        verdict      = "0.05"
        explanation  = "複数の警戒条件が重なっています。保守的な重みで開始してください。"
        next_action  = "追加観察を続け、Flat+Down が安定したら 0.07 → 0.10 へ段階的に引き上げる。"
    elif serious_warns >= 1 or warn_alerts:
        verdict      = "0.05"
        explanation  = "警戒条件が 1 件以上あります。保守的な重みを推奨します。"
        next_action  = "2〜3週間の追加観察後、安定していれば 0.07 へ引き上げを検討。"
    elif good_fd_stable and good_up_healthy:
        verdict      = "0.10"
        explanation  = "Flat+Down が安定し、Up 偏りも許容範囲内です。積極的な重みが使えます。"
        next_action  = "0.10 で本反映し、2週間後に再評価。問題があれば 0.07 に下げる。"
    else:
        verdict      = "0.07"
        explanation  = "おおむね安定していますが Up% がやや高め。標準重みを推奨します。"
        next_action  = "0.07 で本反映後、Flat+Down の推移を引き続き確認。"

    return {
        "verdict": verdict,
        "recommended_w_c15": float(verdict),
        "explanation": explanation,
        "next_action": next_action,
        "evidence": reasons,
    }


# ── class15_score 影響シミュレーション ───────────────────────────────────────

def _print_weight_impact_table() -> None:
    """w_c15=0.05 / 0.07 / 0.10 が final_rank_score に与える変化量を示す。"""
    print("\n[4] class15_score → final_rank_score 変化量シミュレーション")
    print("  class15_score = prob_up - prob_down  (−1〜+1)")
    print(f"  {'score':>7}  {'c15_comp':>9}  {'w=0.05':>8}  {'w=0.07':>8}  {'w=0.10':>8}  意味")
    print("  " + "-" * 68)
    cases = [
        (+1.00, "最強 Up"),
        (+0.60, "強い Up"),
        (+0.30, "Up 傾向"),
        ( 0.00, "中立 (影響ゼロ)"),
        (-0.30, "Down 傾向"),
        (-0.60, "強い Down"),
        (-1.00, "最強 Down"),
    ]
    for score, meaning in cases:
        c15 = (score + 1.0) / 2.0 * 100.0
        d05 = round((c15 - 50.0) * 0.05, 2)
        d07 = round((c15 - 50.0) * 0.07, 2)
        d10 = round((c15 - 50.0) * 0.10, 2)
        print(
            f"  {score:+7.2f}  {c15:9.1f}  "
            f"{d05:+8.2f}  {d07:+8.2f}  {d10:+8.2f}  {meaning}"
        )
    print()
    print("  ※ 変化量は final_rank_score (0〜100) へのポイント加算量")
    print("  ※ class15=None のとき c15_component=50 → 変化ゼロ (フォールバック)")


# ── メインレポート ────────────────────────────────────────────────────────────

def run_report(log_path: str | Path, output: str | None = None) -> dict:
    entries = load_log(log_path)
    n = len(entries)

    print(f"{'='*70}")
    print(f"class15 運用観察レポート  ({n} 件のログエントリ)")
    print(f"{'='*70}")

    if n == 0:
        print("\nログエントリがありません。")
        print("以下のコマンドでログを蓄積してください:\n")
        print("  # 1. class15 予測を DB に保存")
        print("  uv run python backend/scripts/run_class15_batch.py")
        print()
        print("  # 2. 観察ログに追記")
        print("  uv run python backend/scripts/monitor_class15.py \\")
        print("    --asof $(date +%Y-%m-%d) \\")
        print("    --append-log backend/ml_predictor_data/class15_observe_log.jsonl")
        print()
        print("  # 3. このレポートを再実行")
        print("  uv run python backend/scripts/report_class15_observe.py")
        return {"ok": True, "n": 0, "verdict": "NO_DATA"}

    _print_trend_table(entries)
    _print_screener_trend(entries)
    _print_divergence_trend(entries)
    _print_weight_impact_table()

    stab = _compute_stability(entries)
    rec  = _recommend_weight(stab)

    print("\n[5] 安定性サマリ")
    print(f"  観察回数           : {stab['n']}")
    print(f"  Flat+Down% 平均    : {stab['flat_down_mean']:.2f}%  "
          f"(最小 {stab['flat_down_min']:.1f}%  最大 {stab['flat_down_max']:.1f}%)")
    print(f"  Up% 平均           : {stab['up_mean']:.2f}%  (最大 {stab['up_max']:.1f}%)")
    print(f"  Flat+Down トレンド : {stab['flat_down_trend']}")
    print(f"  アラート合計       : {stab['alert_total']} 件")
    print(f"  直近 BUY+Down 率   : {stab['recent_buy_down_rate_pct']:.1f}%  "
          f"(BUY 銘柄のうち class15=Down の割合)")

    print(f"\n{'='*70}")
    print(f"[判定] 推奨 w_c15 = {rec['verdict']}")
    print(f"{'='*70}")
    print(f"  {rec.get('explanation', rec.get('reason', ''))}")
    print(f"  次のアクション: {rec.get('next_action', '')}")
    if rec.get("evidence"):
        print("\n  根拠:")
        for ev in rec["evidence"]:
            print(f"    - {ev}")
    print(f"{'='*70}\n")

    result = {
        "ok": True,
        "n": n,
        "stability": stab,
        "recommendation": rec,
        "entries": entries,
    }
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"[report] 出力 -> {output}")

    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="class15 観察ログのトレンド分析と重み推奨レポート。"
    )
    parser.add_argument(
        "--log", default=str(DEFAULT_LOG),
        help=f"JSONL 観察ログのパス (default: {DEFAULT_LOG})",
    )
    parser.add_argument("--output", default=None, help="JSON レポート出力パス")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = run_report(log_path=args.log, output=args.output)
    sys.exit(0 if result.get("ok") else 1)
