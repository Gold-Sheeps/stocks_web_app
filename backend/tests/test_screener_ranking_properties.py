"""
性質ベーステスト: final_rank_score / action_label / _compute_action_label

絶対スコアではなく「順序・性質・不変条件」を検証する。
係数を変えても成立すべきロジック上の保証を担保する。
"""
import sys
from pathlib import Path

# backend/ をパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.services.screener_service import ScreenerService
from app.services.ai_prediction_service import _compute_action_label


@pytest.fixture
def svc():
    """ScreenerService を DB 接続なしで使う（pure logic メソッドのみテスト）"""
    return ScreenerService.__new__(ScreenerService)


# ─────────────────────────────────────────────────────────────
# _compute_action_label
# ─────────────────────────────────────────────────────────────

class TestComputeActionLabel:
    def test_buy_passes_when_no_gate(self):
        assert _compute_action_label("BUY", None) == "BUY"

    def test_gate_blocked_becomes_blocked(self):
        assert _compute_action_label("GATE_BLOCKED", "新規買い停止中（市場環境）") == "BLOCKED"

    def test_caution_becomes_watch(self):
        assert _compute_action_label("CAUTION", "注意") == "WATCH"

    def test_no_trade_variations_become_hold(self):
        for dec in ("HOLD", "WAIT", "NO TRADE", "NO_TRADE"):
            assert _compute_action_label(dec, None) == "HOLD", f"failed for decision={dec!r}"

    def test_sell_passes_through(self):
        assert _compute_action_label("SELL", None) == "SELL"

    def test_none_decision_becomes_watch(self):
        assert _compute_action_label(None, None) == "WATCH"

    def test_gate_blocked_overrides_decision_regardless(self):
        # decision が何であれ gate_reason に「停止」があれば BLOCKED
        assert _compute_action_label("HOLD", "新規買い停止中（市場環境）") == "BLOCKED"


# ─────────────────────────────────────────────────────────────
# _compute_screener_ai_labels
# ─────────────────────────────────────────────────────────────

class TestScreenerAiLabels:
    def test_buy_passes_on_gate_on(self, svc):
        al, gr, pc = svc._compute_screener_ai_labels("ON", 0.72, 0.5, "BUY")
        assert al == "BUY"
        assert gr == []

    def test_buy_blocked_on_gate_off(self, svc):
        al, gr, pc = svc._compute_screener_ai_labels("OFF", 0.72, 0.5, "BUY")
        assert al == "BLOCKED"
        assert "market_regime_blocked" in gr

    def test_buy_watched_on_gate_half_low_prob(self, svc):
        al, gr, pc = svc._compute_screener_ai_labels("HALF", 0.55, 0.5, "BUY")
        assert al == "WATCH"
        assert "market_regime_caution" in gr
        assert "prob_below_caution_threshold" in gr

    def test_buy_passes_on_gate_half_high_prob(self, svc):
        """HALF でも p >= 0.6 なら BUY 通過"""
        al, gr, pc = svc._compute_screener_ai_labels("HALF", 0.65, 0.5, "BUY")
        assert al == "BUY"
        assert gr == []

    def test_no_prediction_returns_watch_no_gate(self, svc):
        al, gr, pc = svc._compute_screener_ai_labels("ON", None, None, None)
        assert al == "WATCH"
        assert gr == []
        assert pc is None

    def test_predicted_class_from_threshold(self, svc):
        """threshold 以上は class=2, 0.3未満は class=0, その間は class=1"""
        _, _, pc_up   = svc._compute_screener_ai_labels("ON", 0.70, 0.5, "BUY")
        _, _, pc_flat = svc._compute_screener_ai_labels("ON", 0.40, 0.5, "NO_TRADE")
        _, _, pc_down = svc._compute_screener_ai_labels("ON", 0.20, 0.5, "NO_TRADE")
        assert pc_up   == 2
        assert pc_flat == 1
        assert pc_down == 0


# ─────────────────────────────────────────────────────────────
# _compute_final_rank_score — 性質（順序）テスト
# ─────────────────────────────────────────────────────────────

