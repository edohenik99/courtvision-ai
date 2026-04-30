"""Tests for the new scoring modules (Phase 2 migration).

This test file validates the migrated scoring logic from runtime_scoring.py
to the courtvision/scoring/ package modules.
"""

from __future__ import annotations

import pytest

from courtvision.scoring import (
    compute_confidence,
    compute_edge,
    compute_penalties,
    compute_selection_score,
    edge_pct_denominator,
    edge_pct_value,
    favorite_bias_factor,
    historical_confidence_multiplier,
    longshot_penalty_points,
    player_points_scoring_stability,
    player_tier_weight,
    projection_realism_penalty_points,
    volatility_penalty_points,
)
from courtvision.scoring.candidate_scoring import CandidateScoringConfig, CandidateScoringPolicy


class TestEdgeFunctions:
    """Test edge calculation functions."""

    def test_edge_pct_denominator_player_points(self):
        """Test floor value for player_points market."""
        assert edge_pct_denominator("player_points", 10.0) == 14.0  # floor applied
        assert edge_pct_denominator("player_points", 20.0) == 20.0  # line used

    def test_edge_pct_denominator_other_markets(self):
        """Test floor values for other market types."""
        assert edge_pct_denominator("player_rebounds", 5.0) == 7.0
        assert edge_pct_denominator("player_assists", 4.0) == 6.0
        assert edge_pct_denominator("player_3pt_made", 1.0) == 2.5
        assert edge_pct_denominator("unknown_market", 8.0) == 10.0  # default floor

    def test_favorite_bias_factor_moneyline(self):
        """Test bias factor for moneyline markets."""
        assert favorite_bias_factor("moneyline", 0.0, 500) == 0.72  # extreme longshot
        assert favorite_bias_factor("moneyline", 0.0, 300) == 0.82  # big underdog
        assert favorite_bias_factor("moneyline", 0.0, 200) == 0.90  # moderate underdog
        assert favorite_bias_factor("moneyline", 0.0, -200) == 1.05  # favorite
        assert favorite_bias_factor("moneyline", 0.0, -100) == 1.0  # near even

    def test_favorite_bias_factor_player_props(self):
        """Test bias factor for player prop markets."""
        assert favorite_bias_factor("player_points", 25.0, None) == 1.05  # high line
        assert favorite_bias_factor("player_points", 10.0, None) == 0.90  # low line
        assert favorite_bias_factor("player_points", 15.0, None) == 1.0  # mid line

    def test_edge_pct_value_moneyline(self):
        """Test edge percentage for moneyline."""
        assert edge_pct_value("moneyline", 0.08, 0.0) == 8.0  # 8% edge
        assert edge_pct_value("moneyline", 0.15, 0.0) == 15.0  # 15% edge

    def test_edge_pct_value_player_props(self):
        """Test edge percentage for player props (normalized by line)."""
        # edge_abs=3.0, line=24.0 -> (3.0/24.0)*100 = 12.5%
        assert edge_pct_value("player_points", 3.0, 24.0) == pytest.approx(12.5, abs=0.1)
        # edge_abs=2.0, line=8.0 (rebounds floor is 7.0) -> (2.0/8.0)*100 = 25%
        assert edge_pct_value("player_rebounds", 2.0, 8.0) == 25.0

    def test_compute_edge_complete(self):
        """Test complete edge computation."""
        # For player_points with line=24.5 (>=20), bias factor = 1.05
        result = compute_edge("player_points", 3.0, 24.5, -110)
        assert "bias_factor" in result
        assert "adjusted_edge_abs" in result
        assert "edge_pct" in result
        # For player_points with line 24.5, bias factor should be 1.05 (high line)
        assert result["bias_factor"] == 1.05
        assert result["adjusted_edge_abs"] == 3.15  # 3.0 * 1.05

    def test_compute_edge_moneyline(self):
        """Test edge computation for moneyline."""
        # For moneyline with -110 odds, bias factor = 1.0 (near even)
        result = compute_edge("moneyline", 0.08, 0.0, -110)
        assert result["bias_factor"] == 1.0
        assert result["adjusted_edge_abs"] == 0.08  # 0.08 * 1.0


