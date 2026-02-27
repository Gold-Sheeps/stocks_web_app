from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd


class FeatureFusionService:
    """Fuse precomputed LLM features into inference feature frames (best-effort)."""

    def merge_inference_features(
        self,
        x_df: pd.DataFrame,
        model_feature_columns: List[str],
        llm_features: Dict[str, Any] | None,
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        if x_df is None or x_df.empty:
            return x_df, {"llm_features_used": False, "llm_feature_columns_applied": [], "llm_feature_columns_ignored": []}
        out = x_df.copy()
        normalized: Dict[str, float] = {}
        for key, value in (llm_features or {}).items():
            if key is None:
                continue
            name = str(key).strip()
            if not name:
                continue
            try:
                normalized[name] = float(value)
            except Exception:
                continue
        for col, val in normalized.items():
            out[col] = val
        if model_feature_columns:
            out = out.reindex(columns=list(model_feature_columns), fill_value=0.0).astype(float)
        applied = [c for c in normalized.keys() if c in out.columns]
        ignored = [c for c in normalized.keys() if c not in out.columns]
        return out, {
            "llm_features_used": bool(applied),
            "llm_feature_columns_supplied": sorted(list(normalized.keys())),
            "llm_feature_columns_applied": sorted(applied),
            "llm_feature_columns_ignored": sorted(ignored),
        }