class TestFinalRankScoreProperties:

    def score(self, svc, total, p_up5, canslim, action, pc):
        return svc._compute_final_rank_score(total, p_up5, canslim, action, pc)

    def test_blocked_is_always_zero(self, svc):
        """BLOCKED は何があっても 0"""
        assert self.score(svc, 99, 0.99, 7, "BLOCKED", 2) == 0.0

    def test_buy_beats_hold_realistic(self, svc):
        """
        現実では BUY = p >= threshold (例: 0.65), HOLD = p < threshold (例: 0.40)。
        同じテクニカル・CANSLIM でも AI 確率が高い BUY がスコア上になる。
        (同一 p_up5 で BUY/HOLD が変わることは threshold 変化による稀なケースのみ)
        """
        s_buy  = self.score(svc, 80, 0.65, 4, "BUY",  2)  # p >= threshold
        s_hold = self.score(svc, 80, 0.40, 4, "HOLD", 1)  # p < threshold
        assert s_buy > s_hold

    def test_hold_beats_blocked(self, svc):
        """HOLD > BLOCKED"""
        s_hold    = self.score(svc, 80, 0.65, 4, "HOLD",    1)
        s_blocked = self.score(svc, 80, 0.65, 4, "BLOCKED", 2)
        assert s_hold > s_blocked

    def test_gate_watch_below_no_gate_watch(self, svc):
        """ゲート起因 WATCH (pc あり) < 非ゲート WATCH (同等テクニカル)"""
        s_gate_watch    = self.score(svc, 80, 0.55, 3, "WATCH", 1)   # pc=1: ゲート起因ペナルティ
        s_nogate_watch  = self.score(svc, 80, 0.55, 3, "WATCH", None) # pc=None: AI未予測、ペナルティなし
        assert s_gate_watch < s_nogate_watch

    def test_higher_ai_prob_improves_score(self, svc):
        """AI確率が高いほどスコアが上がる（action が同じなら）"""
        s_high = self.score(svc, 70, 0.80, 3, "BUY", 2)
        s_low  = self.score(svc, 70, 0.40, 3, "BUY", 1)
        assert s_high > s_low

    def test_higher_canslim_improves_score(self, svc):
        """CANSLIM 通過数が多いほどスコアが上がる"""
        s_high = self.score(svc, 70, 0.60, 7, "BUY", 2)
        s_low  = self.score(svc, 70, 0.60, 1, "BUY", 2)
        assert s_high > s_low

    def test_no_prediction_does_not_collapse_score(self, svc):
        """AI未予測でも高テクニカルなら 30 以上は確保する（極端な落とし方をしない）"""
        s = self.score(svc, 85, None, 5, "WATCH", None)
        assert s >= 30.0

    def test_score_bounded_0_to_100(self, svc):
        """スコアは常に [0, 100] の範囲に収まる"""
        extremes = [
            (100, 1.0, 7, "BUY",     2),
            (0,   0.0, 0, "BLOCKED", 0),
            (50,  0.5, 3, "WATCH",   None),
            (100, 0.0, 7, "HOLD",    1),
        ]
        for args in extremes:
            s = self.score(svc, *args)
            assert 0.0 <= s <= 100.0, f"Out of range: {s} for args={args}"

    def test_ordering_matches_investment_intent(self, svc):
        """
        優先順位:
        1. 優良BUY (BUY + 高AI + 高CANSLIM)
        2. AI未予測だが高テクニカル
        3. HOLD
        4. WATCHゲート起因
        5. BLOCKED
        """
        s1 = self.score(svc, 85, 0.72, 5, "BUY",     2)   # 優良BUY
        s2 = self.score(svc, 80, None, 3, "WATCH",   None) # AI未予測高テク
        s3 = self.score(svc, 75, 0.40, 3, "HOLD",    1)    # HOLD
        s4 = self.score(svc, 80, 0.55, 3, "WATCH",   1)    # WATCHゲート起因
        s5 = self.score(svc, 80, 0.72, 5, "BLOCKED", 2)    # BLOCKED

        assert s1 > s3, "BUY must beat HOLD"
        assert s3 > s4, "HOLD must beat gate-WATCH"
        assert s4 > s5, "gate-WATCH must beat BLOCKED"
        assert s5 == 0.0, "BLOCKED must be exactly 0"


# ─────────────────────────────────────────────────────────────
# _compute_final_rank_score — class15 性質テスト (Phase 3-4)
# ─────────────────────────────────────────────────────────────

