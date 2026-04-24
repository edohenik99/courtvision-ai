"""Tests for the market modules (Phase 3 migration).

This test file validates the migrated market logic from runtime_markets.py
to the courtvision/market/ package modules.
"""

from __future__ import annotations

import pandas as pd
import pytest

from courtvision.market import (
    MarketContext,
    MarketEvaluator,
    MarketQualityConfig,
    MarketQualityScorer,
    evaluate_market_context,
    filter_player_markets,
    normalize_market_alias,
    score_market_quality,
)


class TestNormalizeMarketAlias:
    """Test market alias normalization."""

    def test_player_points_aliases(self):
        assert normalize_market_alias("player_points") == "player_points"
        assert normalize_market_alias("points") == "player_points"
        assert normalize_market_alias("pts") == "player_points"
        assert normalize_market_alias("player_pts") == "player_points"

    def test_player_rebounds_aliases(self):
        assert normalize_market_alias("player_rebounds") == "player_rebounds"
        assert normalize_market_alias("rebounds") == "player_rebounds"
        assert normalize_market_alias("reb") == "player_rebounds"

    def test_player_assists_aliases(self):
        assert normalize_market_alias("player_assists") == "player_assists"
        assert normalize_market_alias("assists") == "player_assists"
        assert normalize_market_alias("ast") == "player_assists"

    def test_player_3pt_aliases(self):
        assert normalize_market_alias("player_3pt_made") == "player_3pt_made"
        assert normalize_market_alias("3pm") == "player_3pt_made"
        assert normalize_market_alias("threes") == "player_3pt_made"

    def test_team_markets(self):
        assert normalize_market_alias("moneyline") == "moneyline"
        assert normalize_market_alias("h2h") == "moneyline"
        assert normalize_market_alias("team_total") == "team_total"

    def test_unknown_market(self):
        assert normalize_market_alias("unknown_market") is None
        assert normalize_market_alias("") is None
        assert normalize_market_alias(None) is None


class TestFilterPlayerMarkets:
    """Test player market filtering."""

    def test_empty_dataframe(self):
        result = filter_player_markets(pd.DataFrame(), "LeBron", "LAL")
        assert result.empty

    def test_player_id_match(self):
        game_odds = pd.DataFrame([
            {"player_id": 123, "player_name": "LeBron James", "team": "LAL", "market": "points"},
            {"player_id": 456, "player_name": "Other Player", "team": "BOS", "market": "points"},
        ])
        result = filter_player_markets(game_odds, "LeBron James", "LAL", 123)
        assert len(result) == 1
        assert result.iloc[0]["player_id"] == 123

    def test_exact_name_match(self):
        game_odds = pd.DataFrame([
            {"player_name": "LeBron James", "team": "LAL", "market": "points"},
            {"player_name": "Other Player", "team": "BOS", "market": "points"},
        ])
        result = filter_player_markets(game_odds, "LeBron James", "LAL")
        assert len(result) == 1
        assert result.iloc[0]["player_name"] == "LeBron James"

    def test_last_name_match_with_team(self):
        game_odds = pd.DataFrame([
            {"player_name": "LeBron James", "team": "LAL", "market": "points"},
            {"player_name": "Other James", "team": "BOS", "market": "points"},  # Same last name, different team
        ])
        result = filter_player_markets(game_odds, "LeBron James", "LAL")
        # Should match only LAL player despite shared last name
        assert len(result) == 1
        assert result.iloc[0]["team"] == "LAL"


class TestMarketQualityScorer:
    """Test market quality scoring."""

    def test_default_config(self):
        scorer = MarketQualityScorer()
        assert scorer.config.min_edge == 0.5
        assert scorer.config.min_confidence == 0.35

    def test_custom_config(self):
        config = MarketQualityConfig(min_edge=0.8, min_confidence=0.50)
        scorer = MarketQualityScorer(config)
        assert scorer.config.min_edge == 0.8

    def test_market_type_weights(self):
        scorer = MarketQualityScorer()
        assert scorer.market_type_weight("player_points") == 1.0
        assert scorer.market_type_weight("player_steals") == 0.85
        assert scorer.market_type_weight("moneyline") == 1.0
        assert scorer.market_type_weight("unknown") == 1.0  # default

    def test_quality_bands(self):
        scorer = MarketQualityScorer()
        assert scorer.quality_band(95.0) == "elite"
        assert scorer.quality_band(85.0) == "high"
        assert scorer.quality_band(75.0) == "mid"
        assert scorer.quality_band(65.0) == "low"

    def test_passes_thresholds(self):
        scorer = MarketQualityScorer()
        assert scorer.passes_minimum_thresholds(1.0, 0.70, 80.0) is True
        assert scorer.passes_minimum_thresholds(0.3, 0.70, 80.0) is False  # edge too low
        assert scorer.passes_minimum_thresholds(1.0, 0.30, 80.0) is False  # confidence too low
        assert scorer.passes_minimum_thresholds(1.0, 0.70, 60.0) is False  # quality too low


