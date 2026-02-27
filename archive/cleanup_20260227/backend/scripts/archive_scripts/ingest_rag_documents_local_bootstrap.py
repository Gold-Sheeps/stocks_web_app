from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from typing import Any, Dict, List

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.rag_ingest_service import RagIngestService


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap rag_documents from local DB/CSV signals (textified).")
    p.add_argument("--asof", required=True, help="As-of date YYYY-MM-DD")
    p.add_argument("--tickers-file", default=None, help="Optional ticker file (US:AAPL per line)")
    p.add_argument("--max-symbols", type=int, default=100)
    return p.parse_args()


def _load_tickers_file(path: str | None) -> List[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"tickers file not found: {path}")
    out: List[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip().upper()
        if not s:
            continue
        if ":" not in s:
            s = f"US:{s}"
        if s not in out:
            out.append(s)
    return out


def _fallback_symbols(db: Database, asof: str, limit: int) -> List[str]:
    rows = db.execute_query(
        """
        SELECT DISTINCT symbol_key
        FROM price_daily
        WHERE trading_date <= %s
        ORDER BY symbol_key
        LIMIT %s
        """,
        (asof, int(limit)),
    ) or []
    return [str(r[0]).upper() for r in rows if r and r[0]]


def _latest_market_summary_text(db: Database) -> str:
    rows = db.execute_query(
        """
        SELECT data
        FROM market_environment
        ORDER BY check_date DESC
        LIMIT 1
        """
    ) or []
    if not rows:
        return "market environment update unavailable"
    raw = rows[0][0]
    if isinstance(raw, dict):
        d = raw
    else:
        import json
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
    gate = d.get("market_gate", "UNKNOWN")
    vix = d.get("vix_mode", "Unknown")
    ndd = d.get("nasdaq_dd_count_5w", "-")
    sdd = d.get("sp500_dd_count_5w", "-")
    return f"Market environment {gate}. VIX mode {vix}. Distribution days NASDAQ {ndd}, SP500 {sdd}."


def _symbol_snapshot_doc(db: Database, symbol_key: str, asof: str, market_text: str) -> Dict[str, Any] | None:
    rows = db.execute_query(
        """
        SELECT p.close, p.volume, ind.rsi14, ind.rs_rating, ind.dist_to_52w_high_pct, ind.pivot
        FROM price_daily p
        LEFT JOIN LATERAL (
            SELECT rsi14, rs_rating, dist_to_52w_high_pct, pivot
            FROM indicator_daily
            WHERE symbol_key = p.symbol_key AND trading_date <= p.trading_date
            ORDER BY trading_date DESC
            LIMIT 1
        ) ind ON true
        WHERE p.symbol_key = %s AND p.trading_date <= %s
        ORDER BY p.trading_date DESC
        LIMIT 1
        """,
        (symbol_key, asof),
    ) or []
    if not rows:
        return None
    close, volume, rsi, rs_rating, dist_52w, pivot = rows[0]
    sym = symbol_key.split(":", 1)[1] if ":" in symbol_key else symbol_key

    tags: List[str] = []
    positives: List[str] = []
    negatives: List[str] = []
    if rs_rating is not None and float(rs_rating) >= 80:
        positives.append("strong relative strength")
        tags.append("leader")
    elif rs_rating is not None and float(rs_rating) < 50:
        negatives.append("relative strength warning")
    if rsi is not None and 40 <= float(rsi) <= 70:
        positives.append("momentum neutral to strong")
    elif rsi is not None and float(rsi) > 75:
        negatives.append("overbought warning")
    elif rsi is not None and float(rsi) < 30:
        negatives.append("oversold risk warning")
    if dist_52w is not None and float(dist_52w) >= -10:
        positives.append("near 52 week high breakout setup")
        tags.append("breakout")
    if pivot is not None and close is not None:
        if float(close) >= float(pivot):
            positives.append("breakout above pivot")
        else:
            negatives.append("below pivot no breakout yet")

    tone_words = []
    if positives:
        tone_words.append("strong")
        tone_words.append("growth")
    if negatives:
        tone_words.append("warning")
    if not tone_words:
        tone_words.append("neutral")

    title = f"{sym} local signal digest {'/'.join(tags) if tags else 'snapshot'}"
    body = (
        f"{sym} technical summary on {asof}. Price {close}, volume {volume}, RSI {rsi}, RS {rs_rating}. "
        f"{'; '.join(positives) if positives else 'no positive headline'}. "
        f"{'; '.join(negatives) if negatives else 'no major warning'}. "
        f"Analyst tone {' '.join(tone_words)}. {market_text}"
    )
    return {
        "source_type": "local_snapshot",
        "source_ref": f"local_snapshot:{symbol_key}:{asof}",
        "published_at": asof,
        "title": title,
        "content_text": body,
        "metadata": {"bootstrap": True, "kind": "symbol_snapshot"},
    }


def _canslim_docs(symbols: List[str], asof: str) -> List[Dict[str, Any]]:
    csv_path = Path(__file__).resolve().parents[2] / "canslim_breakout_report.csv"
    if not csv_path.exists():
        return []
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return []
    if "ApiTicker" not in df.columns:
        return []
    symbol_set = {s.upper() for s in symbols}
    docs: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        sym = str(row.get("ApiTicker") or "").strip().upper()
        if not sym or sym not in symbol_set:
            continue
        title = f"{sym} CANSLIM breakout report snapshot"
        body = (
            f"{sym} breakout screen snapshot. RS rating {row.get('RS_Rating')}. "
            f"base_break={row.get('base_break')} within_buy_range={row.get('within_buy_range')} "
            f"volume_up_on_break={row.get('volume_up_on_break')} pivot={row.get('pivot')} "
            f"current_price={row.get('current_price')} near_high={row.get('near_high')}. "
            "Potential breakout setup strong if volume confirms."
        )
        docs.append(
            {
                "source_type": "canslim_csv",
                "source_ref": f"canslim_csv:{sym}:{asof}",
                "published_at": asof,
                "title": title,
                "content_text": body,
                "metadata": {"bootstrap": True, "kind": "canslim_csv"},
            }
        )
    return docs


def main() -> int:
    args = _parse_args()
    _ = datetime.strptime(args.asof, "%Y-%m-%d")
    db = Database()
    if not db.connect():
        print("[ERROR] DB connection failed")
        return 1
    try:
        symbols = _load_tickers_file(args.tickers_file) or _fallback_symbols(db, args.asof, args.max_symbols)
        market_text = _latest_market_summary_text(db)
        ingest = RagIngestService()
        docs_by_symbol: Dict[str, List[Dict[str, Any]]] = {s: [] for s in symbols}

        for sym in symbols:
            d = _symbol_snapshot_doc(db, sym, args.asof, market_text)
            if d:
                docs_by_symbol[sym].append(d)

        for d in _canslim_docs(symbols, args.asof):
            sym_ref = str(d.get("source_ref") or "")
            # source_ref is canslim_csv:{sym}:{asof}
            parts = sym_ref.split(":")
            sym = ":".join(parts[1:3]) if len(parts) >= 4 else None
            if sym and sym in docs_by_symbol:
                docs_by_symbol[sym].append(d)

        total_docs = 0
        symbols_written = 0
        for sym, docs in docs_by_symbol.items():
            if not docs:
                continue
            res = ingest.ingest_documents(sym, docs)
            total_docs += int(res.get("inserted", 0))
            symbols_written += 1
            print(f"symbol={sym} docs={len(docs)} ingest_ok={res.get('ok')}")
        print(f"symbols={len(symbols)} symbols_written={symbols_written} total_docs={total_docs}")
        return 0
    finally:
        db.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())

