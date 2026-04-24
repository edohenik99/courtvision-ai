"""Injury volatility calculations.

This module extracts recent form ratio and injury-independent support
logic from courtvision_ai.py.
"""

from __future__ import annotations

from typing import Any, Mapping

from courtvision.calibration.buckets import to_float


def compute_recent_form_ratio(
    player_row: Mapping[str, Any],
    baseline_projection: float,
) -> float:
    """Compute recent form ratio based on recent vs season averages.

    Returns a value between 0.0 and 1.5 indicating recent performance
    relative to baseline/season average.
    """
    recent_avg = to_float(player_row.get("pts_recent"))
    season_avg = to_float(player_row.get("pts_avg"))
    anchor = max(abs(season_avg or 0.0), abs(float(baseline_projection)), 12.0)

    if recent_avg is not None:
        return max(0.0, min(1.5, float(recent_avg) / anchor))
    if season_avg is not None:
        return max(0.0, min(1.5, float(season_avg) / anchor))
    return 1.0


def compute_injury_independent_support(
    player_row: Mapping[str, Any],
    baseline_projection: float,
    recent_form_ratio: float,
) -> float:
    """Compute injury-independent support score.

    This measures how much confidence uplift is appropriate based on
    factors independent of injury context (recent form, minutes, season avg).

    Returns a value between 0.0 and 0.05.
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


def compute_injury_volatility(
    player_row: Mapping[str, Any],
    baseline_projection: float,
) -> dict[str, float]:
    """Compute injury volatility metrics.

    Returns dict with:
    - recent_form_ratio: Performance ratio vs baseline
    - independent_support: Injury-independent confidence support
    """
    recent_form_ratio = compute_recent_form_ratio(player_row, baseline_projection)
    independent_support = compute_injury_independent_support(
        player_row, baseline_projection, recent_form_ratio
    )

    return {
        "recent_form_ratio": round(float(recent_form_ratio), 4),
        "independent_support": round(float(independent_support), 4),
    }


def compute_volatility_confidence_penalty(
    player_row: Mapping[str, Any],
    baseline_projection: float,
    injury_impact: float = 0.0,
) -> dict[str, Any]:
    """Compute confidence penalty from injury volatility factors.

    Ensures injury volatility appropriately reduces confidence by combining:
    - Recent form instability (large deviation from baseline)
    - High injury impact (significant minutes/projection reduction)
    - Low independent support (weak non-injury confidence signals)

    Args:
        player_row: Player data with stats
        baseline_projection: Baseline projection for the player
        injury_impact: Injury impact score (0.0-1.0, where 1.0 = high impact)

    Returns dict with:
    - penalty: Confidence reduction (0.0-0.20)
    - reasons: List of reasons for the penalty
    - volatility_score: Computed volatility metric
    """
    volatility = compute_injury_volatility(player_row, baseline_projection)
    recent_form_ratio = volatility["recent_form_ratio"]
    independent_support = volatility["independent_support"]

    penalty = 0.0
    reasons: list[str] = []

    # Recent form instability penalty (large deviation from baseline)
    if recent_form_ratio < 0.75:
        penalty += 0.06
        reasons.append(f"recent_form_well_below_baseline_{recent_form_ratio:.2f}")
    elif recent_form_ratio < 0.85:
        penalty += 0.03
        reasons.append(f"recent_form_below_baseline_{recent_form_ratio:.2f}")
    elif recent_form_ratio > 1.35:
        penalty += 0.04
        reasons.append(f"recent_form_unstable_high_{recent_form_ratio:.2f}")

    # High injury impact penalty
    if injury_impact > 0.4:
        penalty += 0.08
        reasons.append(f"high_injury_impact_{injury_impact:.2f}")
    elif injury_impact > 0.2:
        penalty += 0.04
        reasons.append(f"moderate_injury_impact_{injury_impact:.2f}")

    # Low independent support penalty (no strong non-injury signals)
    if independent_support < 0.02:
        penalty += 0.04
        reasons.append(f"weak_independent_support_{independent_support:.3f}")

    # Combined volatility score for diagnostics
    volatility_score = min(1.0, (1.0 - recent_form_ratio) + injury_impact + (0.05 - independent_support))

    return {
        "penalty": round(min(penalty, 0.20), 4),  # Cap at 20%
        "reasons": reasons,
        "volatility_score": round(volatility_score, 4),
        "recent_form_ratio": recent_form_ratio,
        "independent_support": independent_support,
    }
