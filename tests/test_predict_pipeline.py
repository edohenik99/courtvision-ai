"""Tests for the prediction pipeline (Phase 4 migration).

This test file validates the package-owned prediction pipeline
delegates correctly to specialized modules.
"""

from __future__ import annotations

import logging
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from courtvision.pipeline import (
    PredictionConfig,
    PredictionPipeline,
    PredictionResult,
    run_prediction_pipeline,
)
from courtvision.scoring import CandidateScoringPolicy
from courtvision.injuries import InjuryEngine
from courtvision.market import MarketEvaluator


class TestPredictionConfig:
    """Test prediction configuration."""

    def test_default_config(self):
        config = PredictionConfig(prediction_date="2024-01-15")
        assert config.prediction_date == "2024-01-15"
        assert config.out_dir == "outputs"
        assert config.min_edge == 0.5
        assert config.min_confidence == 0.35
        assert config.enable_injury_context is True
        assert config.enable_market_quality is True

    def test_custom_config(self):
        config = PredictionConfig(
            prediction_date="2024-01-15",
            min_edge=0.8,
            min_confidence=0.50,
            verbose_outputs=True,
        )
        assert config.min_edge == 0.8
        assert config.min_confidence == 0.50
        assert config.verbose_outputs is True


class TestPredictionResult:
    """Test prediction result container."""

    def test_empty_result(self):
        result = PredictionResult(prediction_date="2024-01-15")
        assert result.prediction_date == "2024-01-15"
        assert result.selected_props.empty
        assert result.elite_props.empty

    def test_result_to_dict(self):
        result = PredictionResult(
            prediction_date="2024-01-15",
            summary={"games_count": 5},
        )
        d = result.to_dict()
        assert d["prediction_date"] == "2024-01-15"
        assert d["summary"]["games_count"] == 5
        assert "selected_props" in d


