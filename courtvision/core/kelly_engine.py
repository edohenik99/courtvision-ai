"""Compatibility facade for the unchanged production Kelly implementation."""

from courtvision.betting.kelly import (
    MAX_STAKE_FRACTION,
    MIN_CONFIDENCE_THRESHOLD,
    compute_kelly_fraction,
    compute_recommended_bet,
)

__all__ = [
    "MAX_STAKE_FRACTION",
    "MIN_CONFIDENCE_THRESHOLD",
    "compute_kelly_fraction",
    "compute_recommended_bet",
]
