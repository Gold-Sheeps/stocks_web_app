from app.services.override_service import OverrideService


def test_preview_numeric_high_impact():
    svc = OverrideService()
    out = svc.preview(
        {
            "category": "fundamentals",
            "field_name": "eps_diluted_q",
            "original_value": 10,
            "override_value": 13,
        }
    )

    assert out["diff"]["change_pct"] == 30.0
    assert "growth_score" in out["affected_features"]
    assert out["forecast_impact"]["level"] == "high"
    assert out["forecast_impact"]["direction"] == "up"


def test_preview_numeric_medium_impact_down():
    svc = OverrideService()
    out = svc.preview(
        {
            "category": "market",
            "field_name": "vix",
            "original_value": 20,
            "override_value": 19,
        }
    )

    assert out["diff"]["change_pct"] == -5.0
    assert out["forecast_impact"]["level"] == "medium"
    assert out["forecast_impact"]["direction"] == "down"


def test_preview_non_numeric_change():
    svc = OverrideService()
    out = svc.preview(
        {
            "category": "metadata",
            "field_name": "sector",
            "original_value": "Technology",
            "override_value": "Healthcare",
        }
    )

    assert out["diff"]["change_pct"] is None
    assert "peer_grouping" in out["affected_features"]
    assert out["forecast_impact"]["level"] == "medium"


def test_preview_unknown_field_falls_back_manual_review():
    svc = OverrideService()
    out = svc.preview(
        {
            "category": "fundamentals",
            "field_name": "unknown_metric",
            "original_value": 1,
            "override_value": 1.01,
        }
    )

    assert out["affected_features"] == ["manual_review"]
