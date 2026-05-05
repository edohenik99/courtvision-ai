"""Core injury impact engine.

This module extracts injury context building and application logic from courtvision_ai.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import pandas as pd

from courtvision.calibration.buckets import to_float


@dataclass(frozen=True, slots=True)
class InjuryContextConfig:
    """Configuration for injury context evaluation."""

    # Status weights for injury impact calculation
    out_weight: float = 1.0
    doubtful_weight: float = 0.75
    questionable_weight: float = 0.35
    probable_weight: float = 0.15
    day_to_day_weight: float = 0.35

    # Impact calculation thresholds
    impact_score_cap: float = 1.0
    usage_boost_cap: float = 0.18
    rebound_boost_cap: float = 0.10
    defensive_boost_cap: float = 0.08
    offense_penalty_cap: float = 0.18
    defense_penalty_cap: float = 0.12
    rim_penalty_cap: float = 0.08

    # Projection adjustment caps
    player_injury_boost_cap: float = 0.25
    max_confidence: float = 0.98
    min_confidence: float = 0.25

    # Player points uplift dampening
    max_confidence_injury_uplift: float = 0.035


def injury_status_weight(status: Any, config: InjuryContextConfig | None = None) -> float:
    """Compute injury impact weight based on status.

    Maps injury status strings to impact weights.
    """
    cfg = config or InjuryContextConfig()
    status_key = str(status or "").strip().lower()

    weights = {
        "out": cfg.out_weight,
        "inactive": cfg.out_weight,
        "doubtful": cfg.doubtful_weight,
        "questionable": cfg.questionable_weight,
        "probable": cfg.probable_weight,
        "day to day": cfg.day_to_day_weight,
        "day-to-day": cfg.day_to_day_weight,
    }
    return float(weights.get(status_key, 0.25 if status_key else 0.0))


def build_injury_context(
    injuries: pd.DataFrame,
    player_baselines: pd.DataFrame,
    active_teams: set[str],
    config: InjuryContextConfig | None = None,
) -> dict[str, Any]:
    """Build comprehensive injury context for teams and players.

    Returns dict with:
    - players: dict of player_key -> player injury details
    - teams: dict of team_abbr -> team injury impact summary
    - active_injuries: DataFrame of active injury records
    """
    cfg = config or InjuryContextConfig()
    context: dict[str, Any] = {
        "players": {},
        "teams": {},
        "active_injuries": pd.DataFrame(),
        "metadata": {
            "rows_team_enriched": 0,
            "rows_missing_team_identity": 0,
            "rows_active_team_matched": 0,
        },
    }

    if injuries.empty or player_baselines.empty:
        return context

    def _clean_text(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    def _coerce_positive_int(value: Any) -> int | None:
        try:
            if value is None or pd.isna(value):
                return None
            numeric = int(float(value))
        except (TypeError, ValueError):
            return None
        return numeric if numeric > 0 else None

    active_team_set = {str(team).strip().upper() for team in active_teams if str(team).strip()}

    # Build baseline lookups. Prefer player_id for live injury rows; keep name fallback for
    # older fixtures and providers that only expose names.
    player_cols = [
        c for c in ["player_name", "team_abbr", "player_id", "team_id",
                   "pts_avg", "reb_avg", "ast_avg", "stl_avg", "blk_avg", "min_avg"]
        if c in player_baselines.columns
    ]
    baseline_lookup = player_baselines[player_cols].copy()
    for col in ["player_name", "team_abbr", "player_id", "team_id"]:
        if col not in baseline_lookup.columns:
            baseline_lookup[col] = pd.NA
    baseline_lookup["_player_id_key"] = baseline_lookup["player_id"].map(_coerce_positive_int)
    baseline_lookup["player_name_key"] = (
        baseline_lookup.get("player_name", pd.Series("", index=baseline_lookup.index))
        .fillna("").astype(str).str.strip().str.lower()
    )

    baseline_by_id: dict[int, dict[str, Any]] = {}
    baseline_by_name: dict[str, dict[str, Any]] = {}
    for _, baseline_row in baseline_lookup.iterrows():
        row_dict = dict(baseline_row)
        baseline_id = _coerce_positive_int(row_dict.get("_player_id_key"))
        if baseline_id is not None and baseline_id not in baseline_by_id:
            baseline_by_id[baseline_id] = row_dict
        name_key = _clean_text(row_dict.get("player_name_key"))
        if name_key and name_key not in baseline_by_name:
            baseline_by_name[name_key] = row_dict

    enriched_rows: list[dict[str, Any]] = []
    for _, injury_row in injuries.iterrows():
        row_dict = dict(injury_row)
        status = _clean_text(row_dict.get("status"))
        player_id = _coerce_positive_int(row_dict.get("player_id") or row_dict.get("player.id"))
        player_name = _clean_text(row_dict.get("player_name"))
        if not player_name:
            player_name = f"{_clean_text(row_dict.get('first_name'))} {_clean_text(row_dict.get('last_name'))}".strip()
        if not status or (player_id is None and not player_name):
            continue

        name_key = player_name.lower()
        baseline = baseline_by_id.get(player_id) if player_id is not None else None
        if baseline is None and name_key:
            baseline = baseline_by_name.get(name_key)
        baseline = baseline or {}

        team_id = _coerce_positive_int(row_dict.get("team_id") or row_dict.get("team.id") or row_dict.get("player.team_id"))
        team_abbr = _clean_text(row_dict.get("team_abbr") or row_dict.get("team.abbreviation")).upper()
        was_missing_team_id = team_id is None
        was_missing_team_abbr = not team_abbr
        was_missing_team = was_missing_team_id and was_missing_team_abbr

        if player_id is None:
            player_id = _coerce_positive_int(baseline.get("player_id"))
        if not player_name:
            player_name = _clean_text(baseline.get("player_name"))
        if team_id is None:
            team_id = _coerce_positive_int(baseline.get("team_id"))
        if not team_abbr:
            team_abbr = _clean_text(baseline.get("team_abbr")).upper()

        team_enriched = bool(
            (was_missing_team_id and team_id is not None)
            or (was_missing_team_abbr and bool(team_abbr))
        )
        row_dict["player_id"] = player_id
        row_dict["player_name"] = player_name
        row_dict["team_id"] = team_id
        row_dict["team_abbr"] = team_abbr
        row_dict["status"] = status
        row_dict["injury_original_missing_team_identity"] = bool(was_missing_team)
        row_dict["injury_team_enriched"] = bool(team_enriched)
        if team_enriched:
            row_dict["injury_normalized"] = True
            row_dict["injury_rejection_reason"] = ""
            row_dict["injury_enrichment_reason"] = "team_identity_enriched_from_player_baseline"
        elif team_id is None and not team_abbr:
            row_dict["injury_normalized"] = False
            row_dict["injury_rejection_reason"] = row_dict.get("injury_rejection_reason") or "missing_team_identity"
            row_dict["injury_enrichment_reason"] = "team_identity_unresolved"

        for stat_col in ["pts_avg", "reb_avg", "ast_avg", "stl_avg", "blk_avg", "min_avg"]:
            if stat_col not in row_dict or pd.isna(row_dict.get(stat_col)):
                row_dict[stat_col] = baseline.get(stat_col, 0.0)
        enriched_rows.append(row_dict)

    enriched = pd.DataFrame(enriched_rows)
    if enriched.empty:
        return context

    # Compute injury weights
    enriched["injury_weight"] = enriched["status"].map(
        lambda s: injury_status_weight(s, cfg)
    ).fillna(0.0)

    # Convert stat columns to numeric
    for col in ["pts_avg", "reb_avg", "ast_avg", "stl_avg", "blk_avg", "min_avg"]:
        if col in enriched.columns:
            enriched[col] = pd.to_numeric(enriched[col], errors="coerce").fillna(0.0)
        else:
            enriched[col] = 0.0

    # Compute weighted stats
    enriched["weighted_pts"] = enriched["pts_avg"] * enriched["injury_weight"]
    enriched["weighted_reb"] = enriched["reb_avg"] * enriched["injury_weight"]
    enriched["weighted_ast"] = enriched["ast_avg"] * enriched["injury_weight"]
    enriched["weighted_stocks"] = (enriched["stl_avg"] + enriched["blk_avg"]) * enriched["injury_weight"]
    enriched["weighted_minutes"] = enriched["min_avg"] * enriched["injury_weight"]

    active_details = []
    for _, row in enriched.iterrows():
        row_dict = dict(row)
        player_id = _coerce_positive_int(row_dict.get("player_id"))
        player_name = _clean_text(row_dict.get("player_name"))
        team_abbr = str(row_dict.get("team_abbr") or "").strip().upper()
        if player_id is None and not player_name:
            continue

        availability_multiplier = max(0.0, 1.0 - float(row_dict.get("injury_weight") or 0.0))
        row_dict["availability_multiplier"] = availability_multiplier
        if player_id is not None:
            context["players"][f"player_id:{player_id}"] = row_dict
        if player_name and team_abbr:
            context["players"][f"{player_name}:{team_abbr}"] = row_dict
        active_details.append(row_dict)

    context["active_injuries"] = pd.DataFrame(active_details)
    if context["active_injuries"].empty:
        return context

    context["metadata"]["rows_team_enriched"] = int(
        context["active_injuries"].get("injury_team_enriched", pd.Series(False, index=context["active_injuries"].index))
        .fillna(False)
        .astype(bool)
        .sum()
    )
    context["metadata"]["rows_missing_team_identity"] = int(
        (
            context["active_injuries"].get("team_abbr", pd.Series("", index=context["active_injuries"].index))
            .fillna("")
            .astype(str)
            .str.strip()
            .eq("")
        ).sum()
    )

    team_context_rows = context["active_injuries"].copy()
    team_context_rows["team_abbr"] = team_context_rows.get("team_abbr", pd.Series("", index=team_context_rows.index)).fillna("").astype(str).str.upper()
    team_context_rows = team_context_rows[team_context_rows["team_abbr"].str.len() > 0].copy()
    if active_team_set:
        team_context_rows = team_context_rows[team_context_rows["team_abbr"].isin(active_team_set)].copy()
    context["metadata"]["rows_active_team_matched"] = int(len(team_context_rows))

    # Build team context
    for team_abbr, grp in team_context_rows.groupby("team_abbr"):
        weighted_pts = float(
            pd.to_numeric(grp.get("weighted_pts", pd.Series(dtype=float)), errors="coerce")
            .fillna(0.0).sum()
        )
        weighted_reb = float(
            pd.to_numeric(grp.get("weighted_reb", pd.Series(dtype=float)), errors="coerce")
            .fillna(0.0).sum()
        )
        weighted_ast = float(
            pd.to_numeric(grp.get("weighted_ast", pd.Series(dtype=float)), errors="coerce")
            .fillna(0.0).sum()
        )
        weighted_stocks = float(
            pd.to_numeric(grp.get("weighted_stocks", pd.Series(dtype=float)), errors="coerce")
            .fillna(0.0).sum()
        )
        weighted_minutes = float(
            pd.to_numeric(grp.get("weighted_minutes", pd.Series(dtype=float)), errors="coerce")
            .fillna(0.0).sum()
        )

        impact_score = min(
            cfg.impact_score_cap,
            (weighted_pts / 35.0) + (weighted_minutes / 120.0) + (weighted_stocks / 6.0),
        )

        context["teams"][str(team_abbr)] = {
            "weighted_pts": weighted_pts,
            "weighted_reb": weighted_reb,
            "weighted_ast": weighted_ast,
            "weighted_stocks": weighted_stocks,
            "weighted_minutes": weighted_minutes,
            "impact_score": round(float(impact_score), 4),
            "usage_boost": round(
                min(cfg.usage_boost_cap, (weighted_pts / 90.0) + (weighted_ast / 60.0) + (weighted_minutes / 500.0)),
                4,
            ),
            "rebound_boost": round(min(cfg.rebound_boost_cap, weighted_reb / 70.0), 4),
            "defensive_event_boost": round(min(cfg.defensive_boost_cap, weighted_stocks / 25.0), 4),
            "offense_penalty": round(
                min(cfg.offense_penalty_cap, (weighted_pts / 110.0) + (weighted_ast / 90.0)),
                4,
            ),
            "defense_penalty": round(min(cfg.defense_penalty_cap, weighted_stocks / 30.0), 4),
            "rim_penalty": round(min(cfg.rim_penalty_cap, weighted_stocks / 40.0), 4),
            "affected_players": int(len(grp)),
            "status_mix": ", ".join(sorted({
                str(s).strip()
                for s in grp.get("status", pd.Series(dtype=str)).astype(str).tolist()
                if str(s).strip()
            })),
        }

    return context


def apply_player_injury_context(
    player_row: Mapping[str, Any],
    team_abbr: str,
    opp_abbr: str,
    market_type: str,
    projection: float,
    confidence: float,
    injury_context: Mapping[str, Any] | None,
    config: InjuryContextConfig | None = None,
) -> tuple[float, float, dict[str, Any]]:
    """Apply injury context to adjust player projection and confidence.

    Returns (adjusted_projection, adjusted_confidence, metadata_dict)
    """
    cfg = config or InjuryContextConfig()

    if not injury_context:
        return projection, confidence, _empty_injury_result(projection, confidence)

    players_ctx = injury_context.get("players", {}) if isinstance(injury_context, Mapping) else {}
    teams_ctx = injury_context.get("teams", {}) if isinstance(injury_context, Mapping) else {}

    player_name = str(player_row.get("player_name", "")).strip()
    player_key = f"{player_name}:{str(team_abbr).upper()}"

    own_injury: Mapping[str, Any] = {}
    if isinstance(players_ctx, Mapping):
        player_id = to_float(player_row.get("player_id"))
        if player_id is not None and player_id > 0:
            own_injury = players_ctx.get(f"player_id:{int(player_id)}", {}) or {}
        if not own_injury:
            own_injury = players_ctx.get(player_key, {}) or {}
    team_ctx = teams_ctx.get(str(team_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}
    opp_ctx = teams_ctx.get(str(opp_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}

    notes: list[str] = []
    injury_status = str(own_injury.get("status", "")).strip()
    own_impact = float(own_injury.get("injury_weight") or 0.0)
    team_impact = float(team_ctx.get("impact_score") or 0.0)
    opp_impact = float(opp_ctx.get("impact_score") or 0.0)

    adjusted_projection = float(projection)
    adjusted_confidence = float(confidence)
    baseline_projection = float(projection)
    baseline_confidence = float(confidence)

    # Import here to avoid circular dependency
    from courtvision.injuries.volatility import compute_injury_volatility

    volatility_result = compute_injury_volatility(player_row, baseline_projection)
    points_recent_form_ratio = volatility_result["recent_form_ratio"]
    points_independent_support = volatility_result["independent_support"]

    confidence_uplift_dampened = False
    confidence_uplift_reason = ""

    # Apply own injury availability reduction
    if injury_status:
        availability = max(0.0, float(own_injury.get("availability_multiplier") or (1.0 - own_impact)))
        adjusted_projection *= availability
        adjusted_confidence *= max(0.2, availability)
        notes.append(f"player_status:{injury_status}")

    # Compute role factor based on minutes
    minutes_avg = to_float(player_row.get("min_avg")) or 0.0
    role_factor = min(max((minutes_avg - 18.0) / 18.0, 0.0), 1.0)

    # Apply teammate absence boosts (if no personal injury)
    if not injury_status:
        team_usage_boost = float(team_ctx.get("usage_boost") or 0.0)
        if market_type in {"player_points", "player_assists", "player_3pt_made"}:
            adjusted_projection *= 1.0 + (team_usage_boost * (0.65 + role_factor * 0.35))
        elif market_type == "player_rebounds":
            adjusted_projection *= 1.0 + float(team_ctx.get("rebound_boost") or 0.0) * (0.60 + role_factor * 0.40)
        elif market_type in {"player_steals", "player_blocks"}:
            adjusted_projection *= 1.0 + float(team_ctx.get("defensive_event_boost") or 0.0)
        adjusted_confidence *= 1.0 + min(team_impact * 0.06, 0.08)
        if team_impact > 0:
            notes.append(f"teammate_absences:{team_impact:.2f}")

    # Apply opponent absence adjustments
    if opp_impact > 0:
        if market_type in {"player_points", "player_assists", "player_3pt_made"}:
            adjusted_projection *= 1.0 + float(opp_ctx.get("defense_penalty") or 0.0) * 0.60
        elif market_type == "player_rebounds":
            adjusted_projection *= 1.0 + float(opp_ctx.get("rebound_boost") or 0.0) * 0.40
        elif market_type == "player_blocks":
            adjusted_projection *= 1.0 + float(opp_ctx.get("rim_penalty") or 0.0)
        adjusted_confidence *= 1.0 + min(opp_impact * 0.04, 0.05)
        notes.append(f"opponent_absences:{opp_impact:.2f}")

    # Cap injury boost for non-injured players
    if not injury_status and market_type.startswith("player_"):
        capped_projection = min(adjusted_projection, float(projection) * (1.0 + cfg.player_injury_boost_cap))
        if capped_projection < adjusted_projection:
            adjusted_projection = capped_projection
            notes.append("injury_boost_capped")

    # Player points specific confidence uplift dampening
    if market_type == "player_points" and not injury_status:
        projection_delta = adjusted_projection - baseline_projection
        confidence_delta = adjusted_confidence - baseline_confidence
        injury_strength = max(team_impact, opp_impact)

        if (
            projection_delta > max(0.75, baseline_projection * 0.04)
            and confidence_delta > 0.0
            and injury_strength >= 0.15
        ):
            max_allowed_confidence_delta = cfg.max_confidence_injury_uplift
            max_allowed_confidence_delta = min(max_allowed_confidence_delta, 0.018 + points_independent_support)

            if points_recent_form_ratio < 0.95:
                max_allowed_confidence_delta = min(
                    max_allowed_confidence_delta,
                    0.015 + (points_independent_support * 0.35),
                )
            if injury_strength >= 0.30:
                max_allowed_confidence_delta = min(
                    max_allowed_confidence_delta,
                    0.02 + (points_independent_support * 0.30),
                )

            capped_confidence = min(adjusted_confidence, baseline_confidence + max_allowed_confidence_delta)
            if capped_confidence < adjusted_confidence:
                adjusted_confidence = capped_confidence
                confidence_uplift_dampened = True
                confidence_uplift_reason = "projection_injury_uplift_already_applied"
                notes.append("points_confidence_uplift_dampened")

    adjusted_confidence = min(max(adjusted_confidence, cfg.min_confidence), cfg.max_confidence)
    projection_delta = adjusted_projection - baseline_projection
    confidence_delta = adjusted_confidence - baseline_confidence

    return adjusted_projection, adjusted_confidence, {
        "injury_status": injury_status,
        "injury_impact_score": round(float(max(own_impact, team_impact, opp_impact)), 4),
        "team_injury_impact": round(float(team_impact), 4),
        "opponent_injury_impact": round(float(opp_impact), 4),
        "injury_notes": "; ".join(notes),
        "injury_baseline_projection": round(float(baseline_projection), 4),
        "injury_adjusted_projection": round(float(adjusted_projection), 4),
        "injury_projection_delta": round(float(projection_delta), 4),
        "injury_baseline_confidence": round(float(baseline_confidence), 4),
        "injury_adjusted_confidence": round(float(adjusted_confidence), 4),
        "injury_confidence_delta": round(float(confidence_delta), 4),
        "player_points_recent_form_ratio": round(float(points_recent_form_ratio), 4),
        "player_points_injury_independent_support": round(float(points_independent_support), 4),
        "player_points_confidence_uplift_dampened": bool(confidence_uplift_dampened),
        "player_points_confidence_uplift_reason": confidence_uplift_reason,
    }


def _empty_injury_result(projection: float, confidence: float) -> dict[str, Any]:
    """Return empty/default injury result when no context available."""
    return {
        "injury_status": "",
        "injury_impact_score": 0.0,
        "team_injury_impact": 0.0,
        "opponent_injury_impact": 0.0,
        "injury_notes": "",
        "injury_baseline_projection": round(float(projection), 4),
        "injury_adjusted_projection": round(float(projection), 4),
        "injury_projection_delta": 0.0,
        "injury_baseline_confidence": round(float(confidence), 4),
        "injury_adjusted_confidence": round(float(confidence), 4),
        "injury_confidence_delta": 0.0,
        "player_points_recent_form_ratio": 1.0,
        "player_points_injury_independent_support": 0.0,
        "player_points_confidence_uplift_dampened": False,
        "player_points_confidence_uplift_reason": "",
    }


class InjuryEngine:
    """Engine for comprehensive injury impact evaluation."""

    def __init__(self, config: InjuryContextConfig | None = None) -> None:
        self.config = config or InjuryContextConfig()

    def build_context(
        self,
        injuries: pd.DataFrame,
        player_baselines: pd.DataFrame,
        active_teams: set[str],
    ) -> dict[str, Any]:
        """Build injury context for teams and players."""
        return build_injury_context(injuries, player_baselines, active_teams, self.config)

    def apply_context(
        self,
        player_row: Mapping[str, Any],
        team_abbr: str,
        opp_abbr: str,
        market_type: str,
        projection: float,
        confidence: float,
        injury_context: Mapping[str, Any] | None,
    ) -> tuple[float, float, dict[str, Any]]:
        """Apply injury context to player projection and confidence."""
        return apply_player_injury_context(
            player_row, team_abbr, opp_abbr, market_type, projection, confidence, injury_context, self.config
        )

    def status_weight(self, status: Any) -> float:
        """Get injury status weight."""
        return injury_status_weight(status, self.config)
