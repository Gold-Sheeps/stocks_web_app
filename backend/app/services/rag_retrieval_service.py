from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List

from app.db.database import Database
from app.services.rag_ingest_service import RagIngestService


class RagRetrievalService:
    """Point-in-time constrained retrieval for pre-ingested documents."""

    def _normalize_text(self, value: Any) -> str:
        txt = str(value or "").lower().strip()
        txt = re.sub(r"https?://\S+", " ", txt)
        txt = re.sub(r"[^a-z0-9]+", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt

    def _dedupe_documents(self, docs: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        out: List[Dict[str, Any]] = []
        for d in docs:
            title_key = self._normalize_text(d.get("title"))
            ref_key = self._normalize_text(d.get("source_ref"))
            # Prefer headline-level dedupe; fallback to source ref when title is weak/missing.
            dedupe_key = f"title:{title_key}" if len(title_key) >= 12 else f"ref:{ref_key}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(d)
            if len(out) >= int(limit):
                break
        return out

    def get_documents_for_asof(
        self,
        symbol_key: str,
        asof: dt.date,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        symbol_key = symbol_key.upper()
        db = Database()
        if not db.connect():
            return []
        try:
            RagIngestService().ensure_tables(db)
            rows = db.execute_query(
                """
                SELECT document_id, source_type, source_ref, published_at, title, content_text, metadata_json
                FROM rag_documents
                WHERE symbol_key = %s
                  AND (published_at IS NULL OR published_at <= %s)
                ORDER BY published_at DESC NULLS LAST, updated_at DESC
                LIMIT %s
                """,
                (symbol_key, asof, int(max(limit * 4, limit))),
            ) or []
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "document_id": r[0],
                        "source_type": r[1],
                        "source_ref": r[2],
                        "published_at": r[3],
                        "title": r[4],
                        "content_text": r[5],
                        "metadata_json": r[6],
                    }
                )
            return self._dedupe_documents(out, limit=int(limit))
        except Exception:
            return []
        finally:
            db.disconnect()
