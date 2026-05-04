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

from courtvision.calibration.grading_summary import summarize_graded_props
from courtvision.pipeline import (
    PredictionConfig,
    PredictionPipeline,
    PredictionResult,
    run_prediction_pipeline,
)
from courtvision.scoring import CandidateScoringPolicy
from courtvision.injuries import InjuryEngine
from courtvision.market import MarketEvaluator
from courtvision.data.candidates import score_player_markets
from scripts.market_shadow_grading import build_market_shadow_grading
from scripts.write_daily_summary import write_daily_summary_outputs


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

    def test_over_under_rows_materialize_two_side_candidates(self):
        players = pd.DataFrame([{
            "player_name": "Two Way Player",
            "team_abbr": "LAL",
            "player_id": 123,
        }])
        odds = pd.DataFrame([
            {
                "player_id": 123,
                "player_name": "Two Way Player",
                "team_abbr": "LAL",
                "market_type": "player_points",
                "selection": "over",
                "line": 21.5,
                "odds": -110,
            },
            {
                "player_id": 123,
                "player_name": "Two Way Player",
                "team_abbr": "LAL",
                "market_type": "player_points",
                "selection": "under",
                "line": 21.5,
                "odds": -110,
            },
        ])

        def build_candidate_row(*, player_row, market, market_rows, partial_fill=False):
            market_row = market_rows.iloc[0]
            return {
                "player_name": player_row["player_name"],
                "market_type": market,
                "selection": market_row["selection"],
                "edge": 1.0,
                "side_edge": 1.0,
                "confidence": 0.7,
                "projection_support_status": "modeled",
            }

        accepted, rejected = score_player_markets(
            players_df=players,
            odds_df=odds,
            is_player_inactive=lambda _: False,
            build_candidate_row=build_candidate_row,
            score_candidate_fn=lambda **kwargs: kwargs["candidate_row"],
            reject_candidate_fn=lambda **kwargs: {
                "market_type": kwargs.get("market"),
                "rejection_reason": kwargs.get("reason"),
            },
            allow_partial_fill=False,
        )

        assert not rejected
        assert [row["selection"] for row in accepted] == ["over", "under"]

    def test_over_and_under_keep_raw_edge_and_side_edge(self):
        config = PredictionConfig(prediction_date="2024-01-15", enable_partial_fill=False)
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
                "player_name": "Over Player",
                "raw_market_type": "over_under",
                "market_type": "player_points",
                "line": 24.5,
                "odds": -110,
                "selection": "over",
                "is_live": True,
            },
            {
                "game_id": 1,
                "player_id": 124,
                "player_name": "Under Player",
                "raw_market_type": "over_under",
                "market_type": "player_points",
                "line": 24.5,
                "odds": -110,
                "selection": "under",
                "is_live": True,
            },
        ])
        baselines = pd.DataFrame([
            {
                "player_name": "Over Player",
                "team_abbr": "LAL",
                "player_id": 123,
                "pts_avg": 27.0,
                "pts_recent": 27.0,
                "min_avg": 34.0,
            },
            {
                "player_name": "Under Player",
                "team_abbr": "BOS",
                "player_id": 124,
                "pts_avg": 22.0,
                "pts_recent": 22.0,
                "min_avg": 34.0,
            },
        ])

        result = pipeline.run(games, odds, baselines)
        by_selection = result.merged_market_props.set_index("selection")

        assert by_selection.loc["over", "edge"] > 0
        assert by_selection.loc["over", "side_edge"] > 0
        assert by_selection.loc["under", "edge"] < 0
        assert by_selection.loc["under", "side_edge"] > 0
        assert by_selection.loc["under", "side_edge_pct"] > 0

    def test_wrong_side_candidates_are_rejected_by_direction(self):
        config = PredictionConfig(prediction_date="2024-01-15", enable_partial_fill=False)
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
                "player_name": "Wrong Over",
                "market_type": "player_points",
                "line": 24.5,
                "odds": -110,
                "selection": "over",
                "is_live": True,
            },
            {
                "game_id": 1,
                "player_id": 124,
                "player_name": "Wrong Under",
                "market_type": "player_points",
                "line": 24.5,
                "odds": -110,
                "selection": "under",
                "is_live": True,
            },
        ])
        baselines = pd.DataFrame([
            {
                "player_name": "Wrong Over",
                "team_abbr": "LAL",
                "player_id": 123,
                "pts_avg": 22.0,
                "pts_recent": 22.0,
                "min_avg": 34.0,
            },
            {
                "player_name": "Wrong Under",
                "team_abbr": "BOS",
                "player_id": 124,
                "pts_avg": 27.0,
                "pts_recent": 27.0,
                "min_avg": 34.0,
            },
        ])

        result = pipeline.run(games, odds, baselines)

        assert result.merged_market_props.empty

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
        from datetime import datetime, timedelta
        out_dir = Path("test_outputs") / "market_readiness_pipeline"
        shutil.rmtree(out_dir, ignore_errors=True)
        config = PredictionConfig(prediction_date="2024-01-15", out_dir=str(out_dir))
        pipeline = PredictionPipeline(config)

        # Include a future date so game status gate doesn't block all candidates
        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
            "date": (datetime.now() + timedelta(hours=2)).isoformat(),
            "status": "scheduled",
        }])
        fresh_time = (datetime.now() - timedelta(minutes=5)).isoformat()
        odds = pd.DataFrame([
            {
                "game_id": 1,
                "player_id": 123,
                "player_name": "Points Star",
                "raw_prop_type": "points",
                "raw_market_type": "over_under",
                "market_type": "player_points",
                "line": 21.5,
                "odds": -110,
                "selection": "over",
                "is_live": True,
                "updated_at": fresh_time,
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
                "updated_at": fresh_time,
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
                "updated_at": fresh_time,
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

    def test_market_shadow_grading_summarizes_full_market_by_market(self):
        runtime_root = Path("test_outputs") / "market_shadow_runtime"
        history_root = Path("test_outputs") / "market_shadow_history"
        shutil.rmtree(runtime_root, ignore_errors=True)
        shutil.rmtree(history_root, ignore_errors=True)
        (runtime_root / "operator").mkdir(parents=True, exist_ok=True)
        (runtime_root / "history").mkdir(parents=True, exist_ok=True)

        pd.DataFrame([
            {
                "prediction_date": "2024-01-15",
                "player_name": "Points Star",
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 20.5,
                "edge": 2.5,
                "confidence": 0.80,
                "quality_score": 90.0,
                "odds": -110,
                "context_pick_alignment": "aligned",
            },
            {
                "prediction_date": "2024-01-15",
                "player_name": "Rebound Star",
                "market_type": "player_rebounds",
                "selection": "under",
                "sportsbook_line": 9.5,
                "edge": 1.0,
                "confidence": 0.70,
                "quality_score": 75.0,
                "odds": 120,
                "context_pick_alignment": "aligned",
            },
            {
                "prediction_date": "2024-01-15",
                "player_name": "Pending Assist",
                "market_type": "player_assists",
                "selection": "over",
                "sportsbook_line": 5.5,
                "edge": 0.8,
                "confidence": 0.65,
                "quality_score": 70.0,
                "odds": -105,
                "context_pick_alignment": "conflicted",
            },
        ]).to_csv(runtime_root / "operator" / "full_market_board_2024-01-15.csv", index=False)

        pd.DataFrame([
            {
                "prediction_date": "2024-01-15",
                "player_name": "Points Star",
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 20.5,
                "result_status": "hit",
            },
            {
                "prediction_date": "2024-01-15",
                "player_name": "Rebound Star",
                "market_type": "player_rebounds",
                "selection": "under",
                "sportsbook_line": 9.5,
                "result_status": "miss",
            },
        ]).to_csv(runtime_root / "history" / "graded_picks_2024-01-15.csv", index=False)

        payload = build_market_shadow_grading(
            prediction_date="2024-01-15",
            runtime_root=runtime_root,
            history_root=history_root,
        )

        by_market = {row["market_type"]: row for row in payload["markets"]}
        assert payload["totals"]["total_picks"] == 3
        assert payload["totals"]["graded_picks"] == 2
        assert payload["totals"]["pending_picks"] == 1
        assert by_market["player_points"]["hit_rate"] == 1.0
        assert by_market["player_points"]["roi"] == 0.909091
        assert by_market["player_rebounds"]["hit_rate"] == 0.0
        assert by_market["player_rebounds"]["roi"] == -1.0
        assert by_market["player_assists"]["pending_picks"] == 1
        assert payload["totals"]["context_alignment"] == {
            "aligned": 2,
            "conflicted": 1,
            "neutral": 0,
            "insufficient_data": 0,
        }
        assert by_market["player_assists"]["context_alignment"]["conflicted"] == 1
        alignment_perf = payload["context_alignment_performance"]
        assert alignment_perf["status"] == "ok"
        assert alignment_perf["by_alignment"]["aligned"]["graded_picks"] == 2
        assert alignment_perf["by_alignment"]["aligned"]["hit_rate"] == 0.5
        assert alignment_perf["by_alignment"]["aligned"]["roi"] == -0.045455
        assert alignment_perf["by_alignment"]["conflicted"]["pending_picks"] == 1
        assert alignment_perf["by_alignment"]["conflicted"]["status"] == "insufficient_sample"
        assert alignment_perf["by_alignment_and_selection_side"]["aligned"]["over"]["hit_rate"] == 1.0
        assert alignment_perf["by_alignment_and_selection_side"]["aligned"]["under"]["hit_rate"] == 0.0
        assert alignment_perf["by_alignment_and_market_type"]["aligned"]["player_rebounds"]["roi"] == -1.0

    def test_daily_summary_includes_operator_sections(self):
        runtime_root = Path("test_outputs") / "daily_summary_runtime"
        shutil.rmtree(runtime_root, ignore_errors=True)
        (runtime_root / "operator").mkdir(parents=True, exist_ok=True)
        (runtime_root / "diagnostics").mkdir(parents=True, exist_ok=True)

        pd.DataFrame([
            {
                "prediction_date": "2024-01-15",
                "player_name": "Points Star",
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 20.5,
                "odds": -110,
                "edge": 2.5,
                "confidence": 0.80,
                "quality_score": 90.0,
                "manual_status": "active",
                "manual_minutes_limit": "",
                "manual_projection_adjustment": 0,
                "manual_confidence_adjustment": 0.00,
                "manual_context_reason": "manual test row only",
                "manual_context_applied": False,
                "pace_context_signal": "supports_over",
                "defense_context_signal": "neutral",
                "rest_context_signal": "supports_under",
                "playoff_context_signal": "supports_under",
                "overall_context_signal": "supports_under",
                "context_pick_alignment": "conflicted",
                "context_caution_level": "high",
                "context_preview_applied": False,
            },
        ]).to_csv(runtime_root / "operator" / "elite_board_2024-01-15.csv", index=False)
        pd.DataFrame([
            {
                "prediction_date": "2024-01-15",
                "player_name": "Points Star",
                "market_type": "player_points",
                "selection": "over",
                "line": 20.5,
                "american_odds": -110,
                "edge_pct": 0.12,
                "stake_amount": 8.50,
                "expected_value": 1.02,
                "eligible": True,
                "context_caution_level": "high",
            },
        ]).to_csv(runtime_root / "operator" / "kelly_stakes_2024-01-15.csv", index=False)
        pd.DataFrame([
            {"market_type": "player_points", "player_name": "Points Star", "context_pick_alignment": "aligned"},
            {"market_type": "player_rebounds", "player_name": "Rebound Star", "context_pick_alignment": "neutral"},
        ]).to_csv(runtime_root / "operator" / "full_market_board_2024-01-15.csv", index=False)
        (runtime_root / "diagnostics" / "market_shadow_grading_2024-01-15.json").write_text(
            json.dumps(
                {
                    "totals": {
                        "total_picks": 2,
                        "graded_picks": 1,
                        "pending_picks": 1,
                        "hit_rate": 1.0,
                    },
                    "markets": [],
                    "context_alignment_performance": {
                        "status": "ok",
                        "by_alignment": {
                            "aligned": {
                                "total_picks": 1,
                                "graded_picks": 1,
                                "pending_picks": 0,
                                "hits": 1,
                                "misses": 0,
                                "pushes": 0,
                                "hit_rate": 1.0,
                                "roi": 0.909091,
                                "status": "ok",
                            },
                            "conflicted": {
                                "total_picks": 0,
                                "graded_picks": 0,
                                "pending_picks": 0,
                                "hits": 0,
                                "misses": 0,
                                "pushes": 0,
                                "hit_rate": None,
                                "roi": None,
                                "status": "insufficient_sample",
                            },
                            "neutral": {
                                "total_picks": 1,
                                "graded_picks": 0,
                                "pending_picks": 1,
                                "hits": 0,
                                "misses": 0,
                                "pushes": 0,
                                "hit_rate": None,
                                "roi": None,
                                "status": "insufficient_sample",
                            },
                        },
                        "by_alignment_and_selection_side": {
                            "aligned": {
                                "over": {
                                    "total_picks": 1,
                                    "graded_picks": 1,
                                    "pending_picks": 0,
                                    "hit_rate": 1.0,
                                    "roi": 0.909091,
                                    "status": "ok",
                                },
                                "under": {
                                    "total_picks": 0,
                                    "graded_picks": 0,
                                    "pending_picks": 0,
                                    "hit_rate": None,
                                    "roi": None,
                                    "status": "insufficient_sample",
                                },
                            },
                            "conflicted": {},
                            "neutral": {
                                "over": {
                                    "total_picks": 0,
                                    "graded_picks": 0,
                                    "pending_picks": 0,
                                    "hit_rate": None,
                                    "roi": None,
                                    "status": "insufficient_sample",
                                },
                                "under": {
                                    "total_picks": 1,
                                    "graded_picks": 0,
                                    "pending_picks": 1,
                                    "hit_rate": None,
                                    "roi": None,
                                    "status": "insufficient_sample",
                                },
                            },
                        },
                        "by_alignment_and_market_type": {
                            "aligned": {
                                "player_points": {
                                    "total_picks": 1,
                                    "graded_picks": 1,
                                    "pending_picks": 0,
                                    "hit_rate": 1.0,
                                    "roi": 0.909091,
                                    "status": "ok",
                                }
                            },
                            "conflicted": {},
                            "neutral": {
                                "player_rebounds": {
                                    "total_picks": 1,
                                    "graded_picks": 0,
                                    "pending_picks": 1,
                                    "hit_rate": None,
                                    "roi": None,
                                    "status": "insufficient_sample",
                                }
                            },
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        (runtime_root / "diagnostics" / "market_performance_readiness_2024-01-15.json").write_text(
            json.dumps(
                {
                    "markets": [
                        {
                            "market_type": "player_rebounds",
                            "count": 1,
                            "avg_confidence": 0.7,
                            "avg_quality_score": 75.0,
                        }
                    ],
                    "rejection_count_by_market_type_reason": {
                        "player_rebounds": {"market_gate_minutes_lt_24": 2}
                    },
                }
            ),
            encoding="utf-8",
        )
        (runtime_root / "diagnostics" / "manual_context_2024-01-15.json").write_text(
            json.dumps(
                {
                    "file_found": True,
                    "rows": 1,
                    "candidate_matches": 1,
                    "passive_mode": True,
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )

        output_path, metadata = write_daily_summary_outputs(
            prediction_date="2024-01-15",
            runtime_root=runtime_root,
        )

        text = output_path.read_text(encoding="utf-8")
        assert output_path.name == "daily_summary_2024-01-15.txt"
        assert "Elite Picks" in text
        assert "Kelly Stakes" in text
        assert "Total exposure: $8.50" in text
        assert "Expected EV: $1.02" in text
        assert "Full-Market Market Counts" in text
        assert "- player_rebounds: 1" in text
        assert "Shadow Grading Totals" in text
        assert "Pending grading count: 1" in text
        assert "Manual Context" in text
        assert "- candidate matches: 1" in text
        assert "manual_status: active" in text
        assert "manual_projection_adjustment: 0" in text
        assert "manual_confidence_adjustment: 0.0" in text
        assert "manual_context_reason: manual test row only" in text
        assert "manual_context_applied: False" in text
        assert "pace_context_signal: supports_over" in text
        assert "defense_context_signal: neutral" in text
        assert "rest_context_signal: supports_under" in text
        assert "playoff_context_signal: supports_under" in text
        assert "overall_context_signal: supports_under" in text
        assert "context_pick_alignment: conflicted" in text
        assert "context_caution_level: high" in text
        assert "context_preview_applied: False" in text
        assert "Context-Pick Alignment" in text
        assert "- elite: aligned=0, conflicted=1, neutral=0, insufficient_data=0" in text
        assert "- elite caution: high=1, medium=0, low=0, insufficient_data=0" in text
        assert "- full_market: aligned=1, conflicted=0, neutral=1, insufficient_data=0" in text
        assert "caution=high" in text
        assert "Context Alignment Performance" in text
        assert "- aligned: graded=1, pending=0, hit_rate=100.0%, roi=90.9%, status=ok" in text
        assert "- neutral/under: graded=0, pending=1, hit_rate=n/a, roi=n/a, status=insufficient_sample" in text
        assert "- aligned/player_points: graded=1, pending=0, hit_rate=100.0%, roi=90.9%, status=ok" in text
        assert "Elite board locked to player_points only." in text
        assert "Context preview signals do not alter projections." in text
        assert "High-caution conflicted OVER context gates final elite admission and Kelly staking." in text
        assert "Manual player context is diagnostic only; matched candidates: 1." in text
        assert metadata["full_market_counts"] == {"player_points": 1, "player_rebounds": 1}
        assert metadata["elite_context_alignment"]["conflicted"] == 1
        assert metadata["elite_high_caution_count"] == 1
        assert metadata["elite_medium_caution_count"] == 0
        assert metadata["elite_low_caution_count"] == 0
        assert metadata["full_market_context_alignment"]["neutral"] == 1
        assert metadata["context_alignment_performance"]["by_alignment"]["aligned"]["hit_rate"] == 1.0
        assert metadata["manual_context"]["candidate_matches"] == 1

    def test_grading_summary_tracks_context_alignment_buckets(self):
        summary = summarize_graded_props([
            {
                "market_type": "player_points",
                "selection": "over",
                "context_pick_alignment": "aligned",
                "odds": -110,
                "result": "win",
            },
            {
                "market_type": "player_rebounds",
                "selection": "under",
                "context_pick_alignment": "conflicted",
                "odds": 120,
                "result": "loss",
            },
        ])

        assert summary["by_context_pick_alignment"]["aligned"]["wins"] == 1
        assert summary["by_context_pick_alignment"]["aligned"]["roi"] == 0.909091
        assert summary["by_context_pick_alignment"]["conflicted"]["losses"] == 1
        assert summary["by_context_pick_alignment"]["conflicted"]["roi"] == -1.0
        assert summary["joint_context_alignment_side"]["aligned|over"]["win_rate"] == 1.0
        assert summary["joint_context_alignment_market_type"]["conflicted|player_rebounds"]["losses"] == 1

    def test_board_selection_trace_explains_live_gate_admission(self, caplog):
        """Live candidates with line_source should be admitted (live-gate fix)."""
        config = PredictionConfig(
            prediction_date="2024-01-15",
            synthetic_odds_default=-110,
        )
        logger = logging.getLogger("test_predict_pipeline.selection_trace")
        pipeline = PredictionPipeline(config, logger=logger)

        from datetime import datetime, timedelta
        games = pd.DataFrame([{
            "game_id": 1,
            "home_team_abbr": "LAL",
            "visitor_team_abbr": "BOS",
            "game_date": (datetime.now() + timedelta(hours=2)).isoformat(),
            "status": "scheduled",
        }])

        odds = pd.DataFrame([{
            "game_id": 1,
            "player_name": "LeBron James",
            "raw_market_name": "player_points",
            "line": 25.5,
            "odds": -110,
            "is_live": True,
            "team": "LAL",
            "updated_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
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
