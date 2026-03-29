"""
Backtesting service: runs historical strategy simulations on price_daily data.
Strategies: ai_signals, ma_cross, breakout_52w
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

import psycopg

DB_CONNINFO = "host=localhost port=5432 dbname=postgres user=postgres password=test"


def _fetch_prices(symbol_key: str, start_date: date, end_date: date) -> list[dict]:
    """Return sorted list of {date, open, high, low, close, adj_close} dicts."""
    with psycopg.connect(DB_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT trading_date, open, high, low, close, adj_close
                FROM price_daily
                WHERE symbol_key = %s
                  AND trading_date BETWEEN %s AND %s
                ORDER BY trading_date ASC
                """,
                (symbol_key, start_date, end_date),
            )
            rows = cur.fetchall()
    return [
        {"date": r[0], "open": float(r[1] or 0), "high": float(r[2] or 0),
         "low": float(r[3] or 0), "close": float(r[4] or 0), "adj_close": float(r[5] or r[4] or 0)}
        for r in rows if r[4] and float(r[4]) > 0
    ]


def _fetch_ai_signals(symbol_key: str, start_date: date, end_date: date) -> dict[date, str]:
    """Return {date: decision} for the symbol."""
    with psycopg.connect(DB_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (asof) asof, decision
                FROM ai_predictions
                WHERE symbol_key = %s AND asof BETWEEN %s AND %s
                ORDER BY asof, CASE LOWER(COALESCE(cal_method,'')) WHEN 'none' THEN 0 ELSE 1 END
                """,
                (symbol_key, start_date, end_date),
            )
            return {r[0]: (r[1] or "").upper() for r in cur.fetchall()}


def _calc_metrics(equity_curve: list[dict], initial_capital: float, trades: list[dict]) -> dict:
    if len(equity_curve) < 2:
        return {"sharpe_ratio": 0.0, "max_drawdown_pct": 0.0, "annualized_return_pct": 0.0}

    values = [p["value"] for p in equity_curve]
    daily_returns = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]

    # Sharpe ratio (risk-free = 0)
    if len(daily_returns) > 1:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
        # Sortino ratio (downside deviation only)
        downside = [r for r in daily_returns if r < 0]
        if downside:
            downside_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
            sortino = (mean_r / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0
        else:
            sortino = 0.0
    else:
        sharpe = 0.0
        sortino = 0.0

    # Max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Annualized return
    trading_days = len(equity_curve)
    final = values[-1]
    if trading_days > 1 and initial_capital > 0:
        ann_ret = ((final / initial_capital) ** (252 / trading_days) - 1) * 100
    else:
        ann_ret = 0.0

    return {
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "annualized_return_pct": round(ann_ret, 2),
    }


def _strategy_ai_signals(prices: list[dict], signals: dict[date, str], initial_capital: float, hold_days: int = 20) -> tuple[list, list]:
    """Buy on BUY signal (entry next bar), hold hold_days or until SELL signal."""
    trades = []
    equity = initial_capital
    equity_curve = []
    price_map = {p["date"]: p for p in prices}
    dates = [p["date"] for p in prices]

    i = 0
    while i < len(prices):
        bar = prices[i]
        equity_curve.append({"date": bar["date"].isoformat(), "value": round(equity, 2)})
        decision = signals.get(bar["date"], "")
        if decision == "BUY" and i + 1 < len(prices):
            entry_bar = prices[i + 1]
            entry_price = entry_bar["close"]
            if entry_price <= 0:
                i += 1
                continue
            # Find exit
            exit_idx = min(i + 1 + hold_days, len(prices) - 1)
            for j in range(i + 2, min(i + 1 + hold_days, len(prices))):
                if signals.get(prices[j]["date"], "") == "SELL":
                    exit_idx = j
                    break
            exit_bar = prices[exit_idx]
            exit_price = exit_bar["close"]
            ret_pct = (exit_price - entry_price) / entry_price * 100
            pnl = equity * (exit_price - entry_price) / entry_price
            equity += pnl
            trades.append({
                "entry_date": entry_bar["date"].isoformat(),
                "exit_date": exit_bar["date"].isoformat(),
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "return_pct": round(ret_pct, 2),
                "pnl": round(pnl, 2),
            })
            # Fast-forward to exit
            for k in range(i + 1, exit_idx + 1):
                equity_curve.append({"date": prices[k]["date"].isoformat(), "value": round(equity, 2)})
            i = exit_idx + 1
        else:
            i += 1

    return trades, equity_curve


def _strategy_ma_cross(prices: list[dict], initial_capital: float, fast: int = 50, slow: int = 200) -> tuple[list, list]:
    """Buy on golden cross (fast MA > slow MA), sell on death cross."""
    trades = []
    equity = initial_capital
    equity_curve = []
    in_trade = False
    entry_price = 0.0
    entry_date = None

    for i, bar in enumerate(prices):
        equity_curve.append({"date": bar["date"].isoformat(), "value": round(equity, 2)})
        if i < slow:
            continue
        closes = [prices[j]["close"] for j in range(i - slow, i + 1)]
        ma_fast = sum(closes[-fast:]) / fast
        ma_slow = sum(closes) / slow

        prev_closes = [prices[j]["close"] for j in range(i - slow - 1, i)]
        prev_fast = sum(prev_closes[-fast:]) / fast
        prev_slow = sum(prev_closes) / slow

        if not in_trade and prev_fast <= prev_slow and ma_fast > ma_slow:
            # Golden cross — buy
            in_trade = True
            entry_price = bar["close"]
            entry_date = bar["date"]
        elif in_trade and prev_fast >= prev_slow and ma_fast < ma_slow:
            # Death cross — sell
            exit_price = bar["close"]
            ret_pct = (exit_price - entry_price) / entry_price * 100
            pnl = equity * (exit_price - entry_price) / entry_price
            equity += pnl
            trades.append({
                "entry_date": entry_date.isoformat(),
                "exit_date": bar["date"].isoformat(),
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "return_pct": round(ret_pct, 2),
                "pnl": round(pnl, 2),
            })
            in_trade = False

    return trades, equity_curve


def _strategy_breakout_52w(prices: list[dict], initial_capital: float, hold_days: int = 20) -> tuple[list, list]:
    """Buy when close > 52-week high of previous day. Sell after hold_days."""
    trades = []
    equity = initial_capital
    equity_curve = []
    window = 252
    i = 0

    while i < len(prices):
        bar = prices[i]
        equity_curve.append({"date": bar["date"].isoformat(), "value": round(equity, 2)})
        if i >= window:
            prev_highs = [prices[j]["high"] for j in range(i - window, i)]
            yearly_high = max(prev_highs)
            if bar["close"] > yearly_high and i + 1 < len(prices):
                entry_bar = prices[i + 1]
                entry_price = entry_bar["close"]
                exit_idx = min(i + 1 + hold_days, len(prices) - 1)
                exit_bar = prices[exit_idx]
                exit_price = exit_bar["close"]
                ret_pct = (exit_price - entry_price) / entry_price * 100
                pnl = equity * (exit_price - entry_price) / entry_price
                equity += pnl
                trades.append({
                    "entry_date": entry_bar["date"].isoformat(),
                    "exit_date": exit_bar["date"].isoformat(),
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(exit_price, 4),
                    "return_pct": round(ret_pct, 2),
                    "pnl": round(pnl, 2),
                })
                for k in range(i + 1, exit_idx + 1):
                    equity_curve.append({"date": prices[k]["date"].isoformat(), "value": round(equity, 2)})
                i = exit_idx + 1
                continue
        i += 1

    return trades, equity_curve


class BacktestService:
    def run_backtest(
        self,
        symbol_key: str,
        strategy: str = "ai_signals",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        initial_capital: float = 10000,
    ) -> dict:
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = date(end_date.year - 3, end_date.month, end_date.day)

        # Fetch extra history for MA calculation
        fetch_start = start_date - timedelta(days=300)
        prices = _fetch_prices(symbol_key, fetch_start, end_date)

        if len(prices) < 10:
            return {
                "symbol_key": symbol_key, "strategy": strategy,
                "error": f"Not enough price data ({len(prices)} bars found)",
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            }

        # Filter prices to requested range (keep extra history for MA seeding)
        if strategy == "ai_signals":
            signals = _fetch_ai_signals(symbol_key, start_date, end_date)
            trades, equity_curve = _strategy_ai_signals(
                [p for p in prices if p["date"] >= start_date], signals, initial_capital
            )
        elif strategy == "ma_cross":
            trades, equity_curve = _strategy_ma_cross(prices, initial_capital)
            # Trim equity curve to requested range
            equity_curve = [e for e in equity_curve if e["date"] >= start_date.isoformat()]
        elif strategy == "breakout_52w":
            trades, equity_curve = _strategy_breakout_52w(
                [p for p in prices if p["date"] >= start_date], initial_capital
            )
        else:
            return {"error": f"Unknown strategy: {strategy}"}

        final_capital = equity_curve[-1]["value"] if equity_curve else initial_capital
        total_return_pct = (final_capital - initial_capital) / initial_capital * 100 if initial_capital > 0 else 0
        winning = [t for t in trades if t["return_pct"] > 0]
        win_rate = len(winning) / len(trades) * 100 if trades else 0

        metrics = _calc_metrics(equity_curve, initial_capital, trades)

        return {
            "symbol_key": symbol_key,
            "strategy": strategy,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "initial_capital": initial_capital,
            "final_capital": round(final_capital, 2),
            "total_return_pct": round(total_return_pct, 2),
            "annualized_return_pct": metrics["annualized_return_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "sharpe_ratio": metrics["sharpe_ratio"],
            "sortino_ratio": metrics["sortino_ratio"],
            "win_rate_pct": round(win_rate, 1),
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "trades": trades[-50:],  # return last 50 trades
            "equity_curve": equity_curve,
        }
