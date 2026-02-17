from __future__ import annotations

import argparse
import sys
from pathlib import Path
from io import StringIO
import json
from typing import List
from urllib.parse import quote
from urllib.request import urlopen

import pandas as pd
import yfinance as yf

# backend root -> import app.*
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database


def symbol_key(raw: str) -> str:
    s = raw.strip().upper()
    return s if ":" in s else f"US:{s}"


def raw_symbol(sym: str) -> str:
    s = sym.strip().upper()
    return s.split(":", 1)[1] if ":" in s else s


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] for c in out.columns]
    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "adj close": "Adj Close",
        "volume": "Volume",
    }
    out.columns = [rename_map.get(str(c).strip().lower(), c) for c in out.columns]
    for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if c not in out.columns:
            out[c] = pd.NA
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out[["Open", "High", "Low", "Close", "Adj Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out.sort_index()


def ensure_instrument(db: Database, sk: str, raw: str) -> None:
    market = sk.split(":", 1)[0] if ":" in sk else "US"
    currency = "JPY" if market == "JP" else "USD"
    q = """
        INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (symbol_key) DO NOTHING
    """
    db.execute_command(q, (sk, market, raw, currency))


def upsert_price_daily(db: Database, sk: str, raw: str, df: pd.DataFrame, source: str = "yfinance_cache") -> int:
    ensure_instrument(db, sk, raw)
    q = """
        INSERT INTO price_daily (
            symbol_key, trading_date, open, high, low, close, adj_close, volume, source, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (symbol_key, trading_date) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            adj_close = EXCLUDED.adj_close,
            volume = EXCLUDED.volume,
            source = EXCLUDED.source,
            updated_at = CURRENT_TIMESTAMP
    """
    if db.cursor is None or db.connection is None:
        return 0
    count = 0
    for idx, row in df.iterrows():
        d = pd.to_datetime(idx).date()
        op = float(row["Open"])
        hi = float(row["High"])
        lo = float(row["Low"])
        cl = float(row["Close"])
        ac = float(row["Adj Close"]) if pd.notna(row["Adj Close"]) else cl
        vol = int(row["Volume"]) if pd.notna(row["Volume"]) else 0
        db.cursor.execute(q, (sk, d, op, hi, lo, cl, ac, vol, source))
        count += 1
    db.connection.commit()
    return count


def fetch_history(raw: str, period: str = "10y") -> pd.DataFrame:
    try:
        df = yf.download(
            raw,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        out = normalize_ohlcv(df)
        if not out.empty:
            return out
    except Exception:
        pass
    via_download = fetch_history_http(raw)
    if not via_download.empty:
        return via_download
    return fetch_history_chart(raw)


def fetch_history_http(raw: str) -> pd.DataFrame:
    try:
        period1 = 0
        period2 = int(pd.Timestamp.utcnow().timestamp())
        sym = quote(raw, safe="")
        url = (
            f"https://query1.finance.yahoo.com/v7/finance/download/{sym}"
            f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
        )
        with urlopen(url, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        if not text.strip() or "Date,Open,High,Low,Close" not in text:
            return pd.DataFrame()
        df = pd.read_csv(StringIO(text), parse_dates=["Date"])
        if df.empty:
            return pd.DataFrame()
        return normalize_ohlcv(df.set_index("Date").sort_index())
    except Exception:
        return pd.DataFrame()


def fetch_history_chart(raw: str) -> pd.DataFrame:
    try:
        sym = quote(raw, safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=10y"
        with urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
        if not result:
            return pd.DataFrame()
        ts = result.get("timestamp") or []
        quote_obj = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
        if not ts or not quote_obj:
            return pd.DataFrame()
        dt = pd.to_datetime(ts, unit="s", utc=True).tz_convert(None)
        df = pd.DataFrame(
            {
                "Open": quote_obj.get("open"),
                "High": quote_obj.get("high"),
                "Low": quote_obj.get("low"),
                "Close": quote_obj.get("close"),
                "Volume": quote_obj.get("volume"),
            },
            index=dt,
        )
        return normalize_ohlcv(df)
    except Exception:
        return pd.DataFrame()


def run(symbols: List[str], period: str) -> int:
    db = Database()
    if not db.connect():
        print("[ERROR] Failed to connect DB")
        return 1
    try:
        for s in symbols:
            sk = symbol_key(s)
            raw = raw_symbol(s)
            print(f"[INFO] Fetching {raw} ({sk}) ...")
            df = fetch_history(raw, period=period)
            if df.empty:
                print(f"[WARN] No yfinance data for {raw}")
                continue
            rows = upsert_price_daily(db, sk, raw, df)
            print(f"[OK] Upserted {rows} rows into price_daily for {sk}")
    except Exception as e:
        if db.connection is not None:
            db.connection.rollback()
        print(f"[ERROR] {e}")
        return 1
    finally:
        db.disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time benchmark cache into DB for prediction feature.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["^SOX"],
        help="Raw tickers or symbol_keys (default: ^SOX)",
    )
    parser.add_argument("--period", default="10y", help="yfinance period (default: 10y)")
    args = parser.parse_args()
    return run(args.symbols, args.period)


if __name__ == "__main__":
    raise SystemExit(main())