class TestFinalRankScoreClass15Properties:

    def _s(self, svc, total, p_up5, canslim, action, pc,
           prob_up=None, prob_down=None):
        return svc._compute_final_rank_score(
            total, p_up5, canslim, action, pc, prob_up, prob_down
        )

    def test_class15_ordering_up_none_down(self, svc):
        """class15 Up > None(中立) > Down の順序が成立する"""
        s_up   = self._s(svc, 70, 0.70, 4, "BUY", 2, 0.70, 0.10)
        s_none = self._s(svc, 70, 0.70, 4, "BUY", 2, None, None)
        s_down = self._s(svc, 70, 0.70, 4, "BUY", 2, 0.10, 0.70)
        assert s_up > s_none > s_down

    def test_blocked_with_class15_still_zero(self, svc):
        """class15=Up でも BLOCKED は 0"""
        assert self._s(svc, 99, 0.99, 7, "BLOCKED", 2, 1.0, 0.0) == 0.0

    def test_score_bounded_with_class15(self, svc):
        """class15 込みでもスコアは [0, 100] に収まる"""
        for pu, pd in [(1.0, 0.0), (0.0, 1.0), (0.5, 0.5), (None, None)]:
            for ts, p, canslim, action, pc in [
                (100, 1.0, 7, "BUY",  2),
                (0,   0.0, 0, "HOLD", 1),
                (50,  0.5, 3, "WATCH", None),
            ]:
                s = self._s(svc, ts, p, canslim, action, pc, pu, pd)
                assert 0.0 <= s <= 100.0, f"Out of range: {s} (pu={pu}, pd={pd})"

    def test_class15_max_impact_is_small(self, svc):
        """最大 Up/Down シグナルでも既存スコアとの差は ±5pt 以内 (順位崩壊なし)"""
        for ts, p, canslim, action, pc in [
            (80, 0.75, 5, "BUY",  2),
            (60, 0.55, 3, "BUY",  2),
            (70, 0.65, 4, "HOLD", 1),
        ]:
            s_base = self._s(svc, ts, p, canslim, action, pc, None, None)
            s_up   = self._s(svc, ts, p, canslim, action, pc, 1.0, 0.0)
            s_down = self._s(svc, ts, p, canslim, action, pc, 0.0, 1.0)
            assert abs(s_up   - s_base) <= 5.0, \
                f"Up impact too large: {s_up - s_base:.2f}pt (ts={ts},p={p})"
            assert abs(s_down - s_base) <= 5.0, \
                f"Down impact too large: {s_base - s_down:.2f}pt (ts={ts},p={p})"


# ─────────────────────────────────────────────────────────────
# Institutional footprint score — 性質テスト
# ─────────────────────────────────────────────────────────────

def _make_prices(n=25, base=100.0, trending_up=False, vol_base=1_000_000) -> list:
    """n日分の (close, high, low, volume) リスト, newest-first を生成するヘルパー"""
    import random
    random.seed(42)
    prices = []
    c = base
    for i in range(n):
        if trending_up:
            c = c * (1 + random.uniform(0.002, 0.012))
        else:
            c = c * (1 + random.uniform(-0.008, 0.008))
        h = c * random.uniform(1.001, 1.015)
        l = c * random.uniform(0.985, 0.999)
        v = int(vol_base * random.uniform(0.7, 1.3))
        prices.append((c, h, l, v))
    return prices  # already "newest-first" for test purposes


