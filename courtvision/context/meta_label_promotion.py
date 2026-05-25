"""Meta-Label Promotion context rules-baseline.

Calculates rules-baseline scoring for candidates to rank them for shadow review.
Strictly report-only; does not change any live betting, Elite gates, Kelly stakes,
or final decisions.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

MODEL_VERSION = "v1.0_rules_baseline"
FEATURES_VERSION = "f1.0_rules_context_role_calibration"
DIAGNOSTIC_ONLY_NOTE = (
    "Meta-Label Promotion is shadow-only and is not an Elite/Kelly input."
)


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


def _safe_bool(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def calculate_meta_label_rules_score_row(
    row: Mapping[str, Any] | pd.Series,
    role_payload: dict[str, Any] | None = None,
    cal_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate deterministic meta-label rules score, bucket, status and reason codes for a single candidate row."""
    # Base candidate fields
    prediction_date = _safe_text(row.get("prediction_date"))
    player_id = _safe_text(row.get("player_id"))
    player_name = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))
    game_id = _safe_text(row.get("game_id")) or _safe_text(row.get("game_key"))
    market_type = _safe_text(row.get("market_type"))
    selection = _safe_text(row.get("selection"))
    line = _safe_float(row.get("line"))
    odds = _safe_float(row.get("odds"))

    # Features
    edge = _safe_float(row.get("edge"))
    edge_pct = _safe_float(row.get("edge_pct"))
    confidence = _safe_float(row.get("confidence"))
    quality_score = _safe_float(row.get("quality_score"))
    context_pick_alignment = _safe_text(row.get("context_pick_alignment")).lower()
    context_caution_level = _safe_text(row.get("context_caution_level")).lower()

    # Fallback to optional features or columns
    same_opponent_under_warning = _safe_bool(row.get("same_opponent_under_warning"))
    player_points_realism_dampened = _safe_bool(row.get("player_points_realism_dampened"))
    blocked_by_elite_points_risk_guard = _safe_bool(row.get("blocked_by_elite_points_risk_guard"))

    # Fragility / Survivability features
    fragility_score = _safe_float(row.get("fragility_score"))
    fragility_bucket = _safe_text(row.get("fragility_bucket")).upper()
    survivability_score = _safe_float(row.get("survivability_score"))
    survivability_bucket = _safe_text(row.get("survivability_bucket")).upper()

    # Missing warnings and reason codes tracking
    missing_warnings: list[str] = []
    reason_codes: list[str] = []

    # 1. Resolve role stability from row or role_payload
    role_stability_bucket = _safe_text(row.get("role_stability_bucket")).lower()
    role_stability_score = _safe_float(row.get("role_stability_score"))
    role_stability_coverage = _safe_float(row.get("role_stability_coverage"))

    if not role_stability_bucket and role_payload is not None:
        # Search in role payload rows
        role_rows = role_payload.get("rows", [])
        matched_role = None
        for r_row in role_rows:
            r_name = _safe_text(r_row.get("player_name"))
            r_id = _safe_text(r_row.get("player_id"))
            if player_name and r_name and r_name.lower() == player_name.lower():
                matched_role = r_row
                break
            if player_id and r_id and r_id == player_id:
                matched_role = r_row
                break
        if matched_role:
            role_stability_bucket = _safe_text(matched_role.get("role_stability_bucket")).lower()
            role_stability_score = _safe_float(matched_role.get("role_stability_score"))
            role_stability_coverage = _safe_float(matched_role.get("role_stability_coverage"))

    if not role_stability_bucket:
        role_stability_bucket = "unknown"
        missing_warnings.append("missing_role_stability_coverage")

    # 2. Resolve calibration bucket diagnostic if available
    calibration_observation = "unknown"
    if cal_payload is not None:
        cal_rows = cal_payload.get("rows", [])
        matched_cal = None
        # Match by market_type and selection
        for c_row in cal_rows:
            c_market = _safe_text(c_row.get("market_type"))
            c_sel = _safe_text(c_row.get("selection"))
            if (
                c_market.lower() == market_type.lower()
                and c_sel.lower() == selection.lower()
            ):
                matched_cal = c_row
                break
        if matched_cal:
            cal_gap = _safe_float(matched_cal.get("calibration_gap"))
            graded_n = _safe_float(matched_cal.get("graded_n")) or 0.0
            if cal_gap is not None:
                if cal_gap < -0.15 and graded_n >= 5.0:
                    calibration_observation = "poor"
                elif cal_gap >= -0.05 and graded_n >= 10.0:
                    calibration_observation = "good"

    # Score calculation logic
    base_score = 50.0

    # A. Positive Signal Rewards
    # Quality score rewards
    if quality_score is not None:
        if quality_score >= 80.0:
            base_score += 10.0
            reason_codes.append("high_quality_score")
        elif quality_score >= 70.0:
            base_score += 5.0
            reason_codes.append("good_quality_score")
    else:
        missing_warnings.append("missing_quality_score")

    # Confidence rewards
    if confidence is not None:
        if confidence >= 0.75:
            base_score += 12.0
            reason_codes.append("strong_confidence")
        elif confidence >= 0.70:
            base_score += 8.0
            reason_codes.append("good_confidence")
    else:
        missing_warnings.append("missing_confidence")

    # Edge rewards
    edge_to_check = edge or edge_pct
    if edge_to_check is not None:
        if edge_to_check >= 5.0 or edge_to_check >= 0.05:
            base_score += 8.0
            reason_codes.append("strong_edge")
        elif edge_to_check >= 2.0 or edge_to_check >= 0.02:
            base_score += 4.0
            reason_codes.append("positive_edge")
    else:
        missing_warnings.append("missing_edge")

    # Context alignment
    if context_pick_alignment == "aligned":
        base_score += 8.0
        reason_codes.append("context_aligned")
    elif not context_pick_alignment:
        missing_warnings.append("missing_context_alignment")

    # Caution rewards
    if context_caution_level in {"low", ""}:
        base_score += 6.0
        reason_codes.append("low_caution")
    elif context_caution_level == "medium":
        base_score += 3.0
        reason_codes.append("medium_caution")
    elif not context_caution_level:
        missing_warnings.append("missing_caution_level")

    # Role stability rewards
    if role_stability_bucket == "stable":
        base_score += 10.0
        reason_codes.append("role_stable")
    elif role_stability_bucket == "mostly_stable":
        base_score += 5.0
        reason_codes.append("role_mostly_stable")

    # Fragility/Survivability rewards
    if fragility_bucket == "LOW" or (fragility_score is not None and fragility_score < 34.0):
        base_score += 8.0
        reason_codes.append("low_fragility")
    elif not fragility_bucket and fragility_score is None:
        missing_warnings.append("missing_fragility_data")

    if survivability_bucket == "HIGH" or (survivability_score is not None and survivability_score >= 67.0):
        base_score += 8.0
        reason_codes.append("high_survivability")
    elif not survivability_bucket and survivability_score is None:
        missing_warnings.append("missing_survivability_data")

    # Calibration rewards
    if calibration_observation == "good":
        base_score += 5.0
        reason_codes.append("good_calibration")

    # B. Negative Signal Penalties
    # Caution penalties
    if context_caution_level == "extreme":
        base_score -= 25.0
        reason_codes.append("extreme_caution")
    elif context_caution_level == "high":
        base_score -= 15.0
        reason_codes.append("high_caution")

    # Alignment penalties
    if context_pick_alignment == "conflicted":
        base_score -= 10.0
        reason_codes.append("context_conflicted")

    # Role volatility penalties
    if role_stability_bucket == "highly_volatile":
        base_score -= 20.0
        reason_codes.append("role_highly_volatile")
    elif role_stability_bucket == "volatile":
        base_score -= 12.0
        reason_codes.append("role_volatile")
    elif role_stability_bucket == "unknown":
        base_score -= 8.0
        reason_codes.append("role_stability_unknown")

    # Fragility penalties
    if fragility_bucket == "HIGH" or (fragility_score is not None and fragility_score >= 67.0):
        base_score -= 12.0
        reason_codes.append("high_fragility")

    # Same opponent rematch warning penalty
    if same_opponent_under_warning:
        base_score -= 10.0
        reason_codes.append("same_opponent_warning")

    # Realism dampened penalty
    if player_points_realism_dampened:
        base_score -= 15.0
        reason_codes.append("realism_dampened")

    # Elite points risk guard blocked penalty
    if blocked_by_elite_points_risk_guard:
        base_score -= 15.0
        reason_codes.append("elite_points_risk_guard_blocked")

    # Calibration penalties
    if calibration_observation == "poor":
        base_score -= 10.0
        reason_codes.append("poor_calibration_observation")

    # Missing coverage penalty
    if missing_warnings:
        base_score -= 5.0
        reason_codes.append("missing_coverage")

    # Final score formatting
    meta_label_rules_score = min(100.0, max(0.0, base_score))

    # Bucket mapping
    if meta_label_rules_score >= 80.0:
        meta_label_bucket = "shadow_strong_review_candidate"
        meta_label_status = "review_candidate"
    elif meta_label_rules_score >= 65.0:
        meta_label_bucket = "shadow_watch_candidate"
        meta_label_status = "watch_only"
    elif meta_label_rules_score >= 50.0:
        meta_label_bucket = "shadow_neutral"
        meta_label_status = "neutral"
    elif meta_label_rules_score >= 35.0:
        meta_label_bucket = "shadow_weak"
        meta_label_status = "weak_signal"
    else:
        meta_label_bucket = "shadow_avoid_review"
        meta_label_status = "avoid_review"

    return {
        "prediction_date": prediction_date,
        "player_id": player_id,
        "player_name": player_name,
        "game_id": game_id,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "odds": odds,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "role_stability_bucket": role_stability_bucket,
        "context_pick_alignment": context_pick_alignment,
        "context_caution_level": context_caution_level,
        "meta_label_rules_score": round(meta_label_rules_score, 1),
        "meta_label_bucket": meta_label_bucket,
        "meta_label_status": meta_label_status,
        "reason_codes": reason_codes,
        "missing_feature_warnings": missing_warnings,
        "features_version": FEATURES_VERSION,
        "model_version": MODEL_VERSION,
        "diagnostic_only_note": DIAGNOSTIC_ONLY_NOTE,
    }


def apply_meta_label_promotion(
    candidates: pd.DataFrame,
    role_payload: dict[str, Any] | None = None,
    cal_payload: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Enrich candidates DataFrame with meta-label rules score, bucket, status, reason codes and missing warnings."""
    if candidates.empty:
        # Return a copy of candidates with new empty columns of object type
        out = candidates.copy()
        new_cols = [
            "meta_label_rules_score",
            "meta_label_bucket",
            "meta_label_status",
            "reason_codes",
            "missing_feature_warnings",
            "features_version",
            "model_version",
            "diagnostic_only_note",
        ]
        for col in new_cols:
            out[col] = pd.Series(dtype="object")
        return out

    enriched_rows = []
    for _, row in candidates.iterrows():
        enriched_rows.append(
            calculate_meta_label_rules_score_row(
                row, role_payload=role_payload, cal_payload=cal_payload
            )
        )

    enriched_df = pd.DataFrame(enriched_rows)

    # Merge candidates with enriched columns to preserve any existing columns
    result = candidates.copy()
    for col in enriched_df.columns:
        result[col] = enriched_df[col].values

    return result
