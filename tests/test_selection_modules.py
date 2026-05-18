"""Tests for the new selection modules (operator_boards and lanes).

This test file validates the migrated board construction logic.
"""

import pandas as pd
import pytest

from courtvision.context.game_context import (
    IDENTITY_QUARANTINE_ACTION,
    IDENTITY_QUARANTINE_REJECTION_REASON,
    is_identity_quarantined,
)
from courtvision.reporting.paper_kelly_performance import _normalize_paper_rows
from courtvision.reporting.paper_kelly_simulation import build_paper_kelly_simulation
from courtvision.selection import (
    ACTIVE_OPERATOR_MARKETS,
    DUPLICATE_BETTING_IDENTITY_REASON,
    assign_candidate_lanes,
    build_operator_boards,
    classify_candidate_lane,
    classify_candidates_batch,
)
from courtvision.runtime_audit import BoardAuditPolicy, get_elite_rejection_reason
from scripts.history_tracking import _normalize_market_shadow_rows
from scripts.run_kelly_stakes import _build_stake_row


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


def test_generic_context_suppression_is_not_identity_quarantine():
    row = _base_live_candidate(
        game_context_suppressed=True,
        high_caution_over=True,
        same_opponent_warning=True,
        context_warning="rematch warning only",
        context_caution_level="high",
        context_pick_alignment="conflicted",
    )

    assert is_identity_quarantined(row) is None


def test_identity_quarantine_helper_detects_outside_and_stale_teams():
    dennis_style = _base_live_candidate(
        player_name="Dennis Schroder",
        entity_name="Dennis Schroder",
        team="SAC",
        team_abbr="SAC",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
    )
    harden_style = _base_live_candidate(
        player_name="James Harden",
        entity_name="James Harden",
        team="CLE",
        team_abbr="CLE",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
        provider_team_abbr="LAC",
        baseline_team_abbr="CLE",
        identity_source_team_abbr="LAC",
    )

    assert is_identity_quarantined(dennis_style) == "outside_team_identity"
    assert is_identity_quarantined(harden_style) == "stale_team_identity"


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


def test_identity_quarantine_precedes_duplicate_identity_for_team_not_in_game_context():
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
    assert traces["full_market"]["post_live_market_gate_count"] == 2
    assert traces["full_market"]["post_duplicate_betting_identity_dedupe_count"] == 2
    assert traces["full_market"]["duplicate_betting_identity_drop_count"] == 0
    assert traces["full_market"]["duplicate_betting_identity_drop_counts_by_market_type"] == {}
    assert traces["full_market"]["identity_quarantine_count"] == 1
    assert traces["full_market"]["identity_quarantine_reason_counts"] == {"outside_team_identity": 1}
    assert {
        row["reason"]: row["count"]
        for row in traces["selection_rejection_reasons"]
    }[IDENTITY_QUARANTINE_REJECTION_REASON] == 1
    quarantined = [
        row for row in traces["qualified_but_not_selected_rows"]
        if row.get("player_name") == "James Harden" and row.get("team") == "LAC"
    ][0]
    assert quarantined["recommended_action"] == IDENTITY_QUARANTINE_ACTION
    assert quarantined["identity_quarantine_reason"] == "outside_team_identity"


def test_outside_team_identity_is_excluded_from_boards_and_retained_in_rejections():
    valid = _base_live_candidate(
        player_name="Valid Cavalier",
        entity_name="Valid Cavalier",
        team="CLE",
        team_abbr="CLE",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
    )
    outside = _base_live_candidate(
        player_name="Dennis Schroder",
        entity_name="Dennis Schroder",
        player_id=17,
        team="SAC",
        team_abbr="SAC",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
        selection_score=999.0,
        quality_score=999.0,
    )

    elite_df, full_market_df, traces = build_operator_boards(
        pd.DataFrame([valid, outside]),
        select_elite_board=lambda df: df.copy(),
        select_top_per_market=lambda df, limit: df.copy(),
    )

    assert list(full_market_df["player_name"]) == ["Valid Cavalier"]
    assert list(elite_df["player_name"]) == ["Valid Cavalier"]
    assert traces["identity_quarantine"]["total_rows_dropped"] == 1
    assert traces["identity_quarantine"]["counts_by_reason"] == {"outside_team_identity": 1}
    retained = traces["qualified_but_not_selected_rows"][0]
    assert retained["player_name"] == "Dennis Schroder"
    assert retained["selection_rejection_reason"] == IDENTITY_QUARANTINE_REJECTION_REASON
    assert retained["recommended_action"] == IDENTITY_QUARANTINE_ACTION


