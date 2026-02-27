from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.rag_ingest_service import RagIngestService


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest rag_documents from JSON file.")
    p.add_argument("--symbol", required=True, help="symbol_key (e.g., US:AAPL)")
    p.add_argument("--file", required=True, help="JSON array of documents")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    path = Path(args.file)
    if not path.exists():
        print(f"[ERROR] file not found: {path}")
        return 1
    docs = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(docs, list):
        print("[ERROR] JSON root must be array")
        return 1
    res = RagIngestService().ingest_documents(args.symbol, docs)
    print(res)
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

