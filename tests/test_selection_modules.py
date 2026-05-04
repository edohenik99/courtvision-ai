"""Tests for the new selection modules (operator_boards and lanes).

This test file validates the migrated board construction logic.
"""

import pandas as pd
import pytest

from courtvision.selection import (
    assign_candidate_lanes,
    build_operator_boards,
    classify_candidate_lane,
    classify_candidates_batch,
)
from courtvision.runtime_audit import get_elite_rejection_reason


def test_classify_candidate_lane_live_market():
    """Test lane classification for live market candidates."""
    row = {
        "market_type": "player_points",
        "qualification_reason": "live_market_qualified",
        "sportsbook_line": 25.5,
        "odds": -110,
    }
    lane, reason = classify_candidate_lane(row, ["player_points"])
    assert lane == "elite"
    assert reason == "live_market_qualified"


def test_directional_validation_rejects_wrong_side_points_picks():
    from datetime import datetime, timedelta
    base = {
        "market_type": "player_points",
        "sportsbook_line": 24.5,
        "odds": -110,
        # Include valid future game status so game-status gate doesn't block
        "game_status": "scheduled",
        "game_date": (datetime.now() + timedelta(hours=2)).isoformat(),
    }

    assert get_elite_rejection_reason({**base, "selection": "over", "edge_pct": 0.10}) is None
    assert get_elite_rejection_reason({**base, "selection": "under", "edge_pct": -0.10}) is None
    assert get_elite_rejection_reason({**base, "selection": "over", "edge_pct": -0.10}) == "reject_negative_edge_direction"
    assert get_elite_rejection_reason({**base, "selection": "under", "edge_pct": 0.10}) == "reject_negative_edge_direction"


def test_classify_candidate_lane_stat_only():
    """Test lane classification for stat-only (projection) candidates."""
    row = {
        "market_type": "player_points",
        "qualification_reason": "predictive_market_fill",
        "sportsbook_line": None,
        "odds": None,
    }
    lane, reason = classify_candidate_lane(row, [])
    assert lane == "stat_only"
    assert reason == "predictive_market_fill"


def test_classify_candidate_lane_team_board():
    """Test lane classification for team markets."""
    row = {
        "market_type": "moneyline",
        "qualification_reason": "live_market_qualified",
        "sportsbook_line": -110,
        "odds": -110,
    }
    lane, reason = classify_candidate_lane(row, ["moneyline"])
    assert lane == "team_board"
    assert reason == "team_live_market"


def test_classify_candidate_lane_rejected():
    """Test lane classification for rejected candidates."""
    row = {
        "market_type": "double_double",
        "qualification_reason": "unsupported_market",
        "sportsbook_line": None,
        "odds": None,
    }
    lane, reason = classify_candidate_lane(row, [])
    assert lane == "rejected"


def test_classify_candidates_batch():
    """Test batch lane classification."""
    df = pd.DataFrame([
        {
            "market_type": "player_points",
            "qualification_reason": "live_market_qualified",
            "sportsbook_line": 25.5,
            "odds": -110,
        },
        {
            "market_type": "player_points",
            "qualification_reason": "predictive_market_fill",
            "sportsbook_line": None,
            "odds": None,
        },
        {
            "market_type": "moneyline",
            "qualification_reason": "live_market_qualified",
            "sportsbook_line": -110,
            "odds": -110,
        },
    ])

    lanes_dict = classify_candidates_batch(
        df,
        live_supported_markets=["player_points", "moneyline"],
    )

    assert len(lanes_dict["elite"]) == 1  # First row (player_points)
    assert len(lanes_dict["stat_only"]) == 1  # Second row (predictive)
    assert len(lanes_dict["team_board"]) == 1  # Third row (moneyline)
    assert len(lanes_dict["rejected"]) == 0


def test_build_operator_boards_empty():
    """Test board construction with empty input."""
    elite_df, full_market_df, traces = build_operator_boards(
        pd.DataFrame(),
        select_elite_board=lambda x: pd.DataFrame(),
        select_top_per_market=lambda x, n: pd.DataFrame(),
    )

    assert elite_df.empty
    assert full_market_df.empty
    assert traces["elite"]["input_count"] == 0
    assert traces["full_market"]["input_count"] == 0


def test_build_operator_boards_with_candidates():
    """Test board construction with live market candidates."""
    candidates = pd.DataFrame([
        {
            "market_type": "player_points",
            "entity_name": "LeBron James O25.5",
            "qualification_reason": "live_market_fill",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "quality_score": 0.85,
        },
        {
            "market_type": "player_points",
            "entity_name": "Kevin Durant O27.5",
            "qualification_reason": "live_market_fill",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "quality_score": 0.80,
        },
    ])

    def mock_elite_select(df):
        return df.head(1) if not df.empty else pd.DataFrame()

    def mock_top_per_market(df, limit):
        return df.head(limit) if not df.empty else pd.DataFrame()

    elite_df, full_market_df, traces = build_operator_boards(
        candidates,
        per_market_limit=20,
        select_elite_board=mock_elite_select,
        select_top_per_market=mock_top_per_market,
    )

    assert len(elite_df) == 1
    assert len(full_market_df) == 2  # Top 2 per market
    assert traces["elite"]["input_count"] == 2
    assert traces["elite"]["selected_count"] == 1


def test_milestone_market_excluded_from_betting_boards():
    candidates = pd.DataFrame([
        {
            "market_type": "player_points",
            "raw_prop_type": "points",
            "raw_market_type": "over_under",
            "entity_name": "LeBron James O25.5",
            "qualification_reason": "live_market_qualified",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "selection_score": 0.90,
            "quality_score": 0.85,
        },
        {
            "market_type": "player_points",
            "raw_prop_type": "points",
            "raw_market_type": "milestone",
            "entity_name": "LeBron James 30+",
            "qualification_reason": "live_market_qualified",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "selection": "milestone",
            "selection_score": 0.99,
            "quality_score": 0.95,
        },
    ])

    def mock_elite_select(df):
        return df.copy()

    def mock_top_per_market(df, limit):
        return df.head(limit).copy()

    elite_df, full_market_df, traces = build_operator_boards(
        candidates,
        select_elite_board=mock_elite_select,
        select_top_per_market=mock_top_per_market,
    )

    assert len(elite_df) == 1
    assert len(full_market_df) == 1
    assert elite_df.iloc[0]["raw_market_type"] == "over_under"
    assert full_market_df.iloc[0]["raw_market_type"] == "over_under"
    assert traces["elite"]["unsupported_milestone_count"] == 1
    assert traces["full_market"]["unsupported_milestone_count"] == 1


def test_assign_candidate_lanes_summary():
    """Test lane assignment summary generation."""
    qualified_df = pd.DataFrame([
        {
            "market_type": "player_points",
            "entity_name": "LeBron James O25.5",
        },
        {
            "market_type": "player_points",
            "entity_name": "Kevin Durant O27.5",
        },
        {
            "market_type": "player_rebounds",
            "entity_name": "Giannis O12.5",
        },
    ])

    elite_df = qualified_df.iloc[:1]  # First row
    full_market_df = qualified_df.iloc[1:]  # Second and third rows

    summary = assign_candidate_lanes(qualified_df, elite_df, full_market_df)

    assert summary["total_qualified"] == 3
    assert summary["assigned_to_elite"] == 1
    assert summary["assigned_to_full_market"] == 2
    assert summary["qualified_but_not_selected"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
