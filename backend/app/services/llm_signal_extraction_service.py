from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.parse
from typing import Any, Dict, List

from app.db.database import Database


class LlmSignalExtractionService:
    MAX_NEWS_DOCS_FOR_EXTRACTION = 75
    SOURCE_DOMAIN_WEIGHTS = {
        "reuters.com": 1.35,
        "bloomberg.com": 1.25,
        "cnbc.com": 1.15,
        "wsj.com": 1.2,
        "ft.com": 1.2,
        "marketwatch.com": 0.95,
        "benzinga.com": 0.85,
        "seekingalpha.com": 0.85,
        "fool.com": 0.8,
        "motleyfool.com": 0.8,
        "investing.com": 0.9,
        "finviz.com": 0.75,
        "aol.com": 0.7,
    }
    """
    Stores/loads structured event features extracted from unstructured documents.
    CoT is intentionally not stored; only short structured rationale is persisted.
    """

    schema_version = "llm_signal_schema_v1"
    feature_set_version = "xgb_base_plus_llm_v1"

    def ensure_tables(self, db: Database) -> None:
        db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS llm_signal_feature_sets (
                evidence_set_id TEXT PRIMARY KEY,
                symbol_key TEXT NOT NULL,
                asof DATE NOT NULL,
                feature_set_version TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                llm_provider TEXT NULL,
                llm_model TEXT NULL,
                extraction_status TEXT NOT NULL,
                extraction_error TEXT NULL,
                llm_features_json TEXT NOT NULL,
                quality_metrics_json TEXT NOT NULL,
                point_in_time_ok BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.execute_command(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_signal_feature_sets_symbol_asof_version
            ON llm_signal_feature_sets (symbol_key, asof, feature_set_version)
            """
        )
        db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS llm_signal_evidence_items (
                evidence_set_id TEXT NOT NULL,
                evidence_rank INTEGER NOT NULL,
                source_id TEXT NULL,
                source_type TEXT NULL,
                source_ref TEXT NULL,
                published_at TIMESTAMP NULL,
                claim_type TEXT NULL,
                direction TEXT NULL,
                strength REAL NULL,
                relevance REAL NULL,
                short_rationale TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (evidence_set_id, evidence_rank)
            )
            """
        )
    def get_latest_structured_features(self, symbol_key: str, asof: dt.date) -> Dict[str, Any] | None:
        symbol_key = symbol_key.upper()
        db = Database()
        if not db.connect():
            return None
        try:
            self.ensure_tables(db)
            rows = db.execute_query(
                """
                SELECT evidence_set_id, feature_set_version, extractor_version, llm_features_json,
                       quality_metrics_json, point_in_time_ok, extraction_status, extraction_error,
                       created_at, updated_at
                FROM llm_signal_feature_sets
                WHERE symbol_key = %s
                  AND asof = %s
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (symbol_key, asof),
            ) or []
            if not rows:
                return None
            row = rows[0]
            evidence_rows = db.execute_query(
                """
                SELECT evidence_rank, source_id, source_type, source_ref, published_at,
                       claim_type, direction, strength, relevance, short_rationale
                FROM llm_signal_evidence_items
                WHERE evidence_set_id = %s
                ORDER BY evidence_rank ASC
                """,
                (row[0],),
            ) or []
            evidences: List[Dict[str, Any]] = []
            for e in evidence_rows:
                evidences.append(
                    {
                        "rank": int(e[0]),
                        "source_id": e[1],
                        "source_type": e[2],
                        "source_ref": e[3],
                        "published_at": str(e[4]) if e[4] is not None else None,
                        "claim_type": e[5],
                        "direction": e[6],
                        "strength": float(e[7]) if e[7] is not None else None,
                        "relevance": float(e[8]) if e[8] is not None else None,
                        "short_rationale": e[9],
                    }
                )
            return {
                "symbol_key": symbol_key,
                "asof": str(asof),
                "evidence_set_id": row[0],
                "feature_set_version": row[1],
                "extractor_version": row[2],
                "llm_features": self._json_loads(row[3], {}),
                "quality_metrics": self._json_loads(row[4], {}),
                "point_in_time_ok": bool(row[5]),
                "extraction_status": row[6],
                "extraction_error": row[7],
                "evidence_items": evidences,
                "updated_at": str(row[9]) if row[9] is not None else None,
            }
        except Exception:
            return None
        finally:
            db.disconnect()

    def save_structured_features(
        self,
        symbol_key: str,
        asof: dt.date,
        llm_features: Dict[str, Any],
        evidence_items: List[Dict[str, Any]],
        quality_metrics: Dict[str, Any] | None = None,
        extraction_status: str = "success",
        extraction_error: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        point_in_time_ok: bool = True,
    ) -> Dict[str, Any]:
        symbol_key = symbol_key.upper()
        llm_features = dict(llm_features or {})
        quality_metrics = dict(quality_metrics or {})
        evidence_items = list(evidence_items or [])
        evidence_set_id = self._evidence_set_id(symbol_key, asof, llm_features, evidence_items)
        db = Database()
        if not db.connect():
            return {"ok": False, "evidence_set_id": evidence_set_id}
        try:
            self.ensure_tables(db)
            # Keep (symbol_key, asof, feature_set_version) unique stable across reruns.
            existing = db.execute_query(
                """
                SELECT evidence_set_id
                FROM llm_signal_feature_sets
                WHERE symbol_key = %s AND asof = %s AND feature_set_version = %s
                LIMIT 1
                """,
                (symbol_key, asof, self.feature_set_version),
            ) or []
            if existing and existing[0] and existing[0][0] and existing[0][0] != evidence_set_id:
                old_id = existing[0][0]
                db.execute_command(
                    "DELETE FROM llm_signal_evidence_items WHERE evidence_set_id = %s",
                    (old_id,),
                )
                db.execute_command(
                    "DELETE FROM llm_signal_feature_sets WHERE evidence_set_id = %s",
                    (old_id,),
                )
            db.execute_command(
                """
                INSERT INTO llm_signal_feature_sets
                    (evidence_set_id, symbol_key, asof, feature_set_version, extractor_version,
                     llm_provider, llm_model, extraction_status, extraction_error,
                     llm_features_json, quality_metrics_json, point_in_time_ok, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT (evidence_set_id)
                DO UPDATE SET
                    extraction_status = EXCLUDED.extraction_status,
                    extraction_error = EXCLUDED.extraction_error,
                    llm_features_json = EXCLUDED.llm_features_json,
                    quality_metrics_json = EXCLUDED.quality_metrics_json,
                    point_in_time_ok = EXCLUDED.point_in_time_ok,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    evidence_set_id,
                    symbol_key,
                    asof,
                    self.feature_set_version,
                    self.schema_version,
                    llm_provider,
                    llm_model,
                    extraction_status,
                    extraction_error,
                    json.dumps(llm_features, ensure_ascii=False),
                    json.dumps(quality_metrics, ensure_ascii=False),
                    bool(point_in_time_ok),
                ),
            )
            for idx, ev in enumerate(evidence_items[:10], start=1):
                db.execute_command(
                    """
                    INSERT INTO llm_signal_evidence_items
                        (evidence_set_id, evidence_rank, source_id, source_type, source_ref, published_at,
                         claim_type, direction, strength, relevance, short_rationale)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (evidence_set_id, evidence_rank)
                    DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        source_type = EXCLUDED.source_type,
                        source_ref = EXCLUDED.source_ref,
                        published_at = EXCLUDED.published_at,
                        claim_type = EXCLUDED.claim_type,
                        direction = EXCLUDED.direction,
                        strength = EXCLUDED.strength,
                        relevance = EXCLUDED.relevance,
                        short_rationale = EXCLUDED.short_rationale
                    """,
                    (
                        evidence_set_id,
                        idx,
                        ev.get("source_id"),
                        ev.get("source_type"),
                        ev.get("source_ref"),
                        self._parse_ts(ev.get("published_at")),
                        ev.get("claim_type"),
                        ev.get("direction"),
                        self._safe_float(ev.get("strength")),
                        self._safe_float(ev.get("relevance")),
                        self._safe_text(ev.get("short_rationale"), limit=240),
                    ),
                )
            return {"ok": True, "evidence_set_id": evidence_set_id}
        finally:
            db.disconnect()

    def extract_structured_features(
        self,
        symbol_key: str,
        asof: dt.date,
        documents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Minimal local extractor fallback. Can be replaced by an LLM provider behind the same schema.
        """
        symbol_key = symbol_key.upper()
        docs = [d for d in (documents or []) if self._doc_is_point_in_time_valid(d, asof)]
        tone = 0.0
        reg_risk = 0.0
        supply_risk = 0.0
        sector_sent = 0.0
        weighted_doc_count = 0.0
        source_quality_sum = 0.0
        used_sources: set[str] = set()
        source_doc_counts: Dict[str, int] = {}
        evidences: List[Dict[str, Any]] = []
        pos_terms = ("beat", "growth", "upgrade", "strong", "launch", "record", "raise")
        neg_terms = ("miss", "downgrade", "lawsuit", "probe", "delay", "shortage", "warning")
        for doc in docs[: self.MAX_NEWS_DOCS_FOR_EXTRACTION]:
            text = f"{doc.get('title') or ''} {doc.get('content_text') or ''}".lower()
            src_dom = self._source_domain(doc)
            src_weight = self._source_weight(doc)
            prev_src_count = source_doc_counts.get(src_dom or "__unknown__", 0)
            source_doc_counts[src_dom or "__unknown__"] = prev_src_count + 1
            concentration_penalty = 1.0 / (1.0 + 0.12 * prev_src_count)
            recency_weight = self._recency_weight(doc, asof)
            doc_weight = max(0.30, min(1.8, src_weight * recency_weight * concentration_penalty))
            pos = sum(1 for t in pos_terms if t in text)
            neg = sum(1 for t in neg_terms if t in text)
            score = float(pos - neg)
            tone += score * doc_weight
            if any(t in text for t in ("regulator", "lawsuit", "probe", "antitrust")):
                reg_risk += 1.0 * doc_weight
            if any(t in text for t in ("shortage", "supply", "shipment delay", "capacity")):
                supply_risk += 1.0 * doc_weight
            if any(t in text for t in ("sector", "industry", "peer")):
                sector_sent += score * 0.5 * doc_weight
            weighted_doc_count += doc_weight
            source_quality_sum += src_weight
            if src_dom:
                used_sources.add(src_dom)
            evidences.append(
                {
                    "source_id": doc.get("document_id"),
                    "source_type": doc.get("source_type"),
                    "source_ref": doc.get("source_ref"),
                    "published_at": doc.get("published_at"),
                    "claim_type": "news_event",
                    "direction": "positive" if score > 0 else ("negative" if score < 0 else "neutral"),
                    "strength": min(1.0, abs(score) / 5.0),
                    "relevance": max(0.4, min(0.95, 0.6 + 0.15 * (doc_weight - 1.0))),
                    "short_rationale": self._safe_text((doc.get("title") or doc.get("content_text") or "")[:200], 160),
                }
            )
        n_docs = len(docs)
        mean_src_quality = (source_quality_sum / max(1, min(n_docs, self.MAX_NEWS_DOCS_FOR_EXTRACTION)))
        llm_features = {
            "llm_earnings_tone_score": float(max(-3.0, min(3.0, tone / max(1.0, weighted_doc_count)))),
            "llm_regulatory_risk_score": float(max(0.0, min(3.0, reg_risk))),
            "llm_supply_chain_disruption_score": float(max(0.0, min(3.0, supply_risk))),
            "llm_sector_sentiment_score": float(max(-3.0, min(3.0, sector_sent / max(1.0, weighted_doc_count)))),
            "llm_event_recency_decay_weight": 1.0 if n_docs > 0 else 0.0,
            "llm_low_evidence_flag": 1.0 if n_docs < 2 else 0.0,
            "llm_news_source_quality_score": float(max(0.0, min(2.0, mean_src_quality))),
        }
        quality = {
            "coverage_ratio": 1.0 if n_docs > 0 else 0.0,
            "source_diversity_score": float(min(1.0, len(used_sources or {d.get('source_type') for d in docs if d.get('source_type')}) / 4.0)),
            "timestamp_alignment_score": 1.0,
            "extraction_confidence": 0.6 if n_docs > 0 else 0.0,
            "parser_validation_pass": True,
            "weighted_doc_count": float(round(weighted_doc_count, 3)),
            "mean_source_quality_weight": float(round(mean_src_quality, 3)),
            "source_concentration_penalty_applied": True,
        }
        return {
            "symbol_key": symbol_key,
            "asof": str(asof),
            "feature_set_version": self.feature_set_version,
            "schema_version": self.schema_version,
            "llm_features": llm_features,
            "evidence_items": evidences[: min(25, self.MAX_NEWS_DOCS_FOR_EXTRACTION)],
            "quality_metrics": quality,
            "point_in_time_ok": True,
        }

    def _doc_is_point_in_time_valid(self, doc: Dict[str, Any], asof: dt.date) -> bool:
        published_at = self._parse_ts(doc.get("published_at"))
        return published_at is None or published_at.date() <= asof

    def _source_domain(self, doc: Dict[str, Any]) -> str:
        ref = str(doc.get("source_ref") or "").strip()
        if ref:
            try:
                parsed = urllib.parse.urlparse(ref)
                host = (parsed.netloc or "").lower()
                if host.startswith("www."):
                    host = host[4:]
                if host:
                    return host
            except Exception:
                pass
        title = str(doc.get("title") or "")
        if " - " in title:
            suffix = title.rsplit(" - ", 1)[-1].strip().lower()
            suffix = re.sub(r"[^a-z0-9. ]+", "", suffix)
            suffix = suffix.replace(" ", "")
            return suffix
        return ""

    def _source_weight(self, doc: Dict[str, Any]) -> float:
        dom = self._source_domain(doc)
        if not dom:
            return 1.0
        for k, w in self.SOURCE_DOMAIN_WEIGHTS.items():
            if dom == k or dom.endswith("." + k) or k in dom:
                return float(w)
        return 1.0

    def _recency_weight(self, doc: Dict[str, Any], asof: dt.date) -> float:
        pub = self._parse_ts(doc.get("published_at"))
        if pub is None:
            return 0.9
        days = max(0, (asof - pub.date()).days)
        # Mild decay across 75d lookback to keep older context while favoring recent events.
        return float(max(0.45, min(1.2, 1.0 * (0.985 ** days))))

    def _evidence_set_id(self, symbol_key: str, asof: dt.date, features: Dict[str, Any], evidences: List[Dict[str, Any]]) -> str:
        payload = json.dumps(
            {"f": features, "e": evidences[:5]},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        seed = f"{symbol_key}|{asof.isoformat()}|{payload}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]

    def _json_loads(self, raw: Any, default: Any) -> Any:
        if raw is None:
            return default
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return default

    def _safe_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _safe_text(self, value: Any, limit: int = 200) -> str | None:
        if value is None:
            return None
        txt = str(value).strip().replace("\n", " ")
        return txt[:limit] if txt else None

    def _parse_ts(self, value: Any) -> dt.datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, dt.datetime):
            return value
        if isinstance(value, dt.date):
            return dt.datetime.combine(value, dt.time.min)
        raw = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
        return None
