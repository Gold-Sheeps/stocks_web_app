from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
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
    ytd_gain_loss_jpy: Optional[Decimal] = None
    ytd_gain_loss_pct: Optional[Decimal] = None
    ytd_start_date: Optional[date] = None


class MonitorResponse(BaseModel):
    """Monitor画面用レスポンス"""
    portfolio: PortfolioSummary
    indices: list[MarketIndexData]
    fx_rates: list[MarketIndexData]
    metals: list[MarketIndexData]


# Stock Detail Models
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
    # Deprecated fields (kept for backward compatibility if needed, 
    # but Frontend Phase 5 will ignore them or we can remove if Phase 5-2 handles /price)
    price_history: list[PricePoint] = [] 


# ========== Trade Models ==========
class TradeCreate(BaseModel):
    """取引作成用モデル"""
    symbol_key: str
    market: str  # US, JP
    asset_type: str  # EQUITY, ETF, FX
    side: str  # BUY or SELL
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


# Portfolio Models
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
    best_performer: str | None = None
    worst_performer: str | None = None


class PortfolioDetailResponse(BaseModel):
    """Portfolio画面用レスポンス"""
    performance: PortfolioPerformance
    holdings: list[Holding]
    recent_trades: list[TradeHistory]
    last_updated: datetime


# Screener Models
class ScreenerResult(BaseModel):
    """スクリーニング結果の1銘柄"""
    symbol: str
    name: str
    price: Decimal
    change_pct: Decimal
    volume: int
    market_cap: Decimal | None = None
    rsi: Decimal | None = None
    total_score: float | None = None
    ma_cross: str | None = None


class ScreenerResponse(BaseModel):
    """Screener画面用レスポンス"""
    results: list[ScreenerResult]
    total_count: int
    filter_applied: dict


# Rotation Models  
class SectorPerformance(BaseModel):
    """セクター別パフォーマンス"""
    sector: str
    current_return: Decimal
    momentum: Decimal  # 勢い（最近のトレンド）
    relative_strength: Decimal  # 相対的強さ
    rank: int


class RotationResponse(BaseModel):
    """Rotation画面用レスポンス"""
    sectors: list[SectorPerformance]
    last_updated: datetime


# ========== Sector Detail Models ==========
class SectorConstituent(BaseModel):
    rank: int
    symbol: str
    name: str
    weight: Optional[Decimal] = None
    market_cap: Optional[Decimal] = None
    rs_score: Optional[int] = None
    volume_ratio: Optional[Decimal] = None
    institution_flag: bool = False


class SectorDetailResponse(BaseModel):
    sector_name: str
    etf_symbol: str
    performance: dict
    top_3: list[str]
    chart_data: list[dict]
    constituents: list[dict]

# ========== Watchlist Models ==========
class WatchlistRequest(BaseModel):
    """ウォッチリスト登録/更新リクエスト"""
    symbol: str
    status: Optional[str] = "Research"  # Research, Setup, Action
    memo: Optional[str] = None
    tags: Optional[list[str]] = []
    fair_value_min: Optional[Decimal] = None
    fair_value_max: Optional[Decimal] = None
    alert_config: Optional[dict] = None


class WatchlistItem(BaseModel):
    """ウォッチリスト項目（表示用）"""
    id: int
    symbol: str
    name: str | None = None
    market: str | None = None
    status: str
    memo: str | None = None
    tags: list[str] = []
    fair_value_min: Decimal | None = None
    fair_value_max: Decimal | None = None
    alert_config: dict | None = None
    
    # Live/Daily Data
    current_price: Decimal | None = None
    change_pct: Decimal | None = None
    volume: int | None = None
    
    # Technical Indicators
    rsi: Decimal | None = None
    macd: Decimal | None = None
    macd_signal: Decimal | None = None
    ema21: Decimal | None = None
    sma50: Decimal | None = None
    sma200: Decimal | None = None
    pivot: Decimal | None = None
    rs_score: int | None = None
    volume_ratio: Decimal | None = None
    
    # Flags/Analysis
    institution_flag: bool = False
    buy_zone: bool = False
    
    # Entry Info
    entry_price: Decimal | None = None
    entry_date: date | None = None
    change_from_entry: Decimal | None = None
    change_pct_from_entry: Decimal | None = None

    last_updated: datetime | None = None

    class Config:
        from_attributes = True