class TestPredictionPipeline:
    """Test prediction pipeline orchestration."""

    def test_pipeline_initialization(self):
        config = PredictionConfig(prediction_date="2024-01-15")
        pipeline = PredictionPipeline(config)
        assert pipeline.config == config
        assert pipeline.scoring_policy is not None
        assert pipeline.injury_engine is not None
        assert pipeline.market_evaluator is not None

    def test_pipeline_with_custom_components(self):
        config = PredictionConfig(prediction_date="2024-01-15")
        scoring = CandidateScoringPolicy()
        injury = InjuryEngine()
        market = MarketEvaluator()

        pipeline = PredictionPipeline(
            config=config,
            scoring_policy=scoring,
            injury_engine=injury,
            market_evaluator=market,
        )
        assert pipeline.scoring_policy == scoring
        assert pipeline.injury_engine == injury
        assert pipeline.market_evaluator == market

    def test_empty_data_returns_empty_result(self):
        config = PredictionConfig(prediction_date="2024-01-15")
        pipeline = PredictionPipeline(config)

        games = pd.DataFrame()
        odds = pd.DataFrame()
        baselines = pd.DataFrame()

        result = pipeline.run(games, odds, baselines)

        assert result.prediction_date == "2024-01-15"
        assert result.selected_props.empty
        assert result.summary["candidate_count"] == 0
        assert result.summary["market_quality_status"] == "no_candidates"

    def test_basic_prediction_flow(self):
        """Test a basic prediction flow with minimal data."""
        config = PredictionConfig(
            prediction_date="2024-01-15",
            min_edge=0.5,
        )
        pipeline = PredictionPipeline(config, logger=logging.getLogger("test"))

        # Setup minimal test data
        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
            "game_date": "2024-01-15",
        }])

        odds = pd.DataFrame([{
            "game_id": 1,
            "player_name": "LeBron James",
            "market": "player_points",
            "line": 25.5,
            "odds": -110,
            "is_live": True,
        }])

        baselines = pd.DataFrame([{
            "player_name": "LeBron James",
            "team_abbr": "LAL",
            "player_id": 123,
            "pts_avg": 27.0,
            "pts_recent": 28.0,
            "reb_avg": 8.0,
            "ast_avg": 8.0,
            "min_avg": 35.0,
        }])

        result = pipeline.run(games, odds, baselines)

        # Verify result structure
        assert result.prediction_date == "2024-01-15"
        assert result.summary["games_count"] == 1
        assert result.summary["odds_count"] == 1
        assert "candidate_count" in result.summary
        assert "market_quality_status" in result.summary

    def test_injury_context_integration(self):
        """Test that injury context is built and applied."""
        config = PredictionConfig(
            prediction_date="2024-01-15",
            enable_injury_context=True,
        )
        pipeline = PredictionPipeline(config)

        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
        }])

        odds = pd.DataFrame([{
            "game_id": 1,
            "player_name": "Anthony Davis",
            "market": "player_points",
            "line": 22.5,
            "odds": -110,
            "is_live": True,
        }])

        baselines = pd.DataFrame([{
            "player_name": "Anthony Davis",
            "team_abbr": "LAL",
            "player_id": 456,
            "pts_avg": 25.0,
            "min_avg": 34.0,
        }])

        injuries = pd.DataFrame([{
            "player_name": "LeBron James",
            "team_abbr": "LAL",
            "status": "out",
        }])

        result = pipeline.run(games, odds, baselines, injuries=injuries)

        # Should indicate injury context was built
        assert result.summary["injury_context_built"] is True

    def test_none_injury_context_is_normalized(self, caplog):
        """Pipeline should never expose None injury_context downstream."""
        class NullInjuryEngine:
            def build_context(self, **kwargs):
                return None

            def apply_context(
                self,
                player_row,
                team_abbr,
                opp_abbr,
                market_type,
                projection,
                confidence,
                injury_context,
            ):
                return projection, confidence, {}

        config = PredictionConfig(
            prediction_date="2024-01-15",
            enable_injury_context=True,
        )
        logger = logging.getLogger("test_predict_pipeline.injury_context")
        pipeline = PredictionPipeline(config, injury_engine=NullInjuryEngine(), logger=logger)

        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
        }])

        odds = pd.DataFrame([{
            "game_id": 1,
            "player_name": "Anthony Davis",
            "market": "player_points",
            "line": 22.5,
            "odds": -110,
            "is_live": True,
        }])

        baselines = pd.DataFrame([{
            "player_name": "Anthony Davis",
            "team_abbr": "LAL",
            "player_id": 456,
            "pts_avg": 25.0,
            "min_avg": 34.0,
        }])

        injuries = pd.DataFrame([{
            "player_name": "LeBron James",
            "team_abbr": "LAL",
            "status": "out",
        }])

        with caplog.at_level(logging.INFO, logger=logger.name):
            result = pipeline.run(games, odds, baselines, injuries=injuries)

        assert result.injury_context == {
            "teams": {},
            "players": {},
            "metadata": {},
        }
        assert result.summary["injury_context_built"] is False
        assert "injury_context_normalized teams=0 players=0 source=injury_engine_none" in caplog.text

    @pytest.mark.parametrize(
        ("odds_value", "expected_odds"),
        [
            (None, -110),
            ("", -110),
            (float("nan"), -110),
            ("-105", -105),
            (-115, -115),
            (105.0, 105),
        ],
    )
    def test_candidate_odds_normalization_handles_nan_like_values(
        self,
        odds_value,
        expected_odds,
    ):
        """Candidate construction should never crash on missing or NaN odds."""
        config = PredictionConfig(
            prediction_date="2024-01-15",
            synthetic_odds_default=-110,
        )
        pipeline = PredictionPipeline(config)

        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
        }])

        odds = pd.DataFrame([{
            "game_id": 1,
            "player_name": "LeBron James",
            "market": "player_points",
            "line": 25.5,
            "odds": odds_value,
            "is_live": True,
        }])

        baselines = pd.DataFrame([{
            "player_name": "LeBron James",
            "team_abbr": "LAL",
            "player_id": 123,
            "pts_avg": 27.0,
            "pts_recent": 28.0,
            "reb_avg": 8.0,
            "ast_avg": 8.0,
            "min_avg": 35.0,
        }])

        result = pipeline.run(games, odds, baselines)

        player_points = result.merged_market_props[
            result.merged_market_props["market_type"] == "player_points"
        ].copy()
        assert not player_points.empty
        assert int(player_points.iloc[0]["odds"]) == expected_odds

    def test_raw_prop_type_is_preserved_through_candidate_row(self):
        """Raw provider prop type should survive into candidate/output rows."""
        config = PredictionConfig(prediction_date="2024-01-15")
        pipeline = PredictionPipeline(config)

        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
        }])
        odds = pd.DataFrame([{
            "game_id": 1,
            "player_id": 123,
            "player_name": "LeBron James",
            "raw_prop_type": "points",
            "raw_market_type": "over_under",
            "market_type": "player_points",
            "line": 25.5,
            "odds": -110,
            "selection": "over",
            "is_live": True,
        }])
        baselines = pd.DataFrame([{
            "player_name": "LeBron James",
            "team_abbr": "LAL",
            "player_id": 123,
            "pts_avg": 27.0,
            "pts_recent": 28.0,
            "min_avg": 35.0,
        }])

        result = pipeline.run(games, odds, baselines)

        assert not result.merged_market_props.empty
        row = result.merged_market_props.iloc[0]
        assert row["market_type"] == "player_points"
        assert row["raw_prop_type"] == "points"
        assert row["raw_market_type"] == "over_under"

    def test_non_points_readiness_gates_and_diagnostics_keep_elite_points_only(self):
        out_dir = Path("test_outputs") / "market_readiness_pipeline"
        shutil.rmtree(out_dir, ignore_errors=True)
        config = PredictionConfig(prediction_date="2024-01-15", out_dir=str(out_dir))
        pipeline = PredictionPipeline(config)

        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
        }])
        odds = pd.DataFrame([
            {
                "game_id": 1,
                "player_id": 123,
                "player_name": "Points Star",
                "raw_prop_type": "points",
                "raw_market_type": "over_under",
                "market_type": "player_points",
                "line": 20.5,
                "odds": -110,
                "selection": "over",
                "is_live": True,
            },
            {
                "game_id": 1,
                "player_id": 124,
                "player_name": "Combo Star",
                "raw_prop_type": "points_rebounds",
                "raw_market_type": "over_under",
                "market_type": "player_points_rebounds",
                "line": 28.5,
                "odds": -110,
                "selection": "over",
                "is_live": True,
            },
            {
                "game_id": 1,
                "player_id": 125,
                "player_name": "Low Minutes Rebounder",
                "raw_prop_type": "rebounds",
                "raw_market_type": "over_under",
                "market_type": "player_rebounds",
                "line": 5.5,
                "odds": -110,
                "selection": "over",
                "is_live": True,
            },
        ])
        baselines = pd.DataFrame([
            {
                "player_name": "Points Star",
                "team_abbr": "LAL",
                "player_id": 123,
                "pts_avg": 24.0,
                "reb_avg": 4.0,
                "ast_avg": 5.0,
                "min_avg": 34.0,
            },
            {
                "player_name": "Combo Star",
                "team_abbr": "LAL",
                "player_id": 124,
                "pts_avg": 22.0,
                "reb_avg": 10.0,
                "ast_avg": 4.0,
                "min_avg": 30.0,
            },
            {
                "player_name": "Low Minutes Rebounder",
                "team_abbr": "LAL",
                "player_id": 125,
                "pts_avg": 8.0,
                "reb_avg": 8.0,
                "ast_avg": 1.0,
                "min_avg": 20.0,
            },
        ])

        result = pipeline.run(games, odds, baselines)

        assert set(result.elite_props["market_type"]) == {"player_points"}
        assert "player_points_rebounds" in set(result.full_market_props["market_type"])
        assert "player_rebounds" not in set(result.full_market_props["market_type"])

        readiness_path = out_dir / "runtime" / "diagnostics" / "market_performance_readiness_2024-01-15.json"
        payload = json.loads(readiness_path.read_text(encoding="utf-8"))
        assert payload["elite_locked_to"] == ["player_points"]
        assert payload["kelly_locked_to"] == ["player_points"]
        assert payload["full_market_by_market_type"]["player_points_rebounds"]["count"] == 1
        assert payload["rejection_count_by_market_type_reason"]["player_rebounds"]["market_gate_minutes_lt_24"] == 1

    def test_board_selection_trace_explains_live_gate_admission(self, caplog):
        """Live candidates with line_source should be admitted (live-gate fix)."""
        config = PredictionConfig(
            prediction_date="2024-01-15",
            synthetic_odds_default=-110,
        )
        logger = logging.getLogger("test_predict_pipeline.selection_trace")
        pipeline = PredictionPipeline(config, logger=logger)

        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
            "game_date": "2024-01-15",
        }])

        odds = pd.DataFrame([{
            "game_id": 1,
            "player_name": "LeBron James",
            "raw_market_name": "player_points",
            "line": 25.5,
            "odds": -110,
            "is_live": True,
            "team": "LAL",
        }])

        baselines = pd.DataFrame([{
            "player_name": "LeBron James",
            "team_abbr": "LAL",
            "player_id": 123,
            "pts_avg": 27.0,
            "pts_recent": 28.0,
            "reb_avg": 8.0,
            "ast_avg": 8.0,
            "min_avg": 35.0,
        }])

        with caplog.at_level(logging.INFO, logger=logger.name):
            result = pipeline.run(games, odds, baselines)

        # After live-gate fix, candidates SHOULD flow through
        assert not result.elite_props.empty, "Live candidates should be admitted after fix"
        assert "selection_rejection_reason" in result.merged_market_props.columns
        # Should NOT be rejected for missing qualification reason
        assert result.merged_market_props.iloc[0].get("selection_rejection_reason") != (
            "selection_live_gate_missing_qualification_reason"
        )
        assert "board_selection_trace" in caplog.text
        assert "selection_rejection_reasons" in caplog.text


