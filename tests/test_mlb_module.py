from __future__ import annotations

from courtvision.sports.mlb import MLBProjectionFeatures, MLBProjectionModel, SPORT


def test_mlb_module_loads_with_statcast_feature_placeholders() -> None:
    result = MLBProjectionModel().project(
        "total_bases",
        [1.0, 2.0, 0.0, 3.0, 2.0],
        MLBProjectionFeatures(handedness_matchup=0.6, ballpark_factor=1.05),
    )

    assert SPORT.supports_market("pitcher_outs")
    assert result.is_placeholder is True
    assert result.context["features_applied"] is False
    assert result.context["features"]["handedness_matchup"] == 0.6
