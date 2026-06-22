from __future__ import annotations

from courtvision.sports.nfl import NFLProjectionFeatures, NFLProjectionModel, SPORT


def test_nfl_module_loads_with_usage_feature_placeholders() -> None:
    result = NFLProjectionModel().project(
        "receiving_yards",
        [55.0, 72.0, 68.0],
        NFLProjectionFeatures(snap_share=0.82, target_share=0.24, injury_status="healthy"),
    )

    assert SPORT.supports_market("completions")
    assert result.is_placeholder is True
    assert result.context["features_applied"] is False
    assert result.context["features"]["target_share"] == 0.24
