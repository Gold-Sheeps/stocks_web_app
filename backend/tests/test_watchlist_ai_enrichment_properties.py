"""
性質ベーステスト: Watchlist AI enrichment ロジック

DB接続なし。watchlist_service の gate 変換・AI付与ロジックをユニットテストする。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.services.ai_prediction_service import _compute_action_label, _compute_ai_signal_strength
from app.models.schemas import WatchlistItem


# ─────────────────────────────────────────────────────────────
# Helper: watchlist_service 内の gate 変換ロジック (純粋関数として抽出)
# ─────────────────────────────────────────────────────────────

def _regime_to_gate(market_regime: str) -> str:
    """watchlist_service.get_watchlist() と同一マッピング"""
    if market_regime == "新規買い停止":
        return "OFF"
    elif market_regime == "注意":
        return "HALF"
    else:
        return "ON"


def _enrich_ai(gate: str, p_up5, decision: str):
    """
    watchlist_service のAI enrichment ロジックを模倣。
    Returns (action_label, gate_reasons, ai_signal_strength)
    """
    if p_up5 is None:
        return None, [], None

    dec = (decision or "").upper()
    gate_reason = None
    gate_reasons = []

    if gate == "OFF" and dec == "BUY":
        dec = "GATE_BLOCKED"
        gate_reason = "新規買い停止中（市場環境）"
        gate_reasons.append("market_regime_blocked")
    elif gate == "HALF" and p_up5 < 0.6 and dec == "BUY":
        dec = "CAUTION"
        gate_reason = "市場環境「注意」のため閾値引き上げ（p<0.6）"
        gate_reasons.append("market_regime_caution")
        gate_reasons.append("prob_below_caution_threshold")

    action_label = _compute_action_label(dec, gate_reason)
    ai_signal_strength = _compute_ai_signal_strength(p_up5, action_label, gate_reasons)
    return action_label, gate_reasons, ai_signal_strength


# ─────────────────────────────────────────────────────────────
# Gate 変換テスト
# ─────────────────────────────────────────────────────────────

class TestRegimeToGate:
    def test_normal_regime_is_on(self):
        assert _regime_to_gate("通常") == "ON"
        assert _regime_to_gate("") == "ON"
        assert _regime_to_gate("強気") == "ON"

    def test_caution_is_half(self):
        assert _regime_to_gate("注意") == "HALF"

    def test_stop_is_off(self):
        assert _regime_to_gate("新規買い停止") == "OFF"


# ─────────────────────────────────────────────────────────────
# AI enrichment 性質テスト
# ─────────────────────────────────────────────────────────────

class TestWatchlistAiEnrichment:

    def test_no_prediction_returns_none(self):
        al, gr, sig = _enrich_ai("ON", None, None)
        assert al is None
        assert gr == []
        assert sig is None

    def test_buy_passes_on_normal_gate(self):
        al, gr, sig = _enrich_ai("ON", 0.72, "BUY")
        assert al == "BUY"
        assert gr == []
        assert sig > 60  # 72 * 100 * 1.0 ≈ 72

    def test_buy_blocked_on_off_gate(self):
        al, gr, sig = _enrich_ai("OFF", 0.72, "BUY")
        assert al == "BLOCKED"
        assert "market_regime_blocked" in gr
        assert sig == 0.0

    def test_buy_becomes_watch_on_half_gate_low_prob(self):
        al, gr, sig = _enrich_ai("HALF", 0.50, "BUY")
        assert al == "WATCH"
        assert "market_regime_caution" in gr
        assert "prob_below_caution_threshold" in gr
        assert sig < 50  # gate caution → 0.5倍

    def test_buy_passes_on_half_gate_high_prob(self):
        """p >= 0.6 なら HALF でも BUY 通過"""
        al, gr, sig = _enrich_ai("HALF", 0.65, "BUY")
        assert al == "BUY"
        assert gr == []

    def test_no_trade_becomes_hold(self):
        al, gr, sig = _enrich_ai("ON", 0.35, "NO_TRADE")
        assert al == "HOLD"

    def test_signal_strength_zero_for_blocked(self):
        al, gr, sig = _enrich_ai("OFF", 0.99, "BUY")
        assert al == "BLOCKED"
        assert sig == 0.0

    def test_signal_strength_halved_for_gate_watch(self):
        al, gr, sig = _enrich_ai("HALF", 0.55, "BUY")
        assert al == "WATCH"
        expected = round(55 * 0.5, 1)  # 27.5
        assert sig == expected


# ─────────────────────────────────────────────────────────────
# WatchlistItem schema テスト
# ─────────────────────────────────────────────────────────────

class TestWatchlistItemSchema:
    def test_ai_fields_are_optional_with_none_default(self):
        """AI フィールドは Optional で、DB 未登録銘柄でも WatchlistItem を構築できる"""
        item = WatchlistItem(symbol="US:NVDA")
        assert item.action_label is None
        assert item.gate_reasons is None
        assert item.ai_signal_strength is None
        assert item.p_up5 is None

    def test_ai_fields_accepted_when_provided(self):
        item = WatchlistItem(
            symbol="US:NVDA",
            action_label="BUY",
            gate_reasons=[],
            ai_signal_strength=72.0,
            p_up5=0.72,
        )
        assert item.action_label == "BUY"
        assert item.gate_reasons == []
        assert item.ai_signal_strength == 72.0

    def test_blocked_item_has_zero_signal(self):
        item = WatchlistItem(
            symbol="US:NVDA",
            action_label="BLOCKED",
            gate_reasons=["market_regime_blocked"],
            ai_signal_strength=0.0,
            p_up5=0.80,
        )
        assert item.action_label == "BLOCKED"
        assert item.ai_signal_strength == 0.0
