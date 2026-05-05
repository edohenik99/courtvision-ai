"""Player Points Projection Recalibration

Feature-flagged experimental layer for reducing over-projection in player_points markets.

Environment variable:
    COURTVISION_PLAYER_POINTS_RECALIBRATION=off|shadow|enabled

Modes:
    off:     Production behavior unchanged (default)
    shadow:  Compute recalibrated values but don't use for selection
    enabled: Currently treated as shadow-only until edge semantics are tested
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RECALIBRATION_ENV_VAR = "COURTVISION_PLAYER_POINTS_RECALIBRATION"
DEFAULT_MODE = "off"
ENABLED_UNSUPPORTED_REASON = "recalibration_enabled_mode_not_supported_shadow_only"


class RecalibrationMode:
    OFF = "off"
    SHADOW = "shadow"
    ENABLED = "enabled"


@dataclass(frozen=True)
class RecalibrationConfig:
    """Configuration for player_points recalibration adjustments."""

    # 0. Flat over penalty (reduce all over projections)
    over_penalty_multiplier: float = 0.88  # 12% reduction for overs

    # 1. Sportsbook-line shrinkage
    shrinkage_weight: float = 0.45  # 45% market line, 55% model
    shrinkage_weight_over_extra: float = 0.10  # +10% for overs (total 55%)

    # 2. Opponent defense adjustment
    defense_adj_weight: float = 0.03
    defense_league_avg: float = 113.0

    # 3. Pace adjustment
    pace_adj_weight: float = 0.015
    pace_adj_cap: float = 0.03  # +/-3%
    pace_league_avg: float = 100.0

    # 4. Playoff/role dampener
    playoff_role_dampener: float = 0.96  # role players in playoffs
    playoff_star_boost: float = 1.02  # stars in playoffs

    # 5. Recent-form ratio dampener
    form_damp_floor: float = 0.90
    form_damp_ceil: float = 1.08

    # 6. Minutes sanity check
    minutes_sanity_threshold: float = 24.0
    minutes_sanity_factor_base: float = 24.0

    # Minimum recalibrated edge for player_points OVERs
    min_recalibrated_edge_over: float = 1.0


# Default config instance
DEFAULT_CONFIG = RecalibrationConfig()


# ---------------------------------------------------------------------------
# Feature Flag
# ---------------------------------------------------------------------------

def get_recalibration_mode() -> str:
    """Get the effective recalibration mode.

    ``enabled`` is intentionally downgraded to ``shadow`` for now. The current
    pipeline has not validated enabled-mode edge semantics for UNDER picks, so
    this module must not allow recalibration to alter production selection.
    """
    mode = get_recalibration_requested_mode()
    return _effective_mode(mode)


def get_recalibration_requested_mode() -> str:
    """Get the raw requested mode from the environment."""
    return _normalize_recalibration_mode(os.environ.get(RECALIBRATION_ENV_VAR, DEFAULT_MODE))


def is_recalibration_off() -> bool:
    return get_recalibration_mode() == RecalibrationMode.OFF


def is_recalibration_shadow() -> bool:
    return get_recalibration_mode() == RecalibrationMode.SHADOW


def is_recalibration_enabled() -> bool:
    return get_recalibration_mode() == RecalibrationMode.ENABLED


def is_enabled_mode_requested() -> bool:
    return get_recalibration_requested_mode() == RecalibrationMode.ENABLED


# ---------------------------------------------------------------------------
# Recalibration Logic
# ---------------------------------------------------------------------------

def recalibrate_player_points(
    row: Mapping[str, Any],
    config: RecalibrationConfig | None = None,
    requested_mode: str | None = None,
) -> dict[str, Any]:
    """Apply all recalibration adjustments to a player_points pick.

    Returns dict with:
        - recalibrated_projection: float
        - recalibrated_edge: float (direction-aware)
        - components: dict of adjustment details
        - rejection_reason: str (empty if valid)
    """
    cfg = config or DEFAULT_CONFIG

    original_projection = float(row.get("model_projection") or row.get("projection") or 0)
    sportsbook_line = float(row.get("sportsbook_line") or row.get("line") or 0)
    selection = str(row.get("selection") or "").strip().lower()
    requested_mode_value = _normalize_recalibration_mode(requested_mode) if requested_mode is not None else get_recalibration_requested_mode()
    effective_mode = _effective_mode(requested_mode_value)

    # Parse contextual fields (may be missing)
    minutes_avg = _to_float(row.get("minutes_avg")) or _to_float(row.get("min_avg")) or 0.0
    recent_form_ratio = _to_float(row.get("player_points_recent_form_ratio")) or 1.0
    opponent_def_rating = _to_float(row.get("opponent_def_rating"))
    matchup_pace = _to_float(row.get("matchup_pace"))
    postseason = _to_bool(row.get("postseason"))
    profile_bucket = str(row.get("player_profile_bucket") or "")

    projection = original_projection
    components: dict[str, Any] = {
        "original_projection": round(original_projection, 4),
        "selection": selection,
        "line": sportsbook_line,
        "requested_mode": requested_mode_value,
        "effective_mode": effective_mode,
    }
    notes: list[str] = []

    # 0. Flat over penalty
    if selection == "over":
        projection *= cfg.over_penalty_multiplier
        notes.append(f"over_penalty:{cfg.over_penalty_multiplier}")
        components["over_penalty"] = cfg.over_penalty_multiplier

    # 1. Sportsbook-line shrinkage
    if sportsbook_line > 0:
        weight = cfg.shrinkage_weight
        if selection == "over":
            weight = min(0.60, cfg.shrinkage_weight + cfg.shrinkage_weight_over_extra)
        projection = (1.0 - weight) * projection + weight * sportsbook_line
        notes.append(f"shrinkage:{weight:.2f}")
        components["shrinkage_weight"] = round(weight, 2)

    # 2. Opponent defense adjustment
    if opponent_def_rating is not None:
        adj = (opponent_def_rating - cfg.defense_league_avg) * cfg.defense_adj_weight
        projection *= 1.0 + adj
        notes.append(f"defense_adj:{adj:.4f}")
        components["defense_adj"] = round(adj, 4)

    # 3. Pace adjustment
    if matchup_pace is not None:
        adj = (matchup_pace - cfg.pace_league_avg) * cfg.pace_adj_weight
        adj = max(-cfg.pace_adj_cap, min(cfg.pace_adj_cap, adj))
        projection *= 1.0 + adj
        notes.append(f"pace_adj:{adj:.4f}")
        components["pace_adj"] = round(adj, 4)

    # 4. Playoff/role dampener
    if postseason:
        if profile_bucket in {"role_low_usage", "starter_secondary"}:
            projection *= cfg.playoff_role_dampener
            notes.append(f"playoff_damp:{cfg.playoff_role_dampener}")
            components["playoff_dampener"] = cfg.playoff_role_dampener
        elif profile_bucket == "star_high_usage":
            projection *= cfg.playoff_star_boost
            notes.append(f"playoff_boost:{cfg.playoff_star_boost}")
            components["playoff_boost"] = cfg.playoff_star_boost

    # 5. Recent-form ratio dampener
    form_damp = max(cfg.form_damp_floor, min(cfg.form_damp_ceil, recent_form_ratio))
    projection *= form_damp
    notes.append(f"form_damp:{form_damp:.4f}")
    components["form_damp"] = round(form_damp, 4)

    # 6. Minutes sanity check
    if 0 < minutes_avg < cfg.minutes_sanity_threshold:
        factor = min(1.0, minutes_avg / cfg.minutes_sanity_factor_base)
        projection *= factor
        notes.append(f"minutes_sanity:{factor:.4f}")
        components["minutes_sanity_factor"] = round(factor, 4)

    # Compute recalibrated edge (direction-aware)
    if selection == "over":
        recalibrated_edge = projection - sportsbook_line
    elif selection == "under":
        recalibrated_edge = sportsbook_line - projection
    else:
        recalibrated_edge = abs(projection - sportsbook_line)

    # Determine rejection reason
    rejection_reason = ""
    if selection == "over" and recalibrated_edge < cfg.min_recalibrated_edge_over:
        rejection_reason = f"recalibrated_edge_below_{cfg.min_recalibrated_edge_over}"

    if requested_mode_value == RecalibrationMode.ENABLED:
        rejection_reason = ENABLED_UNSUPPORTED_REASON

    components["adjustments"] = "; ".join(notes)

    return {
        "recalibrated_projection": round(max(0.0, projection), 4),
        "recalibrated_edge": round(recalibrated_edge, 4),
        "recalibration_components_json": json.dumps(components),
        "recalibration_selected": rejection_reason == "",
        "recalibration_rejection_reason": rejection_reason,
    }


def _to_float(v: Any) -> float | None:
    """Safely convert value to float."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_bool(v: Any) -> bool:
    """Safely convert value to bool."""
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"true", "1", "yes", "t"}


