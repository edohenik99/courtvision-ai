"""Scoring package for candidate evaluation.

This package provides modular scoring logic extracted from runtime_scoring.py:
- edge: Edge percentage and bias factor calculations
- confidence: Confidence computation and historical multipliers
- penalties: Volatility, longshot, and projection realism penalties
- candidate_scoring: Main scoring orchestration
"""

from __future__ import annotations

from courtvision.scoring.candidate_scoring import (
    CandidateScoringConfig,
    CandidateScoringPolicy,
    compute_selection_score,
    player_tier_weight,
)
from courtvision.scoring.confidence import (
    compute_confidence,
    historical_confidence_multiplier,
    player_points_scoring_stability,
)
from courtvision.scoring.edge import (
    compute_edge,
    edge_pct_denominator,
    edge_pct_value,
    favorite_bias_factor,
)
from courtvision.scoring.penalties import (
    compute_penalties,
    longshot_penalty_points,
    projection_realism_penalty_points,
    volatility_penalty_points,
)

__all__ = [
    # candidate_scoring
    "CandidateScoringConfig",
    "CandidateScoringPolicy",
    "compute_selection_score",
    "player_tier_weight",
    # confidence
    "compute_confidence",
    "historical_confidence_multiplier",
    "player_points_scoring_stability",
    # edge
    "compute_edge",
    "edge_pct_denominator",
    "edge_pct_value",
    "favorite_bias_factor",
    # penalties
    "compute_penalties",
    "longshot_penalty_points",
    "projection_realism_penalty_points",
    "volatility_penalty_points",
]
