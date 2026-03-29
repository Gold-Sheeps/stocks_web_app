import logging
from datetime import date, timedelta
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
import yfinance as yf

router = APIRouter(prefix="/earnings", tags=["earnings"])
logger = logging.getLogger(__name__)

def _ticker_from_symbol_key(symbol_key: str) -> str:
    """US:AAPL -> AAPL, JP:7203 -> 7203.T"""
    if ":" in symbol_key:
        market, ticker = symbol_key.split(":", 1)
        if market == "JP":
            return f"{ticker}.T"
        return ticker
    return symbol_key


@router.get("")
def get_earnings(symbols: str = Query(..., description="Comma-separated symbol keys e.g. US:NVDA,US:AAPL")):
    """
    指定銘柄の決算日情報を返す。
    yfinance の calendar / info から取得。
    """
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols required")

    results = []
    for sym in symbol_list:
        ticker_str = _ticker_from_symbol_key(sym)
        try:
            tk = yf.Ticker(ticker_str)
            info = tk.info or {}

            # next earnings date from info
            next_earnings_ts = info.get("earningsTimestamp") or info.get("earningsDate")
            next_date: Optional[str] = None
            if next_earnings_ts:
                try:
                    import datetime
                    if isinstance(next_earnings_ts, (int, float)):
                        next_date = datetime.datetime.utcfromtimestamp(next_earnings_ts).strftime("%Y-%m-%d")
                    else:
                        next_date = str(next_earnings_ts)[:10]
                except Exception:
                    pass

            # Try calendar dataframe
            eps_estimate: Optional[float] = None
            revenue_estimate: Optional[float] = None
            try:
                cal = tk.calendar
                if cal is not None and not cal.empty:
                    # calendar returns a dict-like or DataFrame
                    if hasattr(cal, "T"):
                        cal_t = cal.T
                        if "Earnings Date" in cal_t.columns:
                            ed = cal_t["Earnings Date"].iloc[0]
                            if ed and str(ed) != "NaT":
                                next_date = str(ed)[:10]
                    if isinstance(cal, dict):
                        ed = cal.get("Earnings Date", [None])[0] if isinstance(cal.get("Earnings Date"), list) else cal.get("Earnings Date")
                        if ed:
                            next_date = str(ed)[:10]
            except Exception:
                pass

            eps_estimate = info.get("epsForward") or info.get("forwardEps")
            revenue_estimate = info.get("revenueEstimate") or info.get("totalRevenue")

            results.append({
                "symbol": sym,
                "ticker": ticker_str,
                "name": info.get("longName") or info.get("shortName") or sym,
                "next_earnings_date": next_date,
                "eps_estimate": eps_estimate,
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector"),
            })
        except Exception as e:
            logger.warning("earnings fetch failed for %s: %s", sym, e)
            results.append({
                "symbol": sym,
                "ticker": ticker_str,
                "name": sym,
                "next_earnings_date": None,
                "eps_estimate": None,
                "market_cap": None,
                "sector": None,
            })

    # Sort by next_earnings_date ascending (None last)
    results.sort(key=lambda r: (r["next_earnings_date"] is None, r["next_earnings_date"] or ""))
    return results


@router.get("/watchlist")
def get_watchlist_earnings():
    """ウォッチリスト + ポートフォリオ銘柄の決算日一覧"""
    from app.services.watchlist_service import WatchlistService
    from app.services.portfolio_service import PortfolioService

    symbols = set()
    try:
        wl = WatchlistService().get_watchlist()
        for item in wl:
            symbols.add(item.symbol)
    except Exception:
        pass
    try:
        pf = PortfolioService().get_portfolio()
        for h in pf.get("holdings", []):
            sym = h.get("symbol") or h.get("symbol_key")
            if sym:
                symbols.add(sym)
    except Exception:
        pass

    if not symbols:
        return []

    return get_earnings(symbols=",".join(symbols))
