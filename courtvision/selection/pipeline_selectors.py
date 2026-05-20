from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from courtvision.market import normalize_market_alias
from courtvision.reason_codes import REJECT_NEGATIVE_EDGE_DIRECTION

ELITE_MARKET_POLICY_REJECTION_REASON = "market_filtered_by_elite_policy"


def _normalize_elite_game_key(row: Any) -> str:
    """Return the current elite exposure game key for a candidate row."""
    if hasattr(row, "get"):
        raw_game_id = row.get("game_id")
        team = str(row.get("team_abbr", row.get("team", ""))).strip().upper()
        opponent = str(row.get("opponent", "")).strip().upper()
    else:
        raw_game_id = row.game_id if hasattr(row, "game_id") else None
        team = str(getattr(row, "team_abbr", getattr(row, "team", ""))).strip().upper()
        opponent = str(getattr(row, "opponent", "")).strip().upper()

    if raw_game_id is not None and str(raw_game_id).strip():
        try:
            return str(int(float(str(raw_game_id))))
        except (ValueError, TypeError):
            pass

    if team and opponent:
        teams_sorted = sorted([team, opponent])
        return f"{teams_sorted[0]}@{teams_sorted[1]}"

    if team:
        return team

    return "unknown"


@dataclass(frozen=True, slots=True)
class EliteExposureCapDecision:
    index: Any
    player: Any
    team: str
    game_key: str
    current_team_count: int
    current_game_count: int
    team_would_skip: Any
    game_would_skip: bool
    action: str
    selected_team_count: int | None = None
    selected_game_count: int | None = None


@dataclass(frozen=True, slots=True)
class EliteExposureCapResult:
    capped_df: pd.DataFrame
    annotated_df: pd.DataFrame
    team_counts: dict[str, int] = field(default_factory=dict)
    game_counts: dict[str, int] = field(default_factory=dict)
    skipped_by_team_cap: int = 0
    skipped_by_game_cap: int = 0
    sample_game_keys: list[str] = field(default_factory=list)
    first_10_game_keys: list[str] = field(default_factory=list)
    row_decisions: list[EliteExposureCapDecision] = field(default_factory=list)


def apply_elite_exposure_caps(
    admitted_df: pd.DataFrame,
    *,
    elite_team_cap: int,
    elite_game_cap: int,
) -> EliteExposureCapResult:
    """Apply the current elite team/game exposure caps without changing order."""
    working_df = admitted_df.copy()
    capped_selection = []
    team_counts: dict[str, int] = {}
    game_counts: dict[str, int] = {}
    skipped_by_team_cap = 0
    skipped_by_game_cap = 0
    row_decisions: list[EliteExposureCapDecision] = []

    sample_game_keys = [
        _normalize_elite_game_key(row)
        for _, row in working_df.head(10).iterrows()
    ]

    for idx, row in working_df.iterrows():
        team = str(row.get("team_abbr", row.get("team", ""))).strip().upper()
        game_key = _normalize_elite_game_key(row)
        player = row.get("player_name", "unknown")

        current_game_count = game_counts.get(game_key, 0)
        current_team_count = team_counts.get(team, 0)
        game_cap_check = game_key != "unknown" and current_game_count >= elite_game_cap
        team_cap_check = team and current_team_count >= elite_team_cap

        if team_cap_check:
            if "selection_rejection_reason" in working_df.columns:
                working_df.at[idx, "selection_rejection_reason"] = "reject_team_exposure_cap"
            working_df.at[idx, "team_exposure_count_at_decision"] = current_team_count
            skipped_by_team_cap += 1
            row_decisions.append(
                EliteExposureCapDecision(
                    index=idx,
                    player=player,
                    team=team,
                    game_key=game_key,
                    current_team_count=current_team_count,
                    current_game_count=current_game_count,
                    team_would_skip=team_cap_check,
                    game_would_skip=game_cap_check,
                    action="team_cap",
                )
            )
            continue

        if game_cap_check:
            if "selection_rejection_reason" in working_df.columns:
                working_df.at[idx, "selection_rejection_reason"] = "reject_game_exposure_cap"
            working_df.at[idx, "game_exposure_count_at_decision"] = current_game_count
            skipped_by_game_cap += 1
            row_decisions.append(
                EliteExposureCapDecision(
                    index=idx,
                    player=player,
                    team=team,
                    game_key=game_key,
                    current_team_count=current_team_count,
                    current_game_count=current_game_count,
                    team_would_skip=team_cap_check,
                    game_would_skip=game_cap_check,
                    action="game_cap",
                )
            )
            continue

        capped_selection.append(idx)
        if team:
            team_counts[team] = team_counts.get(team, 0) + 1
        if game_key != "unknown":
            game_counts[game_key] = game_counts.get(game_key, 0) + 1

        selected_team_count = team_counts.get(team, 0)
        selected_game_count = game_counts.get(game_key, 0)
        row_decisions.append(
            EliteExposureCapDecision(
                index=idx,
                player=player,
                team=team,
                game_key=game_key,
                current_team_count=current_team_count,
                current_game_count=current_game_count,
                team_would_skip=team_cap_check,
                game_would_skip=game_cap_check,
                action="selected",
                selected_team_count=selected_team_count,
                selected_game_count=selected_game_count,
            )
        )

        working_df.at[idx, "team_exposure_count_at_decision"] = selected_team_count - 1
        working_df.at[idx, "game_exposure_count_at_decision"] = selected_game_count - 1

    capped_df = working_df.loc[capped_selection].copy() if capped_selection else pd.DataFrame()
    first_10_game_keys = [
        str(_normalize_elite_game_key(working_df.iloc[i]))
        for i in range(min(10, len(working_df)))
    ]

    return EliteExposureCapResult(
        capped_df=capped_df,
        annotated_df=working_df,
        team_counts=team_counts,
        game_counts=game_counts,
        skipped_by_team_cap=skipped_by_team_cap,
        skipped_by_game_cap=skipped_by_game_cap,
        sample_game_keys=sample_game_keys,
        first_10_game_keys=first_10_game_keys,
        row_decisions=row_decisions,
    )


