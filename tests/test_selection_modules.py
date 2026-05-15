"""Tests for the new selection modules (operator_boards and lanes).

This test file validates the migrated board construction logic.
"""

import pandas as pd
import pytest

from courtvision.selection import (
    ACTIVE_OPERATOR_MARKETS,
    DUPLICATE_BETTING_IDENTITY_REASON,
    assign_candidate_lanes,
    build_operator_boards,
    classify_candidate_lane,
    classify_candidates_batch,
)
from courtvision.runtime_audit import BoardAuditPolicy, get_elite_rejection_reason


def _base_live_candidate(**overrides):
    row = {
        "prediction_date": "2026-05-15",
        "player_name": "Test Player",
        "entity_name": "Test Player",
        "player_id": 123,
        "team": "AAA",
        "team_abbr": "AAA",
        "game_id": 999,
        "market_type": "player_points",
        "selection": "over",
        "line": 20.5,
        "sportsbook_line": 20.5,
        "odds": -110,
        "qualification_reason": "live_market_qualified",
        "is_live_market": True,
        "synthetic_line": False,
        "line_source": "live_market",
        "source_lane": "live_market_candidate",
        "selection_score": 30.0,
        "quality_score": 60.0,
    }
    row.update(overrides)
    return row


def _betting_identity_tuples(df: pd.DataFrame) -> list[tuple[str, str, str, str, str]]:
    if df.empty:
        return []
    identities = []
    for _, row in df.iterrows():
        player_key = str(row.get("player_id") or row.get("player_name") or "").strip().lower()
        game_id = str(row.get("game_id") or "").strip()
        market = str(row.get("market_type") or "").strip().lower()
        selection = str(row.get("selection") or "").strip().lower()
        line = str(float(row.get("line")) if str(row.get("line") or "").strip() else "").rstrip("0").rstrip(".")
        identities.append((player_key, game_id, market, selection, line))
    return identities


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
        # Include fresh odds timestamp so odds-stale gate doesn't block
        "odds_updated_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
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


def test_unsupported_active_operator_markets_excluded_from_boards():
    candidates = pd.DataFrame([
        {
            "market_type": "player_points",
            "entity_name": "Supported Points",
            "qualification_reason": "live_market_qualified",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "selection_score": 0.90,
            "quality_score": 0.85,
        },
        {
            "market_type": "player_points_assists",
            "entity_name": "Supported Combo",
            "qualification_reason": "live_market_qualified",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "selection_score": 0.88,
            "quality_score": 0.82,
        },
        {
            "market_type": "player_blocks",
            "entity_name": "Unsupported Blocks",
            "qualification_reason": "live_market_qualified",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "selection_score": 0.99,
            "quality_score": 0.99,
        },
        {
            "market_type": "player_steals",
            "entity_name": "Unsupported Steals",
            "qualification_reason": "live_market_qualified",
            "is_live_market": True,
            "synthetic_line": False,
            "line_source": "live_market",
            "selection_score": 0.98,
            "quality_score": 0.98,
        },
    ])

    def mock_elite_select(df):
        return df.copy()

    def mock_top_per_market(df, limit):
        return df.copy()

    elite_df, full_market_df, traces = build_operator_boards(
        candidates,
        select_elite_board=mock_elite_select,
        select_top_per_market=mock_top_per_market,
    )

    assert set(elite_df["market_type"]) == {"player_points", "player_points_assists"}
    assert set(full_market_df["market_type"]) == {"player_points", "player_points_assists"}
    assert set(elite_df["market_type"]).issubset(ACTIVE_OPERATOR_MARKETS)
    assert set(full_market_df["market_type"]).issubset(ACTIVE_OPERATOR_MARKETS)
    assert traces["elite"]["unsupported_active_operator_market_count"] == 2
    assert traces["full_market"]["unsupported_active_operator_market_count"] == 2
    assert traces["full_market"]["unsupported_active_operator_market_counts"] == {
        "player_blocks": 1,
        "player_steals": 1,
    }
    assert {
        row["reason"]: row["count"]
        for row in traces["selection_rejection_reasons"]
    }["unsupported_active_operator_market"] == 2

    diagnostics = BoardAuditPolicy().build_diagnostics(
        prediction_date="2026-05-15",
        qualified_pool_df=candidates,
        elite_df=elite_df,
        full_market_df=full_market_df,
        rejected_df=pd.DataFrame(),
        final_board_construction=traces,
    )
    assert diagnostics["unsupported_active_operator_markets"] == {
        "rejection_reason": "unsupported_active_operator_market",
        "total_rows_dropped": 2,
        "counts_by_market_type": {
            "player_blocks": 1,
            "player_steals": 1,
        },
    }
    assert diagnostics["final_board_construction"]["full_market"]["unsupported_active_operator_market_count"] == 2
    assert diagnostics["final_board_construction"]["full_market"]["unsupported_active_operator_market_counts"] == {
        "player_blocks": 1,
        "player_steals": 1,
    }


