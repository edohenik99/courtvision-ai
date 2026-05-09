from __future__ import annotations

import json
from typing import Any

import pandas as pd

FRAGILITY_COLUMNS = [
    "fragility_score",
    "fragility_bucket",
    "fragility_reasons",
    "fragility_component_json",
    "survivability_score",
    "survivability_bucket",
    "survivability_reasons",
    "survivability_component_json",
]


def _txt(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"", "nan", "none", "null"} else s


def _num(v: Any) -> float | None:
    t = _txt(v)
    if not t:
        return None
    try:
        return float(t)
    except Exception:
        return None


def _truthy(v: Any) -> bool:
    return _txt(v).lower() in {"1", "true", "yes", "y"}


def _bucket(score: float) -> str:
    if score >= 67:
        return "HIGH"
    if score >= 34:
        return "MEDIUM"
    return "LOW"


def add_fragility_survivability_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    if out.empty:
        for c in FRAGILITY_COLUMNS:
            if c not in out.columns:
                out[c] = pd.Series(dtype="object")
        return out

    f_scores: list[float] = []
    f_buckets: list[str] = []
    f_reasons: list[str] = []
    f_components: list[str] = []
    s_scores: list[float] = []
    s_buckets: list[str] = []
    s_reasons: list[str] = []
    s_components: list[str] = []

    for _, row in out.iterrows():
        market = _txt(row.get("market_type")).lower()
        selection = _txt(row.get("selection")).lower()
        confidence = _num(row.get("confidence")) or 0.0
        quality = _num(row.get("quality_score")) or 0.0
        side_edge = _num(row.get("side_edge"))
        minutes = _num(row.get("minutes_avg"))
        blowout = _num(row.get("blowout_risk")) or 0.0
        competitiveness = _num(row.get("expected_competitiveness")) or 0.0
        trust = _num(row.get("market_trust_weight"))
        caution = _txt(row.get("context_caution_level")).lower()
        alignment = _txt(row.get("context_pick_alignment")).lower()
        profile = _txt(row.get("player_profile_bucket")).lower()
        line_band = _txt(row.get("player_points_line_band")).lower()
        defense = _txt(row.get("defense_context_signal")).lower()
        injury_impact = abs(_num(row.get("injury_impact_score")) or 0.0)
        injury_conf_delta = _num(row.get("injury_confidence_delta")) or 0.0

        frag = 20.0
        surv = 40.0
        fr: list[str] = []
        sr: list[str] = []
        fc: dict[str, float] = {}
        sc: dict[str, float] = {}

        if market == "player_points" and selection == "over":
            frag += 12; surv -= 8; fr.append("player_points_over"); fc["player_points_over"] = 12
        if market == "player_points" and selection == "over" and line_band in {"low", "very_low", "low_line"}:
            frag += 10; surv -= 6; fr.append("low_line_points_over"); fc["low_line_points_over"] = 10
        if "role" in profile or "rookie" in profile:
            frag += 8; surv -= 5; fr.append("profile_uncertainty"); fc["profile_uncertainty"] = 8
        if minutes is not None and minutes < 24:
            frag += 8; surv -= 6; fr.append("low_minutes"); fc["low_minutes"] = 8
        if confidence < 0.55:
            frag += 10; surv -= 8; fr.append("low_confidence"); fc["low_confidence"] = 10
        if injury_impact >= 0.5:
            frag += 8; surv -= 6; fr.append("injury_impact") ; fc["injury_impact"] = 8
        if injury_conf_delta < -0.05:
            frag += 7; surv -= 5; fr.append("injury_confidence_drop"); fc["injury_confidence_drop"] = 7
        if caution == "high":
            frag += 12; surv -= 10; fr.append("high_context_caution"); fc["high_context_caution"] = 12
        elif caution == "medium":
            frag += 6; surv -= 3; fr.append("medium_context_caution"); fc["medium_context_caution"] = 6
        if alignment == "conflicted":
            frag += 8; surv -= 6; fr.append("context_conflicted"); fc["context_conflicted"] = 8
        if "defense" in defense and "resist" in defense:
            frag += 6; surv -= 4; fr.append("defensive_resistance"); fc["defensive_resistance"] = 6
        if blowout >= 0.6:
            frag += 6; surv -= 4; fr.append("blowout_risk"); fc["blowout_risk"] = 6
        if _truthy(row.get("same_opponent_under_warning")):
            frag += 8; surv -= 5; fr.append("same_opponent_warning"); fc["same_opponent_warning"] = 8
        for col, reason in [
            ("blocked_by_elite_points_risk_guard", "elite_points_risk_guard"),
            ("blocked_by_player_points_strong_over_calibration", "strong_over_calibration"),
            ("player_points_realism_dampened", "realism_dampened"),
        ]:
            if _truthy(row.get(col)):
                frag += 9; surv -= 6; fr.append(reason); fc[reason] = 9
        if trust is not None and trust < 0.8:
            frag += 8; surv -= 6; fr.append("low_market_trust"); fc["low_market_trust"] = 8

        # survivability boosters
        if confidence >= 0.7:
            surv += 12; sc["high_confidence"] = 12; sr.append("high_confidence")
        if quality >= 70:
            surv += 10; sc["high_quality"] = 10; sr.append("high_quality")
        if side_edge is not None and side_edge > 0:
            surv += 8; sc["positive_side_edge"] = 8; sr.append("positive_side_edge")
        if minutes is not None and minutes >= 30:
            surv += 6; sc["stable_minutes"] = 6; sr.append("stable_minutes")
        if alignment == "aligned":
            surv += 8; sc["context_aligned"] = 8; sr.append("context_aligned")
        if caution in {"low", ""}:
            surv += 6; sc["low_context_caution"] = 6; sr.append("low_context_caution")
        if injury_impact < 0.25:
            surv += 6; sc["low_injury_impact"] = 6; sr.append("low_injury_impact")
        if trust is not None and trust >= 1.0:
            surv += 6; sc["high_market_trust"] = 6; sr.append("high_market_trust")
        if market in {"player_rebounds", "player_assists"} or selection == "under":
            surv += 5; sc["stable_market_profile"] = 5; sr.append("stable_market_profile")
        if competitiveness >= 0.6 and blowout < 0.4:
            surv += 5; sc["competitive_game"] = 5; sr.append("competitive_game")

        frag = min(100.0, max(0.0, frag))
        surv = min(100.0, max(0.0, surv))
        f_scores.append(round(frag, 2)); s_scores.append(round(surv, 2))
        f_buckets.append(_bucket(frag)); s_buckets.append(_bucket(surv))
        f_reasons.append("; ".join(fr)); s_reasons.append("; ".join(sr))
        f_components.append(json.dumps(fc, sort_keys=True)); s_components.append(json.dumps(sc, sort_keys=True))

    out["fragility_score"] = f_scores
    out["fragility_bucket"] = f_buckets
    out["fragility_reasons"] = f_reasons
    out["fragility_component_json"] = f_components
    out["survivability_score"] = s_scores
    out["survivability_bucket"] = s_buckets
    out["survivability_reasons"] = s_reasons
    out["survivability_component_json"] = s_components
    return out
