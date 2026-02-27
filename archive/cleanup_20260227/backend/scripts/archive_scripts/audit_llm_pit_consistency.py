from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit point-in-time consistency for LLM feature sets and evidence.")
    p.add_argument("--limit", type=int, default=1000)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    db = Database()
    if not db.connect():
        print("[ERROR] DB connection failed")
        return 1
    try:
        rows = db.execute_query(
            """
            SELECT fs.symbol_key, fs.asof, fs.evidence_set_id, ev.evidence_rank, ev.published_at
            FROM llm_signal_feature_sets fs
            LEFT JOIN llm_signal_evidence_items ev
              ON ev.evidence_set_id = fs.evidence_set_id
            ORDER BY fs.updated_at DESC
            LIMIT %s
            """,
            (int(args.limit),),
        ) or []
        violations = []
        checked = 0
        for r in rows:
            checked += 1
            sym, asof, evidence_set_id, rank, published_at = r
            if asof is None or published_at is None:
                continue
            if hasattr(published_at, "date") and published_at.date() > asof:
                violations.append((sym, asof, evidence_set_id, rank, published_at))
        print(f"checked_rows={checked} violations={len(violations)}")
        for v in violations[:20]:
            print("violation", v)
        return 0 if not violations else 2
    finally:
        db.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())

