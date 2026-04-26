"""Board construction and lane assignment for operator mode.

This module extracts board construction logic from courtvision_ai.py into
package-owned, testable components. Board names correspond to operator-facing
lanes: elite (strong conviction), full_market (live market), stat_only (projection).
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd


def _display_name(row: pd.Series) -> str:
    return str(
        row.get("entity_name")
        or row.get("player_name")
        or row.get("player")
        or ""
    ).strip()


def _selection_identity_frame(df: pd.DataFrame) -> set[tuple[Any, ...]]:
    if df.empty:
        return set()
    identities: set[tuple[Any, ...]] = set()
    for idx, row in df.iterrows():
        identities.add(
            (
                idx,
                str(row.get("market_type", "")),
                _display_name(row),
                str(row.get("team", row.get("team_abbr", "")) or "").strip().upper(),
            )
        )
    return identities


def _non_milestone_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    raw_market_type = df.get("raw_market_type", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
    selection = df.get("selection", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
    return raw_market_type.ne("milestone") & selection.ne("milestone")


def compute_board_diversity_metrics(board_df: pd.DataFrame) -> dict[str, Any]:
    """Compute diversity metrics for a board.

    Tracks market type distribution, player clustering, and game exposure
    to identify potential bias issues.

    Returns dict with:
    - market_type_counts: Dict of market type frequencies
    - player_counts: Dict of player appearance frequencies
    - game_counts: Dict of game appearance frequencies
    - points_bias_ratio: Ratio of points markets to total
    - max_player_clustering: Max picks for any single player
    - max_game_exposure: Max picks for any single game
    """
    if board_df.empty:
        return {
            "market_type_counts": {},
            "player_counts": {},
            "game_counts": {},
            "points_bias_ratio": 0.0,
            "max_player_clustering": 0,
            "max_game_exposure": 0,
        }

    market_type_counts = board_df["market_type"].value_counts().to_dict() if "market_type" in board_df.columns else {}
    player_counts = board_df["player_name"].value_counts().to_dict() if "player_name" in board_df.columns else {}
    game_counts = board_df["game_id"].value_counts().to_dict() if "game_id" in board_df.columns else {}

    total_picks = len(board_df)
    points_picks = market_type_counts.get("player_points", 0)
    points_bias_ratio = points_picks / total_picks if total_picks > 0 else 0.0

    max_player_clustering = max(player_counts.values()) if player_counts else 0
    max_game_exposure = max(game_counts.values()) if game_counts else 0

    return {
        "market_type_counts": market_type_counts,
        "player_counts": player_counts,
        "game_counts": game_counts,
        "points_bias_ratio": round(points_bias_ratio, 3),
        "max_player_clustering": max_player_clustering,
        "max_game_exposure": max_game_exposure,
    }


def apply_diversity_penalty(
    row: pd.Series,
    board_metrics: dict[str, Any],
    max_points_ratio: float = 0.5,
    max_per_player: int = 2,
    max_per_game: int = 3,
) -> tuple[float, list[str]]:
    """Apply diversity-based confidence penalty to a candidate.

    Penalizes picks that would exacerbate existing board biases:
    - Points market bias (too many points picks)
    - Same-player clustering (too many picks for one player)
    - Same-game overexposure (too many picks from one game)

    Returns:
    - penalty: Confidence reduction (0.0 to 1.0)
    - reasons: List of penalty reasons for diagnostics
    """
    penalty = 0.0
    reasons: list[str] = []

    market_type = row.get("market_type", "")
    player_name = row.get("player_name", "")
    game_id = row.get("game_id", "")

    # Points market bias penalty
    if market_type == "player_points":
        current_ratio = board_metrics.get("points_bias_ratio", 0.0)
        if current_ratio > max_points_ratio:
            penalty += 0.03
            reasons.append(f"points_bias_ratio_{current_ratio:.2f}")

    # Same-player clustering penalty
    player_counts = board_metrics.get("player_counts", {})
    if player_name in player_counts:
        current_count = player_counts[player_name]
        if current_count >= max_per_player:
            penalty += 0.04
            reasons.append(f"player_clustering_{current_count}")

    # Same-game overexposure penalty
    game_counts = board_metrics.get("game_counts", {})
    if game_id in game_counts:
        current_count = game_counts[game_id]
        if current_count >= max_per_game:
            penalty += 0.02
            reasons.append(f"game_exposure_{current_count}")

    return min(penalty, 0.15), reasons  # Cap total penalty at 15%


def build_operator_boards(
    prepared_df: pd.DataFrame,
    *,
    per_market_limit: int = 20,
    select_elite_board: Callable[[pd.DataFrame], pd.DataFrame],
    select_top_per_market: Callable[[pd.DataFrame, int], pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build elite and full_market boards from prepared candidates.

    Elite board: strong conviction, high confidence, live market qualified.
    Full market board: top N per market type, live market qualified.

    Args:
        prepared_df: DataFrame with enriched candidate rows (live and stat-only)
        per_market_limit: Max candidates per market type in full_market board
        select_elite_board: Callable to filter elite candidates with trace
        select_top_per_market: Callable to select top N per market with trace

    Returns:
        (elite_df, full_market_df, final_board_construction_traces)
    """
    if prepared_df.empty:
        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {
                "elite": {"input_count": 0, "selected_count": 0},
                "full_market": {"input_count": 0, "selected_count": 0},
                "required_selector_columns": [],
                "selection_rejection_reasons": [],
                "qualified_but_not_selected_rows": [],
            },
        )

    if "selection_rejection_reason" not in prepared_df.columns:
        prepared_df["selection_rejection_reason"] = ""

    required_selector_columns = [
        "selection_score",
        "confidence",
        "edge",
        "quality_score",
        "market_type",
        "qualification_reason",
        "is_live_market",
        "synthetic_line",
        "source_lane",
    ]
    required_selector_diagnostics = [
        {
            "column": column,
            "exists": column in prepared_df.columns,
            "non_null_count": int(prepared_df[column].notna().sum()) if column in prepared_df.columns else 0,
        }
        for column in required_selector_columns
    ]

    # Unified live gate logic: single mask for both eligibility marking AND filtering
    qualification_reason_series = prepared_df.get("qualification_reason", pd.Series("", index=prepared_df.index))
    is_live_market_series = prepared_df.get("is_live_market", pd.Series(False, index=prepared_df.index))
    synthetic_line_series = prepared_df.get("synthetic_line", pd.Series(False, index=prepared_df.index))
    line_source_series = prepared_df.get("line_source", pd.Series("", index=prepared_df.index))

    # Unified live eligibility mask: must satisfy ALL conditions
    # 1. is_live_market=True (diagnostic flag)
    # 2. NOT synthetic_line (must have real sportsbook line)
    # 3. qualification_reason OR line_source indicates live market origin
    legacy_live_mask = qualification_reason_series.astype(str).str.contains(
        "live_market|sportsbook", regex=True, case=False, na=False
    )
    line_source_live_mask = line_source_series.astype(str).str.contains(
        "live_market", regex=False, case=False, na=False
    )
    origin_live_mask = legacy_live_mask | line_source_live_mask

    diagnostic_live_mask = (
        is_live_market_series.fillna(False).astype(bool)
        & ~synthetic_line_series.fillna(False).astype(bool)
    )

    # Unified mask: ALL conditions must be met for live eligibility
    unified_live_mask = diagnostic_live_mask & origin_live_mask

    # Rejection reasons - unified logic ensures no row marked valid is later dropped
    prepared_df.loc[
        prepared_df["selection_rejection_reason"].eq("")
        & ~unified_live_mask
        & ~diagnostic_live_mask,
        "selection_rejection_reason",
    ] = "selection_not_live_market_eligible"
    prepared_df.loc[
        prepared_df["selection_rejection_reason"].eq("")
        & diagnostic_live_mask
        & ~origin_live_mask
        & qualification_reason_series.fillna("").astype(str).str.strip().eq(""),
        "selection_rejection_reason",
    ] = "selection_live_gate_missing_qualification_reason"
    prepared_df.loc[
        prepared_df["selection_rejection_reason"].eq("")
        & diagnostic_live_mask
        & ~origin_live_mask,
        "selection_rejection_reason",
    ] = "selection_live_gate_filtered"

    # Filter using the SAME unified mask used for eligibility marking
    milestone_mask = ~_non_milestone_mask(prepared_df)
    if milestone_mask.any():
        prepared_df.loc[
            prepared_df["selection_rejection_reason"].eq("") & milestone_mask,
            "selection_rejection_reason",
        ] = "unsupported_milestone_market"

    live_candidates_df = prepared_df[unified_live_mask & ~milestone_mask].copy()
    unsupported_milestone_count = int(milestone_mask.sum())

    final_board_construction: dict[str, Any] = {"elite": {}, "full_market": {}}
    final_board_construction["required_selector_columns"] = required_selector_diagnostics

    final_board_construction["elite"]["input_count"] = len(prepared_df)
    final_board_construction["elite"]["post_live_market_gate_count"] = len(live_candidates_df)
    final_board_construction["elite"]["unsupported_milestone_count"] = unsupported_milestone_count
    final_board_construction["elite"]["diagnostic_live_flag_count"] = int(diagnostic_live_mask.sum())
    final_board_construction["elite"]["qualification_reason_missing_count"] = int(
        qualification_reason_series.fillna("").astype(str).str.strip().eq("").sum()
    )

    final_board_construction["full_market"]["input_count"] = len(prepared_df)
    final_board_construction["full_market"]["post_live_market_gate_count"] = len(live_candidates_df)
    final_board_construction["full_market"]["unsupported_milestone_count"] = unsupported_milestone_count
    final_board_construction["full_market"]["diagnostic_live_flag_count"] = int(diagnostic_live_mask.sum())

    elite_df = select_elite_board(live_candidates_df)
    elite_count = len(elite_df)
    final_board_construction["elite"]["selected_count"] = elite_count

    full_market_df = select_top_per_market(live_candidates_df, per_market_limit)
    full_market_count = len(full_market_df)
    final_board_construction["full_market"]["selected_count"] = full_market_count

    selected_identities = _selection_identity_frame(elite_df) | _selection_identity_frame(full_market_df)
    if not live_candidates_df.empty:
        for idx, row in live_candidates_df.iterrows():
            identity = (
                idx,
                str(row.get("market_type", "")),
                _display_name(row),
                str(row.get("team", row.get("team_abbr", "")) or "").strip().upper(),
            )
            if identity not in selected_identities and not str(prepared_df.at[idx, "selection_rejection_reason"]).strip():
                prepared_df.at[idx, "selection_rejection_reason"] = "selection_not_selected_by_board_selector"

    not_selected_df = prepared_df[prepared_df["selection_rejection_reason"].astype(str).str.strip().ne("")].copy()
    final_board_construction["selection_rejection_reasons"] = (
        not_selected_df["selection_rejection_reason"].value_counts().rename_axis("reason").reset_index(name="count").to_dict("records")
        if not not_selected_df.empty
        else []
    )
    sample_cols = [
        "player_name",
        "entity_name",
        "market_type",
        "team",
        "team_abbr",
        "selection_score",
        "confidence",
        "edge",
        "quality_score",
        "qualification_reason",
        "source_lane",
        "selection_rejection_reason",
    ]
    available_sample_cols = [col for col in sample_cols if col in prepared_df.columns]
    final_board_construction["qualified_but_not_selected_rows"] = (
        not_selected_df[available_sample_cols].head(10).to_dict("records")
        if available_sample_cols and not not_selected_df.empty
        else []
    )

    return elite_df, full_market_df, final_board_construction


