"""Confidence computation functions for candidate scoring.

This module extracts confidence-related logic from runtime_scoring.py including:
- Historical confidence multiplier
- Player points scoring stability
- Base confidence computation
"""

from __future__ import annotations

from typing import Any, Mapping

from courtvision.calibration.buckets import player_profile_bucket
from courtvision.scoring.penalties import longshot_penalty_points, to_float


def player_points_scoring_stability(row: Mapping[str, Any], sportsbook_line: float) -> float:
    """Compute scoring stability metric for player points markets.

    Considers:
    - Recent form ratio (direct metric)
    - Recent vs season average drift
    - Model projection drift vs line

    Returns a value between 0.0 and 1.0 where higher is more stable.
    """
    recent_form_ratio = to_float(row.get("recent_form_ratio"))
    if recent_form_ratio is not None:
        return min(max(recent_form_ratio, 0.0), 1.0)

    recent_avg = to_float(row.get("recent_avg"))
    season_avg = to_float(row.get("season_avg"))
    if recent_avg is not None and season_avg is not None:
        baseline = max(abs(season_avg), abs(sportsbook_line), 12.0)
        drift = abs(recent_avg - season_avg) / baseline
        return min(max(1.0 - min(1.0, drift * 1.35), 0.0), 1.0)

    model_projection = to_float(row.get("model_projection"))
    if model_projection is not None:
        baseline = max(abs(model_projection), abs(sportsbook_line), 12.0)
        drift = abs(model_projection - sportsbook_line) / baseline
        return min(max(0.62 + min(0.22, drift * 0.35), 0.0), 1.0)

    return 0.62


def historical_confidence_multiplier(row: Mapping[str, Any]) -> float:
    """Compute historical confidence multiplier for a candidate.

    This is a complex weighting of:
    - Minutes projection and stability
    - Base confidence score
    - Edge ratio (edge as % of line)
    - Injury risk
    - For player_points: scoring stability and profile bucket adjustments

    Returns a multiplier between 0.55 and 1.05 (or 1.02 for moneyline).
    """
    market_type = str(row.get("market_type", ""))
    confidence = to_float(row.get("confidence")) or 0.0

    if market_type == "moneyline":
        multiplier = 0.78 + max(0.0, confidence - 0.50) * 0.45
        multiplier -= longshot_penalty_points(row.get("odds")) / 100.0
        return min(max(multiplier, 0.60), 1.02)

    minutes_avg = to_float(row.get("minutes_avg")) or 0.0
    minutes_recent = to_float(row.get("minutes_recent")) or minutes_avg
    minutes_projection = max(minutes_avg, minutes_recent)
    stability = max(
        0.0,
        1.0 - min(1.0, abs(minutes_recent - minutes_avg) / max(minutes_projection, 1.0)),
    )
    minutes_component = min(1.0, minutes_projection / 36.0)
    injury_risk = min(
        0.75,
        max(
            to_float(row.get("injury_impact_score")) or 0.0,
            to_float(row.get("selection_team_injury_impact")) or 0.0,
            to_float(row.get("team_injury_impact")) or 0.0,
        ),
    )
    edge_abs = to_float(row.get("edge_abs"))
    if edge_abs is None:
        edge_abs = abs(to_float(row.get("edge")) or 0.0)

    if market_type == "player_points":
        sportsbook_line = abs(to_float(row.get("sportsbook_line")) or 0.0)
        edge_ratio = min(1.0, edge_abs / max(sportsbook_line, 14.0))
        scoring_stability = player_points_scoring_stability(row, sportsbook_line)
        profile_bucket = player_profile_bucket(row)
        selection = str(row.get("selection", "")).strip().lower()

        multiplier = (
            0.42
            + (0.22 * minutes_component)
            + (0.08 * stability)
            + (0.22 * confidence)
            + (0.16 * edge_ratio)
            + (0.12 * scoring_stability)
            - (0.14 * injury_risk)
        )

        if profile_bucket == "role_low_usage" and selection == "over" and sportsbook_line <= 16.5 and injury_risk >= 0.25:
            multiplier -= 0.12
        elif selection == "under" and profile_bucket == "role_low_usage":
            multiplier -= 0.08
        elif selection == "under" and profile_bucket == "starter_secondary" and sportsbook_line <= 26.5:
            multiplier -= 0.06

        return min(max(multiplier, 0.55), 1.02)

    multiplier = 0.45 + (0.30 * minutes_component) + (0.15 * stability) + (0.20 * confidence) - (0.15 * injury_risk)
    return min(max(multiplier, 0.55), 1.05)


