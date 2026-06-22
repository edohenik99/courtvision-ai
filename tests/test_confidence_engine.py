from __future__ import annotations

import pytest

from courtvision.core.confidence_engine import (
    ConfidenceEngine,
    ConfidenceInputs,
    RecommendationTier,
    calculate_confidence,
    recommendation_for_score,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, RecommendationTier.ELITE),
        (85, RecommendationTier.ELITE),
        (84, RecommendationTier.STRONG),
        (75, RecommendationTier.STRONG),
        (74, RecommendationTier.WATCHLIST),
        (65, RecommendationTier.WATCHLIST),
        (64, RecommendationTier.PASS),
    ],
)
def test_recommendation_tier_boundaries(score: int, expected: RecommendationTier) -> None:
    assert recommendation_for_score(score) is expected


def test_confidence_scoring_is_bounded_and_explainable() -> None:
    inputs = ConfidenceInputs(
        projection_edge_percent=28.0,
        hit_rate_consistency=70.0,
        matchup_rating=95.0,
        line_movement=90.0,
        recent_form=95.0,
        market_type="total_bases",
        data_quality=100.0,
    )
    assessment = calculate_confidence(inputs)

    assert assessment.confidence_score == 90
    assert assessment.recommendation is RecommendationTier.ELITE
    assert sum(assessment.component_scores.values()) == pytest.approx(90.5)
    assert ConfidenceEngine.score(inputs) == assessment
    assert ConfidenceEngine.recommend(90) is RecommendationTier.ELITE
