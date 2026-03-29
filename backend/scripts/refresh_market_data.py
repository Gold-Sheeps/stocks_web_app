from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Add backend root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

# yfinance timezone cache path fix (Windows env dependent issues)
_YF_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "yfinance"
_YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YFINANCE_TZ_CACHE_LOCATION", str(_YF_CACHE_DIR))

import yfinance as yf

yf.set_tz_cache_location(str(_YF_CACHE_DIR))

from app.db.database import Database

MAX_PRICE_ABS = 99_999_999.9999


@dataclass
class SymbolResult:
    symbol_key: str
    status: str
    db_before: Dict[str, Any]
    db_after: Dict[str, Any]
    fetch: Dict[str, Any]
    upsert: Dict[str, Any]
    error: Optional[str] = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh market price_daily data via external source, then store to DB."
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help='Comma-separated symbols (e.g. "US:NVDA,US:QQQ,US:^SOX,US:SMH").',
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD). Optional.")
    parser.add_argument("--end", help="End date (YYYY-MM-DD). Optional. Default: today.")
    parser.add_argument(
        "--source",
        default="yfinance",
        choices=["yfinance"],
        help="External source. Default: yfinance.",
    )
    parser.add_argument(
        "--db-only-check",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Do not fetch externally; only report DB freshness.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Skip fetch when (end - db_max_date).days <= N.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries per symbol for external fetch.",
    )
    parser.add_argument(
        "--force-start",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Respect --start exactly (do not auto-shift to db max_date + 1).",
    )
    return parser.parse_args()


def _parse_date_opt(value: Optional[str], name: str) -> Optional[date]:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD: {value}") from exc


def _normalize_symbol_key(raw: str) -> str:
    s = raw.strip().upper()
    if not s:
        return s
    if ":" in s:
        return s
    if re.fullmatch(r"\d{4}", s):
        return f"JP:{s}"
    return f"US:{s}"


def _to_yf_symbol(symbol_key: str) -> str:
    if ":" in symbol_key:
        market, raw = symbol_key.split(":", 1)
        market = market.strip().upper()
    else:
        market, raw = "US", symbol_key
    raw = raw.strip()
    if market == "JP" and re.fullmatch(r"\d{4}", raw):
        # Yahoo Finance JP equities use the .T suffix (e.g. 7203.T).
        return f"{raw}.T"
    return raw.replace(".", "-")


def _db_stats(db: Database, symbol_key: str, as_of: date) -> Dict[str, Any]:
    q = """
        SELECT COUNT(*), MAX(trading_date)
        FROM price_daily
        WHERE symbol_key = %s AND trading_date <= %s
    """
    rows = db.execute_query(q, (symbol_key, as_of))
    if not rows:
        return {"rows": 0, "max_date": None}
    return {
        "rows": int(rows[0][0] or 0),
        "max_date": rows[0][1].isoformat() if rows[0][1] is not None else None,
    }


