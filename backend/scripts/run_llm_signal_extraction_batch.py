from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.rag_retrieval_service import RagRetrievalService
from app.services.llm_signal_extraction_service import LlmSignalExtractionService
from app.services.explanation_summary_service import ExplanationSummaryService


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Precompute LLM-derived structured signal features (best-effort).")
    p.add_argument("--asof", required=True, help="As-of date (YYYY-MM-DD)")
    p.add_argument("--max-symbols", type=int, default=500)
    p.add_argument("--tickers-file", default=None, help="Optional ticker file to target specific symbols")
    return p.parse_args()


def _candidate_symbols(asof_str: str, limit: int) -> List[str]:
    db = Database()
    if not db.connect():
        return []
    try:
        rows = db.execute_query(
            """
            SELECT DISTINCT symbol_key
            FROM price_daily
            WHERE trading_date <= %s
            ORDER BY symbol_key
            LIMIT %s
            """,
            (asof_str, int(limit)),
        ) or []
        return [str(r[0]) for r in rows if r and r[0]]
    finally:
        db.disconnect()


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


def main() -> int:
    args = _parse_args()
    asof = datetime.strptime(args.asof, "%Y-%m-%d").date()
    symbols = _load_tickers_file(args.tickers_file) or _candidate_symbols(args.asof, args.max_symbols)
    retr = RagRetrievalService()
    ext = LlmSignalExtractionService()
    expl = ExplanationSummaryService()

    processed = 0
    extracted = 0
    for sym in symbols:
        processed += 1
        docs = retr.get_documents_for_asof(sym, asof=asof, limit=20)
        if not docs:
            continue
        out = ext.extract_structured_features(sym, asof, docs)
        save = ext.save_structured_features(
            symbol_key=sym,
            asof=asof,
            llm_features=dict(out.get("llm_features") or {}),
            evidence_items=list(out.get("evidence_items") or []),
            quality_metrics=dict(out.get("quality_metrics") or {}),
            extraction_status="success",
            point_in_time_ok=bool(out.get("point_in_time_ok", True)),
        )
        if save.get("ok"):
            extracted += 1
            summary = expl.summarize_evidence(list(out.get("evidence_items") or []))
            print(
                f"symbol={sym} asof={args.asof} evidence_set_id={save.get('evidence_set_id')} "
                f"llm_features={len(out.get('llm_features') or {})} summary={summary.get('summary_text') or '-'}"
            )

    print(f"processed={processed} extracted={extracted} asof={args.asof}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