class TestScoreMarketQuality:
    """Test market quality scoring function."""

    def test_basic_scoring(self):
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
        }
        result = score_market_quality(candidate)
        assert result["market_type"] == "player_points"
        assert result["passes_thresholds"] is True
        # quality_score = 0.75*100 + 3.0*8 = 75 + 24 = 99
        # weighted by 1.0 for player_points = 99, which is "elite" (>=90)
        assert result["quality_band"] == "elite"

    def test_with_precomputed_quality(self):
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
            "quality_score": 85.0,
        }
        result = score_market_quality(candidate)
        assert result["base_score"] == 85.0  # Uses pre-computed

    def test_weighted_scores(self):
        low_weight = score_market_quality({"market_type": "player_steals", "edge": 2.0, "confidence": 0.70})
        high_weight = score_market_quality({"market_type": "player_points", "edge": 2.0, "confidence": 0.70})
        # Same inputs, different weights
        assert low_weight["weighted_score"] < high_weight["weighted_score"]


class TestEvaluateMarketContext:
    """Test comprehensive market context evaluation."""

    def test_eligible_live_market(self):
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
            "quality_score": 85.0,
            "is_live_market": True,
            "synthetic_line": False,
            "odds": -110,
        }
        result = evaluate_market_context(candidate)
        assert result["eligibility"]["eligible"] is True
        assert "quality_pass" in result["eligibility"]["reasons"]
        assert "live_market" in result["eligibility"]["reasons"]

    def test_ineligible_synthetic(self):
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
            "quality_score": 85.0,
            "is_live_market": True,
            "synthetic_line": True,
        }
        result = evaluate_market_context(candidate)
        assert result["eligibility"]["eligible"] is False
        assert "synthetic_line" in result["eligibility"]["disqualifiers"]

    def test_ineligible_not_live(self):
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
            "quality_score": 85.0,
            "is_live_market": False,
            "synthetic_line": False,
        }
        result = evaluate_market_context(candidate)
        assert result["eligibility"]["eligible"] is False
        assert "not_live_market" in result["eligibility"]["disqualifiers"]

    def test_with_injury_context(self):
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
            "quality_score": 85.0,
            "is_live_market": True,
            "team": "LAL",
            "minutes_projection": 35.0,
        }
        # Moderate injury impact (0.25 to 0.5 range triggers moderate_injury_ok)
        injury_context = {
            "teams": {
                "LAL": {"impact_score": 0.30},  # > 0.25 triggers moderate
            },
        }
        result = evaluate_market_context(candidate, injury_context)
        assert result["context"]["injury_impact"] == 0.30
        # Moderate injury (>0.25) is flagged but not disqualifying
        assert "moderate_injury_ok" in result["eligibility"]["reasons"]

    def test_high_injury_disqualifies(self):
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
            "quality_score": 85.0,
            "is_live_market": True,
            "team": "LAL",
        }
        injury_context = {
            "teams": {
                "LAL": {"impact_score": 0.60},  # High impact
            },
        }
        result = evaluate_market_context(candidate, injury_context)
        assert "high_injury_impact" in result["eligibility"]["disqualifiers"]


class TestMarketEvaluator:
    """Test MarketEvaluator class."""

    def test_evaluator_default_config(self):
        evaluator = MarketEvaluator()
        assert evaluator.config.min_edge == 0.5

    def test_evaluator_custom_config(self):
        config = MarketQualityConfig(min_edge=1.0)
        evaluator = MarketEvaluator(config)
        assert evaluator.config.min_edge == 1.0

    def test_evaluator_evaluate(self):
        evaluator = MarketEvaluator()
        candidate = {
            "market_type": "player_points",
            "edge": 3.0,
            "confidence": 0.75,
            "is_live_market": True,
        }
        result = evaluator.evaluate(candidate)
        assert "quality" in result
        assert "eligibility" in result

    def test_evaluator_score_quality(self):
        evaluator = MarketEvaluator()
        candidate = {"market_type": "player_points", "edge": 3.0, "confidence": 0.75}
        result = evaluator.score_quality(candidate)
        assert result["passes_thresholds"] is True

    def test_evaluator_passes_thresholds(self):
        evaluator = MarketEvaluator()
        assert evaluator.passes_thresholds(1.0, 0.70, 80.0) is True
        assert evaluator.passes_thresholds(0.3, 0.70, 80.0) is False

    def test_evaluator_market_weight(self):
        evaluator = MarketEvaluator()
        assert evaluator.market_weight("player_points") == 1.0
        assert evaluator.market_weight("player_steals") == 0.85


class TestMarketContext:
    """Test MarketContext dataclass."""

    def test_context_creation(self):
        ctx = MarketContext(
            market_type="player_points",
            is_live=True,
            has_odds=True,
            sportsbook_line=24.5,
            odds=-110,
            edge=3.0,
            confidence=0.75,
            quality_score=85.0,
            minutes_projection=35.0,
            injury_impact=0.15,
        )
        assert ctx.market_type == "player_points"
        assert ctx.is_live is True
        assert ctx.quality_score == 85.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
