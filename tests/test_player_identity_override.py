from __future__ import annotations

import pandas as pd
import pytest

from courtvision.context.game_context import (
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_STALE_TEAM_REASON,
)
from courtvision.context.player_identity import (
    BASELINE_PROVIDER_TEAM_CONFLICT_REASON,
    PLAYER_ID_TEAM_CONFLICT_REASON,
    PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON,
    build_canonical_player_identity_resolver,
    annotate_source_identity_conflicts,
)
from courtvision.pipeline.predict_pipeline import PredictionPipeline, PredictionConfig


def test_multiple_historical_stints_resolves_and_suppresses_conflict() -> None:
    # 1. Test that a player with multiple stints in baselines (SAC and SAS)
    # where SAC is active, resolves to SAC as valid_current_team_override
    # and has conflict suppressed (source_identity_conflicted = False).
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-28",
                "game_id": 101,
                "home_team_abbr": "SAC",
                "visitor_team_abbr": "LAL",
            }
        ]
    )
    baselines = pd.DataFrame(
        [
            {"player_id": 44, "player_name": "De'Aaron Fox", "team_abbr": "SAS"},
            {"player_id": 44, "player_name": "De'Aaron Fox", "team_abbr": "SAC"},
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 101,
                "player_id": 44,
                "player_name": "De'Aaron Fox",
                "team_abbr": "SAC",
                "provider_team_abbr": "SAC",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-28",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )

    # Valid candidate row (Fox for SAC)
    valid_record = {
        "player_id": 44,
        "player_name": "De'Aaron Fox",
        "team_abbr": "SAC",
        "baseline_team_abbr": "SAC",
        "provider_team_abbr": "SAC",
        "game_id": 101,
        "game_home_team_abbr": "SAC",
        "game_away_team_abbr": "LAL",
    }
    annotated_valid = resolver.annotate_record(valid_record)
    assert annotated_valid["player_identity_valid"] is True
    assert annotated_valid["identity_resolution_category"] == "valid_current_team_override"
    assert annotated_valid["player_identity_conflict_reason"] == ""

    # Source conflict is suppressed for valid overrides
    annotated_df = annotate_source_identity_conflicts(pd.DataFrame([annotated_valid]), resolver.summary())
    assert bool(annotated_df.iloc[0]["source_identity_conflicted"]) is False

    # Stale candidate row (Fox for SAS)
    stale_record = {
        "player_id": 44,
        "player_name": "De'Aaron Fox",
        "team_abbr": "SAS",
        "baseline_team_abbr": "SAS",
        "provider_team_abbr": "SAC",
        "game_id": 101,
        "game_home_team_abbr": "SAC",
        "game_away_team_abbr": "LAL",
    }
    annotated_stale = resolver.annotate_record(stale_record)
    assert annotated_stale["player_identity_valid"] is False
    assert annotated_stale["identity_resolution_category"] == "historical_stint_mismatch"
    assert annotated_stale["player_identity_conflict_reason"] == PLAYER_ID_TEAM_CONFLICT_REASON


def test_true_conflicts_remain_blocked() -> None:
    # 2. Test that true mismatches (impossible team or inactive rosters) still trigger rejections and are blocked.
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-28",
                "game_id": 102,
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "MIA",
            }
        ]
    )
    # Schroder is listed on SAS, but SAS is not on the slate
    baselines = pd.DataFrame(
        [{"player_id": 17, "player_name": "Dennis Schroder", "team_abbr": "SAS"}]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 102,
                "player_id": 17,
                "player_name": "Dennis Schroder",
                "team_abbr": "SAS",
                "provider_team_abbr": "SAS",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-28",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )

    record = {
        "player_id": 17,
        "player_name": "Dennis Schroder",
        "team_abbr": "SAS",
        "baseline_team_abbr": "SAS",
        "provider_team_abbr": "SAS",
        "game_id": 102,
        "game_home_team_abbr": "BOS",
        "game_away_team_abbr": "MIA",
    }
    annotated = resolver.annotate_record(record)
    assert annotated["player_identity_valid"] is False
    assert annotated["identity_resolution_category"] == "true_identity_conflict"
    assert annotated["player_identity_conflict_reason"] == PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON

    # Stale baseline team conflict that cannot be resolved (no active game)
    stale_baselines = pd.DataFrame(
        [{"player_id": 17, "player_name": "Dennis Schroder", "team_abbr": "BKN"}]
    )
    stale_odds = pd.DataFrame(
        [
            {
                "game_id": 102,
                "player_id": 17,
                "player_name": "Dennis Schroder",
                "team_abbr": "SAS",
                "provider_team_abbr": "SAS",
                "market_type": "player_points",
            }
        ]
    )
    resolver2 = build_canonical_player_identity_resolver(
        prediction_date="2026-05-28",
        player_baselines=stale_baselines,
        odds=stale_odds,
        games=games,
    )
    record2 = {
        "player_id": 17,
        "player_name": "Dennis Schroder",
        "team_abbr": "BKN",
        "baseline_team_abbr": "BKN",
        "provider_team_abbr": "SAS",
        "game_id": 102,
        "game_home_team_abbr": "BOS",
        "game_away_team_abbr": "MIA",
    }
    annotated2 = resolver2.annotate_record(record2)
    assert annotated2["player_identity_valid"] is False
    assert annotated2["identity_resolution_category"] == "stale_baseline_team"
    assert annotated2["player_identity_conflict_reason"] == BASELINE_PROVIDER_TEAM_CONFLICT_REASON


def test_pipeline_baseline_runtime_filtering() -> None:
    # 3. Test that the prediction pipeline's runtime-resolved baseline view
    # correctly filters player_baselines before scoring candidates
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-28",
                "game_id": 101,
                "home_team_abbr": "SAC",
                "visitor_team_abbr": "LAL",
                "game_status": "scheduled",
                "game_datetime": "2026-05-28T20:00:00",
            }
        ]
    )
    baselines = pd.DataFrame(
        [
            {"player_id": 44, "player_name": "De'Aaron Fox", "team_abbr": "SAS", "min_avg": 35.0, "pts_avg": 25.0},
            {"player_id": 44, "player_name": "De'Aaron Fox", "team_abbr": "SAC", "min_avg": 35.0, "pts_avg": 25.0},
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 101,
                "player_id": 44,
                "player_name": "De'Aaron Fox",
                "team_abbr": "SAC",
                "provider_team_abbr": "SAC",
                "market_type": "player_points",
                "line": 24.5,
                "odds": -110,
                "selection": "over",
                "is_live": True,
            }
        ]
    )

    config = PredictionConfig(
        prediction_date="2026-05-28",
        min_edge=0.1,
        min_confidence=0.1,
        enable_partial_fill=True,
    )
    pipeline = PredictionPipeline(config=config)
    res = pipeline.run(
        games=games,
        odds=odds,
        player_baselines=baselines,
    )

    # Verify that the stale SAS stint is filtered out entirely,
    # and only the SAC candidates are built
    full_market = res.full_market_props
    assert not full_market.empty
    fox_rows = full_market[full_market["player_name"] == "De'Aaron Fox"]
    assert (fox_rows["team"] == "SAC").all()
    assert (fox_rows["source_identity_conflicted"] == False).all()