def _load_price_daily_columns(db: Database) -> List[str]:
    q = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'price_daily'
        ORDER BY ordinal_position
    """
    rows = db.execute_query(q)
    if not rows:
        raise RuntimeError("price_daily table not found.")
    return [str(r[0]) for r in rows]


def _fetch_yf_history(yf_symbol: str, start_date: Optional[date], end_date: date) -> pd.DataFrame:
    ticker = yf.Ticker(yf_symbol)
    # yfinance end is exclusive; add 1 day to include end_date.
    yf_end = end_date + timedelta(days=1)
    kwargs: Dict[str, Any] = {"auto_adjust": False}
    if start_date is None:
        kwargs["period"] = "max"
    else:
        kwargs["start"] = start_date.isoformat()
        kwargs["end"] = yf_end.isoformat()
    hist = ticker.history(**kwargs)
    if hist is None or hist.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])
    if start_date is not None:
        idx = pd.to_datetime(hist.index).tz_localize(None).date
        hist = hist[(idx >= start_date) & (idx <= end_date)]
    return hist


def _fetch_yahoo_chart_fallback(yf_symbol: str, start_date: Optional[date], end_date: date) -> pd.DataFrame:
    start_dt = start_date if start_date is not None else date(1970, 1, 1)
    period1 = int(datetime(start_dt.year, start_dt.month, start_dt.day, tzinfo=timezone.utc).timestamp())
    period2 = int(
        datetime(
            (end_date + timedelta(days=1)).year,
            (end_date + timedelta(days=1)).month,
            (end_date + timedelta(days=1)).day,
            tzinfo=timezone.utc,
        ).timestamp()
    )
    path_symbol = urllib.parse.quote(yf_symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{path_symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
    if not result:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])

    ts = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
    adj = (((result.get("indicators") or {}).get("adjclose") or [None])[0]) or {}
    if not ts:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])

    df = pd.DataFrame(
        {
            "Date": [datetime.utcfromtimestamp(int(t)).date() for t in ts],
            "Open": quote.get("open", []),
            "High": quote.get("high", []),
            "Low": quote.get("low", []),
            "Close": quote.get("close", []),
            "Adj Close": adj.get("adjclose", []),
            "Volume": quote.get("volume", []),
        }
    )
    df = df.dropna(subset=["Close"])
    if df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])
    df = df.set_index(pd.to_datetime(df["Date"]))
    df = df.drop(columns=["Date"])
    return df


def _fetch_with_retry(
    symbol_key: str,
    source: str,
    start_date: Optional[date],
    end_date: date,
    max_retries: int,
) -> Tuple[pd.DataFrame, str, int]:
    if source != "yfinance":
        raise RuntimeError(f"Unsupported source: {source}")

    yf_symbol = _to_yf_symbol(symbol_key)
    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            df = _fetch_yf_history(yf_symbol, start_date, end_date)
            if not df.empty:
                return df, "yfinance", attempt
            df_fb = _fetch_yahoo_chart_fallback(yf_symbol, start_date, end_date)
            return df_fb, "yahoo_chart_fallback", attempt
        except Exception as exc:
            last_err = exc
            if attempt >= max_retries:
                break
            sleep_sec = float(2 ** (attempt - 1))
            time.sleep(sleep_sec)
    raise RuntimeError(f"Fetch failed for {symbol_key}: {last_err}")


def _upsert_rows(
    db: Database,
    symbol_key: str,
    df: pd.DataFrame,
    available_cols: List[str],
    source_label: str,
) -> Dict[str, int]:
    if df is None or df.empty:
        return {"fetched_rows": 0, "inserted": 0, "updated": 0, "skipped_overflow": 0}

    base_cols = ["symbol_key", "trading_date", "open", "high", "low", "close", "volume"]
    optional_cols = ["adj_close", "source", "updated_at", "symbol_key_old_backup"]
    cols = [c for c in base_cols if c in available_cols] + [c for c in optional_cols if c in available_cols]

    if "symbol_key" not in cols or "trading_date" not in cols or "close" not in cols:
        raise RuntimeError("price_daily columns are missing required keys: symbol_key/trading_date/close")

    placeholders = ", ".join(["%s"] * len(cols))
    update_cols = [c for c in cols if c not in ("symbol_key", "trading_date")]
    update_clause = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols]) or "close = EXCLUDED.close"
    sql = (
        f"INSERT INTO price_daily ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (symbol_key, trading_date) DO UPDATE SET {update_clause} "
        f"RETURNING (xmax = 0) AS inserted"
    )

    inserted = 0
    updated = 0
    skipped_overflow = 0
    for idx, row in df.iterrows():
        open_v = _to_num(row.get("Open"))
        high_v = _to_num(row.get("High"))
        low_v = _to_num(row.get("Low"))
        close_v = _to_num(row.get("Close"))
        adj_close_v = _to_num(row.get("Adj Close"))
        if (
            _is_price_overflow(open_v)
            or _is_price_overflow(high_v)
            or _is_price_overflow(low_v)
            or _is_price_overflow(close_v)
            or _is_price_overflow(adj_close_v)
        ):
            skipped_overflow += 1
            continue

        trading_date = pd.to_datetime(idx).date()
        params: List[Any] = []
        for col in cols:
            if col == "symbol_key":
                params.append(symbol_key)
            elif col == "trading_date":
                params.append(trading_date)
            elif col == "open":
                params.append(open_v)
            elif col == "high":
                params.append(high_v)
            elif col == "low":
                params.append(low_v)
            elif col == "close":
                params.append(close_v)
            elif col == "adj_close":
                params.append(adj_close_v)
            elif col == "volume":
                params.append(_to_int(row.get("Volume")))
            elif col == "source":
                params.append(source_label)
            elif col == "updated_at":
                params.append(datetime.now())
            elif col == "symbol_key_old_backup":
                # Legacy schema compatibility: this column may be NOT NULL.
                params.append(symbol_key)
            else:
                params.append(None)
        db.cursor.execute(sql, tuple(params))
        ret = db.cursor.fetchone()
        if ret and bool(ret[0]):
            inserted += 1
        else:
            updated += 1

    db.connection.commit()
    return {
        "fetched_rows": int(len(df)),
        "inserted": inserted,
        "updated": updated,
        "skipped_overflow": skipped_overflow,
    }


def _to_num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
        return None if math.isnan(v) else v
    except Exception:
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        v = int(float(value))
        return None if v < 0 else v
    except Exception:
        return None


def _is_price_overflow(value: Optional[float]) -> bool:
    if value is None:
        return False
    return abs(value) > MAX_PRICE_ABS


def main() -> int:
    args = _parse_args()
    symbols = [_normalize_symbol_key(s) for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise ValueError("No valid symbols provided.")

    start_opt = _parse_date_opt(args.start, "start")
    end_date = _parse_date_opt(args.end, "end") or date.today()
    if start_opt is not None and start_opt > end_date:
        raise ValueError(f"start must be <= end: {start_opt} > {end_date}")

    db = Database()
    if not db.connect():
        raise RuntimeError("DB connect failed.")

    run_at = datetime.now().isoformat(timespec="seconds")
    failures: List[str] = []
    results: List[SymbolResult] = []
    try:
        available_cols = _load_price_daily_columns(db)
        for symbol_key in symbols:
            before = _db_stats(db, symbol_key, end_date)
            last_date = (
                datetime.strptime(before["max_date"], "%Y-%m-%d").date()
                if before.get("max_date")
                else None
            )
            fetch_start = start_opt
            if last_date is not None and not args.force_start:
                next_day = last_date + timedelta(days=1)
                fetch_start = max(fetch_start, next_day) if fetch_start is not None else next_day

            fetch_meta: Dict[str, Any] = {
                "from": fetch_start.isoformat() if fetch_start is not None else None,
                "to": end_date.isoformat(),
                "attempts": 0,
                "source_used": None,
                "skipped": False,
            }
            upsert_meta: Dict[str, Any] = {"fetched_rows": 0, "inserted": 0, "updated": 0, "skipped_overflow": 0}

            if args.db_only_check:
                fetch_meta["skipped"] = True
                fetch_meta["skip_reason"] = "db-only-check"
            elif fetch_start is not None and fetch_start > end_date:
                fetch_meta["skipped"] = True
                fetch_meta["skip_reason"] = "already_up_to_date"
            elif (
                args.max_age_days is not None
                and last_date is not None
                and (end_date - last_date).days <= args.max_age_days
            ):
                fetch_meta["skipped"] = True
                fetch_meta["skip_reason"] = f"max-age-days({args.max_age_days})"
            else:
                try:
                    fetched_df, source_used, attempts = _fetch_with_retry(
                        symbol_key=symbol_key,
                        source=args.source,
                        start_date=fetch_start,
                        end_date=end_date,
                        max_retries=args.max_retries,
                    )
                    fetch_meta["attempts"] = attempts
                    fetch_meta["source_used"] = source_used
                    upsert_meta = _upsert_rows(
                        db=db,
                        symbol_key=symbol_key,
                        df=fetched_df,
                        available_cols=available_cols,
                        source_label=args.source,
                    )
                except Exception as exc:
                    # Recover transaction after failed INSERT/UPDATE before further queries.
                    if db.connection is not None:
                        db.connection.rollback()
                    failures.append(symbol_key)
                    after_fail = _db_stats(db, symbol_key, end_date)
                    results.append(
                        SymbolResult(
                            symbol_key=symbol_key,
                            status="FAILED",
                            db_before=before,
                            db_after=after_fail,
                            fetch=fetch_meta,
                            upsert=upsert_meta,
                            error=str(exc),
                        )
                    )
                    continue

            after = _db_stats(db, symbol_key, end_date)
            results.append(
                SymbolResult(
                    symbol_key=symbol_key,
                    status="OK",
                    db_before=before,
                    db_after=after,
                    fetch=fetch_meta,
                    upsert=upsert_meta,
                )
            )
    finally:
        db.disconnect()

    payload: Dict[str, Any] = {
        "run_at": run_at,
        "source": args.source,
        "db_only_check": bool(args.db_only_check),
        "start": start_opt.isoformat() if start_opt else None,
        "end": end_date.isoformat(),
        "symbols": [r.__dict__ for r in results],
        "totals": {
            "symbols": len(results),
            "failed": len(failures),
            "fetched_rows": int(sum(r.upsert.get("fetched_rows", 0) for r in results)),
            "inserted": int(sum(r.upsert.get("inserted", 0) for r in results)),
            "updated": int(sum(r.upsert.get("updated", 0) for r in results)),
            "skipped_overflow": int(sum(r.upsert.get("skipped_overflow", 0) for r in results)),
        },
    }

    out_dir = Path(__file__).resolve().parents[1] / "ml_predictor_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"refresh_market_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("[REFRESH SUMMARY]")
    print(f"  source: {args.source}")
    print(f"  period: {payload['start']} -> {payload['end']}")
    print(f"  symbols: {payload['totals']['symbols']}  failed: {payload['totals']['failed']}")
    print(
        "  fetched_rows: {fetched}  inserted: {inserted}  updated: {updated}  skipped_overflow: {skipped}".format(
            fetched=payload["totals"]["fetched_rows"],
            inserted=payload["totals"]["inserted"],
            updated=payload["totals"]["updated"],
            skipped=payload["totals"]["skipped_overflow"],
        )
    )
    for r in results:
        print(
            "  - {sym}: {st} before(max={bmax},rows={brows}) "
            "after(max={amax},rows={arows}) fetch_from={f_from} attempts={att} source={src}".format(
                sym=r.symbol_key,
                st=r.status,
                bmax=r.db_before.get("max_date"),
                brows=r.db_before.get("rows"),
                amax=r.db_after.get("max_date"),
                arows=r.db_after.get("rows"),
                f_from=r.fetch.get("from"),
                att=r.fetch.get("attempts"),
                src=r.fetch.get("source_used"),
            )
        )
        if r.error:
            print(f"    error: {r.error}")
    print(f"  json: {out_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