class TestRunPredictionPipeline:
    """Test the convenience function."""

    def test_convenience_function(self):
        games = pd.DataFrame()
        odds = pd.DataFrame()
        baselines = pd.DataFrame()

        result = run_prediction_pipeline(
            prediction_date="2024-01-15",
            games=games,
            odds=odds,
            player_baselines=baselines,
        )

        assert isinstance(result, PredictionResult)
        assert result.prediction_date == "2024-01-15"


class TestPipelineIntegration:
    """Integration tests with real module delegation."""

    def test_delegates_to_scoring_policy(self):
        """Verify pipeline delegates scoring to CandidateScoringPolicy."""
        config = PredictionConfig(prediction_date="2024-01-15")
        scoring = CandidateScoringPolicy()
        pipeline = PredictionPipeline(config, scoring_policy=scoring)

        # Check that the scoring policy is available
        assert pipeline.scoring_policy is not None
        assert hasattr(pipeline.scoring_policy, 'apply_scoring_metadata')

    def test_delegates_to_injury_engine(self):
        """Verify pipeline delegates injury to InjuryEngine."""
        config = PredictionConfig(prediction_date="2024-01-15")
        injury = InjuryEngine()
        pipeline = PredictionPipeline(config, injury_engine=injury)

        assert pipeline.injury_engine is not None
        assert hasattr(pipeline.injury_engine, 'build_context')
        assert hasattr(pipeline.injury_engine, 'apply_context')

    def test_delegates_to_market_evaluator(self):
        """Verify pipeline delegates market to MarketEvaluator."""
        config = PredictionConfig(prediction_date="2024-01-15")
        market = MarketEvaluator()
        pipeline = PredictionPipeline(config, market_evaluator=market)

        assert pipeline.market_evaluator is not None
        assert hasattr(pipeline.market_evaluator, 'evaluate')

    def test_projection_computation(self):
        """Test that projection computation works correctly."""
        config = PredictionConfig(prediction_date="2024-01-15")
        pipeline = PredictionPipeline(config)

        # Test points projection
        player_row = pd.Series({
            "pts_avg": 27.5,
            "pts_recent": 28.0,
        })
        proj = pipeline._compute_projection(player_row, "player_points")
        assert proj == 27.5

        # Test rebounds projection
        player_row = pd.Series({
            "reb_avg": 8.2,
        })
        proj = pipeline._compute_projection(player_row, "player_rebounds")
        assert proj == 8.2

        # Test fallback to recent
        player_row = pd.Series({
            "pts_recent": 26.0,
        })
        proj = pipeline._compute_projection(player_row, "player_points")
        assert proj == 26.0

    def test_confidence_computation(self):
        """Test that confidence computation respects thresholds."""
        config = PredictionConfig(prediction_date="2024-01-15")
        pipeline = PredictionPipeline(config)

        # High minutes -> high confidence
        player_row = pd.Series({"min_avg": 35.0})
        conf = pipeline._compute_confidence(player_row, "player_points")
        assert conf >= 0.75
        assert conf <= 0.98

        # Low minutes -> lower confidence
        player_row = pd.Series({"min_avg": 12.0})
        conf = pipeline._compute_confidence(player_row, "player_points")
        assert conf < 0.70

        # Defensive stats have lower confidence
        player_row = pd.Series({"min_avg": 30.0})
        conf_points = pipeline._compute_confidence(player_row, "player_points")
        conf_steals = pipeline._compute_confidence(player_row, "player_steals")
        assert conf_steals < conf_points


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