def _normalize_recalibration_mode(mode: str) -> str:
    text = str(mode or DEFAULT_MODE).strip().lower()
    if text not in {RecalibrationMode.OFF, RecalibrationMode.SHADOW, RecalibrationMode.ENABLED}:
        return RecalibrationMode.OFF
    return text


def _effective_mode(requested_mode: str) -> str:
    if requested_mode == RecalibrationMode.ENABLED:
        return RecalibrationMode.SHADOW
    return requested_mode


# ---------------------------------------------------------------------------
# Selection Helpers
# ---------------------------------------------------------------------------

def should_use_recalibrated_for_selection(
    market_type: str,
    recalibrated_edge: float,
    min_edge: float = 1.0,
) -> bool:
    """Determine if recalibrated values should be used for selection.

    Only applies to player_points markets when enabled.
    """
    if not is_recalibration_enabled():
        return False
    if market_type != "player_points":
        return False
    return recalibrated_edge >= min_edge


def get_effective_projection_and_edge(
    row: Mapping[str, Any],
    config: RecalibrationConfig | None = None,
) -> dict[str, Any]:
    """Get the effective projection and edge for selection logic.

    Returns dict with:
        - projection: float (effective value to use)
        - edge: float (effective edge to use)
        - source: str ('original' or 'recalibrated')
        - recalibration_applied: bool
    """
    mode = get_recalibration_mode()
    requested_mode = get_recalibration_requested_mode()
    market_type = str(row.get("market_type") or "").strip().lower()

    if mode == RecalibrationMode.OFF or market_type != "player_points":
        return {
            "projection": float(row.get("model_projection") or row.get("projection") or 0),
            "edge": float(row.get("edge") or row.get("edge_pct") or 0),
            "source": "original",
            "recalibration_applied": False,
        }

    # Compute recalibrated values
    recal = recalibrate_player_points(row, config)

    if mode == RecalibrationMode.SHADOW:
        # Shadow mode: compute but don't use
        return {
            "projection": float(row.get("model_projection") or row.get("projection") or 0),
            "edge": float(row.get("edge") or row.get("edge_pct") or 0),
            "source": "original",
            "recalibration_applied": False,
            "recalibration_requested_mode": requested_mode,
            "recalibration_effective_mode": mode,
            "recalibrated_projection": recal["recalibrated_projection"],
            "recalibrated_edge": recal["recalibrated_edge"],
            "recalibration_components_json": recal["recalibration_components_json"],
            "recalibration_selected": recal["recalibration_selected"],
            "recalibration_rejection_reason": recal["recalibration_rejection_reason"],
        }

    # Enabled mode: use recalibrated if valid
    if recal["recalibration_selected"]:
        return {
            "projection": recal["recalibrated_projection"],
            "edge": recal["recalibrated_edge"],
            "source": "recalibrated",
            "recalibration_applied": True,
            "recalibrated_projection": recal["recalibrated_projection"],
            "recalibrated_edge": recal["recalibrated_edge"],
            "recalibration_components_json": recal["recalibration_components_json"],
            "recalibration_selected": True,
            "recalibration_rejection_reason": "",
        }
    else:
        # Rejected by recalibration - return original but mark as rejected
        return {
            "projection": float(row.get("model_projection") or row.get("projection") or 0),
            "edge": float(row.get("edge") or row.get("edge_pct") or 0),
            "source": "original",
            "recalibration_applied": False,
            "recalibrated_projection": recal["recalibrated_projection"],
            "recalibrated_edge": recal["recalibrated_edge"],
            "recalibration_components_json": recal["recalibration_components_json"],
            "recalibration_selected": False,
            "recalibration_rejection_reason": recal["recalibration_rejection_reason"],
        }
