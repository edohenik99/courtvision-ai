"""Tests for the injury modules (Phase 3 migration).

This test file validates the migrated injury logic from courtvision_ai.py
to the courtvision/injuries/ package modules.
"""

from __future__ import annotations

import pandas as pd
import pytest

from courtvision.injuries import (
    InjuryContextConfig,
    InjuryEngine,
    apply_player_injury_context,
    build_injury_context,
    compute_injury_volatility,
    compute_recent_form_ratio,
    injury_status_weight,
)


class TestInjuryStatusWeight:
    """Test injury status weight calculations."""

    def test_out_status(self):
        assert injury_status_weight("Out") == 1.0
        assert injury_status_weight("out") == 1.0
        assert injury_status_weight("inactive") == 1.0

    def test_doubtful_status(self):
        assert injury_status_weight("Doubtful") == 0.75
        assert injury_status_weight("doubtful") == 0.75

    def test_questionable_status(self):
        assert injury_status_weight("Questionable") == 0.35

    def test_probable_status(self):
        assert injury_status_weight("Probable") == 0.15

    def test_day_to_day_status(self):
        assert injury_status_weight("Day To Day") == 0.35
        assert injury_status_weight("day-to-day") == 0.35

    def test_unknown_status(self):
        assert injury_status_weight("Unknown") == 0.25
        assert injury_status_weight("") == 0.0
        assert injury_status_weight(None) == 0.0

    def test_custom_config(self):
        config = InjuryContextConfig(out_weight=0.9, doubtful_weight=0.6)
        assert injury_status_weight("out", config) == 0.9
        assert injury_status_weight("doubtful", config) == 0.6


class TestBuildInjuryContext:
    """Test injury context building."""

    def test_empty_dataframes(self):
        context = build_injury_context(
            pd.DataFrame(),
            pd.DataFrame(),
            {"LAL", "BOS"},
        )
        assert context["players"] == {}
        assert context["teams"] == {}
        assert context["active_injuries"].empty

    def test_basic_context_building(self):
        injuries = pd.DataFrame([
            {
                "player_name": "LeBron James",
                "team_abbr": "LAL",
                "status": "out",
            },
        ])
        baselines = pd.DataFrame([
            {
                "player_name": "LeBron James",
                "team_abbr": "LAL",
                "pts_avg": 25.0,
                "reb_avg": 8.0,
                "ast_avg": 8.0,
                "min_avg": 35.0,
            },
        ])
        context = build_injury_context(injuries, baselines, {"LAL"})

        assert "players" in context
        assert "teams" in context
        assert "LAL" in context["teams"]

        team_ctx = context["teams"]["LAL"]
        assert "impact_score" in team_ctx
        assert "usage_boost" in team_ctx
        assert team_ctx["affected_players"] == 1

    def test_team_filtering(self):
        injuries = pd.DataFrame([
            {"player_name": "Player A", "team_abbr": "LAL", "status": "out"},
            {"player_name": "Player B", "team_abbr": "BOS", "status": "out"},
        ])
        baselines = pd.DataFrame([
            {"player_name": "Player A", "team_abbr": "LAL", "pts_avg": 20.0, "min_avg": 30.0},
            {"player_name": "Player B", "team_abbr": "BOS", "pts_avg": 15.0, "min_avg": 25.0},
        ])
        context = build_injury_context(injuries, baselines, {"LAL"})

        assert "LAL" in context["teams"]
        assert "BOS" not in context["teams"]

    def test_impact_score_calculation(self):
        injuries = pd.DataFrame([
            {
                "player_name": "Star Player",
                "team_abbr": "LAL",
                "status": "out",
            },
        ])
        baselines = pd.DataFrame([
            {
                "player_name": "Star Player",
                "team_abbr": "LAL",
                "pts_avg": 30.0,
                "reb_avg": 10.0,
                "ast_avg": 10.0,
                "stl_avg": 2.0,
                "blk_avg": 2.0,
                "min_avg": 38.0,
            },
        ])
        context = build_injury_context(injuries, baselines, {"LAL"})

        team_ctx = context["teams"]["LAL"]
        # impact_score = min(1.0, (30/35) + (38/120) + (4/6))
        # = min(1.0, 0.857 + 0.317 + 0.667) = 1.0 (capped)
        assert team_ctx["impact_score"] == 1.0


