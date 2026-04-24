"""Injuries package for injury impact evaluation.

This package provides modular injury logic extracted from courtvision_ai.py:
- injury_engine: Core injury context building and application
- volatility: Injury volatility and recent form calculations
- realism: Player points realism dampening
"""

from __future__ import annotations

from courtvision.injuries.injury_engine import (
    InjuryContextConfig,
    InjuryEngine,
    apply_player_injury_context,
    build_injury_context,
    injury_status_weight,
)
from courtvision.injuries.realism import (
    apply_realism_dampener,
    compute_injury_independent_support,
)
from courtvision.injuries.volatility import (
    compute_injury_volatility,
    compute_injury_independent_support,
    compute_recent_form_ratio,
    compute_volatility_confidence_penalty,
)

__all__ = [
    # injury_engine
    "InjuryContextConfig",
    "InjuryEngine",
    "apply_player_injury_context",
    "build_injury_context",
    "injury_status_weight",
    # volatility
    "compute_injury_volatility",
    "compute_recent_form_ratio",
    "compute_injury_independent_support",
    "compute_volatility_confidence_penalty",
    # realism
    "apply_realism_dampener",
    "compute_injury_independent_support",
]
