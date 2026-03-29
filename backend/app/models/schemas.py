from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Any
from pydantic import BaseModel


# ========== Instrument Models ==========
class InstrumentBase(BaseModel):
    symbol_key: str
    market: str
    name: str
    currency: str
    is_active: bool = True


class Instrument(InstrumentBase):
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== Price Models ==========
class PriceDaily(BaseModel):
    symbol_key: str
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Optional[Decimal] = None
    volume: Optional[int] = None
    source: Optional[str] = None
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== Market Data Models (Monitor用) ==========
class MarketIndexData(BaseModel):
    """市場指数データ"""
    symbol: str
    name: str
    current_price: Optional[Decimal]
    change_pct: Optional[Decimal]
    last_updated: Optional[datetime]


class PortfolioSummary(BaseModel):
    """資産サマリー"""
    total_value_jpy: Decimal
    total_cost_jpy: Decimal
    total_gain_loss_jpy: Decimal
    total_gain_loss_pct: Decimal
    cash_balance_jpy: Optional[Decimal] = None
    equity_value_jpy: Optional[Decimal] = None
    ytd_gain_loss_jpy: Optional[Decimal] = None
    ytd_gain_loss_pct: Optional[Decimal] = None
    ytd_start_date: Optional[date] = None


# ========== Watchlist & Monitor Models ==========
class WatchlistItem(BaseModel):
    """ウォッチリスト項目（表示用 & アラート用）"""
    id: Optional[int] = None
    symbol: str
    name: Optional[str] = None
    market: Optional[str] = None
    status: Optional[str] = "Research"
    memo: Optional[str] = None
    tags: Optional[list[str]] = []
    fair_value_min: Optional[Decimal] = None
    fair_value_max: Optional[Decimal] = None
    alert_config: Optional[dict] = None
    
    # ライブ/日次データ
    current_price: Optional[Decimal] = None
    change_pct: Optional[Decimal] = None
    volume: Optional[int] = None
    
    # テクニカル指標
    rsi: Optional[Decimal] = None
    macd: Optional[Decimal] = None
    macd_signal: Optional[Decimal] = None
    ema21: Optional[Decimal] = None
    sma50: Optional[Decimal] = None
    sma200: Optional[Decimal] = None
    pivot: Optional[Decimal] = None
    rs_rating: Optional[int] = None
    volume_ratio: Optional[Decimal] = None
    institution_flag: bool = False
    buy_zone: bool = False
    
    # アラート情報
    alert_type: Optional[str] = None  # "SURGE", "PLUNGE", None
    entry_price: Optional[Decimal] = None
    entry_date: Optional[date] = None
    change_from_entry: Optional[Decimal] = None
    change_pct_from_entry: Optional[Decimal] = None
    
    last_updated: Optional[datetime] = None

    # AI判定 (DB非保存、API算出 — Screener と同一ロジック)
    action_label: Optional[str] = None        # "BUY"|"HOLD"|"SELL"|"WATCH"|"BLOCKED"
    gate_reasons: Optional[list[str]] = None  # [] = ゲート非発動
    ai_signal_strength: Optional[float] = None  # 0-100 (p_up5×100、gate調整後)
    p_up5: Optional[float] = None             # AI上昇確率 (生値)

    class Config:
        from_attributes = True


class MonitorResponse(BaseModel):
    """Monitor画面用全体レスポンス"""
    indices: List[MarketIndexData]
    rates: List[MarketIndexData] = []
    crypto: List[MarketIndexData] = []
    watchlist: List[WatchlistItem] = []
    alerts: List[WatchlistItem] = []
    portfolio: Optional[PortfolioSummary] = None
    fx_rates: List[MarketIndexData] = []
    metals: List[MarketIndexData] = []
    freshness: dict[str, "DataFreshness"] = {}


# ========== Stock Detail Models ==========
class StockInfo(BaseModel):
    symbol: str
    name: str
    market: str
    currency: str
    current_price: Decimal | None = None
    change_pct: Decimal | None = None
    volume: int | None = None
    market_cap: Decimal | None = None
    last_updated: datetime
    ytd_change_pct: Decimal | None = None
    ytd_start_price: Decimal | None = None


