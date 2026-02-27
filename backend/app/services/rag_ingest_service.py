from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any, Dict, Iterable, List

from app.db.database import Database


class RagIngestService:
    """Normalize and persist source documents for downstream retrieval/extraction."""

    def ensure_tables(self, db: Database) -> None:
        db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS rag_documents (
                document_id TEXT PRIMARY KEY,
                symbol_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                published_at TIMESTAMP NULL,
                title TEXT NULL,
                content_text TEXT NULL,
                metadata_json TEXT NULL,
                content_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.execute_command(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_documents_dedupe
            ON rag_documents (symbol_key, source_type, source_ref, content_hash)
            """
        )
        db.execute_command(
            """
            CREATE INDEX IF NOT EXISTS idx_rag_documents_symbol_published
            ON rag_documents (symbol_key, published_at DESC)
            """
        )

    def normalize_documents(
        self,
        symbol_key: str,
        documents: Iterable[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for doc in documents or []:
            source_type = str(doc.get("source_type") or "unknown").strip().lower()
            source_ref = str(doc.get("source_ref") or doc.get("url") or doc.get("id") or "").strip()
            if not source_ref:
                continue
            title = str(doc.get("title") or "").strip() or None
            content_text = str(doc.get("content_text") or doc.get("text") or "").strip()
            published_at = self._parse_ts(doc.get("published_at"))
            metadata = dict(doc.get("metadata") or {})
            metadata.setdefault("ingest_version", "rag_ingest_v1")
            content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
            key_seed = f"{symbol_key}|{source_type}|{source_ref}|{content_hash}"
            document_id = hashlib.sha256(key_seed.encode("utf-8")).hexdigest()[:40]
            out.append(
                {
                    "document_id": document_id,
                    "symbol_key": symbol_key.upper(),
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "published_at": published_at,
                    "title": title,
                    "content_text": content_text,
                    "metadata_json": json.dumps(metadata, ensure_ascii=False),
                    "content_hash": content_hash,
                }
            )
        return out

    def ingest_documents(self, symbol_key: str, documents: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        symbol_key = symbol_key.upper()
        rows = self.normalize_documents(symbol_key, documents)
        db = Database()
        inserted = 0
        updated = 0
        if not db.connect():
            return {"ok": False, "symbol_key": symbol_key, "inserted": 0, "updated": 0}
        try:
            self.ensure_tables(db)
            for row in rows:
                ok = db.execute_command(
                    """
                    INSERT INTO rag_documents
                        (document_id, symbol_key, source_type, source_ref, published_at, title, content_text, metadata_json, content_hash, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                    ON CONFLICT (document_id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        content_text = EXCLUDED.content_text,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        row["document_id"],
                        row["symbol_key"],
                        row["source_type"],
                        row["source_ref"],
                        row["published_at"],
                        row["title"],
                        row["content_text"],
                        row["metadata_json"],
                        row["content_hash"],
                    ),
                )
                if ok:
                    updated += 1
            inserted = len(rows)
            return {"ok": True, "symbol_key": symbol_key, "inserted": inserted, "updated": updated}
        finally:
            db.disconnect()

    def _parse_ts(self, value: Any) -> dt.datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, dt.datetime):
            return value
        raw = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = dt.datetime.strptime(raw[:19], fmt)
                return parsed
            except ValueError:
                continue
        return None

