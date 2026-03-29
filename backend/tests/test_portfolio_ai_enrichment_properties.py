"""
性質ベーステスト: Portfolio AI enrichment ロジック

DB接続なし。portfolio_service の AI enrichment ロジック（Watchlist と共通）を
Portfolio 固有の観点からユニットテストする。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.services.ai_prediction_service import _compute_action_label, _compute_ai_signal_strength
from app.models.schemas import Holding
from decimal import Decimal


# ─────────────────────────────────────────────────────────────
# Holding schema — AI フィールド
# ─────────────────────────────────────────────────────────────

def _make_holding(**kwargs) -> Holding:
    """最低限フィールドを埋めた Holding を生成するヘルパー"""
    defaults = dict(
        symbol="US:NVDA",
        market="US",
        asset_type="stock",
        shares=Decimal("10"),
        avg_cost=Decimal("100"),
        current_price=Decimal("110"),
        market_value=Decimal("1100"),
        cost_basis=Decimal("1000"),
        gain_loss=Decimal("100"),
        gain_loss_pct=Decimal("10"),
        currency="JPY",
    )
    defaults.update(kwargs)
    return Holding(**defaults)


class TestHoldingSchema:
    def test_ai_fields_default_to_none(self):
        """AI フィールドは Optional で、AI未予測でも Holding を構築できる"""
        h = _make_holding()
        assert h.action_label is None
        assert h.gate_reasons is None
        assert h.ai_signal_strength is None
        assert h.p_up5 is None

    def test_buy_holding_accepted(self):
        h = _make_holding(action_label="BUY", gate_reasons=[], ai_signal_strength=72.0, p_up5=0.72)
        assert h.action_label == "BUY"
        assert h.ai_signal_strength == 72.0

    def test_blocked_holding_has_zero_signal(self):
        h = _make_holding(
            action_label="BLOCKED",
            gate_reasons=["market_regime_blocked"],
            ai_signal_strength=0.0,
            p_up5=0.85,
        )
        assert h.action_label == "BLOCKED"
        assert h.ai_signal_strength == 0.0

    def test_watch_holding_with_caution_gate(self):
        h = _make_holding(
            action_label="WATCH",
            gate_reasons=["market_regime_caution", "prob_below_caution_threshold"],
            ai_signal_strength=27.5,
            p_up5=0.55,
        )
        assert h.action_label == "WATCH"
        assert "market_regime_caution" in h.gate_reasons


# ─────────────────────────────────────────────────────────────
# Portfolio 固有の AI enrichment 性質テスト
# ─────────────────────────────────────────────────────────────

def _enrich(gate: str, p_up5, decision: str):
    """portfolio_service._get_holdings の enrichment ロジックを模倣"""
    if p_up5 is None:
        return None, [], None
    dec = (decision or "").upper()
    gate_reason = None
    gate_reasons: list[str] = []
    if gate == "OFF" and dec == "BUY":
        dec = "GATE_BLOCKED"
        gate_reason = "新規買い停止中（市場環境）"
        gate_reasons.append("market_regime_blocked")
    elif gate == "HALF" and p_up5 < 0.6 and dec == "BUY":
        dec = "CAUTION"
        gate_reason = "市場環境「注意」のため閾値引き上げ（p<0.6）"
        gate_reasons.append("market_regime_caution")
        gate_reasons.append("prob_below_caution_threshold")
    al = _compute_action_label(dec, gate_reason)
    sig = _compute_ai_signal_strength(p_up5, al, gate_reasons)
    return al, gate_reasons, sig


class TestPortfolioAiEnrichment:

    def test_holding_no_prediction_is_none(self):
        al, gr, sig = _enrich("ON", None, None)
        assert al is None
        assert sig is None

    def test_hold_decision_stays_hold(self):
        """保有中は NO_TRADE/HOLD が多い — gate に関わらず HOLD のまま"""
        for gate in ("ON", "HALF", "OFF"):
            al, gr, sig = _enrich(gate, 0.35, "NO_TRADE")
            assert al == "HOLD", f"gate={gate}: expected HOLD, got {al}"

    def test_sell_decision_passes_through(self):
        """SELL シグナルはゲートに関わらず通過する"""
        for gate in ("ON", "HALF", "OFF"):
            al, gr, sig = _enrich(gate, 0.20, "SELL")
            assert al == "SELL", f"gate={gate}: expected SELL, got {al}"

    def test_buy_blocked_when_market_off(self):
        """市場停止時は BUY → BLOCKED (保有中でも買い増し不可を示す)"""
        al, gr, sig = _enrich("OFF", 0.80, "BUY")
        assert al == "BLOCKED"
        assert sig == 0.0
        assert "market_regime_blocked" in gr

    def test_buy_watch_when_market_half_low_prob(self):
        al, gr, sig = _enrich("HALF", 0.52, "BUY")
        assert al == "WATCH"
        assert "market_regime_caution" in gr
        assert sig < 30  # 52 * 0.5 = 26

    def test_buy_passes_when_market_half_high_prob(self):
        """HALF でも p >= 0.6 なら BUY のまま"""
        al, gr, sig = _enrich("HALF", 0.68, "BUY")
        assert al == "BUY"
        assert gr == []

    def test_signal_strength_reflects_p_up5(self):
        """signal_strength は p_up5 * 100 の整合を保つ (gate なし時)"""
        al, gr, sig = _enrich("ON", 0.75, "BUY")
        assert al == "BUY"
        assert sig == pytest.approx(75.0, abs=0.1)

    def test_signal_strength_halved_under_caution(self):
        al, gr, sig = _enrich("HALF", 0.55, "BUY")
        assert al == "WATCH"
        expected = round(55.0 * 0.5, 1)
        assert sig == expected

    def test_gate_reasons_empty_for_clean_buy(self):
        al, gr, sig = _enrich("ON", 0.70, "BUY")
        assert al == "BUY"
        assert gr == []

    def test_multiple_holdings_ordering(self):
        """
        同一ポートフォリオで複数銘柄の signal_strength 大小が直感と一致すること:
        BUY (高確率) > WATCH (ゲート起因) > BLOCKED
        """
        _, _, sig_buy     = _enrich("ON",   0.80, "BUY")
        _, _, sig_watch   = _enrich("HALF", 0.55, "BUY")
        _, _, sig_blocked = _enrich("OFF",  0.80, "BUY")

        assert sig_buy > sig_watch, "BUY > WATCH"
        assert sig_watch > sig_blocked, "WATCH > BLOCKED"
        assert sig_blocked == 0.0
