from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Tuple
import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class MarketStatus:
    symbol: str
    market_ok: bool
    price_above_ma50: bool
    dist_count: Optional[int]  # Volumeが無いときは None
    reason: str


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """yfinanceの戻りを Close/Volume の素直なDFに寄せる（MultiIndexもケア）"""
    if df is None or df.empty:
        return pd.DataFrame()

    # MultiIndex columns（複数ティッカーDL等）
    if isinstance(df.columns, pd.MultiIndex):
        # Close/Volume の最初の列だけ使う（単一ティッカー想定）
        try:
            close = df["Close"].iloc[:, 0]
            vol = (
                df["Volume"].iloc[:, 0]
                if "Volume" in df.columns.get_level_values(0)
                else None
            )
        except Exception:
            return pd.DataFrame()

        out = pd.DataFrame({"Close": close})
        if vol is not None:
            out["Volume"] = vol
        return out.dropna(subset=["Close"])

    # 通常
    if "Close" not in df.columns:
        return pd.DataFrame()

    out = df[["Close"]].copy()
    if "Volume" in df.columns:
        out["Volume"] = df["Volume"]
    return out.dropna(subset=["Close"])


def check_market_direction_by_symbol(
    market_symbol: str,
    *,
    period: str = "6mo",  # MA50のため 3mo はギリギリになりやすい
    interval: str = "1d",
    ma_window: int = 50,
    dist_window: int = 25,
    dist_down_pct: float = 0.002,  # -0.2%
    dist_limit: int = 6,
) -> MarketStatus:
    """
    market_symbol（例: '^GSPC', '^NYA', 'XLI', 'SOXX', 'SPY'）を受け取り
    市場環境を判定して返す（外部から呼び出しやすい形）
    """
    try:
        raw = yf.download(
            market_symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
        )
    except Exception as e:
        return MarketStatus(
            symbol=market_symbol,
            market_ok=False,
            price_above_ma50=False,
            dist_count=None,
            reason=f"取得失敗: {type(e).__name__}: {e}",
        )

    df = _normalize_ohlcv(raw)
    if df.empty:
        return MarketStatus(
            symbol=market_symbol,
            market_ok=False,
            price_above_ma50=False,
            dist_count=None,
            reason="データが空 / Close列が取得できません",
        )

    if len(df) < ma_window:
        return MarketStatus(
            symbol=market_symbol,
            market_ok=False,
            price_above_ma50=False,
            dist_count=None,
            reason=f"データ不足: {len(df)}件（MA{ma_window}に不足）",
        )

    df = df.copy()
    df["MA50"] = df["Close"].rolling(ma_window).mean()

    current_price = float(df["Close"].iloc[-1])
    ma50_now = df["MA50"].iloc[-1]
    if pd.isna(ma50_now):
        return MarketStatus(
            symbol=market_symbol,
            market_ok=False,
            price_above_ma50=False,
            dist_count=None,
            reason=f"MA{ma_window}がNaN（計算不可）",
        )

    price_above_ma = current_price > float(ma50_now)

    # 分配日（Volumeが無い/0ばかりの指数はスキップ）
    dist_count: Optional[int] = 0
    if "Volume" not in df.columns:
        dist_count = None
        reason = f"MA50位置: {'上' if price_above_ma else '下'}, Volume無しのため分配日判定スキップ"
        return MarketStatus(
            symbol=market_symbol,
            market_ok=price_above_ma,  # 分配日なしならMAだけで判断
            price_above_ma50=price_above_ma,
            dist_count=dist_count,
            reason=reason,
        )

    recent = df.tail(dist_window).copy()
    vol = recent["Volume"]

    if vol.isna().all() or (vol.fillna(0) == 0).all():
        dist_count = None
        reason = f"MA50位置: {'上' if price_above_ma else '下'}, Volumeが無効(0/NaN)のため分配日判定スキップ"
        return MarketStatus(
            symbol=market_symbol,
            market_ok=price_above_ma,
            price_above_ma50=price_above_ma,
            dist_count=dist_count,
            reason=reason,
        )

    cnt = 0
    for i in range(1, len(recent)):
        prev_close = float(recent["Close"].iloc[i - 1])
        curr_close = float(recent["Close"].iloc[i])
        prev_vol = float(recent["Volume"].iloc[i - 1])
        curr_vol = float(recent["Volume"].iloc[i])

        if curr_close < prev_close * (1.0 - dist_down_pct) and curr_vol > prev_vol:
            cnt += 1

    dist_count = cnt
    market_ok = price_above_ma and (dist_count < dist_limit)
    reason = f"MA50位置: {'上' if price_above_ma else '下'}, 分配日: {dist_count}日（直近{dist_window}日）"

    return MarketStatus(
        symbol=market_symbol,
        market_ok=market_ok,
        price_above_ma50=price_above_ma,
        dist_count=dist_count,
        reason=reason,
    )
