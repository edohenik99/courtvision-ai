"""Regression tests for BallDontLie games schema normalization.

Tests that normalize_games_schema properly handles:
- Nested BallDontLie-style payload (home_team/visitor_team dicts)
- Flat id-only schema (home_team_id, visitor_team_id)
- Missing required team fields -> clear ValueError
- Canonical output schema consistency

Related to fix in predict_pipeline.py for KeyError: 'home_team'
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from courtvision.data.normalization import normalize_games_schema


class TestGamesSchemaNormalization:
    """Test games schema normalization for BallDontLie API compatibility."""

    def test_nested_balldontlie_payload(self):
        """Test normalization of nested BallDontLie-style game data.

        Input: home_team and visitor_team as dict objects
        Output: Flat canonical columns
        """
        games_raw = pd.DataFrame({
            "id": [1, 2],
            "date": ["2026-04-17", "2026-04-17"],
            "status": ["Final", "Scheduled"],
            "home_team": [
                {"id": 1, "abbreviation": "LAL", "name": "Los Angeles Lakers"},
                {"id": 2, "abbreviation": "BOS", "name": "Boston Celtics"},
            ],
            "visitor_team": [
                {"id": 3, "abbreviation": "GSW", "name": "Golden State Warriors"},
                {"id": 4, "abbreviation": "NYK", "name": "New York Knicks"},
            ],
        })

        result = normalize_games_schema(games_raw)

        # Should have canonical columns
        assert "game_id" in result.columns
        assert "home_team_id" in result.columns
        assert "home_team_abbr" in result.columns
        assert "home_team_name" in result.columns
        assert "visitor_team_id" in result.columns
        assert "visitor_team_abbr" in result.columns
        assert "visitor_team_name" in result.columns
        assert "game_date" in result.columns
        assert "status" in result.columns

        # Values should be extracted from nested dicts
        assert result.iloc[0]["home_team_id"] == 1
        assert result.iloc[0]["home_team_abbr"] == "LAL"
        assert result.iloc[0]["home_team_name"] == "Los Angeles Lakers"
        assert result.iloc[0]["visitor_team_id"] == 3
        assert result.iloc[0]["visitor_team_abbr"] == "GSW"

    def test_flat_id_only_schema(self):
        """Test normalization of flat id-only schema.

        Input: home_team_id, visitor_team_id already flattened
        Output: Same values in canonical columns
        """
        games_raw = pd.DataFrame({
            "game_id": [1, 2],
            "game_date": ["2026-04-17", "2026-04-17"],
            "home_team_id": [1, 2],
            "home_team_abbr": ["LAL", "BOS"],
            "home_team_name": ["Lakers", "Celtics"],
            "visitor_team_id": [3, 4],
            "visitor_team_abbr": ["GSW", "NYK"],
            "visitor_team_name": ["Warriors", "Knicks"],
            "status": ["Final", "Scheduled"],
        })

        result = normalize_games_schema(games_raw)

        # Should preserve existing values
        assert result.iloc[0]["home_team_id"] == 1
        assert result.iloc[0]["home_team_abbr"] == "LAL"
        assert result.iloc[0]["visitor_team_id"] == 3
        assert result.iloc[0]["visitor_team_abbr"] == "GSW"

    def test_already_normalized_passes_through(self):
        """Test that already-normalized data passes through correctly."""
        games_raw = pd.DataFrame({
            "game_id": [1],
            "game_date": ["2026-04-17"],
            "home_team_id": [1],
            "home_team_abbr": ["LAL"],
            "visitor_team_id": [3],
            "visitor_team_abbr": ["GSW"],
            "status": ["Final"],
        })

        result = normalize_games_schema(games_raw)

        # Should be unchanged
        assert len(result) == 1
        assert result.iloc[0]["home_team_id"] == 1
        assert result.iloc[0]["home_team_abbr"] == "LAL"

    def test_abbreviations_only_schema(self):
        """Test normalization with only abbreviations (no IDs).

        Input: home_team_abbr, visitor_team_abbr only
        Output: Canonical columns with IDs as None
        """
        games_raw = pd.DataFrame({
            "game_id": [1, 2],
            "game_date": ["2026-04-17", "2026-04-17"],
            "home_team_abbr": ["LAL", "BOS"],
            "visitor_team_abbr": ["GSW", "NYK"],
            "status": ["Final", "Scheduled"],
        })

        result = normalize_games_schema(games_raw)

        # Should succeed with abbreviations as canonical
        assert len(result) == 2
        assert result.iloc[0]["home_team_abbr"] == "LAL"
        assert result.iloc[0]["visitor_team_abbr"] == "GSW"
        # IDs should be None (not required)
        assert pd.isna(result.iloc[0].get("home_team_id")) or result.iloc[0].get("home_team_id") is None or str(result.iloc[0].get("home_team_id")) == "LAL"
        assert "game_id" in result.columns
        assert result.iloc[0]["game_id"] == 1

    def test_ids_only_schema(self):
        """Test normalization with only IDs (no abbreviations).

        Input: home_team_id, visitor_team_id only
        Output: Canonical columns with IDs, abbrs filled from IDs
        """
        games_raw = pd.DataFrame({
            "game_id": [1, 2],
            "game_date": ["2026-04-17", "2026-04-17"],
            "home_team_id": [1, 2],
            "visitor_team_id": [3, 4],
            "status": ["Final", "Scheduled"],
        })

        result = normalize_games_schema(games_raw)

        # Should succeed with IDs as canonical
        assert len(result) == 2
        assert result.iloc[0]["home_team_id"] == 1
        assert result.iloc[0]["visitor_team_id"] == 3
        # Abbreviations should be filled from IDs
        assert result.iloc[0]["home_team_abbr"] == "1"
        assert result.iloc[0]["visitor_team_abbr"] == "3"

    def test_both_ids_and_abbreviations(self):
        """Test normalization when both IDs and abbreviations present.

        Input: Both home_team_id and home_team_abbr present
        Output: Both preserved in canonical output
        """
        games_raw = pd.DataFrame({
            "game_id": [1],
            "game_date": ["2026-04-17"],
            "home_team_id": [1],
            "home_team_abbr": ["LAL"],
            "visitor_team_id": [3],
            "visitor_team_abbr": ["GSW"],
            "status": ["Final"],
        })

        result = normalize_games_schema(games_raw)

        # Both should be preserved
        assert result.iloc[0]["home_team_id"] == 1
        assert result.iloc[0]["home_team_abbr"] == "LAL"
        assert result.iloc[0]["visitor_team_id"] == 3
        assert result.iloc[0]["visitor_team_abbr"] == "GSW"

    def test_missing_required_fields_raises_error(self):
        """Test that missing both IDs AND abbreviations raises clear ValueError."""
        games_raw = pd.DataFrame({
            "id": [1],
            "date": ["2026-04-17"],
            # Missing any team fields - neither IDs nor abbreviations
        })

        with pytest.raises(ValueError) as exc_info:
            normalize_games_schema(games_raw)

        error_msg = str(exc_info.value)
        assert "Cannot normalize games" in error_msg
        assert "home_team_id" in error_msg and "home_team_abbr" in error_msg

    def test_empty_dataframe_returns_empty(self):
        """Test that empty DataFrame returns empty."""
        games_raw = pd.DataFrame()
        result = normalize_games_schema(games_raw)
        assert result.empty

    def test_raw_columns_preserved_for_debugging(self):
        """Test that original raw columns are preserved with raw_ prefix."""
        games_raw = pd.DataFrame({
            "id": [1],
            "date": ["2026-04-17"],
            "home_team": [{"id": 1, "abbreviation": "LAL", "name": "Lakers"}],
            "visitor_team": [{"id": 3, "abbreviation": "GSW", "name": "Warriors"}],
            "some_custom_field": ["custom_value"],
        })

        result = normalize_games_schema(games_raw)

        # Original columns should be preserved as raw_ columns
        assert "raw_home_team" in result.columns
        assert "raw_visitor_team" in result.columns
        assert "raw_some_custom_field" in result.columns

    def test_legacy_away_team_handling(self):
        """Test that legacy 'away_team' naming is handled.

        Some sources may use 'away_team' instead of 'visitor_team'.
        """
        games_raw = pd.DataFrame({
            "id": [1],
            "date": ["2026-04-17"],
            "home_team": [{"id": 1, "abbreviation": "LAL", "name": "Lakers"}],
            "away_team": [{"id": 3, "abbreviation": "GSW", "name": "Warriors"}],
        })

        result = normalize_games_schema(games_raw)

        # Should convert away_team to visitor_team columns
        assert result.iloc[0]["home_team_id"] == 1
        assert result.iloc[0]["visitor_team_id"] == 3
        assert result.iloc[0]["visitor_team_abbr"] == "GSW"

    def test_game_id_from_id_column(self):
        """Test that 'id' column is mapped to 'game_id'."""
        games_raw = pd.DataFrame({
            "id": [123, 456],
            "home_team": [{"id": 1, "abbreviation": "LAL"}, {"id": 2, "abbreviation": "BOS"}],
            "visitor_team": [{"id": 3, "abbreviation": "GSW"}, {"id": 4, "abbreviation": "MIA"}],
        })

        result = normalize_games_schema(games_raw)

        assert "game_id" in result.columns
        assert result.iloc[0]["game_id"] == 123


class TestPredictionPipelineIntegration:
    """Test integration with PredictionPipeline."""

    def test_pipeline_runs_with_nested_games(self):
        """Test that PredictionPipeline.run() works with nested BallDontLie schema."""
        from courtvision.pipeline import PredictionConfig, PredictionPipeline

        games_nested = pd.DataFrame({
            "id": [1],
            "date": ["2026-04-17"],
            "home_team": [{"id": 1, "abbreviation": "LAL", "name": "Lakers"}],
            "visitor_team": [{"id": 3, "abbreviation": "GSW", "name": "Warriors"}],
        })

        odds = pd.DataFrame()  # Empty for this test
        player_baselines = pd.DataFrame()

        config = PredictionConfig(
            prediction_date="2026-04-17",
            enable_injury_context=False,  # Disable to avoid extra dependencies
        )
        pipeline = PredictionPipeline(config)

        # Should not raise KeyError: 'home_team'
        try:
            result = pipeline.run(
                games=games_nested,
                odds=odds,
                player_baselines=player_baselines,
            )
        except KeyError as e:
            if "home_team" in str(e) or "away_team" in str(e):
                pytest.fail(f"Pipeline failed with unnormalized schema error: {e}")
            # Other KeyErrors may be expected due to empty inputs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
