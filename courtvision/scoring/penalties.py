"""Penalty calculation functions for candidate scoring.

This module extracts penalty logic from runtime_scoring.py including:
- Longshot odds penalties
- Volatility penalties (minutes, injury impact)
- Projection realism penalties (edge/confidence mismatch)
"""

from __future__ import annotations

from typing import Any, Mapping


def longshot_penalty_points(odds: Any) -> float:
    """Compute penalty points for longshot odds.

    Higher penalties for extreme longshots to reduce overvaluation.
    """
    odds_value = to_float(odds)
    if odds_value is None or odds_value <= 0:
        return 0.0
    if odds_value >= 700:
        return 16.0
    if odds_value >= 450:
        return 12.0
    if odds_value >= 250:
        return 8.0
    if odds_value >= 150:
        return 4.0
    return 0.0


def volatility_penalty_points(row: Mapping[str, Any]) -> float:
    """Compute penalty points for player volatility indicators.

    Considers:
    - Low minutes projection (< 22 min = heavy penalty)
    - Minutes drift (recent vs avg)
    - High injury impact
    """
    market_type = str(row.get("market_type", ""))
    if not market_type.startswith("player_"):
        return 0.0

    minutes_avg = to_float(row.get("minutes_avg")) or 0.0
    minutes_recent = to_float(row.get("minutes_recent")) or minutes_avg
    minutes_projection = max(minutes_avg, minutes_recent)
    injury_impact = max(
        to_float(row.get("injury_impact_score")) or 0.0,
        to_float(row.get("selection_team_injury_impact")) or 0.0,
        to_float(row.get("team_injury_impact")) or 0.0,
    )

    penalty = 0.0
    if minutes_projection < 22.0:
        penalty += 12.0
    elif minutes_projection < 26.0:
        penalty += 5.0

    penalty += min(8.0, abs(minutes_recent - minutes_avg) * 1.1)
    if injury_impact > 0.25:
        penalty += min(8.0, injury_impact * 18.0)
    return penalty


def projection_realism_penalty_points(
    row: Mapping[str, Any],
    adjusted_edge_abs: float,
    confidence: float,
) -> float:
    """Compute penalty for unrealistic edge/confidence combinations.

    Flags cases where high edge is paired with low confidence,
    suggesting an unreliable projection.
    """
    market_type = str(row.get("market_type", ""))
    if market_type == "moneyline":
        return 0.0

    penalty = 0.0
    if adjusted_edge_abs > 6.0 and confidence < 0.65:
        penalty += min(14.0, ((adjusted_edge_abs - 6.0) * 2.4) + ((0.65 - confidence) * 42.0))
    elif adjusted_edge_abs > 4.5 and confidence < 0.60:
        penalty += min(7.0, ((adjusted_edge_abs - 4.5) * 1.5) + ((0.60 - confidence) * 28.0))
    return penalty


def compute_penalties(
    row: Mapping[str, Any],
    adjusted_edge_abs: float,
    confidence: float,
) -> dict[str, float]:
    """Compute all penalties for a candidate.

    Returns a dict with:
    - longshot_penalty: Points from odds-based longshot penalty
    - volatility_penalty: Points from minutes/injury volatility
    - realism_penalty: Points from edge/confidence mismatch
    - total_penalty: Sum of all penalties (capped at 12.0)
    """
    longshot = longshot_penalty_points(row.get("odds"))
    volatility = volatility_penalty_points(row)
    realism = projection_realism_penalty_points(row, adjusted_edge_abs, confidence)

    total = volatility + realism
    total = min(total, 12.0)  # prevent over-penalization

    return {
        "longshot_penalty": round(float(longshot), 4),
        "volatility_penalty": round(float(volatility), 4),
        "realism_penalty": round(float(realism), 4),
        "total_penalty": round(float(total), 4),
    }


def to_float(value: Any) -> float | None:
    """Safely convert a value to float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None