class TestPenaltyFunctions:
    """Test penalty calculation functions."""

    def test_longshot_penalty_points(self):
        """Test longshot penalty based on odds."""
        assert longshot_penalty_points(800) == 16.0  # extreme longshot
        assert longshot_penalty_points(500) == 12.0  # big longshot
        assert longshot_penalty_points(300) == 8.0  # moderate longshot
        assert longshot_penalty_points(200) == 4.0  # small longshot
        assert longshot_penalty_points(100) == 0.0  # favorite
        assert longshot_penalty_points(-150) == 0.0  # negative odds (favorite)
        assert longshot_penalty_points(None) == 0.0  # None odds

    def test_volatility_penalty_low_minutes(self):
        """Test volatility penalty for low minutes."""
        row = {
            "market_type": "player_points",
            "minutes_avg": 20.0,
            "minutes_recent": 18.0,
        }
        penalty = volatility_penalty_points(row)
        assert penalty >= 12.0  # Heavy penalty for < 22 min

    def test_volatility_penalty_moderate_minutes(self):
        """Test volatility penalty for moderate minutes."""
        row = {
            "market_type": "player_points",
            "minutes_avg": 24.0,
            "minutes_recent": 23.0,
        }
        penalty = volatility_penalty_points(row)
        assert 5.0 <= penalty <= 12.0  # Moderate penalty for 22-26 min

    def test_volatility_penalty_high_minutes(self):
        """Test volatility penalty for high minutes."""
        row = {
            "market_type": "player_points",
            "minutes_avg": 34.0,
            "minutes_recent": 33.0,
        }
        penalty = volatility_penalty_points(row)
        assert penalty < 5.0  # Low penalty for stable high minutes

    def test_volatility_penalty_with_injury(self):
        """Test volatility penalty with high injury impact."""
        row = {
            "market_type": "player_points",
            "minutes_avg": 30.0,
            "minutes_recent": 30.0,
            "injury_impact_score": 0.35,
        }
        penalty = volatility_penalty_points(row)
        assert penalty > 0  # Should have injury-related penalty

    def test_volatility_penalty_non_player_market(self):
        """Test no volatility penalty for non-player markets."""
        row = {
            "market_type": "moneyline",
            "minutes_avg": 20.0,
        }
        assert volatility_penalty_points(row) == 0.0

    def test_projection_realism_penalty_high_edge_low_confidence(self):
        """Test penalty for high edge with low confidence."""
        row = {"market_type": "player_points"}
        # High edge (7.0) with low confidence (0.60) should trigger penalty
        penalty = projection_realism_penalty_points(row, 7.0, 0.60)
        assert penalty > 0

    def test_projection_realism_penalty_moderate_mismatch(self):
        """Test penalty for moderate edge/confidence mismatch."""
        row = {"market_type": "player_points"}
        penalty = projection_realism_penalty_points(row, 5.0, 0.58)
        assert penalty > 0

    def test_projection_realism_penalty_no_penalty(self):
        """Test no penalty for reasonable combinations."""
        row = {"market_type": "player_points"}
        penalty = projection_realism_penalty_points(row, 3.0, 0.70)
        assert penalty == 0.0

    def test_projection_realism_penalty_moneyline_exempt(self):
        """Test moneyline exempt from realism penalty."""
        row = {"market_type": "moneyline"}
        penalty = projection_realism_penalty_points(row, 10.0, 0.50)
        assert penalty == 0.0

    def test_compute_penalties_complete(self):
        """Test complete penalty computation."""
        row = {
            "market_type": "player_points",
            "odds": 300,
            "minutes_avg": 20.0,
            "minutes_recent": 18.0,
        }
        penalties = compute_penalties(row, 5.0, 0.60)
        assert "longshot_penalty" in penalties
        assert "volatility_penalty" in penalties
        assert "realism_penalty" in penalties
        assert "total_penalty" in penalties
        assert penalties["total_penalty"] <= 12.0  # capped


