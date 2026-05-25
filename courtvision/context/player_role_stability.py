"""Player Role Stability context diagnostics.

Calculates player minutes and role context stability.
Report-only/diagnostic layer; does not affect any active betting, Elite gates,
Kelly staking, or final decisions.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return default if text.lower() in {"nan", "none", "null", "<na>"} else text


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def calculate_player_role_stability_row(
    row: Mapping[str, Any] | pd.Series,
    baseline_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Calculate deterministic stability score, bucket, and reasons for a single candidate row."""
    prediction_date = _safe_text(row.get("prediction_date"))
    game_id = _safe_text(row.get("game_id")) or _safe_text(row.get("game_key"))
    player_id = _safe_text(row.get("player_id"))
    player_name = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))
    
    has_team_abbr = False
    if hasattr(row, "index"):
        has_team_abbr = "team_abbr" in row.index
    else:
        has_team_abbr = "team_abbr" in row
    team_source = "team_abbr" if has_team_abbr else "team"
    team = _safe_text(row.get(team_source))
    
    has_opponent_abbr = False
    has_opp_abbr = False
    if hasattr(row, "index"):
        has_opponent_abbr = "opponent_abbr" in row.index
        has_opp_abbr = "opp_abbr" in row.index
    else:
        has_opponent_abbr = "opponent_abbr" in row
        has_opp_abbr = "opp_abbr" in row
    opponent_source = "opponent_abbr" if has_opponent_abbr else "opp_abbr" if has_opp_abbr else "opponent"
    opponent = _safe_text(row.get(opponent_source))
    
    market_type = _safe_text(row.get("market_type"))
    selection = _safe_text(row.get("selection"))

    # Resolve minutes values from row with baseline fallback
    minutes_avg = _safe_float(row.get("minutes_avg")) or _safe_float(row.get("min_avg"))
    minutes_recent = _safe_float(row.get("minutes_recent")) or _safe_float(row.get("min_recent"))

    if (minutes_avg is None or minutes_recent is None) and baseline_df is not None and not baseline_df.empty:
        match = pd.DataFrame()
        if player_name:
            match = baseline_df[baseline_df["player_name"].fillna("").astype(str).str.lower() == player_name.lower()]
        if match.empty and player_id:
            if "player_id" in baseline_df.columns:
                match = baseline_df[baseline_df["player_id"].fillna("").astype(str) == str(player_id)]
        if not match.empty:
            baseline_row = match.iloc[0]
            if minutes_avg is None:
                minutes_avg = _safe_float(baseline_row.get("min_avg")) or _safe_float(baseline_row.get("minutes_avg"))
            if minutes_recent is None:
                minutes_recent = _safe_float(baseline_row.get("min_recent")) or _safe_float(baseline_row.get("minutes_recent"))

    # Extract other metrics
    minutes_projection = (
        _safe_float(row.get("minutes_projection"))
        or _safe_float(row.get("projected_minutes"))
        or _safe_float(row.get("projection_minutes"))
        or _safe_float(row.get("model_projection"))
        or _safe_float(row.get("expected_minutes"))
    )

    minutes_cv_recent = (
        _safe_float(row.get("minutes_cv_recent"))
        or _safe_float(row.get("minutes_cv"))
        or _safe_float(row.get("cv_recent"))
    )

    manual_minutes_delta = _safe_float(row.get("manual_minutes_delta"))
    if manual_minutes_delta is None:
        # Check if we can compute it from manual context
        manual_limit = _safe_float(row.get("manual_minutes_limit")) or _safe_float(row.get("minutes_limit"))
        if manual_limit is not None:
            reference = minutes_projection or minutes_avg or minutes_recent or 0.0
            manual_minutes_delta = manual_limit - reference

    raw_injury = row.get("injury_role_pressure")
    if raw_injury is None:
        raw_injury = row.get("injury_impact_score") or row.get("team_injury_impact")
    if isinstance(raw_injury, bool):
        injury_role_pressure = 1.0 if raw_injury else 0.0
    else:
        injury_role_pressure = _safe_float(raw_injury)

    starter_or_rotation_status = (
        _safe_text(row.get("starter_or_rotation_status"))
        or _safe_text(row.get("starter_status"))
        or "unknown"
    ).lower()

    role_data_quality = _safe_text(row.get("role_data_quality"), default="medium").lower()

    # Calculate data coverage
    evaluated_fields = [
        minutes_avg,
        minutes_recent,
        minutes_projection,
        minutes_cv_recent,
        manual_minutes_delta,
        injury_role_pressure,
    ]
    present_fields = [val for val in evaluated_fields if val is not None]
    role_stability_coverage = round(len(present_fields) / len(evaluated_fields), 4)

    # Missing critical data safety rule
    if minutes_avg is None or minutes_recent is None:
        return {
            "prediction_date": prediction_date,
            "game_id": game_id,
            "player_id": player_id,
            "player_name": player_name,
            "team": team,
            "opponent": opponent,
            "market_type": market_type,
            "selection": selection,
            "role_stability_score": None,
            "role_stability_bucket": "unknown",
            "role_stability_reasons": ["missing_critical_minutes_data"],
            "role_stability_coverage": role_stability_coverage,
            "minutes_avg": minutes_avg,
            "minutes_recent": minutes_recent,
            "minutes_projection": minutes_projection,
            "minutes_delta_recent_avg": None,
            "minutes_delta_projection_avg": None,
            "minutes_cv_recent": minutes_cv_recent,
            "manual_minutes_delta": manual_minutes_delta,
            "injury_role_pressure": injury_role_pressure,
            "starter_or_rotation_status": starter_or_rotation_status,
            "role_data_quality": role_data_quality,
        }

    # Calculate deltas
    minutes_delta_recent_avg = round(minutes_recent - minutes_avg, 4)
    minutes_delta_projection_avg = (
        round(minutes_projection - minutes_avg, 4) if minutes_projection is not None else None
    )

    # Scoring deductions
    score = 100.0
    reasons: list[str] = []

    # 1. Delta Recent vs Avg
    abs_delta_recent = abs(minutes_delta_recent_avg)
    if abs_delta_recent > 8.0:
        score -= 30.0
        reasons.append("high_recent_avg_delta")
    elif abs_delta_recent > 5.0:
        score -= 20.0
        reasons.append("moderate_recent_avg_delta")
    elif abs_delta_recent > 3.0:
        score -= 10.0
        reasons.append("mild_recent_avg_delta")

    # 2. Delta Projection vs Avg
    if minutes_delta_projection_avg is not None:
        abs_delta_proj = abs(minutes_delta_projection_avg)
        if abs_delta_proj > 8.0:
            score -= 30.0
            reasons.append("high_projection_avg_delta")
        elif abs_delta_proj > 5.0:
            score -= 20.0
            reasons.append("moderate_projection_avg_delta")
        elif abs_delta_proj > 3.0:
            score -= 10.0
            reasons.append("mild_projection_avg_delta")

    # 3. CV Recent
    if minutes_cv_recent is not None:
        if minutes_cv_recent > 0.40:
            score -= 30.0
            reasons.append("high_recent_minutes_cv")
        elif minutes_cv_recent > 0.25:
            score -= 20.0
            reasons.append("moderate_recent_minutes_cv")
        elif minutes_cv_recent > 0.15:
            score -= 10.0
            reasons.append("mild_recent_minutes_cv")

    # 4. Manual Delta
    if manual_minutes_delta is not None and manual_minutes_delta != 0.0:
        abs_manual = abs(manual_minutes_delta)
        if abs_manual > 5.0:
            score -= 20.0
            reasons.append("large_manual_minutes_adjustment")
        elif abs_manual > 2.0:
            score -= 10.0
            reasons.append("moderate_manual_minutes_adjustment")
        else:
            score -= 5.0
            reasons.append("small_manual_minutes_adjustment")

    # 5. Injury Pressure
    if injury_role_pressure is not None and injury_role_pressure > 0.0:
        score -= 20.0
        reasons.append("injury_role_pressure_detected")

    # Final score bounds
    score = round(max(0.0, min(100.0, score)), 4)

    # Bucket Mapping
    if score >= 80.0:
        bucket = "stable"
    elif score >= 60.0:
        bucket = "mostly_stable"
    elif score >= 40.0:
        bucket = "mixed"
    elif score >= 20.0:
        bucket = "volatile"
    else:
        bucket = "highly_volatile"

    if not reasons:
        reasons = ["stable_role_metrics"]

    return {
        "prediction_date": prediction_date,
        "game_id": game_id,
        "player_id": player_id,
        "player_name": player_name,
        "team": team,
        "opponent": opponent,
        "market_type": market_type,
        "selection": selection,
        "role_stability_score": score,
        "role_stability_bucket": bucket,
        "role_stability_reasons": reasons,
        "role_stability_coverage": role_stability_coverage,
        "minutes_avg": minutes_avg,
        "minutes_recent": minutes_recent,
        "minutes_projection": minutes_projection,
        "minutes_delta_recent_avg": minutes_delta_recent_avg,
        "minutes_delta_projection_avg": minutes_delta_projection_avg,
        "minutes_cv_recent": minutes_cv_recent,
        "manual_minutes_delta": manual_minutes_delta,
        "injury_role_pressure": injury_role_pressure,
        "starter_or_rotation_status": starter_or_rotation_status,
        "role_data_quality": role_data_quality,
    }


def apply_player_role_stability(
    candidates: pd.DataFrame,
    baseline_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Enrich candidate DataFrame with report-only stability metrics."""
    if candidates.empty:
        # Return empty frame with required columns
        return pd.DataFrame(
            columns=[
                "prediction_date",
                "game_id",
                "player_id",
                "player_name",
                "team",
                "opponent",
                "market_type",
                "selection",
                "role_stability_score",
                "role_stability_bucket",
                "role_stability_reasons",
                "role_stability_coverage",
                "minutes_avg",
                "minutes_recent",
                "minutes_projection",
                "minutes_delta_recent_avg",
                "minutes_delta_projection_avg",
                "minutes_cv_recent",
                "manual_minutes_delta",
                "injury_role_pressure",
                "starter_or_rotation_status",
                "role_data_quality",
            ]
        )

    enriched_rows = []
    for _, row in candidates.iterrows():
        enriched_rows.append(calculate_player_role_stability_row(row, baseline_df))

    return pd.DataFrame(enriched_rows)
