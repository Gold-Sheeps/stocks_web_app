from __future__ import annotations

from typing import Any, Dict, List


class ExplanationSummaryService:
    """Generate short structured summaries for UI/API payloads without CoT."""

    def summarize_evidence(self, evidence_items: List[Dict[str, Any]] | None) -> Dict[str, Any]:
        items = list(evidence_items or [])
        tags: List[str] = []
        shorts: List[str] = []
        for ev in items[:3]:
            claim = str(ev.get("claim_type") or "event")
            direction = str(ev.get("direction") or "neutral")
            source_type = str(ev.get("source_type") or "source")
            tags.append(f"{claim}:{direction}")
            rationale = str(ev.get("short_rationale") or "").strip()
            if rationale:
                shorts.append(f"[{source_type}] {rationale[:100]}")
        return {
            "event_tags": tags,
            "short_rationales": shorts,
            "summary_text": " / ".join(shorts[:2]) if shorts else None,
        }