def test_stale_team_identity_is_excluded_from_boards_and_retained_in_rejections():
    stale = _base_live_candidate(
        player_name="James Harden",
        entity_name="James Harden",
        player_id=192,
        team="CLE",
        team_abbr="CLE",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
        provider_team_abbr="LAC",
        identity_source_team_abbr="LAC",
        selection_score=999.0,
        quality_score=999.0,
    )
    valid = _base_live_candidate(
        player_name="Valid Piston",
        entity_name="Valid Piston",
        player_id=22,
        team="DET",
        team_abbr="DET",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
    )

    elite_df, full_market_df, traces = build_operator_boards(
        pd.DataFrame([stale, valid]),
        select_elite_board=lambda df: df.copy(),
        select_top_per_market=lambda df, limit: df.copy(),
    )

    assert list(full_market_df["player_name"]) == ["Valid Piston"]
    assert list(elite_df["player_name"]) == ["Valid Piston"]
    assert traces["identity_quarantine"]["counts_by_reason"] == {"stale_team_identity": 1}
    retained = traces["qualified_but_not_selected_rows"][0]
    assert retained["player_name"] == "James Harden"
    assert retained["identity_quarantine_reason"] == "stale_team_identity"


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


def test_identity_quarantine_diagnostics_flow_into_board_audit_policy():
    valid = _base_live_candidate(player_name="Valid", entity_name="Valid", team="CLE", team_abbr="CLE")
    quarantined = _base_live_candidate(
        player_name="Dennis Schroder",
        entity_name="Dennis Schroder",
        team="SAC",
        team_abbr="SAC",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
    )
    candidates = pd.DataFrame([valid, quarantined])

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

    assert diagnostics["identity_quarantine"] == {
        "rejection_reason": IDENTITY_QUARANTINE_REJECTION_REASON,
        "total_rows_dropped": 1,
        "counts_by_reason": {"outside_team_identity": 1},
    }
    assert diagnostics["final_board_construction"]["full_market"]["identity_quarantine_count"] == 1
    assert diagnostics["final_board_construction"]["elite"]["identity_quarantine_reason_counts"] == {
        "outside_team_identity": 1,
    }


def test_kelly_quarantined_row_gets_no_stake_and_data_invalid_action():
    stake = _build_stake_row(
        {
            "player_name": "James Harden",
            "team_abbr": "CLE",
            "opponent": "DET",
            "market_type": "player_points",
            "selection": "over",
            "line": "19.5",
            "odds": "-110",
            "edge_pct": "0.25",
            "confidence": "0.95",
            "game_home_team_abbr": "CLE",
            "game_away_team_abbr": "DET",
            "provider_team_abbr": "LAC",
            "identity_source_team_abbr": "LAC",
        },
        "edge_pct",
        1000.0,
    )

    assert stake.eligible is False
    assert stake.stake_amount == 0.0
    assert stake.expected_value == 0.0
    assert stake.skip_reason == IDENTITY_QUARANTINE_REJECTION_REASON
    assert stake.recommended_action == IDENTITY_QUARANTINE_ACTION
    assert stake.identity_quarantine_reason == "stale_team_identity"


def test_paper_kelly_excludes_identity_quarantine_rows_from_normal_simulation_and_history():
    valid = _base_live_candidate(
        player_name="Valid Paper",
        entity_name="Valid Paper",
        team="CLE",
        team_abbr="CLE",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
        edge=4.0,
        confidence=0.90,
    )
    quarantined = _base_live_candidate(
        player_name="Dennis Schroder",
        entity_name="Dennis Schroder",
        team="SAC",
        team_abbr="SAC",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
        edge=10.0,
        confidence=0.99,
    )

    simulation = build_paper_kelly_simulation(
        prediction_date="2026-05-15",
        combo_under_watchlist=pd.DataFrame([valid, quarantined]),
    )

    assert list(simulation["player_name"]) == ["Valid Paper"]

    persisted = _normalize_paper_rows(
        pd.concat(
            [
                simulation,
                pd.DataFrame([
                    {
                        **quarantined,
                        "paper_bucket": "combo_under_watchlist",
                        "simulated_stake": 0.0025,
                        "pre_cap_simulated_stake": 0.0025,
                    }
                ]),
            ],
            ignore_index=True,
            sort=False,
        ),
        prediction_date="2026-05-15",
        result_lookup={},
    )

    assert list(persisted["player_name"]) == ["Valid Paper"]


def test_market_shadow_history_normalization_excludes_identity_quarantine_rows():
    valid = _base_live_candidate(
        player_name="Valid Shadow",
        entity_name="Valid Shadow",
        team="CLE",
        team_abbr="CLE",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
    )
    quarantined = _base_live_candidate(
        player_name="Dennis Schroder",
        entity_name="Dennis Schroder",
        team="SAC",
        team_abbr="SAC",
        game_home_team_abbr="CLE",
        game_away_team_abbr="DET",
    )

    normalized = _normalize_market_shadow_rows(
        pd.DataFrame([valid, quarantined]),
        prediction_date="2026-05-15",
    )

    assert list(normalized["player_name"]) == ["Valid Shadow"]


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
