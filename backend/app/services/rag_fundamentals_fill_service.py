from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List

from app.db.database import Database


class RagFundamentalsFillService:
    """
    Best-effort fundamentals null filler from RAG documents.
    This is intentionally conservative:
    - fills only existing fundamentals rows
    - updates only NULL columns
    - stores audit evidence for review / DataOverride follow-up
    """

    TARGET_FIELDS = [
        "eps",
        "revenue",
        "net_income",
        "operating_cash_flow",
        "free_cash_flow",
        "capex",
    ]

    _MONEY_RE = re.compile(
        r"(?P<sign>-)?\$?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>billion|million|thousand|bn|mn|m|b|k)?",
        re.IGNORECASE,
    )
    _EPS_RE = re.compile(
        r"\b(?:eps|earnings per share)\b[^$0-9-]{0,20}\$?\s*(?P<val>-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    def ensure_audit_table(self, db: Database) -> None:
        db.execute_command(
            """
            CREATE TABLE IF NOT EXISTS rag_fundamentals_fill_audit (
                audit_id TEXT PRIMARY KEY,
                symbol_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                period_end_date DATE NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT NULL,
                new_value TEXT NULL,
                source_document_id TEXT NULL,
                source_ref TEXT NULL,
                short_rationale TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def fill_missing_from_documents(
        self,
        symbol_key: str,
        asof: dt.date,
        documents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        symbol_key = str(symbol_key or "").upper()
        symbol = symbol_key.split(":", 1)[1] if ":" in symbol_key else symbol_key
        out = {
            "attempted": True,
            "symbol_key": symbol_key,
            "asof": str(asof),
            "row_found": False,
            "updated_fields": [],
            "candidates": {},
        }
        candidates = self._extract_candidates(documents)
        out["candidates"] = dict(candidates)
        if not candidates:
            return out

        db = Database()
        if not db.connect():
            out["error"] = "db_connect_failed"
            return out
        try:
            self.ensure_audit_table(db)
            rows = db.execute_query(
                """
                SELECT symbol, period_end_date, eps, revenue, net_income,
                       operating_cash_flow, free_cash_flow, capex
                FROM fundamentals
                WHERE symbol = %s AND period_end_date <= %s
                ORDER BY period_end_date DESC
                LIMIT 1
                """,
                (symbol, asof),
            ) or []
            if not rows:
                return out
            row = rows[0]
            out["row_found"] = True
            period_end_date = row[1]
            current = {
                "eps": row[2],
                "revenue": row[3],
                "net_income": row[4],
                "operating_cash_flow": row[5],
                "free_cash_flow": row[6],
                "capex": row[7],
            }
            for field in self.TARGET_FIELDS:
                if current.get(field) is not None:
                    continue
                if field not in candidates:
                    continue
                val = candidates[field]["value"]
                ok = db.execute_command(
                    f"UPDATE fundamentals SET {field} = %s, updated_at = CURRENT_TIMESTAMP WHERE symbol = %s AND period_end_date = %s",
                    (val, symbol, period_end_date),
                )
                if not ok:
                    continue
                out["updated_fields"].append(field)
                ev = candidates[field]
                audit_id = f"{symbol_key}|{period_end_date}|{field}|{ev.get('document_id') or 'na'}"
                db.execute_command(
                    """
                    INSERT INTO rag_fundamentals_fill_audit
                        (audit_id, symbol_key, symbol, period_end_date, field_name, old_value, new_value,
                         source_document_id, source_ref, short_rationale)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (audit_id)
                    DO NOTHING
                    """,
                    (
                        audit_id[:200],
                        symbol_key,
                        symbol,
                        period_end_date,
                        field,
                        None,
                        str(val),
                        ev.get("document_id"),
                        ev.get("source_ref"),
                        (ev.get("short_rationale") or "")[:240],
                    ),
                )
            return out
        finally:
            db.disconnect()

    def _extract_candidates(self, documents: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        docs = list(documents or [])
        out: Dict[str, Dict[str, Any]] = {}
        for doc in docs[:10]:
            text = f"{doc.get('title') or ''} {doc.get('content_text') or ''}"
            low = text.lower()
            if "earnings" not in low and "revenue" not in low and "cash flow" not in low and "capex" not in low:
                continue

            eps = self._parse_eps(text)
            if eps is not None and "eps" not in out:
                out["eps"] = self._ev(doc, f"EPS parsed as {eps}", eps)

            revenue = self._parse_money_near(text, ["revenue", "sales"])
            if revenue is not None and "revenue" not in out:
                out["revenue"] = self._ev(doc, f"Revenue parsed as {revenue}", revenue)

            net_income = self._parse_money_near(text, ["net income"])
            if net_income is not None and "net_income" not in out:
                out["net_income"] = self._ev(doc, f"Net income parsed as {net_income}", net_income)

            ocf = self._parse_money_near(text, ["operating cash flow", "cash from operations"])
            if ocf is not None and "operating_cash_flow" not in out:
                out["operating_cash_flow"] = self._ev(doc, f"OCF parsed as {ocf}", ocf)

            fcf = self._parse_money_near(text, ["free cash flow"])
            if fcf is not None and "free_cash_flow" not in out:
                out["free_cash_flow"] = self._ev(doc, f"FCF parsed as {fcf}", fcf)

            capex = self._parse_money_near(text, ["capex", "capital expenditures", "capital expenditure"])
            if capex is not None and "capex" not in out:
                # Capex is often reported as outflow; preserve sign if present.
                out["capex"] = self._ev(doc, f"CapEx parsed as {capex}", capex)
        return out

    def _ev(self, doc: Dict[str, Any], rationale: str, value: float) -> Dict[str, Any]:
        return {
            "value": value,
            "document_id": doc.get("document_id"),
            "source_ref": doc.get("source_ref"),
            "short_rationale": rationale,
        }

    def _parse_eps(self, text: str) -> float | None:
        m = self._EPS_RE.search(text or "")
        if not m:
            return None
        try:
            return float(m.group("val"))
        except Exception:
            return None

    def _parse_money_near(self, text: str, anchors: List[str]) -> float | None:
        raw = text or ""
        low = raw.lower()
        for anchor in anchors:
            idx = low.find(anchor.lower())
            if idx < 0:
                continue
            segment = raw[max(0, idx - 20): idx + 120]
            m = self._MONEY_RE.search(segment)
            if not m:
                continue
            val = self._money_match_to_number(m)
            if val is not None:
                return val
        return None

    def _money_match_to_number(self, m: re.Match) -> float | None:
        try:
            num = float(m.group("num"))
        except Exception:
            return None
        unit = (m.group("unit") or "").lower()
        mult = 1.0
        if unit in ("billion", "bn", "b"):
            mult = 1_000_000_000.0
        elif unit in ("million", "mn", "m"):
            mult = 1_000_000.0
        elif unit in ("thousand", "k"):
            mult = 1_000.0
        val = num * mult
        if m.group("sign") == "-":
            val = -val
        return float(val)