def test_downstream_source_identity_override_exposure() -> None:
    from courtvision.reporting.high_caution_over_watchlist import build_high_caution_over_watchlist
    from courtvision.reporting.paper_kelly_simulation import build_paper_kelly_simulation

    # Setup games, baselines, and odds that trigger valid_current_team_override
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-28",
                "game_id": 101,
                "home_team_abbr": "SAC",
                "visitor_team_abbr": "LAL",
            }
        ]
    )
    # De'Aaron Fox has two stints
    baselines = pd.DataFrame(
        [
            {"player_id": 44, "player_name": "De'Aaron Fox", "team_abbr": "SAS"},
            {"player_id": 44, "player_name": "De'Aaron Fox", "team_abbr": "SAC"},
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 101,
                "player_id": 44,
                "player_name": "De'Aaron Fox",
                "team_abbr": "SAC",
                "provider_team_abbr": "SAC",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-28",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )

    # Let's construct a full_market candidate that mimics De'Aaron Fox with valid_current_team_override
    record = {
        "prediction_date": "2026-05-28",
        "player_id": 44,
        "player_name": "De'Aaron Fox",
        "team_abbr": "SAC",
        "team": "SAC",
        "opponent": "LAL",
        "market_type": "player_points",
        "selection": "over",
        "line": 24.5,
        "sportsbook_line": 24.5,
        "projection": 28.0,
        "model_projection": 28.0,
        "edge": 3.5,
        "directional_edge": 3.5,
        "confidence": 0.75,
        "quality_score": 52.0,
        "odds": -110,
        "american_odds": -110,
        "kelly_projected_skip_reason": "context_high_caution_over",
        "final_elite_rejection_reason": "elite_reject_context_high_caution_over",
        "identity_resolution_category": "valid_current_team_override",
        "player_identity_valid": True,
        "player_identity_status": "valid",
    }

    full_market_df = pd.DataFrame([record])

    # Annotate source identity conflicts on full_market
    annotated_full = annotate_source_identity_conflicts(full_market_df, resolver.summary())
    # Verify full_market has source_identity_conflicted = False
    assert (annotated_full["source_identity_conflicted"] == False).all()

    # Build High-Caution OVER Watchlist
    watchlist = build_high_caution_over_watchlist(annotated_full)
    assert not watchlist.empty
    # The watchlist must retain the identity_resolution_category column!
    assert "identity_resolution_category" in watchlist.columns
    assert (watchlist["identity_resolution_category"] == "valid_current_team_override").all()

    # Build Paper Kelly Simulation
    paper_sim = build_paper_kelly_simulation(
        prediction_date="2026-05-28",
        high_caution_over_watchlist=watchlist,
    )
    assert not paper_sim.empty
    # The paper simulation must retain the identity_resolution_category column!
    assert "identity_resolution_category" in paper_sim.columns
    assert (paper_sim["identity_resolution_category"] == "valid_current_team_override").all()

    # Annotate conflicts on the built watchlist and paper simulation DataFrames
    annotated_watchlist = annotate_source_identity_conflicts(watchlist, resolver.summary())
    annotated_paper = annotate_source_identity_conflicts(paper_sim, resolver.summary())

    # Assert that conflict exposure is 0 (i.e. source_identity_conflicted is False)
    assert (annotated_watchlist["source_identity_conflicted"] == False).all()
    assert (annotated_paper["source_identity_conflicted"] == False).all()

    # Verify that true conflicts still surface and remain blocked
    true_conflict_record = {
        "prediction_date": "2026-05-28",
        "player_id": 44,
        "player_name": "De'Aaron Fox",
        "team_abbr": "SAS",
        "team": "SAS",
        "opponent": "LAL",
        "market_type": "player_points",
        "selection": "over",
        "line": 24.5,
        "sportsbook_line": 24.5,
        "projection": 28.0,
        "model_projection": 28.0,
        "edge": 3.5,
        "directional_edge": 3.5,
        "confidence": 0.75,
        "quality_score": 52.0,
        "odds": -110,
        "american_odds": -110,
        "identity_resolution_category": "historical_stint_mismatch",
        "player_identity_valid": False,
        "player_identity_status": "invalid",
    }
    
    annotated_true = annotate_source_identity_conflicts(pd.DataFrame([true_conflict_record]), resolver.summary())
    assert (annotated_true["source_identity_conflicted"] == True).all()

