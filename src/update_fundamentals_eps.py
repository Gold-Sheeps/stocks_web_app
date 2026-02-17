"""
Fetch quarterly fundamentals from Yahoo Finance and upsert into fundamentals table.

Stored metrics per quarter:
- revenue
- net_income
- eps (diluted EPS preferred)
- operating_cash_flow
- free_cash_flow
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import psycopg
import pandas as pd

# yfinance timezone cache path fix (Windows env dependent issues)
_YF_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "yfinance"
_YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YFINANCE_TZ_CACHE_LOCATION", str(_YF_CACHE_DIR))

import yfinance as yf

yf.set_tz_cache_location(str(_YF_CACHE_DIR))


DB_CONNINFO = "host=localhost port=5432 dbname=postgres user=postgres password=test"


def is_equity_symbol(raw_symbol: str) -> bool:
    if not raw_symbol:
        return False
    if raw_symbol.startswith("^"):
        return False
    if "=X" in raw_symbol or "=F" in raw_symbol:
        return False
    return True


def normalize_to_yf_symbol(raw_symbol: str) -> str:
    # yfinance uses BRK-B style for class share symbols.
    return raw_symbol.replace(".", "-").strip()


def collect_symbols(conn: psycopg.Connection, universe: str) -> list[str]:
    with conn.cursor() as cur:
        if universe == "sector":
            cur.execute(
                """
                SELECT DISTINCT constituent_symbol
                FROM sector_constituents
                WHERE constituent_symbol IS NOT NULL
                ORDER BY constituent_symbol
                """
            )
            symbols = [r[0] for r in cur.fetchall()]
        elif universe == "us-active":
            cur.execute(
                """
                SELECT DISTINCT REPLACE(symbol_key, 'US:', '')
                FROM instruments
                WHERE market = 'US'
                  AND is_active = true
                  AND symbol_key LIKE 'US:%%'
                ORDER BY 1
                """
            )
            symbols = [r[0] for r in cur.fetchall()]
        else:
            cur.execute(
                """
                SELECT DISTINCT
                    CASE
                        WHEN symbol_key LIKE 'US:%%' THEN REPLACE(symbol_key, 'US:', '')
                        ELSE symbol_key
                    END
                FROM instruments
                WHERE symbol_key IS NOT NULL
                ORDER BY 1
                """
            )
            symbols = [r[0] for r in cur.fetchall()]

    return [s for s in symbols if is_equity_symbol(s)]


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol TEXT NOT NULL,
                period_end_date DATE NOT NULL,
                eps DECIMAL,
                revenue DECIMAL,
                net_income DECIMAL,
                operating_cash_flow DECIMAL,
                free_cash_flow DECIMAL,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, period_end_date)
            );
            """
        )

        # Backward compatible ALTERs for existing table.
        for col in ["net_income", "operating_cash_flow", "free_cash_flow"]:
            cur.execute(f"ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS {col} DECIMAL;")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamentals_comparison_latest (
                symbol TEXT PRIMARY KEY,
                latest_period DATE,
                prev_period DATE,
                yoy_period DATE,
                revenue_qoq_diff DECIMAL,
                revenue_qoq_pct DECIMAL,
                revenue_yoy_diff DECIMAL,
                revenue_yoy_pct DECIMAL,
                net_income_qoq_diff DECIMAL,
                net_income_qoq_pct DECIMAL,
                net_income_yoy_diff DECIMAL,
                net_income_yoy_pct DECIMAL,
                eps_qoq_diff DECIMAL,
                eps_qoq_pct DECIMAL,
                eps_yoy_diff DECIMAL,
                eps_yoy_pct DECIMAL,
                operating_cash_flow_qoq_diff DECIMAL,
                operating_cash_flow_qoq_pct DECIMAL,
                operating_cash_flow_yoy_diff DECIMAL,
                operating_cash_flow_yoy_pct DECIMAL,
                free_cash_flow_qoq_diff DECIMAL,
                free_cash_flow_qoq_pct DECIMAL,
                free_cash_flow_yoy_diff DECIMAL,
                free_cash_flow_yoy_pct DECIMAL,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamentals_ratios_latest (
                symbol TEXT PRIMARY KEY,
                as_of_date DATE,
                pe_ratio DECIMAL,
                forward_pe DECIMAL,
                roe_pct DECIMAL,
                roa_pct DECIMAL,
                net_margin_pct DECIMAL,
                debt_equity DECIMAL,
                current_ratio DECIMAL,
                quick_ratio DECIMAL,
                asset_turnover DECIMAL,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    conn.commit()


def _first_series(df: Optional[pd.DataFrame], keys: list[str]) -> Optional[pd.Series]:
    if df is None or df.empty:
        return None
    index_map = {str(i).strip().lower(): i for i in df.index}
    for k in keys:
        key = k.strip().lower()
        if key in index_map:
            return df.loc[index_map[key]]
    return None


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        return float(v)
    except Exception:
        return None


def fetch_quarterly_fundamentals(raw_symbol: str, max_quarters: int = 8, include_snapshot: bool = True) -> list[dict]:
    yf_symbol = normalize_to_yf_symbol(raw_symbol)
    ticker = yf.Ticker(yf_symbol)

    q_income = ticker.quarterly_income_stmt
    if q_income is None or q_income.empty:
        q_income = ticker.quarterly_financials
    q_cash = ticker.quarterly_cashflow
    if q_cash is None or q_cash.empty:
        q_cash = ticker.quarterly_cash_flow

    rev = _first_series(q_income, ["Total Revenue", "Revenue"])
    ni = _first_series(
        q_income,
        [
            "Net Income",
            "Net Income Common Stockholders",
            "Net Income From Continuing Operation Net Minority Interest",
        ],
    )
    eps = _first_series(q_income, ["Diluted EPS", "Basic EPS"])
    diluted_shares = _first_series(q_income, ["Diluted Average Shares", "Diluted Shares Number"])
    ocf = _first_series(q_cash, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    fcf = _first_series(q_cash, ["Free Cash Flow"])
    capex = _first_series(q_cash, ["Capital Expenditure", "Capital Expenditures"])

    periods = set()
    for s in [rev, ni, eps, ocf, fcf]:
        if s is not None:
            periods.update(list(s.index))

    rows: list[dict] = []

    if periods:
        # Latest -> older
        sorted_periods = sorted(periods, reverse=True)[: max(1, max_quarters)]
        for p in sorted_periods:
            period_date = pd.Timestamp(p).date()
            row = {
                "period_end_date": period_date,
                "revenue": _to_float(rev.get(p)) if rev is not None else None,
                "net_income": _to_float(ni.get(p)) if ni is not None else None,
                "eps": _to_float(eps.get(p)) if eps is not None else None,
                "operating_cash_flow": _to_float(ocf.get(p)) if ocf is not None else None,
                "free_cash_flow": _to_float(fcf.get(p)) if fcf is not None else None,
            }
            cpx = _to_float(capex.get(p)) if capex is not None else None
            dshares = _to_float(diluted_shares.get(p)) if diluted_shares is not None else None
            _derive_missing_fields(row, cpx, dshares)
            rows.append(row)

    if include_snapshot:
        info = ticker.info or {}
        today = datetime.today().date()
        snapshot = {
            "period_end_date": today,
            "revenue": _to_float(info.get("totalRevenue")),
            "net_income": _to_float(info.get("netIncomeToCommon")),
            "eps": _to_float(info.get("trailingEps") or info.get("epsTrailingTwelveMonths")),
            "operating_cash_flow": _to_float(info.get("operatingCashflow")),
            "free_cash_flow": _to_float(info.get("freeCashflow")),
        }
        if any(snapshot[k] is not None for k in ["revenue", "net_income", "eps", "operating_cash_flow", "free_cash_flow"]):
            # Keep one row per period_end_date; today's snapshot becomes latest.
            rows = [r for r in rows if r["period_end_date"] != today]
            rows.insert(0, snapshot)

    return rows


def _derive_missing_fields(row: dict, capex: Optional[float], diluted_shares: Optional[float]) -> None:
    # FCF fallback: derive from OCF and CapEx when FCF is missing.
    if row.get("free_cash_flow") is None and row.get("operating_cash_flow") is not None and capex is not None:
        # CapEx is usually negative (cash outflow). Normalize both sign conventions.
        if capex <= 0:
            row["free_cash_flow"] = row["operating_cash_flow"] + capex
        else:
            row["free_cash_flow"] = row["operating_cash_flow"] - capex

    # EPS / Net Income cross-derivation via diluted shares.
    if diluted_shares and diluted_shares != 0:
        if row.get("eps") is None and row.get("net_income") is not None:
            row["eps"] = row["net_income"] / diluted_shares
        if row.get("net_income") is None and row.get("eps") is not None:
            row["net_income"] = row["eps"] * diluted_shares


def _calc_growth(rows_desc: list[dict], metric: str, base_idx: int, prev_idx: Optional[int]) -> tuple[Optional[float], Optional[float]]:
    if prev_idx is None or prev_idx >= len(rows_desc):
        return None, None
    a = rows_desc[base_idx].get(metric)
    b = rows_desc[prev_idx].get(metric)
    if a is None or b is None:
        return None, None
    diff = float(a) - float(b)
    pct = None if float(b) == 0 else (diff / float(b)) * 100.0
    return diff, pct


def _find_yoy_idx(rows_desc: list[dict], base_idx: int) -> Optional[int]:
    if not rows_desc:
        return None
    latest_dt = rows_desc[base_idx]["period_end_date"]
    for idx in range(base_idx + 1, len(rows_desc)):
        d = rows_desc[idx]["period_end_date"]
        if d.year == latest_dt.year - 1 and d.month == latest_dt.month:
            return idx
    return 4 if len(rows_desc) > 4 else None


def upsert_comparison_latest(conn: psycopg.Connection, raw_symbol: str, rows_desc: list[dict]) -> None:
    if len(rows_desc) == 0:
        return

    base_idx = 0
    prev_idx = 1 if len(rows_desc) > 1 else None
    yoy_idx = _find_yoy_idx(rows_desc, base_idx)

    metrics = ["revenue", "net_income", "eps", "operating_cash_flow", "free_cash_flow"]
    vals = {}
    for m in metrics:
        qd, qp = _calc_growth(rows_desc, m, base_idx, prev_idx)
        yd, yp = _calc_growth(rows_desc, m, base_idx, yoy_idx)
        vals[f"{m}_qoq_diff"] = qd
        vals[f"{m}_qoq_pct"] = qp
        vals[f"{m}_yoy_diff"] = yd
        vals[f"{m}_yoy_pct"] = yp

    latest_period = rows_desc[base_idx]["period_end_date"]
    prev_period = rows_desc[prev_idx]["period_end_date"] if prev_idx is not None else None
    yoy_period = rows_desc[yoy_idx]["period_end_date"] if yoy_idx is not None else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fundamentals_comparison_latest (
                symbol, latest_period, prev_period, yoy_period,
                revenue_qoq_diff, revenue_qoq_pct, revenue_yoy_diff, revenue_yoy_pct,
                net_income_qoq_diff, net_income_qoq_pct, net_income_yoy_diff, net_income_yoy_pct,
                eps_qoq_diff, eps_qoq_pct, eps_yoy_diff, eps_yoy_pct,
                operating_cash_flow_qoq_diff, operating_cash_flow_qoq_pct, operating_cash_flow_yoy_diff, operating_cash_flow_yoy_pct,
                free_cash_flow_qoq_diff, free_cash_flow_qoq_pct, free_cash_flow_yoy_diff, free_cash_flow_yoy_pct,
                updated_at
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (symbol) DO UPDATE SET
                latest_period = EXCLUDED.latest_period,
                prev_period = EXCLUDED.prev_period,
                yoy_period = EXCLUDED.yoy_period,
                revenue_qoq_diff = EXCLUDED.revenue_qoq_diff,
                revenue_qoq_pct = EXCLUDED.revenue_qoq_pct,
                revenue_yoy_diff = EXCLUDED.revenue_yoy_diff,
                revenue_yoy_pct = EXCLUDED.revenue_yoy_pct,
                net_income_qoq_diff = EXCLUDED.net_income_qoq_diff,
                net_income_qoq_pct = EXCLUDED.net_income_qoq_pct,
                net_income_yoy_diff = EXCLUDED.net_income_yoy_diff,
                net_income_yoy_pct = EXCLUDED.net_income_yoy_pct,
                eps_qoq_diff = EXCLUDED.eps_qoq_diff,
                eps_qoq_pct = EXCLUDED.eps_qoq_pct,
                eps_yoy_diff = EXCLUDED.eps_yoy_diff,
                eps_yoy_pct = EXCLUDED.eps_yoy_pct,
                operating_cash_flow_qoq_diff = EXCLUDED.operating_cash_flow_qoq_diff,
                operating_cash_flow_qoq_pct = EXCLUDED.operating_cash_flow_qoq_pct,
                operating_cash_flow_yoy_diff = EXCLUDED.operating_cash_flow_yoy_diff,
                operating_cash_flow_yoy_pct = EXCLUDED.operating_cash_flow_yoy_pct,
                free_cash_flow_qoq_diff = EXCLUDED.free_cash_flow_qoq_diff,
                free_cash_flow_qoq_pct = EXCLUDED.free_cash_flow_qoq_pct,
                free_cash_flow_yoy_diff = EXCLUDED.free_cash_flow_yoy_diff,
                free_cash_flow_yoy_pct = EXCLUDED.free_cash_flow_yoy_pct,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                raw_symbol,
                latest_period,
                prev_period,
                yoy_period,
                vals["revenue_qoq_diff"], vals["revenue_qoq_pct"], vals["revenue_yoy_diff"], vals["revenue_yoy_pct"],
                vals["net_income_qoq_diff"], vals["net_income_qoq_pct"], vals["net_income_yoy_diff"], vals["net_income_yoy_pct"],
                vals["eps_qoq_diff"], vals["eps_qoq_pct"], vals["eps_yoy_diff"], vals["eps_yoy_pct"],
                vals["operating_cash_flow_qoq_diff"], vals["operating_cash_flow_qoq_pct"], vals["operating_cash_flow_yoy_diff"], vals["operating_cash_flow_yoy_pct"],
                vals["free_cash_flow_qoq_diff"], vals["free_cash_flow_qoq_pct"], vals["free_cash_flow_yoy_diff"], vals["free_cash_flow_yoy_pct"],
            ),
        )


def fetch_ratio_snapshot(raw_symbol: str) -> dict:
    yf_symbol = normalize_to_yf_symbol(raw_symbol)
    ticker = yf.Ticker(yf_symbol)
    info = ticker.info or {}

    def _to_pct(v):
        vv = _to_float(v)
        if vv is None:
            return None
        # yfinance often returns ROE/ROA/profitMargins as 0.x
        return vv * 100.0 if abs(vv) <= 2 else vv

    pe_ratio = _to_float(info.get("trailingPE"))
    forward_pe = _to_float(info.get("forwardPE"))
    roe_pct = _to_pct(info.get("returnOnEquity"))
    roa_pct = _to_pct(info.get("returnOnAssets"))
    net_margin_pct = _to_pct(info.get("profitMargins"))
    debt_equity = _to_float(info.get("debtToEquity"))
    current_ratio = _to_float(info.get("currentRatio"))
    quick_ratio = _to_float(info.get("quickRatio"))

    # Asset Turnover fallback: totalRevenue / totalAssets
    asset_turnover = None
    total_rev = _to_float(info.get("totalRevenue"))
    total_assets = None
    try:
        qbs = ticker.quarterly_balance_sheet
        if qbs is None or qbs.empty:
            qbs = ticker.balance_sheet
        if qbs is not None and not qbs.empty:
            bs_keys = {str(i).strip().lower(): i for i in qbs.index}
            for k in ["total assets", "totalassets"]:
                if k in bs_keys:
                    s = qbs.loc[bs_keys[k]]
                    if len(s) > 0:
                        total_assets = _to_float(s.iloc[0])
                        break
    except Exception:
        pass
    if total_rev is not None and total_assets not in (None, 0):
        asset_turnover = total_rev / total_assets

    return {
        "pe_ratio": pe_ratio,
        "forward_pe": forward_pe,
        "roe_pct": roe_pct,
        "roa_pct": roa_pct,
        "net_margin_pct": net_margin_pct,
        "debt_equity": debt_equity,
        "current_ratio": current_ratio,
        "quick_ratio": quick_ratio,
        "asset_turnover": asset_turnover,
    }


def upsert_ratios_latest(conn: psycopg.Connection, raw_symbol: str, ratio: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fundamentals_ratios_latest (
                symbol, as_of_date, pe_ratio, forward_pe, roe_pct, roa_pct, net_margin_pct,
                debt_equity, current_ratio, quick_ratio, asset_turnover, updated_at
            ) VALUES (
                %s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
            )
            ON CONFLICT (symbol) DO UPDATE SET
                as_of_date = EXCLUDED.as_of_date,
                pe_ratio = EXCLUDED.pe_ratio,
                forward_pe = EXCLUDED.forward_pe,
                roe_pct = EXCLUDED.roe_pct,
                roa_pct = EXCLUDED.roa_pct,
                net_margin_pct = EXCLUDED.net_margin_pct,
                debt_equity = EXCLUDED.debt_equity,
                current_ratio = EXCLUDED.current_ratio,
                quick_ratio = EXCLUDED.quick_ratio,
                asset_turnover = EXCLUDED.asset_turnover,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                raw_symbol,
                ratio.get("pe_ratio"),
                ratio.get("forward_pe"),
                ratio.get("roe_pct"),
                ratio.get("roa_pct"),
                ratio.get("net_margin_pct"),
                ratio.get("debt_equity"),
                ratio.get("current_ratio"),
                ratio.get("quick_ratio"),
                ratio.get("asset_turnover"),
            ),
        )


def upsert_fundamental(conn: psycopg.Connection, raw_symbol: str, row: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO fundamentals (
                symbol,
                period_end_date,
                eps,
                revenue,
                net_income,
                operating_cash_flow,
                free_cash_flow,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, period_end_date) DO UPDATE SET
                eps = EXCLUDED.eps,
                revenue = EXCLUDED.revenue,
                net_income = EXCLUDED.net_income,
                operating_cash_flow = EXCLUDED.operating_cash_flow,
                free_cash_flow = EXCLUDED.free_cash_flow,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                raw_symbol,
                row["period_end_date"],
                row["eps"],
                row["revenue"],
                row["net_income"],
                row["operating_cash_flow"],
                row["free_cash_flow"],
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Update quarterly fundamentals from Yahoo Finance.")
    parser.add_argument(
        "--universe",
        choices=["sector", "us-active", "all"],
        default="sector",
        help="Target symbol universe.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for quick runs (0 = all).")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay seconds between Yahoo calls.")
    parser.add_argument(
        "--quarters",
        type=int,
        default=8,
        help="Max quarterly periods to upsert per symbol.",
    )
    parser.add_argument(
        "--include-snapshot",
        action="store_true",
        help="Also upsert latest snapshot (today) using ticker.info fields.",
    )
    args = parser.parse_args()

    conn = psycopg.connect(DB_CONNINFO)
    processed = 0
    saved_rows = 0
    failed = 0
    no_data = 0

    try:
        ensure_schema(conn)
        symbols = collect_symbols(conn, args.universe)
        if args.limit and args.limit > 0:
            symbols = symbols[: args.limit]

        print(f"[FUND] universe={args.universe}, targets={len(symbols)}, quarters={args.quarters}")

        for idx, raw_symbol in enumerate(symbols, 1):
            processed += 1
            try:
                rows = fetch_quarterly_fundamentals(
                    raw_symbol,
                    max_quarters=args.quarters,
                    include_snapshot=args.include_snapshot,
                )
                if not rows:
                    no_data += 1
                    print(f"[{idx}/{len(symbols)}] {raw_symbol}: no quarterly fundamentals")
                else:
                    for row in rows:
                        upsert_fundamental(conn, raw_symbol, row)
                    upsert_comparison_latest(conn, raw_symbol, rows)
                    ratio = fetch_ratio_snapshot(raw_symbol)
                    upsert_ratios_latest(conn, raw_symbol, ratio)
                    conn.commit()
                    saved_rows += len(rows)
                    latest = rows[0]
                    print(
                        f"[{idx}/{len(symbols)}] {raw_symbol}: rows={len(rows)} "
                        f"latest={latest['period_end_date']} eps={latest['eps']} revenue={latest['revenue']}"
                    )
            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"[{idx}/{len(symbols)}] {raw_symbol}: failed ({e})")
            time.sleep(max(args.delay, 0))

        print("=" * 60)
        print(
            f"Done at {datetime.now().isoformat(timespec='seconds')} "
            f"processed={processed} saved_rows={saved_rows} no_data={no_data} failed={failed}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
