"""monitor_class15.py — class15 週次運用観察スクリプト

確認項目:
  1. predicted_class の Up/Flat/Down 件数推移 (asof 別)
  2. avg_prob_up の推移
  3. Flat/Down が極端に減っていないかアラート (デフォルト: Flat+Down < 10% で警告)
  4. Screener 上位の class15 分布 (p_up5 上位 N 銘柄での class15 偏り)
  5. up5 と class15 のズレ (up5=BUY だが class15=Down 等)

使い方:
  uv run python backend/scripts/monitor_class15.py
  uv run python backend/scripts/monitor_class15.py --weeks 4 --top-n 30
  uv run python backend/scripts/monitor_class15.py --output backend/ml_predictor_data/class15_monitor.json
  uv run python backend/scripts/monitor_class15.py --append-log backend/ml_predictor_data/class15_observe_log.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database


# ── 閾値 ──────────────────────────────────────────────────────────────────────
FLAT_DOWN_WARN_PCT  = 10.0   # Flat+Down 合計が全体の % を下回ったら警告
UP_DOMINANT_WARN_PCT = 85.0  # Up が全体の % を超えたら警告


# ── 観察項目 1+2+3: asof 別 direction 分布 ────────────────────────────────────

def _fetch_distribution_by_date(db: Database, since: date) -> list[dict]:
    rows = db.execute_query(
        """
        SELECT
            asof,
            COUNT(*) FILTER (WHERE predicted_class = 2) AS up_n,
            COUNT(*) FILTER (WHERE predicted_class = 1) AS flat_n,
            COUNT(*) FILTER (WHERE predicted_class = 0) AS down_n,
            COUNT(*)                                     AS total_n,
            ROUND(AVG(prob_up)::numeric,   3)            AS avg_prob_up,
            ROUND(AVG(prob_flat)::numeric, 3)            AS avg_prob_flat,
            ROUND(AVG(prob_down)::numeric, 3)            AS avg_prob_down,
            ROUND(STDDEV(prob_up)::numeric, 3)           AS std_prob_up
        FROM ai_predictions
        WHERE cal_method = %s
          AND asof >= %s
        GROUP BY asof
        ORDER BY asof DESC
        """,
        ("class15", since),
    ) or []

    results = []
    for r in rows:
        total = int(r[4]) or 1
        up_n, flat_n, down_n = int(r[1]), int(r[2]), int(r[3])
        up_pct   = round(100 * up_n   / total, 1)
        flat_pct = round(100 * flat_n / total, 1)
        down_pct = round(100 * down_n / total, 1)
        flat_down_pct = flat_pct + down_pct

        alerts = []
        if flat_down_pct < FLAT_DOWN_WARN_PCT:
            alerts.append(f"WARN: Flat+Down={flat_down_pct:.1f}% < {FLAT_DOWN_WARN_PCT}% 閾値")
        if up_pct > UP_DOMINANT_WARN_PCT:
            alerts.append(f"WARN: Up={up_pct:.1f}% > {UP_DOMINANT_WARN_PCT}% 閾値")

        results.append({
            "asof": str(r[0]),
            "total": total,
            "up_n": up_n,    "up_pct": up_pct,
            "flat_n": flat_n, "flat_pct": flat_pct,
            "down_n": down_n, "down_pct": down_pct,
            "flat_down_pct": flat_down_pct,
            "avg_prob_up":   float(r[5]) if r[5] is not None else None,
            "avg_prob_flat": float(r[6]) if r[6] is not None else None,
            "avg_prob_down": float(r[7]) if r[7] is not None else None,
            "std_prob_up":   float(r[8]) if r[8] is not None else None,
            "alerts": alerts,
        })
    return results


# ── 観察項目 4: Screener 上位 (p_up5 高い銘柄) での class15 分布 ──────────────

def _fetch_screener_top_class15(db: Database, top_n: int, asof: date) -> dict:
    """
    最新 up5 予測で p_up5 上位 top_n 銘柄について、
    同 asof の class15 direction がどう分かれているかを返す。
    """
    rows = db.execute_query(
        """
        WITH top_up5 AS (
            SELECT DISTINCT ON (symbol_key) symbol_key, asof, p_up5, decision
            FROM ai_predictions
            WHERE COALESCE(cal_method, 'none') != 'class15'
              AND p_up5 IS NOT NULL
              AND asof = %s
            ORDER BY symbol_key, asof DESC, updated_at DESC
            LIMIT %s
        )
        SELECT
            t.symbol_key,
            t.p_up5,
            t.decision,
            c.predicted_class,
            c.prob_up,
            c.prob_flat,
            c.prob_down
        FROM top_up5 t
        LEFT JOIN LATERAL (
            SELECT predicted_class, prob_up, prob_flat, prob_down
            FROM ai_predictions
            WHERE symbol_key = t.symbol_key
              AND cal_method = 'class15'
              AND asof = %s
            LIMIT 1
        ) c ON TRUE
        ORDER BY t.p_up5 DESC
        """,
        (asof, top_n, asof),
    ) or []

    _labels = {0: "Down", 1: "Flat", 2: "Up", None: "N/A"}
    records = []
    for r in rows:
        records.append({
            "symbol_key": str(r[0]),
            "p_up5": float(r[1]) if r[1] is not None else None,
            "up5_decision": str(r[2]) if r[2] else "NO_TRADE",
            "class15_direction": _labels.get(int(r[3]) if r[3] is not None else None, "N/A"),
            "prob_up":   round(float(r[4]), 3) if r[4] is not None else None,
            "prob_flat": round(float(r[5]), 3) if r[5] is not None else None,
            "prob_down": round(float(r[6]), 3) if r[6] is not None else None,
        })

    # 集計
    matched  = [x for x in records if x["class15_direction"] != "N/A"]
    dist = {"Up": 0, "Flat": 0, "Down": 0, "N/A": 0}
    for x in records:
        dist[x["class15_direction"]] = dist.get(x["class15_direction"], 0) + 1

    return {
        "asof": str(asof),
        "top_n": top_n,
        "coverage": len(matched),
        "direction_dist": dist,
        "records": records,
    }


# ── 観察項目 5: up5 vs class15 ズレ分析 ──────────────────────────────────────

def _fetch_divergence(db: Database, asof: date) -> dict:
    """
    up5 の BUY/NO_TRADE と class15 の Up/Flat/Down を突き合わせて
    矛盾ケース（BUY だが Down、NO_TRADE だが Up）を集計する。
    """
    rows = db.execute_query(
        """
        WITH up5_latest AS (
            SELECT DISTINCT ON (symbol_key) symbol_key, p_up5, decision
            FROM ai_predictions
            WHERE COALESCE(cal_method, 'none') != 'class15'
              AND p_up5 IS NOT NULL
              AND asof = %s
            ORDER BY symbol_key, asof DESC, updated_at DESC
        ),
        c15_latest AS (
            SELECT DISTINCT ON (symbol_key) symbol_key, predicted_class,
                   prob_up, prob_flat, prob_down,
                   prob_up - prob_down AS class15_score
            FROM ai_predictions
            WHERE cal_method = 'class15'
              AND asof = %s
            ORDER BY symbol_key, asof DESC, updated_at DESC
        )
        SELECT
            u.symbol_key,
            u.p_up5,
            u.decision,
            c.predicted_class,
            c.prob_up,
            c.prob_flat,
            c.prob_down,
            ROUND(c.class15_score::numeric, 3) AS class15_score
        FROM up5_latest u
        JOIN c15_latest c ON c.symbol_key = u.symbol_key
        ORDER BY u.p_up5 DESC
        """,
        (asof, asof),
    ) or []

    _labels = {0: "Down", 1: "Flat", 2: "Up"}
    total = len(rows)
    buy_down = []   # up5=BUY かつ class15=Down → 強い警告シグナル
    buy_flat = []   # up5=BUY かつ class15=Flat → 軽い警告
    notrade_up = [] # up5=NO_TRADE かつ class15=Up → 見落とし候補
    aligned_buy_up = []  # 両方が上昇方向 → 強い候補

    for r in rows:
        decision = str(r[2]) if r[2] else "NO_TRADE"
        pred_class = int(r[3]) if r[3] is not None else None
        direction = _labels.get(pred_class, "N/A")
        rec = {
            "symbol_key": str(r[0]),
            "p_up5": round(float(r[1]), 3) if r[1] is not None else None,
            "up5_decision": decision,
            "class15_direction": direction,
            "prob_up":    round(float(r[4]), 3) if r[4] is not None else None,
            "prob_flat":  round(float(r[5]), 3) if r[5] is not None else None,
            "prob_down":  round(float(r[6]), 3) if r[6] is not None else None,
            "class15_score": round(float(r[7]), 3) if r[7] is not None else None,
        }
        if decision == "BUY" and direction == "Down":
            buy_down.append(rec)
        elif decision == "BUY" and direction == "Flat":
            buy_flat.append(rec)
        elif decision != "BUY" and direction == "Up":
            notrade_up.append(rec)
        elif decision == "BUY" and direction == "Up":
            aligned_buy_up.append(rec)

    # buy_down を class15_score 昇順（最も Down 方向が強い順）でソート
    buy_down.sort(key=lambda x: x["class15_score"] or 0)
    # notrade_up を class15_score 降順（最も Up 方向が強い順）でソート
    notrade_up.sort(key=lambda x: -(x["class15_score"] or 0))

    return {
        "asof": str(asof),
        "total_matched": total,
        "summary": {
            "buy_and_up_aligned":   len(aligned_buy_up),
            "buy_but_class15_flat": len(buy_flat),
            "buy_but_class15_down": len(buy_down),
            "notrade_but_class15_up": len(notrade_up),
            "alignment_rate_pct": round(100 * len(aligned_buy_up) / max(total, 1), 1),
        },
        "buy_but_class15_down":    buy_down[:20],    # 最大 20 件 (警告候補)
        "notrade_but_class15_up":  notrade_up[:20],  # 最大 20 件 (見落とし候補)
        "aligned_buy_up_top10": sorted(aligned_buy_up, key=lambda x: -(x["class15_score"] or 0))[:10],
    }


# ── main ──────────────────────────────────────────────────────────────────────

def _make_log_entry(result: dict) -> dict:
    """run() の結果から JSONL 用コンパクトエントリを生成する。"""
    asof_str = result.get("asof")
    dist_list = result.get("distribution_by_date", [])
    # run の asof に一致する行を優先。なければ最新行を使う
    dist0 = next((d for d in dist_list if d.get("asof") == asof_str), dist_list[0] if dist_list else {})
    st = result.get("screener_top_class15", {})
    stdd = st.get("direction_dist", {})
    stcov = st.get("coverage", 0)
    div = result.get("divergence_analysis", {})
    sm = div.get("summary", {})

    # screener_top の Up/Flat/Down %
    st_up   = round(100 * stdd.get("Up",   0) / max(stcov, 1), 1) if stcov else None
    st_flat = round(100 * stdd.get("Flat", 0) / max(stcov, 1), 1) if stcov else None
    st_down = round(100 * stdd.get("Down", 0) / max(stcov, 1), 1) if stcov else None

    return {
        "run_date":          result.get("run_date"),
        "asof":              result.get("asof"),
        # ── 全体分布 ──
        "total":             dist0.get("total"),
        "up_n":              dist0.get("up_n"),
        "up_pct":            dist0.get("up_pct"),
        "flat_n":            dist0.get("flat_n"),
        "flat_pct":          dist0.get("flat_pct"),
        "down_n":            dist0.get("down_n"),
        "down_pct":          dist0.get("down_pct"),
        "flat_down_pct":     dist0.get("flat_down_pct"),
        "avg_prob_up":       dist0.get("avg_prob_up"),
        "avg_prob_flat":     dist0.get("avg_prob_flat"),
        "avg_prob_down":     dist0.get("avg_prob_down"),
        "std_prob_up":       dist0.get("std_prob_up"),
        "alerts":            dist0.get("alerts", []),
        # ── Screener 上位 ──
        "screener_top_n":    st.get("top_n"),
        "screener_coverage": stcov,
        "screener_up_pct":   st_up,
        "screener_flat_pct": st_flat,
        "screener_down_pct": st_down,
        # ── Divergence ──
        "div_total_matched":     div.get("total_matched"),
        "div_aligned_buy_up":    sm.get("buy_and_up_aligned"),
        "div_buy_flat":          sm.get("buy_but_class15_flat"),
        "div_buy_down":          sm.get("buy_but_class15_down"),
        "div_notrade_up":        sm.get("notrade_but_class15_up"),
        "div_alignment_rate_pct": sm.get("alignment_rate_pct"),
    }


def run(weeks: int, top_n: int, output: str | None, asof_override: date | None = None,
        append_log: str | None = None) -> dict:
    db = Database()
    if not db.connect():
        return {"ok": False, "error": "db_connect_failed"}

    asof = asof_override or date.today()
    since = asof - timedelta(weeks=weeks)

    try:
        print(f"[monitor_class15] asof={asof}  since={since}  top_n={top_n}", flush=True)

        dist_by_date  = _fetch_distribution_by_date(db, since)
        screener_top  = _fetch_screener_top_class15(db, top_n, asof)
        divergence    = _fetch_divergence(db, asof)

        # 最新 asof のアラートをまとめる
        all_alerts: list[str] = []
        if dist_by_date:
            all_alerts.extend(dist_by_date[0].get("alerts", []))

        result: dict[str, Any] = {
            "ok": True,
            "run_date": str(date.today()),
            "asof": str(asof),
            "since": str(since),
            "alerts": all_alerts,
            "distribution_by_date": dist_by_date,
            "screener_top_class15": screener_top,
            "divergence_analysis": divergence,
        }

        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(
                json.dumps(result, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            print(f"[monitor_class15] output -> {output}", flush=True)

        if append_log:
            log_path = Path(append_log)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = _make_log_entry(result)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            print(f"[monitor_class15] log entry appended -> {append_log}", flush=True)

        return result
    finally:
        db.disconnect()


def _print_report(r: dict) -> None:
    print(f"\n{'='*65}")
    print(f"class15 週次観察レポート  run={r['run_date']}  asof={r['asof']}")
    print(f"{'='*65}")

    if r.get("alerts"):
        print("\n[!!! ALERTS !!!]")
        for a in r["alerts"]:
            print(f"  {a}")

    print("\n[1+2+3] Direction 分布 推移 (asof 別)")
    print(f"  {'asof':12s} {'total':>6}  Up%    Flat%  Down%  Flat+Down%  avg_P(Up)  std")
    for d in r.get("distribution_by_date", []):
        alert_flag = " (!)" if d["alerts"] else ""
        print(
            f"  {d['asof']:12s} {d['total']:6d}  "
            f"{d['up_pct']:5.1f}  {d['flat_pct']:5.1f}  {d['down_pct']:5.1f}  "
            f"{d['flat_down_pct']:10.1f}  "
            f"{d['avg_prob_up'] or 0:.3f}      "
            f"{d['std_prob_up'] or 0:.3f}{alert_flag}"
        )

    st = r.get("screener_top_class15", {})
    dist = st.get("direction_dist", {})
    cov  = st.get("coverage", 0)
    tn   = st.get("top_n", 0)
    print(f"\n[4] Screener 上位 top_n={tn} の class15 分布 (coverage={cov}/{tn})")
    for d_, n_ in dist.items():
        pct = round(100 * n_ / max(cov, 1), 1) if d_ != "N/A" else "-"
        print(f"  {d_:6s}: {n_:4d}  ({pct}%)")

    div = r.get("divergence_analysis", {})
    sm  = div.get("summary", {})
    print(f"\n[5] up5 vs class15 ズレ分析 (asof={div.get('asof')}  matched={div.get('total_matched')})")
    print(f"  BUY & Up 一致          : {sm.get('buy_and_up_aligned', 0):4d}  (alignment={sm.get('alignment_rate_pct')}%)")
    print(f"  BUY だが class15=Flat  : {sm.get('buy_but_class15_flat', 0):4d}")
    print(f"  BUY だが class15=Down  : {sm.get('buy_but_class15_down', 0):4d}  ← 警告候補")
    print(f"  !BUY だが class15=Up   : {sm.get('notrade_but_class15_up', 0):4d}  ← 見落とし候補")

    buy_down = div.get("buy_but_class15_down", [])
    if buy_down:
        print(f"\n  [BUY だが class15=Down 上位] (class15_score 昇順)")
        for x in buy_down[:10]:
            print(
                f"    {x['symbol_key']:15s}  p_up5={x['p_up5']:.3f}  "
                f"class15_score={x['class15_score']:+.3f}  "
                f"P(Down)={x['prob_down']:.3f}"
            )

    notrade_up = div.get("notrade_but_class15_up", [])
    if notrade_up:
        print(f"\n  [!BUY だが class15=Up 上位] (class15_score 降順)")
        for x in notrade_up[:10]:
            print(
                f"    {x['symbol_key']:15s}  p_up5={x['p_up5']:.3f}  "
                f"class15_score={x['class15_score']:+.3f}  "
                f"P(Up)={x['prob_up']:.3f}"
            )

    print(f"{'='*65}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weekly class15 distribution monitor.")
    parser.add_argument("--weeks",  type=int, default=4,    help="何週間分の asof を表示するか (default: 4)")
    parser.add_argument("--top-n",  type=int, default=30,   help="Screener 上位 N 銘柄 (default: 30)")
    parser.add_argument("--asof",       default=None,  help="基準日 YYYY-MM-DD (default: today)")
    parser.add_argument("--output",     default=None,  help="JSON 出力パス (フル結果)")
    parser.add_argument("--append-log", default=None,  help="JSONL 観察ログ追記パス (週次蓄積用)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asof_override = None
    if args.asof:
        from datetime import datetime
        asof_override = datetime.strptime(args.asof, "%Y-%m-%d").date()
    result = run(
        weeks=args.weeks,
        top_n=args.top_n,
        output=args.output,
        asof_override=asof_override,
        append_log=args.append_log,
    )
    if result.get("ok"):
        _print_report(result)
    else:
        print(f"Error: {result}")
    sys.exit(0 if result.get("ok") else 1)