class PricePoint(BaseModel):
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class Indicators(BaseModel):
    ma5: Decimal | None = None
    ma20: Decimal | None = None
    ma50: Decimal | None = None
    sma200: Decimal | None = None
    ema21: Decimal | None = None
    rsi: Decimal | None = None
    macd: Decimal | None = None
    signal: Decimal | None = None
    macd_hist: Decimal | None = None
    rs_rating: int | None = None
    atr14: Decimal | None = None
    dist_52w_high_pct: Decimal | None = None
    high_52w: Decimal | None = None
    volume_ratio: Decimal | None = None
    pivot: Decimal | None = None
    vwap20: Decimal | None = None
    obv: int | None = None
    mfi14: Decimal | None = None
    plus_di14: Decimal | None = None
    minus_di14: Decimal | None = None
    adx14: Decimal | None = None
    bb_upper20: Decimal | None = None
    bb_lower20: Decimal | None = None
    bb_width20: Decimal | None = None
    bb_percent_b: Decimal | None = None
    ichimoku_tenkan9: Decimal | None = None
    ichimoku_kijun26: Decimal | None = None
    ichimoku_senkou_a: Decimal | None = None
    ichimoku_senkou_b: Decimal | None = None
    ichimoku_chikou: Decimal | None = None


# ========== Signal & Analysis Models ==========
class SignalStatus(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"
    CAUTION = "CAUTION"
    NO_DATA = "NO_DATA"


class SignalReason(BaseModel):
    key: str
    status: SignalStatus
    message: str


class StockSignalSummary(BaseModel):
    overall: SignalStatus
    confidence: float
    reasons: list[SignalReason]


class DataQuality(BaseModel):
    price_valid: bool
    price_reason: str
    indicator_missing_count: int
    latest_price_date: datetime | None = None
    latest_indicator_date: datetime | None = None


class StockDetailResponse(BaseModel):
    stock_info: StockInfo
    indicators: Indicators
    signal_summary: StockSignalSummary
    data_quality: DataQuality
    price_history: list[PricePoint] = []
    trade_decision_summary: dict[str, Any] | None = None
    canslim_detail: dict[str, Any] | None = None
    chart_pattern_detail: dict[str, Any] | None = None
    sector_evaluation: dict[str, Any] | None = None
    market_environment_summary: dict[str, Any] | None = None
    ai_screening_summary: dict[str, Any] | None = None


# ========== Trade Models ==========
class TradeCreate(BaseModel):
    """取引作成用モデル"""
    symbol_key: str
    market: str
    asset_type: str
    side: str
    trade_date: date
    shares: Decimal
    price: Decimal
    currency: str
    fx_rate_to_jpy: Optional[Decimal] = 1.0
    memo: Optional[str] = None


class Trade(TradeCreate):
    """取引モデル"""
    trade_id: int
    realized_pnl: Optional[Decimal] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class Holding(BaseModel):
    """保有銘柄情報"""
    symbol: str
    name: Optional[str] = None
    market: str
    asset_type: str
    shares: Decimal
    avg_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    cost_basis: Decimal
    gain_loss: Decimal
    gain_loss_pct: Decimal
    currency: str

    # AI判定 (DB非保存、API算出 — Watchlist/Screener と同一ロジック)
    action_label: Optional[str] = None        # "BUY"|"HOLD"|"SELL"|"WATCH"|"BLOCKED"
    gate_reasons: Optional[list[str]] = None  # [] = ゲート非発動
    ai_signal_strength: Optional[float] = None  # 0-100 (gate調整後)
    p_up5: Optional[float] = None             # AI上昇確率 (生値)


class TradeHistory(BaseModel):
    """取引履歴"""
    trade_id: str
    symbol: str
    side: str
    trade_date: str
    shares: Decimal
    price: Decimal
    amount: Decimal
    currency: str


class PortfolioPerformance(BaseModel):
    """ポートフォリオパフォーマンス"""
    total_value: Decimal
    total_cost: Decimal
    total_gain_loss: Decimal
    total_gain_loss_pct: Decimal
    cash_balance: Decimal = Decimal("0")
    best_performer: str | None = None
    worst_performer: str | None = None


class PortfolioDetailResponse(BaseModel):
    """Portfolio画面用レスポンス"""
    performance: PortfolioPerformance
    holdings: list[Holding]
    recent_trades: list[TradeHistory]
    last_updated: datetime


# ========== Watchlist Models (CRUD用) ==========
class WatchlistRequest(BaseModel):
    """ウォッチリスト登録/更新リクエスト"""
    symbol: str
    status: Optional[str] = "Research"
    memo: Optional[str] = None
    tags: Optional[list[str]] = []
    fair_value_min: Optional[Decimal] = None
    fair_value_max: Optional[Decimal] = None
    alert_config: Optional[dict] = None


# ========== Screener Models ==========
# IMPORTANT:
# RS naming is fixed to `rs_rating`.
# Do not change it back to any `rs_score` field name.
# 禁止: RS Rating を RS Score に修正することを一切禁止。
class ScreenerResult(BaseModel):
    """スクリーニング結果の1銘柄"""
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    market_cap: Optional[float] = None
    rsi: Optional[float] = None
    total_score: Optional[float] = None
    rs_rating: Optional[int] = None
    dist_52w_high_pct: Optional[float] = None
    pivot: Optional[float] = None
    signals: list[str] = []
    ma_cross: Optional[str] = None
    m_judgment: Optional[str] = None
    market_gate: Optional[str] = None
    ho_alert: Optional[str] = None
    ho_days_remaining: Optional[int] = None
    dd_count: Optional[dict[str, Any]] = None
    dd_count_display: Optional[str] = None
    vix_mode: Optional[str] = None
    breakout_effectiveness: Optional[str] = None
    canslim_pass_count: Optional[int] = None
    canslim_pass_count_display: Optional[str] = None
    canslim_criteria: Optional[dict[str, bool]] = None
    overall_grade: Optional[str] = None
    ai_p_up5_2w: Optional[float] = None
    ai_3class: Optional[dict[str, int]] = None
    ai_3class_summary: Optional[str] = None
    # Phase 2: action_label / gate_reasons / predicted_class / final_rank_score (DB非保存、API算出)
    action_label: Optional[str] = None           # "BUY"|"HOLD"|"SELL"|"WATCH"|"BLOCKED"
    gate_reasons: Optional[list[str]] = None     # [] = ゲート非発動
    predicted_class: Optional[int] = None        # 0=下落 / 1=フラット / 2=上昇 (up5閾値から導出)
    final_rank_score: Optional[float] = None     # 総合ランクスコア (0-100): tech*0.5 + AI*0.3 + CANSLIM*0.2 + gate調整
    # Phase 3-1: 15営業日3クラス予測 (class15, DB保存済み分を表示)
    class15: Optional[dict[str, Any]] = None     # {"direction": "Up"|"Flat"|"Down", "prob_up": float, ...}
    sector: Optional[str] = None
    market_summary: Optional[dict[str, Any]] = None
    # Institutional: 機関投資家フットプリント (2層構造)
    institutional_footprint_score: Optional[float] = None  # price/volume代替スコア (0-100)
    institutional_evidence_score: Optional[float] = None   # 直接証拠スコア (将来用, 常にNone)
    institutional_score: Optional[float] = None            # 採用スコア (evidence優先 → footprint)
    institutional_score_source: Optional[str] = None       # "evidence"|"footprint"|"none"
    institutional_flags: list[str] = []                    # breakout_volume_confirmed等


class ScreenerResponse(BaseModel):
    """Screener画面用レスポンス"""
    items: list[ScreenerResult]
    total: int
    page: int
    limit: int
    total_pages: int
    freshness: "DataFreshness | None" = None
    usd_jpy_rate: float | None = None


# ========== Rotation Models ==========
class SectorPerformance(BaseModel):
    """セクター別パフォーマンス"""
    sector: str
    current_return: Decimal
    momentum: Decimal
    relative_strength: Decimal
    rank: int


class RotationResponse(BaseModel):
    """Rotation画面用レスポンス"""
    sectors: list[SectorPerformance]
    last_updated: datetime
    freshness: "DataFreshness | None" = None


class DataFreshness(BaseModel):
    scope: str
    market_timezone: str
    market_now: datetime
    expected_latest_date: date
    latest_date_max: date | None = None
    latest_date_min: date | None = None
    symbols_total: int = 0
    symbols_fresh: int = 0
    symbols_stale: int = 0
    is_fresh: bool = False
    stale_symbols_sample: list[str] = []
    note: str | None = None


# ========== Sector Detail Models ==========
class SectorConstituent(BaseModel):
    rank: int
    symbol: str
    name: str
    weight: Optional[Decimal] = None
    market_cap: Optional[Decimal] = None
    rs_rating: Optional[int] = None
    volume_ratio: Optional[Decimal] = None
    institution_flag: bool = False


class SectorDetailResponse(BaseModel):
    sector_name: str
    etf_symbol: str
    performance: dict
    top_3: list[str]
    chart_data: list[dict]
    constituents: list[dict]