class TestInstitutionalFootprintScore:

    def test_score_bounded_0_to_100(self, svc):
        """スコアは常に [0, 100] の範囲に収まる"""
        prices = _make_prices(50)
        obv = list(range(1_000_000, 1_020_000, 1000))  # 20 values, rising
        score, flags = svc._compute_institutional_footprint_score(prices, obv)
        assert score is not None
        assert 0.0 <= score <= 100.0, f"Out of range: {score}"

    def test_insufficient_data_returns_none_with_flag(self, svc):
        """データ不足時は (None, ['insufficient_data']) を返す"""
        score, flags = svc._compute_institutional_footprint_score([], [])
        assert score is None
        assert "insufficient_data" in flags

    def test_insufficient_data_few_rows(self, svc):
        """9行以下でも insufficient_data"""
        prices = _make_prices(9)
        score, flags = svc._compute_institutional_footprint_score(prices, [])
        assert score is None
        assert "insufficient_data" in flags

    def test_breakout_volume_raises_score(self, svc):
        """ブレイクアウト + 出来高急増でスコアが上がる"""
        base = 100.0
        # 通常の価格データ: 50日高値が110、通常出来高
        normal_prices = [(base * 0.90, base * 0.91, base * 0.89, 1_000_000)] * 50
        # ブレイクアウト価格データ: 最新終値が50日高値付近 + 出来高2倍
        breakout_prices = list(normal_prices)
        breakout_prices[0] = (base * 0.99, base * 1.01, base * 0.98, 2_500_000)
        # 50日高値を設定するため先頭以外を少し低めに
        breakout_prices = [(base * 0.95, base * 0.96, base * 0.94, 1_000_000)] * 49
        breakout_prices.insert(0, (base * 0.99, base * 1.01, base * 0.98, 2_500_000))

        s_normal   = svc._breakout_volume_score(normal_prices)
        s_breakout = svc._breakout_volume_score(breakout_prices)
        assert s_breakout > s_normal, f"Breakout should score higher: {s_breakout} vs {s_normal}"

    def test_accumulation_positive_raises_score(self, svc):
        """上昇日の出来高 > 下落日の出来高でスコアが上がる"""
        # 強いアキュムレーション: 上昇日に大量出来高
        accum_prices = []
        for i in range(20):
            if i % 2 == 0:  # up day
                accum_prices.append((101.0, 102.0, 100.0, 2_000_000))
            else:            # down day
                accum_prices.append((99.0, 100.0, 98.5, 500_000))

        # 分散パターン: 上昇・下落で出来高同等
        flat_prices = [(101.0 if i % 2 == 0 else 99.0, 102.0, 98.5, 1_000_000) for i in range(20)]

        s_accum = svc._accumulation_score(accum_prices)
        s_flat  = svc._accumulation_score(flat_prices)
        assert s_accum > s_flat, f"Accumulation should score higher: {s_accum} vs {s_flat}"

    def test_accumulation_flag_set_when_high(self, svc):
        """accumulation_positive フラグが高スコア時に付く"""
        accum_prices = []
        for i in range(25):
            if i % 2 == 0:
                accum_prices.append((101.0, 102.0, 100.0, 3_000_000))
            else:
                accum_prices.append((99.0, 100.0, 98.5, 300_000))
        _, flags = svc._compute_institutional_footprint_score(accum_prices, [])
        assert "accumulation_positive" in flags

    def test_obv_rising_raises_score(self, svc):
        """OBV 上昇トレンドでスコアが上がる"""
        rising_obv  = list(range(1_000_000, 1_200_000, 10_000))  # 20 rising values
        falling_obv = list(range(1_200_000, 1_000_000, -10_000)) # 20 falling values

        s_rising  = svc._obv_trend_score(rising_obv)
        s_falling = svc._obv_trend_score(falling_obv)
        assert s_rising > s_falling, f"Rising OBV should score higher: {s_rising} vs {s_falling}"

    def test_obv_rising_flag(self, svc):
        """OBV 上昇時に obv_rising フラグが付く"""
        prices = _make_prices(25)
        rising_obv = list(range(1_000_000, 1_200_000, 10_000))
        _, flags = svc._compute_institutional_footprint_score(prices, rising_obv)
        assert "obv_rising" in flags

    def test_resolve_evidence_takes_priority(self, svc):
        """evidence が None でない場合は evidence を採用"""
        score, source = svc._resolve_institutional_score(footprint=40.0, evidence=80.0)
        assert score == 80.0
        assert source == "evidence"

    def test_resolve_footprint_when_no_evidence(self, svc):
        """evidence が None の時は footprint を採用"""
        score, source = svc._resolve_institutional_score(footprint=55.0, evidence=None)
        assert score == 55.0
        assert source == "footprint"

    def test_resolve_none_when_both_none(self, svc):
        """両方 None のときは (None, 'none')"""
        score, source = svc._resolve_institutional_score(footprint=None, evidence=None)
        assert score is None
        assert source == "none"

    def test_sub_scores_bounded(self, svc):
        """各サブスコア関数は 0-100 の範囲に収まる"""
        prices = _make_prices(50)
        prices_20 = prices[:20]
        obv = list(range(1_000_000, 1_200_000, 10_000))

        assert 0.0 <= svc._breakout_volume_score(prices) <= 100.0
        assert 0.0 <= svc._accumulation_score(prices_20) <= 100.0
        assert 0.0 <= svc._obv_trend_score(obv) <= 100.0
        assert 0.0 <= svc._support_rebound_score(prices_20) <= 100.0
        assert 0.0 <= svc._volume_profile_proxy_score(prices_20) <= 100.0
