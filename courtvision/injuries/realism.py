"""Player points realism dampening.

This module extracts the player points realism dampener logic from
courtvision_ai.py to prevent over-optimistic projections.
"""

from __future__ import annotations

from typing import Any, Mapping

from courtvision.calibration.buckets import (
    player_points_line_band,
    player_profile_bucket,
    to_float,
)


def compute_injury_independent_support(
    player_row: Mapping[str, Any],
    baseline_projection: float,
    recent_form_ratio: float,
) -> float:
    """Compute injury-independent support for confidence uplift decisions.

    Same logic as in volatility module for consistency.
    """
    minutes_avg = to_float(player_row.get("min_avg")) or 0.0
    season_avg = to_float(player_row.get("pts_avg")) or baseline_projection

    support = 0.0
    if recent_form_ratio >= 1.05:
        support += 0.02
    elif recent_form_ratio >= 0.98:
        support += 0.01
    if minutes_avg >= 32.0:
        support += 0.01
    if season_avg >= 18.0:
        support += 0.01

    return min(0.05, max(0.0, support))


def apply_realism_dampener(
    player_row: Mapping[str, Any],
    sportsbook_line: float,
    selection: str,
    projection: float,
    confidence: float,
    injury_payload: Mapping[str, Any],
    is_live_market: bool,
) -> tuple[float, float, dict[str, Any]]:
    """Apply realism dampening to prevent over-optimistic projections.

    This logic targets specific risky scenarios:
    - Mid-range lines with significant injury-driven boosts
    - Fragile role players with inflated projections
    - Low lines with high injury boosts

    Returns (dampened_projection, dampened_confidence, metadata)
    """
    payload = {
        "player_points_realism_dampened": False,
        "player_points_realism_dampener_reason": "",
        "player_points_projection_dampener": 0.0,
        "player_points_confidence_dampener": 0.0,
    }

    # Only apply to live market overs
    if not is_live_market or str(selection).strip().lower() != "over":
        return projection, confidence, payload

    # Get profile and line band context
    profile_context = {
        "market_type": "player_points",
        "sportsbook_line": sportsbook_line,
        "minutes_avg": player_row.get("min_avg"),
        "minutes_recent": player_row.get("min_recent"),
    }
    profile_bucket = player_profile_bucket(profile_context)
    line_band = player_points_line_band(profile_context)

    # Get injury impact info
    team_injury_impact = float(injury_payload.get("team_injury_impact") or 0.0)
    opp_injury_impact = float(injury_payload.get("opponent_injury_impact") or 0.0)
    injury_strength = max(team_injury_impact, opp_injury_impact)
    projection_delta = float(injury_payload.get("injury_projection_delta") or 0.0)

    # Calculate dampening factors
    dampened_projection = float(projection)
    dampened_confidence = float(confidence)
    projection_dampener = 0.0
    confidence_dampener = 0.0
    dampened = False
    reason = ""

    # Scenario 1: Fragile mid-line injury over
    # Role player with mid-range line and significant injury boost
    if (
        profile_bucket == "role_low_usage"
        and line_band == "20_to_26_5"
        and injury_strength >= 0.20
        and projection_delta >= 1.2
    ):
        projection_dampener = min(0.08, injury_strength * 0.25)
        confidence_dampener = min(0.04, injury_strength * 0.15)
        dampened = True
        reason = "fragile_mid_line_injury_over"

    # Scenario 2: High line secondary player with big boost
    # Secondary player with high line and large injury-driven projection increase
    elif (
        profile_bucket == "starter_secondary"
        and line_band == "gte_27"
        and injury_strength >= 0.25
        and projection_delta >= 1.5
    ):
        projection_dampener = min(0.06, injury_strength * 0.20)
        confidence_dampener = min(0.03, injury_strength * 0.12)
        dampened = True
        reason = "high_line_secondary_injury_over"

    # Scenario 3: Low line high boost concern
    # Very low line with large injury boost suggests inflated role
    elif (
        line_band == "lte_14_5"
        and injury_strength >= 0.30
        and projection_delta >= 1.8
    ):
        projection_dampener = min(0.10, injury_strength * 0.30)
        confidence_dampener = min(0.05, injury_strength * 0.20)
        dampened = True
        reason = "low_line_high_boost_concern"

    # Apply dampening if triggered
    if dampened:
        dampened_projection = projection * (1.0 - projection_dampener)
        dampened_confidence = confidence * (1.0 - confidence_dampener)
        payload["player_points_realism_dampened"] = True
        payload["player_points_realism_dampener_reason"] = reason
        payload["player_points_projection_dampener"] = round(float(projection_dampener), 4)
        payload["player_points_confidence_dampener"] = round(float(confidence_dampener), 4)

    return dampened_projection, dampened_confidence, payload
