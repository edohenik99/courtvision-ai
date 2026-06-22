"""Sport-neutral confidence scoring for research candidates.

This model is isolated from the existing NBA board scoring and selection gates.
It is a foundation for new sport modules, not a replacement production policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecommendationTier(StrEnum):
    ELITE = "Elite"
    STRONG = "Strong"
    WATCHLIST = "Watchlist"
    PASS = "Pass"


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    projection_edge_percent: float
    hit_rate_consistency: float
    matchup_rating: float
    line_movement: float
    recent_form: float
    market_type: str
    data_quality: float


@dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    confidence_score: int
    recommendation: RecommendationTier
    component_scores: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "confidence_score": self.confidence_score,
            "recommendation": self.recommendation.value,
            "component_scores": self.component_scores,
        }


class ConfidenceEngine:
    """Stateless facade for scoring research candidates."""

    @staticmethod
    def score(inputs: ConfidenceInputs) -> ConfidenceAssessment:
        return calculate_confidence(inputs)

    @staticmethod
    def recommend(score: float) -> RecommendationTier:
        return recommendation_for_score(score)


_MARKET_RELIABILITY = {
    "points": 0.90,
    "rebounds": 0.82,
    "assists": 0.82,
    "pra": 0.80,
    "threes": 0.72,
    "steals": 0.60,
    "blocks": 0.60,
    "hits": 0.82,
    "total_bases": 0.75,
    "runs": 0.68,
    "rbis": 0.65,
    "home_runs": 0.45,
    "strikeouts": 0.88,
    "pitcher_outs": 0.86,
    "passing_yards": 0.86,
    "rushing_yards": 0.80,
    "receiving_yards": 0.78,
    "receptions": 0.84,
    "touchdowns": 0.55,
    "completions": 0.86,
    "interceptions": 0.58,
}

_WEIGHTS = {
    "projection_edge": 25.0,
    "hit_rate_consistency": 20.0,
    "matchup_rating": 15.0,
    "line_movement": 10.0,
    "recent_form": 10.0,
    "market_type": 5.0,
    "data_quality": 15.0,
}


def _unit_interval(value: float) -> float:
    value = float(value)
    if value > 1.0:
        value /= 100.0
    return min(max(value, 0.0), 1.0)


def recommendation_for_score(score: float) -> RecommendationTier:
    if score >= 85:
        return RecommendationTier.ELITE
    if score >= 75:
        return RecommendationTier.STRONG
    if score >= 65:
        return RecommendationTier.WATCHLIST
    return RecommendationTier.PASS


def calculate_confidence(inputs: ConfidenceInputs) -> ConfidenceAssessment:
    """Score confidence from 0 to 100 using explicit weighted components."""

    normalized = {
        "projection_edge": min(max(inputs.projection_edge_percent, 0.0) / 20.0, 1.0),
        "hit_rate_consistency": _unit_interval(inputs.hit_rate_consistency),
        "matchup_rating": _unit_interval(inputs.matchup_rating),
        "line_movement": _unit_interval(inputs.line_movement),
        "recent_form": _unit_interval(inputs.recent_form),
        "market_type": _MARKET_RELIABILITY.get(inputs.market_type.strip().lower(), 0.65),
        "data_quality": _unit_interval(inputs.data_quality),
    }
    components = {
        name: round(normalized[name] * weight, 2)
        for name, weight in _WEIGHTS.items()
    }
    score = int(round(min(max(sum(components.values()), 0.0), 100.0)))
    return ConfidenceAssessment(
        confidence_score=score,
        recommendation=recommendation_for_score(score),
        component_scores=components,
    )
