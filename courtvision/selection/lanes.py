"""Lane and board assignment rules for operator mode.

This module formalizes the logic for determining which board a candidate belongs to
based on market qualification, exposure, and operator policy.
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd


BoardLane = Literal["elite", "full_market", "stat_only", "strike", "predictive", "team_board", "rejected"]


def classify_candidate_lane(
    row: dict[str, Any],
    live_supported_markets: list[str],
    stat_only_eligible_markets: list[str] = None,
) -> tuple[BoardLane, str]:
    """Classify a single candidate row into a board lane.

    Decision tree:
    1. If market is team market (moneyline, team_total) -> team_board (team lane)
    2. Elif market has live sportsbook line and odds -> elite/full_market (live lane)
    3. Elif market is projection-eligible -> stat_only (predictive lane)
    4. Else -> rejected (unknown reason)

    Args:
        row: Candidate row dict
        live_supported_markets: Market types with live sportsbook availability
        stat_only_eligible_markets: Market types eligible for projection-only

    Returns:
        (lane, reason_code) tuple
    """
    if stat_only_eligible_markets is None:
        stat_only_eligible_markets = [
            "player_points",
            "player_rebounds",
            "player_assists",
            "player_3pt_made",
            "player_steals",
            "player_blocks",
        ]

    qualification_reason = str(row.get("qualification_reason", "")).strip().lower()
    market_type = str(row.get("market_type", "")).strip()
    sportsbook_line = row.get("sportsbook_line")
    odds = row.get("odds")

    # Team board lane (checked first)
    if market_type in {"moneyline", "team_total"} and qualification_reason == "live_market_qualified":
        return "team_board", "team_live_market"

    # Live market lane: player props with sportsbook line + odds
    if (
        market_type in stat_only_eligible_markets  # player props only
        and market_type not in {"moneyline", "team_total"}
        and pd.notna(sportsbook_line)
        and pd.notna(odds)
        and qualification_reason
        in {"live_market_qualified", "live_market_fill", "sportsbook_qualified"}
    ):
        return "elite", "live_market_qualified"

    # Stat-only lane: projection-based, no live line
    if market_type in stat_only_eligible_markets and qualification_reason in {
        "predictive_market_fill",
        "stat_only_qualified",
    }:
        return "stat_only", "predictive_market_fill"

    # Rejected/unclassified
    return "rejected", qualification_reason or "unclassified"


def classify_candidates_batch(
    df: pd.DataFrame,
    live_supported_markets: list[str],
    stat_only_eligible_markets: list[str] = None,
) -> dict[BoardLane, pd.DataFrame]:
    """Classify a batch of candidates into lanes.

    Returns a dict mapping lane names to DataFrames of candidates assigned to that lane.
    """
    lanes: dict[BoardLane, list[int]] = {
        "elite": [],
        "full_market": [],
        "stat_only": [],
        "strike": [],
        "predictive": [],
        "team_board": [],
        "rejected": [],
    }

    if df.empty:
        return {lane: pd.DataFrame() for lane in lanes}

    for idx, row in df.iterrows():
        lane, _ = classify_candidate_lane(row, live_supported_markets, stat_only_eligible_markets)
        if lane in lanes:
            lanes[lane].append(idx)

    result: dict[BoardLane, pd.DataFrame] = {}
    for lane, indices in lanes.items():
        result[lane] = df.loc[indices].copy() if indices else pd.DataFrame()

    return result