class TestApplyPlayerInjuryContext:
    """Test injury context application to projections."""

    def test_no_context(self):
        proj, conf, meta = apply_player_injury_context(
            {"player_name": "Test"}, "LAL", "BOS", "player_points",
            25.0, 0.70, None,
        )
        assert proj == 25.0
        assert conf == 0.70
        assert meta["injury_impact_score"] == 0.0
        assert meta["injury_notes"] == ""

    def test_own_injury_reduction(self):
        injury_context = {
            "players": {
                "LeBron James:LAL": {
                    "status": "out",
                    "injury_weight": 1.0,
                    "availability_multiplier": 0.0,
                },
            },
            "teams": {},
        }
        proj, conf, meta = apply_player_injury_context(
            {"player_name": "LeBron James"}, "LAL", "BOS", "player_points",
            25.0, 0.70, injury_context,
        )
        # Out player: projection * 0.0, confidence * max(0.2, availability)
        # conf = 0.70 * max(0.2, 0.0) = 0.70 * 0.2 = 0.14, but capped at 0.25 min
        # Actually: max(0.25, min(0.98, 0.70 * 0.2)) = 0.25 (floor)
        assert proj == 0.0
        assert conf == 0.25  # floored at min_confidence
        assert meta["injury_status"] == "out"
        assert "player_status:out" in meta["injury_notes"]

    def test_teammate_absence_boost(self):
        injury_context = {
            "players": {},
            "teams": {
                "LAL": {
                    "impact_score": 0.30,
                    "usage_boost": 0.10,
                    "rebound_boost": 0.05,
                    "defensive_event_boost": 0.03,
                },
            },
        }
        proj, conf, meta = apply_player_injury_context(
            {"player_name": "Anthony Davis", "min_avg": 34.0},
            "LAL", "BOS", "player_points",
            25.0, 0.70, injury_context,
        )
        # Should get usage boost
        assert proj > 25.0
        assert conf > 0.70
        assert meta["team_injury_impact"] == 0.30

    def test_opponent_absence_boost(self):
        injury_context = {
            "players": {},
            "teams": {
                "BOS": {
                    "impact_score": 0.25,
                    "defense_penalty": 0.08,
                    "rebound_boost": 0.04,
                    "rim_penalty": 0.03,
                },
            },
        }
        proj, conf, meta = apply_player_injury_context(
            {"player_name": "LeBron James", "min_avg": 35.0},
            "LAL", "BOS", "player_points",
            25.0, 0.70, injury_context,
        )
        # Points get boost from opponent defense penalty
        assert proj > 25.0
        assert meta["opponent_injury_impact"] == 0.25

    def test_injury_boost_cap(self):
        injury_context = {
            "players": {},
            "teams": {
                "LAL": {"impact_score": 0.50, "usage_boost": 0.20},
            },
        }
        config = InjuryContextConfig(player_injury_boost_cap=0.15)
        proj, conf, meta = apply_player_injury_context(
            {"player_name": "Test", "min_avg": 30.0},
            "LAL", "BOS", "player_points",
            20.0, 0.70, injury_context, config,
        )
        # Should be capped at 15% boost
        assert proj <= 23.0  # 20 * 1.15


class TestInjuryVolatility:
    """Test injury volatility calculations."""

    def test_recent_form_ratio_with_recent_avg(self):
        result = compute_recent_form_ratio(
            {"pts_recent": 28.0, "pts_avg": 25.0},
            26.0,
        )
        # anchor = max(25, 26, 12) = 26
        # ratio = 28 / 26 = 1.077
        assert 1.0 <= result <= 1.5

    def test_recent_form_ratio_without_recent(self):
        result = compute_recent_form_ratio(
            {"pts_avg": 25.0},
            26.0,
        )
        # Uses season_avg since no recent_avg
        assert result >= 0.0

    def test_injury_volatility_complete(self):
        result = compute_injury_volatility(
            {"pts_recent": 28.0, "pts_avg": 25.0, "min_avg": 34.0},
            26.0,
        )
        assert "recent_form_ratio" in result
        assert "independent_support" in result
        # High minutes + good form = support > 0
        assert result["independent_support"] > 0


class TestInjuryEngine:
    """Test InjuryEngine class."""

    def test_engine_default_config(self):
        engine = InjuryEngine()
        assert engine.config.out_weight == 1.0
        assert engine.config.doubtful_weight == 0.75

    def test_engine_custom_config(self):
        config = InjuryContextConfig(out_weight=0.9)
        engine = InjuryEngine(config)
        assert engine.config.out_weight == 0.9

    def test_engine_status_weight(self):
        engine = InjuryEngine()
        assert engine.status_weight("out") == 1.0
        assert engine.status_weight("probable") == 0.15

    def test_engine_build_context(self):
        engine = InjuryEngine()
        injuries = pd.DataFrame([
            {"player_name": "Test", "team_abbr": "LAL", "status": "out"},
        ])
        baselines = pd.DataFrame([
            {"player_name": "Test", "team_abbr": "LAL", "pts_avg": 20.0, "min_avg": 30.0},
        ])
        context = engine.build_context(injuries, baselines, {"LAL"})
        assert "LAL" in context["teams"]

    def test_engine_apply_context(self):
        engine = InjuryEngine()
        injury_context = {
            "players": {},
            "teams": {"LAL": {"impact_score": 0.20, "usage_boost": 0.05}},
        }
        proj, conf, meta = engine.apply_context(
            {"player_name": "Test", "min_avg": 30.0},
            "LAL", "BOS", "player_points",
            25.0, 0.70, injury_context,
        )
        assert meta["team_injury_impact"] == 0.20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