def resolve_elite_allowed_markets(
    elite_market_mode: Any = "points_only",
    elite_allowed_markets: Iterable[Any] = (),
) -> set[str]:
    """Return the current elite market policy set without selecting rows."""
    mode = str(elite_market_mode or "points_only").strip().lower()
    explicit = [
        normalize_market_alias(m) or str(m).strip().lower()
        for m in elite_allowed_markets
        if str(m).strip()
    ]
    explicit_set = {m for m in explicit if m}
    points_only = {"player_points"}
    player_props = {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_3pt_made",
        "player_steals",
        "player_blocks",
    }
    full = set(player_props) | {"moneyline", "team_total"}
    if explicit_set:
        return explicit_set
    if mode == "full":
        return full
    if mode == "player_props":
        return player_props
    return points_only


def elite_market_policy_rejection_reason(
    market: Any,
    allowed_markets: set[str],
) -> str | None:
    """Return the current elite market-policy rejection reason, if any."""
    normalized_market = normalize_market_alias(market) or str(market)
    if normalized_market not in allowed_markets:
        return ELITE_MARKET_POLICY_REJECTION_REASON
    return None


def is_negative_edge_direction(market: Any, selection: Any, edge: Any) -> bool:
    """Return True when the current player-points edge direction gate rejects."""
    normalized_market = str(market).lower()
    normalized_selection = str(selection).lower()
    edge_value = float(edge)
    return (
        normalized_market == "player_points"
        and (
            (normalized_selection == "over" and edge_value <= 0)
            or (normalized_selection == "under" and edge_value >= 0)
        )
    )


def elite_direction_rejection_reason(market: Any, selection: Any, edge: Any) -> str | None:
    """Return the current elite directional rejection reason, if any."""
    if is_negative_edge_direction(market, selection, edge):
        return REJECT_NEGATIVE_EDGE_DIRECTION
    return None


def select_top_per_market(candidates: pd.DataFrame, per_market_limit: int = 20) -> pd.DataFrame:
    """Select the top full-market candidates per market using current pipeline ranking."""
    if candidates.empty:
        return candidates

    sort_column = "selection_score" if "selection_score" in candidates.columns else "quality_score"
    selected_groups = []
    for _, group in candidates.groupby("market_type", sort=False):
        selected_groups.append(group.sort_values(sort_column, ascending=False).head(per_market_limit))
    selected_df = pd.concat(selected_groups).copy() if selected_groups else pd.DataFrame()

    if "selection_rejection_reason" in selected_df.columns:
        selected_df["selection_rejection_reason"] = ""
    return selected_df


__all__ = [
    "ELITE_MARKET_POLICY_REJECTION_REASON",
    "EliteExposureCapDecision",
    "EliteExposureCapResult",
    "apply_elite_exposure_caps",
    "elite_direction_rejection_reason",
    "elite_market_policy_rejection_reason",
    "is_negative_edge_direction",
    "resolve_elite_allowed_markets",
    "select_top_per_market",
]