def compute_confidence(
    row: Mapping[str, Any],
    edge_abs: float,
    market_type: str,
    minutes_projection: float,
    market_quality_score: float = 1.0,
    recent_form_ratio: float = 1.0,
    injury_status: str = "",
) -> dict[str, float]:
    """Compute adjusted confidence for a candidate.

    Applies edge-based boosts, minutes-based floors, market quality penalties,
    and anti-double-count protection to base confidence.

    Args:
        row: Candidate data row
        edge_abs: Absolute edge value
        market_type: Type of market (e.g., "player_points")
        minutes_projection: Projected minutes for player
        market_quality_score: Quality score for market data (0.0-1.0)
        recent_form_ratio: Recent performance ratio for form tracking
        injury_status: Player injury status

    Returns a dict with:
    - base_confidence: Original confidence value
    - adjusted_confidence: After all adjustments
    - edge_boost: Additional confidence from edge
    - edge_dampening: Confidence reduction from excessive edge
    - market_penalty: Confidence reduction from poor market quality
    - anti_double_count_adj: Adjustment to prevent boost stacking
    - diagnostics: Dict explaining each adjustment applied
    """
    base_confidence = to_float(row.get("confidence")) or 0.0
    diagnostics: dict[str, Any] = {"adjustments": []}

    # Edge-based confidence adjustments with dampening for very large edges
    # Reduces overconfidence from outsized edges
    adjusted_confidence = base_confidence
    edge_boost = 0.0
    edge_dampening = 0.0

    if edge_abs > 10.0:
        # Very large edges get reduced boost + dampening
        edge_boost = 0.02
        edge_dampening = 0.03
        diagnostics["adjustments"].append("large_edge_dampening_applied")
    elif edge_abs > 6.0:
        edge_boost = 0.04
    elif edge_abs > 4.5:
        edge_boost = 0.02

    adjusted_confidence += edge_boost - edge_dampening

    # Market quality penalty: reduce confidence for weak market data
    market_penalty = 0.0
    if market_quality_score < 0.7:
        market_penalty = (0.7 - market_quality_score) * 0.15
        adjusted_confidence -= market_penalty
        diagnostics["adjustments"].append(f"market_quality_penalty_{market_penalty:.3f}")

    # Anti-double-count protection: prevent stacking multiple boosts on same signal
    # If player has strong recent form AND high minutes AND no injury, limit total boost
    anti_double_count_adj = 0.0
    boost_signals = 0

    if recent_form_ratio > 1.15:
        boost_signals += 1
    if minutes_projection >= 28.0:
        boost_signals += 1
    if injury_status in ["", "healthy", "active"]:
        boost_signals += 1

    if boost_signals >= 3 and edge_boost > 0:
        # Cap total boost when multiple positive signals present
        anti_double_count_adj = -0.02
        diagnostics["adjustments"].append("anti_double_count_cap_applied")

    adjusted_confidence += anti_double_count_adj

    # Apply cap before minutes floor
    adjusted_confidence = min(adjusted_confidence, 0.96)  # Lowered from 0.99

    # Minutes-based floor for player props (only if not heavily penalized)
    minutes_floor_applied = False
    if market_type.startswith("player_"):
        if minutes_projection >= 28.0 and market_penalty < 0.10:
            adjusted_confidence = max(adjusted_confidence, 0.58)
            minutes_floor_applied = True

    if minutes_floor_applied:
        diagnostics["adjustments"].append("minutes_floor_28plus")

    # Final bounds check
    adjusted_confidence = max(0.0, min(adjusted_confidence, 0.96))

    diagnostics["final_confidence"] = round(float(adjusted_confidence), 4)

    return {
        "base_confidence": round(float(base_confidence), 4),
        "adjusted_confidence": round(float(adjusted_confidence), 4),
        "edge_boost": round(float(edge_boost), 4),
        "edge_dampening": round(float(edge_dampening), 4),
        "market_penalty": round(float(market_penalty), 4),
        "anti_double_count_adj": round(float(anti_double_count_adj), 4),
        "diagnostics": diagnostics,
    }