def assign_candidate_lanes(
    qualified_df: pd.DataFrame,
    elite_df: pd.DataFrame,
    full_market_df: pd.DataFrame,
) -> dict[str, Any]:
    """Assign candidates to lanes and track which were selected.

    This produces a lane assignment summary showing why candidates ended up
    in different boards (elite vs full_market vs none).

    Args:
        qualified_df: All qualified candidates before board selection
        elite_df: Candidates in elite board
        full_market_df: Candidates in full_market board

    Returns:
        Lane assignment summary with counts and reasons
    """
    elite_set = _selection_identity_frame(elite_df)
    full_market_set = _selection_identity_frame(full_market_df)

    in_elite = 0
    in_full_market = 0
    in_neither = 0

    if not qualified_df.empty:
        for _, row in qualified_df.iterrows():
            key = (
                row.name,
                str(row.get("market_type", "")),
                _display_name(row),
                str(row.get("team", row.get("team_abbr", "")) or "").strip().upper(),
            )
            if key in elite_set:
                in_elite += 1
            elif key in full_market_set:
                in_full_market += 1
            else:
                in_neither += 1

    return {
        "total_qualified": len(qualified_df),
        "assigned_to_elite": in_elite,
        "assigned_to_full_market": in_full_market,
        "qualified_but_not_selected": in_neither,
        "elite_board_size": len(elite_df),
        "full_market_board_size": len(full_market_df),
    }