class TestConfidenceFunctions:
    """Test confidence computation functions."""

    def test_player_points_scoring_stability_direct_ratio(self):
        """Test stability with direct recent_form_ratio."""
        row = {"recent_form_ratio": 0.85}
        stability = player_points_scoring_stability(row, 25.0)
        assert stability == 0.85

    def test_player_points_scoring_stability_recent_vs_season(self):
        """Test stability calculated from recent vs season avg."""
        row = {"recent_avg": 22.0, "season_avg": 25.0}
        stability = player_points_scoring_stability(row, 24.0)
        # drift = |22-25|/max(25,24,12) = 3/25 = 0.12
        # stability = 1 - min(1, 0.12*1.35) = 1 - 0.162 = 0.838
        assert stability == pytest.approx(0.838, abs=0.01)

    def test_player_points_scoring_stability_model_projection(self):
        """Test stability from model projection drift."""
        row = {"model_projection": 26.0}
        stability = player_points_scoring_stability(row, 24.0)
        # drift = |26-24|/max(26,24,12) = 2/26 = 0.077
        # stability = 0.62 + min(0.22, 0.077*0.35) = 0.62 + 0.027 = 0.647
        assert stability == pytest.approx(0.647, abs=0.01)

    def test_historical_confidence_multiplier_moneyline(self):
        """Test multiplier for moneyline."""
        row = {
            "market_type": "moneyline",
            "confidence": 0.70,
            "odds": -110,
        }
        multiplier = historical_confidence_multiplier(row)
        # 0.78 + (0.70-0.50)*0.45 = 0.78 + 0.09 = 0.87
        assert 0.60 <= multiplier <= 1.02

    def test_historical_confidence_multiplier_player_points(self):
        """Test multiplier for player_points with full context."""
        row = {
            "market_type": "player_points",
            "confidence": 0.70,
            "minutes_avg": 34.0,
            "minutes_recent": 33.0,
            "edge_abs": 3.0,
            "sportsbook_line": 25.5,
            "recent_form_ratio": 0.90,
        }
        multiplier = historical_confidence_multiplier(row)
        assert 0.55 <= multiplier <= 1.02

    def test_historical_confidence_multiplier_other_player_markets(self):
        """Test multiplier for other player markets."""
        row = {
            "market_type": "player_rebounds",
            "confidence": 0.65,
            "minutes_avg": 32.0,
            "minutes_recent": 31.0,
        }
        multiplier = historical_confidence_multiplier(row)
        assert 0.55 <= multiplier <= 1.05

    def test_compute_confidence_edge_boost(self):
        """Test confidence with edge-based boost."""
        row = {"confidence": 0.60}
        result = compute_confidence(row, 5.0, "player_points", 30.0)
        # edge > 4.5, so boost = 0.02
        assert result["edge_boost"] == 0.02
        assert result["adjusted_confidence"] == 0.62

    def test_compute_confidence_high_edge_boost(self):
        """Test confidence with high edge boost."""
        row = {"confidence": 0.60}
        result = compute_confidence(row, 7.0, "player_points", 30.0)
        # edge > 6.0, so boost = 0.04
        assert result["edge_boost"] == 0.04
        assert result["adjusted_confidence"] == 0.64

    def test_compute_confidence_minutes_floor(self):
        """Test confidence minutes floor for player props."""
        row = {"confidence": 0.50}
        result = compute_confidence(row, 2.0, "player_points", 30.0)
        # minutes >= 28, so floor = 0.58
        assert result["adjusted_confidence"] == 0.58


class TestCandidateScoringFunctions:
    """Test main candidate scoring functions."""

    def test_player_tier_weight_star_player(self):
        """Test tier weight for star players (34+ min)."""
        assert player_tier_weight("player_points", 36.0) == 1.15

    def test_player_tier_weight_starter(self):
        """Test tier weight for starters (28-34 min)."""
        assert player_tier_weight("player_points", 30.0) == 1.05

    def test_player_tier_weight_rotation(self):
        """Test tier weight for rotation players (20-28 min)."""
        assert player_tier_weight("player_points", 24.0) == 0.95

    def test_player_tier_weight_bench(self):
        """Test tier weight for bench players (< 20 min)."""
        assert player_tier_weight("player_points", 15.0) == 0.75

    def test_player_tier_weight_non_player_market(self):
        """Test neutral weight for non-player markets."""
        assert player_tier_weight("moneyline", 0.0) == 1.0
        assert player_tier_weight("team_total", 0.0) == 1.0

    def test_compute_selection_score_complete(self):
        """Test complete selection score computation."""
        row = {
            "market_type": "player_points",
            "selection": "OVER",
            "sportsbook_line": 24.5,
            "edge": 3.0,
            "edge_abs": 3.0,
            "confidence": 0.70,
            "odds": -110,
            "minutes_avg": 34.0,
            "minutes_recent": 33.0,
        }
        scores = compute_selection_score(row)
        assert "edge_pct" in scores
        assert "player_tier_weight" in scores
        assert "quality_score" in scores
        assert "elite_rank_score" in scores
        assert scores["quality_score"] > 0

    def test_compute_selection_score_under_bias(self):
        """Test under selection has bias multiplier applied."""
        row = {
            "market_type": "player_points",
            "selection": "UNDER",
            "sportsbook_line": 24.5,
            "edge": -3.0,
            "edge_abs": 3.0,
            "confidence": 0.70,
            "odds": -110,
            "minutes_avg": 34.0,
            "minutes_recent": 33.0,
        }
        scores = compute_selection_score(row)
        assert scores["under_bias_multiplier"] == 0.95

    def test_compute_selection_score_moneyline(self):
        """Test moneyline scoring (no under bias)."""
        row = {
            "market_type": "moneyline",
            "selection": "ML",
            "sportsbook_line": 0.0,
            "edge": 0.08,
            "edge_abs": 0.08,
            "confidence": 0.75,
            "odds": 110,
        }
        scores = compute_selection_score(row)
        assert scores["under_bias_multiplier"] == 1.0  # No bias for moneyline