def test_duplicate_betting_identity_prefers_valid_game_context_team():
    stale = _base_live_candidate(
        player_name="James Harden",
        entity_name="James Harden",
        player_id=192,
        team="LAC",
        team_abbr="LAC",
        game_id=21709238,
        market_type="player_points",
        selection="over",
        line=19.5,
        sportsbook_line=19.5,
        selection_score=99.0,
        quality_score=99.0,
        candidate_team_not_in_game=True,
        game_context_suppressed=True,
        game_context_suppression_reason="team_not_in_game_context",
        context_conflict_cause="stale_team_not_in_game",
    )
    valid = _base_live_candidate(
        player_name="James Harden",
        entity_name="James Harden",
        player_id=192,
        team="CLE",
        team_abbr="CLE",
        game_id=21709238,
        market_type="player_points",
        selection="over",
        line=19.5,
        sportsbook_line=19.5,
        selection_score=50.0,
        quality_score=50.0,
        opponent="DET",
        home_away="home",
        game_status="scheduled",
    )
    other = _base_live_candidate(
        player_name="Supported Other",
        entity_name="Supported Other",
        player_id=777,
        team="MIN",
        team_abbr="MIN",
        game_id=21707977,
        market_type="player_assists",
        selection="over",
        line=4.5,
        sportsbook_line=4.5,
    )

    candidates = pd.DataFrame([stale, valid, other])

    def select_all(df):
        return df.copy()

    elite_df, full_market_df, traces = build_operator_boards(
        candidates,
        select_elite_board=select_all,
        select_top_per_market=lambda df, limit: df.copy(),
    )

    assert len(full_market_df) == 2
    assert len(elite_df) == 2
    harden_full = full_market_df[full_market_df["player_name"].eq("James Harden")].iloc[0]
    harden_elite = elite_df[elite_df["player_name"].eq("James Harden")].iloc[0]
    assert harden_full["team"] == "CLE"
    assert harden_elite["team"] == "CLE"
    assert len(_betting_identity_tuples(full_market_df)) == len(set(_betting_identity_tuples(full_market_df)))
    assert len(_betting_identity_tuples(elite_df)) == len(set(_betting_identity_tuples(elite_df)))
    assert traces["full_market"]["post_live_market_gate_count"] == 3
    assert traces["full_market"]["post_duplicate_betting_identity_dedupe_count"] == 2
    assert traces["full_market"]["duplicate_betting_identity_drop_count"] == 1
    assert traces["full_market"]["duplicate_betting_identity_drop_counts_by_market_type"] == {"player_points": 1}
    assert traces["full_market"]["duplicate_betting_identity_drop_groups"][0]["rejection_reason"] == (
        DUPLICATE_BETTING_IDENTITY_REASON
    )
    assert {
        row["reason"]: row["count"]
        for row in traces["selection_rejection_reasons"]
    }[DUPLICATE_BETTING_IDENTITY_REASON] == 1


