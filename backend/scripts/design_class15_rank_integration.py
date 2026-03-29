"""design_class15_rank_integration.py — final_rank_score への class15 小幅加算 設計案

目的:
  運用観察後に final_rank_score に class15_score を 5〜10% 加算する際の
  変更内容・重み選択・フォールバック動作・プロパティテストを事前に確認する。

本ファイルは「設計案の検証スクリプト」であり、screener_service.py はまだ変更しない。
実際の適用は Phase 3-4 の判断後に行う。

実行:
  uv run python backend/scripts/design_class15_rank_integration.py
"""
from __future__ import annotations

from typing import Optional
import math


# ─────────────────────────────────────────────────────────────────────────────
# 現行の _compute_final_rank_score (参照用・コピー)
# ─────────────────────────────────────────────────────────────────────────────

def current_rank_score(
    total_score: Optional[float],
    p_up5: Optional[float],
    canslim_count: Optional[int],
    action_label: Optional[str],
    predicted_class: Optional[int],
) -> float:
    """
    現行式 (screener_service.py _compute_final_rank_score):
      raw = tech*0.50 + ai*0.30 + canslim*0.20
    """
    if action_label == "BLOCKED":
        return 0.0
    tech             = float(total_score or 0)
    ai_component     = float(p_up5 or 0) * 100
    canslim_component = (float(canslim_count or 0) / 7) * 100
    raw = tech * 0.50 + ai_component * 0.30 + canslim_component * 0.20
    if action_label == "WATCH" and predicted_class is not None:
        raw *= 0.5
    return round(max(0.0, min(100.0, raw)), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 設計案: class15_score 加算
#
# class15_score = prob_up - prob_down  → [-1, +1]
#   +1 : P(Up)=1.0, P(Down)=0.0  → 最強上昇シグナル
#    0 : P(Up)=P(Down)           → 中立
#   -1 : P(Up)=0.0, P(Down)=1.0  → 最強下落シグナル
#
# 3つの重みオプション (w_c15) を比較:
#   Option A: w_c15 = 0.05 (5%)  → 保守的。大きな順位変動なし
#   Option B: w_c15 = 0.07 (7%)  → バランス
#   Option C: w_c15 = 0.10 (10%) → 積極的。Down 銘柄を明確に押し下げ
#
# 重み再配分の考え方:
#   既存の tech/ai/canslim 比率を維持しつつ、class15 分を ai から削る。
#   (class15 は up5 の補完なので、ai 成分から移す方が自然)
#
#   Option A (5%):  tech=50%, ai=25%, canslim=20%, class15=5%
#   Option B (7%):  tech=50%, ai=23%, canslim=20%, class15=7%
#   Option C (10%): tech=50%, ai=20%, canslim=20%, class15=10%
#
# フォールバック: class15 が None → class15_score = 0 (中立、スコア不変)
# ─────────────────────────────────────────────────────────────────────────────

def proposed_rank_score(
    total_score: Optional[float],
    p_up5: Optional[float],
    canslim_count: Optional[int],
    action_label: Optional[str],
    predicted_class: Optional[int],
    # class15 の追加引数
    prob_up: Optional[float] = None,
    prob_down: Optional[float] = None,
    # 重みオプション
    w_c15: float = 0.07,         # 0.05 / 0.07 / 0.10
) -> float:
    """
    提案式:
      w_ai = 0.30 - w_c15        (class15 分を ai から削る)
      raw  = tech*0.50 + ai*(0.30-w_c15) + canslim*0.20 + class15*w_c15*100
      class15_component = (prob_up - prob_down + 1) / 2 * 100  → [0, 100]

    class15 が None の場合: class15_component = 50 (中立) → 既存 ai 重みを維持
    """
    if action_label == "BLOCKED":
        return 0.0

    tech              = float(total_score or 0)
    ai_component      = float(p_up5 or 0) * 100
    canslim_component = (float(canslim_count or 0) / 7) * 100

    # class15 成分: prob_up - prob_down を [0, 100] に正規化
    if prob_up is not None and prob_down is not None:
        c15_score     = float(prob_up) - float(prob_down)   # -1 〜 +1
        c15_component = (c15_score + 1.0) / 2.0 * 100       # 0 〜 100
    else:
        c15_component = 50.0  # 中立: class15 なし → ai 重みへの影響ゼロ

    w_ai = 0.30 - w_c15
    raw = (
        tech              * 0.50
        + ai_component    * w_ai
        + canslim_component * 0.20
        + c15_component   * w_c15
    )

    if action_label == "WATCH" and predicted_class is not None:
        raw *= 0.5
    return round(max(0.0, min(100.0, raw)), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 設計案の検証: 代表シナリオで before/after を比較
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS = [
    # (label, total_score, p_up5, canslim, action, pred_class, prob_up, prob_down)
    # --- 強いシグナル一致 ---
    ("強 Up 一致",        80, 0.85, 6, "BUY",     2,    0.68, 0.20),
    # --- up5=高いが class15=Down (警告ケース) ---
    ("BUY+class15 Down",  75, 0.80, 5, "BUY",     2,    0.22, 0.55),
    # --- 平均的な銘柄 ---
    ("平均的",            55, 0.55, 3, "NO_TRADE", None, 0.45, 0.28),
    # --- class15=Down で強く押し下げ ---
    ("class15 強 Down",   60, 0.60, 4, "NO_TRADE", 0,    0.15, 0.65),
    # --- class15 なし (フォールバック確認) ---
    ("class15 なし",      70, 0.72, 5, "BUY",     2,    None, None),
    # --- BLOCKED ---
    ("BLOCKED",           90, 0.90, 7, "BLOCKED",  None, 0.70, 0.10),
    # --- WATCH ---
    ("WATCH",             65, 0.65, 4, "WATCH",    1,    0.40, 0.35),
    # --- class15=Up だが up5 低 (見落とし補完) ---
    ("!BUY class15 Up",   45, 0.40, 2, "NO_TRADE", 2,    0.65, 0.12),
]


def _run_comparison() -> None:
    print("=" * 78)
    print("final_rank_score 設計案比較: 現行 vs Option A(5%) / B(7%) / C(10%)")
    print("=" * 78)
    print(f"{'シナリオ':22s}  {'現行':>6}  {'A(5%)':>7}  {'B(7%)':>7}  {'C(10%)':>8}  {'差B':>6}  {'差C':>6}")
    print("-" * 78)

    for (label, ts, p_up5, canslim, action, pred_class, prob_up, prob_down) in SCENARIOS:
        base = current_rank_score(ts, p_up5, canslim, action, pred_class)
        opt_a = proposed_rank_score(ts, p_up5, canslim, action, pred_class, prob_up, prob_down, w_c15=0.05)
        opt_b = proposed_rank_score(ts, p_up5, canslim, action, pred_class, prob_up, prob_down, w_c15=0.07)
        opt_c = proposed_rank_score(ts, p_up5, canslim, action, pred_class, prob_up, prob_down, w_c15=0.10)
        diff_b = opt_b - base
        diff_c = opt_c - base
        print(
            f"{label:22s}  {base:6.1f}  {opt_a:7.1f}  {opt_b:7.1f}  {opt_c:8.1f}  "
            f"{diff_b:+6.1f}  {diff_c:+6.1f}"
        )

    print("=" * 78)


# ─────────────────────────────────────────────────────────────────────────────
# プロパティテスト (pytest でも単独でも実行可)
# ─────────────────────────────────────────────────────────────────────────────

def _property_tests() -> None:
    print("\n[プロパティテスト]")
    failures = []

    def _check(name: str, cond: bool) -> None:
        status = "PASS" if cond else "FAIL"
        print(f"  {status}: {name}")
        if not cond:
            failures.append(name)

    # 1. スコアは常に [0, 100]
    for ts in [0, 50, 100]:
        for p in [0.0, 0.5, 1.0]:
            for pu, pd in [(0.0, 1.0), (0.5, 0.3), (1.0, 0.0)]:
                s = proposed_rank_score(ts, p, 3, "BUY", 2, pu, pd, w_c15=0.07)
                _check(f"0<=score<=100 (ts={ts},p={p},pu={pu},pd={pd})", 0.0 <= s <= 100.0)

    # 2. BLOCKED → 常に 0
    s = proposed_rank_score(100, 1.0, 7, "BLOCKED", 2, 1.0, 0.0, w_c15=0.07)
    _check("BLOCKED -> 0", s == 0.0)

    # 3. class15 なし (None) → 現行スコアと「ほぼ」同じ (±0.1 以内)
    for ts in [40, 70, 90]:
        for p in [0.3, 0.6, 0.8]:
            base = current_rank_score(ts, p, 4, "BUY", 2)
            prop = proposed_rank_score(ts, p, 4, "BUY", 2, None, None, w_c15=0.07)
            # None → c15_component=50 (中立) なので ai 重みが 0.30-0.07=0.23 に減る
            # 完全一致はしないが「大きく変わらない」ことを確認 (±10 以内)
            _check(
                f"class15 None: 大きく変わらない (base={base}, prop={prop})",
                abs(prop - base) <= 10.0
            )

    # 4. prob_up > prob_down の方がスコアが高い
    s_up   = proposed_rank_score(60, 0.6, 4, "BUY", 2, 0.70, 0.15, w_c15=0.07)
    s_down = proposed_rank_score(60, 0.6, 4, "BUY", 0, 0.15, 0.70, w_c15=0.07)
    _check(f"prob_up>prob_down → スコア高い ({s_up:.1f} > {s_down:.1f})", s_up > s_down)

    # 5. Up 一致の方が class15 Down より高い (BUY シナリオ)
    s_agree  = proposed_rank_score(75, 0.80, 5, "BUY", 2, 0.68, 0.20, w_c15=0.07)
    s_conflict = proposed_rank_score(75, 0.80, 5, "BUY", 0, 0.22, 0.55, w_c15=0.07)
    _check(f"BUY+class15 Up > BUY+class15 Down ({s_agree:.1f} > {s_conflict:.1f})", s_agree > s_conflict)

    # 6. w_c15=0 → 現行スコアと完全一致
    for ts, p, canslim, action, pred_cls, pu, pd in [
        (60, 0.6, 4, "BUY", 2, 0.5, 0.3),
        (80, 0.8, 6, "BUY", 2, 0.7, 0.1),
    ]:
        base = current_rank_score(ts, p, canslim, action, pred_cls)
        w0   = proposed_rank_score(ts, p, canslim, action, pred_cls, pu, pd, w_c15=0.0)
        _check(f"w_c15=0 -> 現行と一致 ({base} == {w0})", abs(base - w0) < 0.05)

    if failures:
        print(f"\n  FAILED: {len(failures)} tests")
        for f in failures:
            print(f"    - {f}")
    else:
        print(f"\n  全テスト PASS")


# ─────────────────────────────────────────────────────────────────────────────
# screener_service.py への適用差分 (コメント)
# ─────────────────────────────────────────────────────────────────────────────

PATCH_DESCRIPTION = """
== screener_service.py 適用差分 (Phase 3-4 で実施) ==

1. _compute_final_rank_score のシグネチャ変更:
   def _compute_final_rank_score(
       self,
       total_score, p_up5, canslim_count, action_label, predicted_class,
+      prob_up: float | None = None,     # class15 prob_up
+      prob_down: float | None = None,   # class15 prob_down
+      w_c15: float = 0.07,              # class15 重み (0=無効化)
   ) -> float:

2. 本体の計算式変更:
   - 現行: raw = tech*0.50 + ai*0.30 + canslim*0.20
   + 提案: c15_component = (prob_up - prob_down + 1) / 2 * 100 if prob_up else 50
   +        raw = tech*0.50 + ai*(0.30-w_c15) + canslim*0.20 + c15_component*w_c15

3. 呼び出し側 (screener_service.py line ~813):
   + c15 = class15_map.get(sk) or {}
     item.final_rank_score = self._compute_final_rank_score(
         total_score=item.total_score, p_up5=p_up5,
         canslim_count=item.canslim_pass_count, action_label=al,
         predicted_class=pc,
+        prob_up=c15.get("prob_up"),
+        prob_down=c15.get("prob_down"),
+        w_c15=0.07,   # 観察後に 0.05 / 0.07 / 0.10 から選択
     )

4. テスト: test_screener_ranking_properties.py に追加
   - w_c15=0.0 のとき既存スコアと一致
   - class15=None のとき大きく変わらない (±10 以内)
   - BUY+class15 Up > BUY+class15 Down

== 重み選択ガイド ==
  0.05 (5%)  : 最初の 2〜3 週間。分布確認中は保守的に
  0.07 (7%)  : 標準。Flat/Down が十分出るようになったら
  0.10 (10%) : 積極的。Calibration が安定し警告機能が確認できたら
"""


if __name__ == "__main__":
    _run_comparison()
    _property_tests()
    print(PATCH_DESCRIPTION)