class TestCandidateScoringPolicy:
    """Test CandidateScoringPolicy class."""

    def test_policy_default_config(self):
        """Test policy with default config."""
        from courtvision.config import EliteThresholds

        policy = CandidateScoringPolicy()
        elite = EliteThresholds.default()
        assert policy.config.elite_min_confidence == elite.confidence
        assert policy.config.elite_min_quality_score == elite.quality_score

    def test_policy_custom_config(self):
        """Test policy with custom config."""
        config = CandidateScoringConfig(elite_min_confidence=0.70, elite_min_quality_score=85.0)
        policy = CandidateScoringPolicy(config=config)
        assert policy.config.elite_min_confidence == 0.70
        assert policy.config.elite_min_quality_score == 85.0

    def test_is_elite_candidate_core_pass(self):
        """Test elite qualification for strong candidate."""
        config = CandidateScoringConfig()
        policy = CandidateScoringPolicy(config=config)
        row = {
            "market_type": "player_points",
            "confidence": 0.75,
            "quality_score": 85.0,
            "edge": 3.0,
            "edge_abs": 3.0,
            "synthetic_line": False,
            "is_live_market": True,
            "minutes_avg": 35.0,
            "minutes_recent": 34.0,
            "qualification_gate_mode": "core_pass",
        }
        assert policy.is_elite_candidate(row) is True

    def test_is_elite_candidate_synthetic_rejected(self):
        """Test synthetic lines rejected from elite."""
        config = CandidateScoringConfig()
        policy = CandidateScoringPolicy(config=config)
        row = {
            "market_type": "player_points",
            "confidence": 0.80,
            "quality_score": 90.0,
            "edge": 4.0,
            "edge_abs": 4.0,
            "synthetic_line": True,
            "is_live_market": True,
            "minutes_avg": 35.0,
            "minutes_recent": 34.0,
        }
        assert policy.is_elite_candidate(row) is False

    def test_is_elite_candidate_low_minutes_rejected(self):
        """Test low minutes players rejected from elite."""
        config = CandidateScoringConfig()
        policy = CandidateScoringPolicy(config=config)
        row = {
            "market_type": "player_points",
            "confidence": 0.75,
            "quality_score": 85.0,
            "edge": 3.0,
            "edge_abs": 3.0,
            "synthetic_line": False,
            "is_live_market": True,
            "minutes_avg": 20.0,  # Below 24.0 threshold
            "minutes_recent": 19.0,
        }
        assert policy.is_elite_candidate(row) is False

    def test_is_elite_candidate_moneyline_longshot_rejected(self):
        """Test extreme moneyline longshots rejected."""
        config = CandidateScoringConfig()
        policy = CandidateScoringPolicy(config=config)
        row = {
            "market_type": "moneyline",
            "confidence": 0.75,
            "quality_score": 85.0,
            "edge": 0.10,
            "edge_abs": 0.10,
            "synthetic_line": False,
            "is_live_market": True,
            "odds": 400,  # Above 300 threshold
        }
        assert policy.is_elite_candidate(row) is False

    def test_apply_scoring_metadata(self):
        """Test scoring metadata application."""
        policy = CandidateScoringPolicy()
        row = {
            "market_type": "player_points",
            "selection": "OVER",
            "sportsbook_line": 24.5,
            "edge": 3.0,
            "edge_abs": 3.0,
            "confidence": 0.70,
            "odds": -110,
            "minutes_avg": 34.0,
            "minutes_recent": 33.0,
        }
        enriched = policy.apply_scoring_metadata(row)
        assert "edge_pct" in enriched
        assert "quality_score" in enriched
        assert "player_tier_weight" in enriched
        assert "elite_rank_score" in enriched


class TestScoringModuleExports:
    """Test that all expected functions are exported."""

    def test_all_functions_importable(self):
        """Verify all expected functions can be imported from scoring package."""
        from courtvision.scoring import __all__ as scoring_all

        expected_exports = [
            "compute_edge",
            "edge_pct_denominator",
            "edge_pct_value",
            "favorite_bias_factor",
            "compute_confidence",
            "historical_confidence_multiplier",
            "player_points_scoring_stability",
            "compute_penalties",
            "longshot_penalty_points",
            "projection_realism_penalty_points",
            "volatility_penalty_points",
            "compute_selection_score",
            "CandidateScoringConfig",
            "CandidateScoringPolicy",
            "player_tier_weight",
        ]
        for export in expected_exports:
            assert export in scoring_all


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