def test_duplicate_betting_identity_uses_score_then_quality_without_context():
    lower_score = _base_live_candidate(
        player_name="Ayo Dosunmu",
        entity_name="Ayo Dosunmu",
        player_id=17895983,
        team="CHI",
        team_abbr="CHI",
        game_id=21707977,
        market_type="player_points",
        selection="over",
        line=13.5,
        sportsbook_line=13.5,
        selection_score=30.0,
        quality_score=90.0,
    )
    higher_score = _base_live_candidate(
        player_name="Ayo Dosunmu",
        entity_name="Ayo Dosunmu",
        player_id=17895983,
        team="MIN",
        team_abbr="MIN",
        game_id=21707977,
        market_type="player_points",
        selection="over",
        line=13.5,
        sportsbook_line=13.5,
        selection_score=31.0,
        quality_score=40.0,
    )
    quality_tie_break = _base_live_candidate(
        player_name="Tie Breaker",
        entity_name="Tie Breaker",
        player_id=555,
        team="AAA",
        team_abbr="AAA",
        game_id=100,
        market_type="player_assists",
        selection="over",
        line=6.5,
        sportsbook_line=6.5,
        selection_score=20.0,
        quality_score=10.0,
    )
    quality_winner = {
        **quality_tie_break,
        "team": "BBB",
        "team_abbr": "BBB",
        "quality_score": 11.0,
    }
    candidates = pd.DataFrame([lower_score, higher_score, quality_tie_break, quality_winner])

    elite_df, full_market_df, traces = build_operator_boards(
        candidates,
        select_elite_board=lambda df: df.copy(),
        select_top_per_market=lambda df, limit: df.copy(),
    )

    assert len(full_market_df) == 2
    assert len(elite_df) == 2
    ayo = full_market_df[full_market_df["player_name"].eq("Ayo Dosunmu")].iloc[0]
    tie = full_market_df[full_market_df["player_name"].eq("Tie Breaker")].iloc[0]
    assert ayo["team"] == "MIN"
    assert tie["team"] == "BBB"
    assert traces["full_market"]["duplicate_betting_identity_drop_count"] == 2
    assert traces["full_market"]["duplicate_betting_identity_drop_counts_by_market_type"] == {
        "player_assists": 1,
        "player_points": 1,
    }


def test_duplicate_betting_identity_diagnostics_flow_into_board_audit_policy():
    candidates = pd.DataFrame([
        _base_live_candidate(player_id=1, player_name="Dupe", entity_name="Dupe", selection_score=1.0),
        _base_live_candidate(player_id=1, player_name="Dupe", entity_name="Dupe", selection_score=2.0),
        _base_live_candidate(player_id=2, player_name="Unique", entity_name="Unique", line=21.5, sportsbook_line=21.5),
    ])

    elite_df, full_market_df, traces = build_operator_boards(
        candidates,
        select_elite_board=lambda df: df.copy(),
        select_top_per_market=lambda df, limit: df.copy(),
    )
    diagnostics = BoardAuditPolicy().build_diagnostics(
        prediction_date="2026-05-15",
        qualified_pool_df=candidates,
        elite_df=elite_df,
        full_market_df=full_market_df,
        rejected_df=pd.DataFrame(),
        final_board_construction=traces,
    )

    assert diagnostics["duplicate_betting_identity"] == {
        "rejection_reason": DUPLICATE_BETTING_IDENTITY_REASON,
        "total_rows_dropped": 1,
        "counts_by_market_type": {"player_points": 1},
        "groups": traces["full_market"]["duplicate_betting_identity_drop_groups"],
    }
    assert diagnostics["final_board_construction"]["full_market"]["duplicate_betting_identity_drop_count"] == 1
    assert diagnostics["final_board_construction"]["elite"]["duplicate_betting_identity_drop_count"] == 1


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
