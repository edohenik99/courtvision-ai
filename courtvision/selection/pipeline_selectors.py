from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from courtvision.market import normalize_market_alias
from courtvision.reason_codes import REJECT_NEGATIVE_EDGE_DIRECTION

ELITE_MARKET_POLICY_REJECTION_REASON = "market_filtered_by_elite_policy"


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
    "elite_direction_rejection_reason",
    "elite_market_policy_rejection_reason",
    "is_negative_edge_direction",
    "resolve_elite_allowed_markets",
    "select_top_per_market",
]
