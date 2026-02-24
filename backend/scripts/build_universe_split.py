from __future__ import annotations

import argparse
import random
import sys
import re
from pathlib import Path
from typing import List

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Database

_STOCKLIKE_RE = re.compile(r"^US:[A-Z][A-Z0-9.-]{0,9}$")


def _normalize_symbol(s: str) -> str:
    t = str(s or "").strip().upper()
    if not t:
        return ""
    return t if ":" in t else f"US:{t}"


def _read_ticker_file(path: Path) -> List[str]:
    if not path.exists():
        return []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        t = _normalize_symbol(line)
        if t and t not in out:
            out.append(t)
    return out


def _read_canslim_csv(project_root: Path) -> List[str]:
    cands = [
        project_root / "canslim_breakout_report.csv",
        project_root / "backend" / "ml_predictor_data" / "canslim_breakout_report.csv",
    ]
    for p in cands:
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p)
            col = next((c for c in ("symbol_key", "symbol", "ticker") if c in df.columns), None)
            if not col:
                continue
            out: List[str] = []
            for v in df[col].dropna().tolist():
                t = _normalize_symbol(str(v))
                if t and t not in out:
                    out.append(t)
            if out:
                return out
        except Exception:
            continue
    return []


def _fetch_volume_ranked_universe(
    limit: int = 400,
    start_date: str | None = None,
    asof_date: str | None = None,
    min_history_rows: int = 300,
) -> List[str]:
    db = Database()
    if not db.connect():
        raise RuntimeError("DB connection failed")
    try:
        sql = """
            SELECT
              symbol_key,
              AVG(
                CASE
                  WHEN (%s::date IS NULL OR trading_date >= %s::date) THEN volume
                  ELSE NULL
                END
              ) AS avg_vol
            FROM price_daily
            WHERE volume > 0
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
              AND symbol_key LIKE 'US:%%'
              AND (%s::date IS NULL OR trading_date <= %s::date)
            GROUP BY symbol_key
            HAVING COUNT(*) >= %s
               AND AVG(
                 CASE
                   WHEN (%s::date IS NULL OR trading_date >= %s::date) THEN volume
                   ELSE NULL
                 END
               ) IS NOT NULL
            ORDER BY avg_vol DESC
            LIMIT %s
        """
        rows = db.execute_query(
            sql,
            (
                start_date, start_date,            # AVG window
                asof_date, asof_date,              # WHERE asof upper bound
                int(min_history_rows),             # HAVING min total history
                start_date, start_date,            # HAVING avg window not null
                limit,
            ),
        ) or []
        return [str(r[0]).upper() for r in rows if r and r[0]]
    finally:
        db.disconnect()


def _fetch_instrument_names(symbols: List[str]) -> dict[str, str]:
    if not symbols:
        return {}
    db = Database()
    if not db.connect():
        return {}
    try:
        rows = db.execute_query(
            "SELECT symbol_key, name FROM instruments WHERE symbol_key = ANY(%s)",
            (symbols,),
        ) or []
        return {str(r[0]).upper(): str(r[1] or "") for r in rows if r and r[0]}
    finally:
        db.disconnect()


def _dedupe_keep_order(*lists: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for lst in lists:
        for x in lst:
            t = _normalize_symbol(x)
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
    return out


def _is_stocklike_us_equity(symbol_key: str) -> bool:
    s = str(symbol_key or "").upper()
    if not _STOCKLIKE_RE.match(s):
        return False
    raw = s.split(":", 1)[1]
    if raw.startswith("^"):
        return False
    if raw.endswith("-USD") or raw.endswith("USD"):
        return False
    # Exclude many broad ETFs / proxies that dilute stock-specific training.
    etf_like = {
        "SPY", "QQQ", "IWM", "DIA", "SMH", "SOXX", "XLF", "XLK", "XLE", "XLV", "EFA", "EEM", "TLT", "GLD", "SLV",
    }
    if raw in etf_like:
        return False
    return True


def _looks_like_fund_from_name(name: str) -> bool:
    n = str(name or "").lower()
    bad_tokens = [
        "etf", "etn", "fund", "trust", "spdr", "ishares", "vanguard", "invesco",
        "direxion", "proshares", "ultrapro", "ultra ", "2x", "3x", "bear ", "bull ",
    ]
    return any(tok in n for tok in bad_tokens)


def _write_list(path: Path, rows: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build universe_100/train_80/holdout_20 ticker files.")
    parser.add_argument("--project-root", default=".", help="Project root path.")
    parser.add_argument("--universe-size", type=int, default=100)
    parser.add_argument("--holdout-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default="backend/ml_predictor_data")
    parser.add_argument("--start", default="2025-06-01", help="Minimum training start date filter (YYYY-MM-DD).")
    parser.add_argument("--asof", default="2026-02-21", help="As-of date filter (YYYY-MM-DD).")
    parser.add_argument(
        "--min-history-rows",
        type=int,
        default=300,
        help="Minimum price_daily row count required in DB for universe candidates.",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    out_dir = (project_root / args.out_dir).resolve()

    base_tickers = _read_ticker_file(project_root / "tickers.txt")
    canslim_tickers = _read_canslim_csv(project_root)
    volume_ranked = _fetch_volume_ranked_universe(
        limit=max(400, int(args.universe_size) * 4),
        start_date=args.start,
        asof_date=args.asof,
        min_history_rows=int(args.min_history_rows),
    )

    # Priority order: explicit project tickers -> CANSLIM candidates -> liquid universe.
    merged = [t for t in _dedupe_keep_order(base_tickers, canslim_tickers, volume_ranked) if _is_stocklike_us_equity(t)]
    names = _fetch_instrument_names(merged)
    merged = [t for t in merged if not _looks_like_fund_from_name(names.get(t, ""))]
    universe_size = int(args.universe_size)
    holdout_size = int(args.holdout_size)
    if universe_size <= holdout_size:
        raise ValueError("universe-size must be greater than holdout-size")
    if len(merged) < universe_size:
        raise RuntimeError(f"Not enough tickers to build universe: {len(merged)} < {universe_size}")

    universe = merged[:universe_size]

    rng = random.Random(int(args.seed))
    shuffled = list(universe)
    rng.shuffle(shuffled)
    holdout = sorted(shuffled[:holdout_size])
    train = sorted(shuffled[holdout_size:])

    _write_list(out_dir / "universe_100.txt", universe)
    _write_list(out_dir / "train_80.txt", train)
    _write_list(out_dir / "holdout_20.txt", holdout)

    print(f"universe={len(universe)} train={len(train)} holdout={len(holdout)}")
    print(f"out_dir={out_dir}")
    print(f"seed={args.seed}")
    print(f"min_history_rows={args.min_history_rows}")
    print("sample_holdout=", ",".join(holdout[:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
