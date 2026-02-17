from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from postgresql_connect import PostgreSQLConnect

import yfinance as yf


def symbol_key(raw_symbol: str) -> str:
    s = raw_symbol.strip().upper()
    return s if ":" in s else f"US:{s}"


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
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out.sort_index()


def ensure_instrument(db: PostgreSQLConnect, sk: str, raw_symbol: str) -> None:
    market = sk.split(":", 1)[0] if ":" in sk else "US"
    currency = "JPY" if market == "JP" else "USD"
    db.command(
        """
        INSERT INTO instruments (symbol_key, market, name, currency, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (symbol_key) DO UPDATE
        SET name = EXCLUDED.name,
            updated_at = CURRENT_TIMESTAMP
        """,
        (sk, market, raw_symbol, currency),
    )


def upsert_price_daily(db: PostgreSQLConnect, sk: str, df: pd.DataFrame, source: str) -> int:
    if df.empty:
        return 0

    inserted = 0
    for idx, row in df.iterrows():
        trading_date = pd.to_datetime(idx).date()
        close = float(row["Close"])
        adj_close = float(row["Adj Close"]) if pd.notna(row["Adj Close"]) else close
        volume = int(row["Volume"]) if pd.notna(row["Volume"]) else 0

        ok = db.command(
            """
            INSERT INTO price_daily (
                symbol_key, symbol_key_old_backup, trading_date, open, high, low, close, adj_close, volume, source, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol_key, trading_date) DO UPDATE SET
                symbol_key_old_backup = EXCLUDED.symbol_key_old_backup,
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                adj_close = EXCLUDED.adj_close,
                volume = EXCLUDED.volume,
                source = EXCLUDED.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                sk,
                sk,
                trading_date,
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                close,
                adj_close,
                volume,
                source,
            ),
        )
        if ok:
            inserted += 1

    return inserted


def fetch_history(raw_symbol: str, period: str) -> pd.DataFrame:
    data = yf.download(
        raw_symbol,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    return normalize_ohlcv(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch SOX history and upsert into PostgreSQL.")
    parser.add_argument("--symbol", default="^SOX", help="Ticker symbol (default: ^SOX)")
    parser.add_argument("--period", default="max", help="yfinance period (default: max)")
    parser.add_argument("--source", default="yfinance", help="price_daily.source value")
    args = parser.parse_args()

    sk = symbol_key(args.symbol)
    print(f"[INFO] Fetching {args.symbol} as {sk} (period={args.period})")

    hist = fetch_history(args.symbol, args.period)
    if hist.empty:
        print(f"[WARN] No data fetched for {args.symbol}")
        return 1
    print(f"[INFO] Fetched {len(hist)} rows")

    db = PostgreSQLConnect()
    if not db.connect():
        print("[ERROR] Failed to connect DB")
        return 1

    try:
        ensure_instrument(db, sk, args.symbol)
        upserted = upsert_price_daily(db, sk, hist, args.source)
        print(f"[OK] Upserted {upserted} rows into price_daily for {sk}")
        return 0
    finally:
        db.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
