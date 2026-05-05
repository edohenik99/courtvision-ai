from __future__ import annotations

from typing import Any, Mapping

QUALITY_BANDS = ["low", "mid", "high", "elite"]
CONFIDENCE_BANDS = ["low", "mid", "high"]
ODDS_BUCKETS = ["heavy_fav", "fav", "near_even", "plus_small", "plus_big"]
MINUTES_BUCKETS = ["lt_22", "22_28", "28_34", "34_plus"]
MARKET_TRUST_WEIGHT_BANDS = ["low", "mid", "high"]
PLAYER_PROFILE_BUCKETS = ["role_low_usage", "starter_secondary", "star_high_usage"]
PLAYER_POINTS_LINE_BANDS = ["lte_14_5", "15_to_19_5", "20_to_26_5", "gte_27"]
INJURY_INFLUENCE_BUCKETS = ["none", "low", "mid", "high"]


def to_float(value: Any) -> float | None:
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


def quality_band(value: Any) -> str:
    score = to_float(value)
    if score is None:
        return "unknown"
    if score >= 90.0:
        return "elite"
    if score >= 80.0:
        return "high"
    if score >= 70.0:
        return "mid"
    return "low"


def confidence_band(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        label_map = {
            "strong": "high",
            "high": "high",
            "medium": "mid",
            "mid": "mid",
            "low": "low",
        }
        if normalized in label_map:
            return label_map[normalized]

    confidence = to_float(value)
    if confidence is None:
        return "unknown"
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.65:
        return "mid"
    return "low"


def odds_bucket(value: Any) -> str:
    odds = to_float(value)
    if odds is None:
        return "not_applicable"
    if odds <= -200:
        return "heavy_fav"
    if odds <= -120:
        return "fav"
    if odds < 120:
        return "near_even"
    if odds < 250:
        return "plus_small"
    return "plus_big"


def projected_minutes(row: Mapping[str, Any]) -> float | None:
    candidates = [
        row.get("minutes_projection"),
        row.get("expected_minutes"),
        row.get("minutes_recent"),
        row.get("minutes_avg"),
        row.get("min_recent"),
        row.get("min_avg"),
    ]
    numeric_values = [to_float(value) for value in candidates]
    valid_values = [float(value) for value in numeric_values if value is not None]
    if not valid_values:
        return None
    return max(valid_values)


def minutes_bucket(row: Mapping[str, Any]) -> str:
    minutes = projected_minutes(row)
    if minutes is None or minutes <= 0.0:
        return "not_applicable"
    if minutes >= 34.0:
        return "34_plus"
    if minutes >= 28.0:
        return "28_34"
    if minutes >= 22.0:
        return "22_28"
    return "lt_22"


def market_trust_weight_band(value: Any) -> str:
    weight = to_float(value)
    if weight is None:
        return "unknown"
    if weight >= 0.95:
        return "high"
    if weight >= 0.85:
        return "mid"
    return "low"


def player_profile_bucket(row: Mapping[str, Any]) -> str:
    market_type = str(row.get("market_type") or row.get("prop_type") or "").strip().lower()
    if not market_type.startswith("player_") and market_type not in {"points", "rebounds", "assists", "threes"}:
        return "not_applicable"

    if market_type == "points":
        market_type = "player_points"
    elif market_type == "rebounds":
        market_type = "player_rebounds"
    elif market_type == "assists":
        market_type = "player_assists"
    elif market_type == "threes":
        market_type = "player_3pt_made"

    line_value = to_float(row.get("sportsbook_line"))
    if line_value is None:
        line_value = to_float(row.get("line_value"))

    tier_weight = to_float(row.get("player_tier_weight"))
    if tier_weight is None:
        minutes = projected_minutes(row)
        if minutes is not None:
            if minutes >= 34.0:
                tier_weight = 1.15
            elif minutes >= 28.0:
                tier_weight = 1.05
            elif minutes >= 20.0:
                tier_weight = 0.95
            else:
                tier_weight = 0.75

    if market_type == "player_points":
        if (tier_weight is not None and tier_weight >= 1.15 and (line_value is None or line_value >= 22.5)):
            return "star_high_usage"
        if line_value is not None and line_value <= 16.5:
            return "role_low_usage"
        if tier_weight is not None and tier_weight <= 1.05 and (line_value is None or line_value <= 22.5):
            return "role_low_usage"
        return "starter_secondary"

    if tier_weight is not None and tier_weight >= 1.15:
        return "star_high_usage"
    if line_value is not None and market_type == "player_3pt_made" and line_value <= 1.5:
        return "role_low_usage"
    if tier_weight is not None and tier_weight <= 0.95:
        return "role_low_usage"
    return "starter_secondary"


def player_points_line_band(row: Mapping[str, Any]) -> str:
    market_type = str(row.get("market_type") or row.get("prop_type") or "").strip().lower()
    if market_type == "points":
        market_type = "player_points"
    if market_type != "player_points":
        return "not_applicable"

    line_value = to_float(row.get("sportsbook_line"))
    if line_value is None:
        line_value = to_float(row.get("line_value"))
    if line_value is None:
        return "unknown"
    if line_value <= 14.5:
        return "lte_14_5"
    if line_value <= 19.5:
        return "15_to_19_5"
    if line_value <= 26.5:
        return "20_to_26_5"
    return "gte_27"


def injury_influence_value(row: Mapping[str, Any]) -> float:
    direct_candidates = [
        row.get("selection_team_injury_impact"),
        row.get("selection_opponent_injury_impact"),
        row.get("injury_impact_score"),
        row.get("team_injury_impact"),
        row.get("opponent_injury_impact"),
    ]
    numeric_values = [to_float(value) for value in direct_candidates]
    valid_values = [float(value) for value in numeric_values if value is not None]

    notes_text = " ".join(
        str(row.get(key, "") or "")
        for key in ["selection_injury_notes", "injury_notes", "notes"]
    )
    for token in str(notes_text).replace(";", " ").split():
        if ":" not in token:
            continue
        label, raw_value = token.split(":", 1)
        if label.strip().lower() not in {"team_relief", "opp_relief"}:
            continue
        parsed = to_float(raw_value)
        if parsed is not None:
            valid_values.append(float(parsed))

    if not valid_values:
        return 0.0
    return max(0.0, max(valid_values))


def injury_influence_bucket(row: Mapping[str, Any]) -> str:
    value = injury_influence_value(row)
    if value < 0.05:
        return "none"
    if value < 0.15:
        return "low"
    if value < 0.30:
        return "mid"
    return "high"


def injury_projection_delta_value(row: Mapping[str, Any]) -> float:
    explicit = to_float(row.get("injury_projection_delta"))
    if explicit is not None:
        return float(explicit)
    baseline = to_float(row.get("injury_baseline_projection"))
    adjusted = to_float(row.get("injury_adjusted_projection"))
    if baseline is None or adjusted is None:
        return 0.0
    return float(adjusted - baseline)


def injury_confidence_delta_value(row: Mapping[str, Any]) -> float:
    explicit = to_float(row.get("injury_confidence_delta"))
    if explicit is not None:
        return float(explicit)
    baseline = to_float(row.get("injury_baseline_confidence"))
    adjusted = to_float(row.get("injury_adjusted_confidence"))
    if baseline is None or adjusted is None:
        return 0.0
    return float(adjusted - baseline)


# Edge bucket thresholds operate on raw absolute edge values (stat units, e.g. points).
# Do NOT pass decimal-fraction (0.07) or percentage-style (7.0) values — they will be
# misclassified. See courtvision/scoring/edge.py for edge_pct_value() which returns %.
ABS_EDGE_BUCKETS = ["<1", "1-2", "2-3", "3-5", "5+"]


def abs_edge_bucket(value: Any) -> str:
    """Classify a raw absolute edge value (stat units, e.g. points) into a named bucket.

    Bucket thresholds (stat-unit scale):
      "<1"      abs_edge < 1.0
      "1-2"     1.0 <= abs_edge < 2.0
      "2-3"     2.0 <= abs_edge < 3.0
      "3-5"     3.0 <= abs_edge < 5.0
      "5+"      abs_edge >= 5.0
      "unknown" value is None or non-numeric
    """
    v = to_float(value)
    if v is None:
        return "unknown"
    abs_v = abs(v)
    if abs_v < 1.0:
        return "<1"
    if abs_v < 2.0:
        return "1-2"
    if abs_v < 3.0:
        return "2-3"
    if abs_v < 5.0:
        return "3-5"
    return "5+"


__all__ = [
    "ABS_EDGE_BUCKETS",
    "CONFIDENCE_BANDS",
    "INJURY_INFLUENCE_BUCKETS",
    "MARKET_TRUST_WEIGHT_BANDS",
    "MINUTES_BUCKETS",
    "ODDS_BUCKETS",
    "PLAYER_PROFILE_BUCKETS",
    "PLAYER_POINTS_LINE_BANDS",
    "QUALITY_BANDS",
    "abs_edge_bucket",
    "confidence_band",
    "injury_confidence_delta_value",
    "injury_influence_bucket",
    "injury_influence_value",
    "injury_projection_delta_value",
    "player_profile_bucket",
    "player_points_line_band",
    "market_trust_weight_band",
    "minutes_bucket",
    "odds_bucket",
    "projected_minutes",
    "quality_band",
    "to_float",
]
