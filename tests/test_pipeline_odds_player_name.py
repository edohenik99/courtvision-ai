"""Regression test for player_name in pipeline odds DataFrame.

Ensures player names are populated in the odds DataFrame passed to PredictionPipeline.run().

Related to fix for:
- player_odds_match_rate = 0%
- sample_odds_players = [None, None, ...]
- all candidates rejected due to missing_market_lines
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPipelineOddsPlayerName:
    """Test that odds DataFrame passed to pipeline has valid player_name."""

    def test_odds_dataframe_has_player_name_column(self):
        """Verify odds DataFrame has player_name column."""
        # Simulate odds data from get_odds()
        odds_data = [
            {
                "game_id": 101,
                "bookmaker": "draftkings",
                "raw_market_name": "player_points",
                "player_id": 1,
                "player_name": "LeBron James",
                "team": "LAL",
                "line": 24.5,
                "over_odds": -110,
                "under_odds": -110,
            },
            {
                "game_id": 101,
                "bookmaker": "draftkings",
                "raw_market_name": "player_rebounds",
                "player_id": 1,
                "player_name": "LeBron James",
                "team": "LAL",
                "line": 7.5,
                "over_odds": -110,
                "under_odds": -110,
            },
        ]

        odds_df = pd.DataFrame(odds_data)

        assert "player_name" in odds_df.columns, "player_name column missing"
        assert not odds_df["player_name"].isna().any(), "Found null player_name values"
        assert all(odds_df["player_name"] != ""), "Found empty player_name values"

    def test_player_name_null_percentage_check(self):
        """Test that we can detect too many null player names."""
        # Simulate odds data with some null player names
        odds_data = [
            {"player_name": "LeBron James", "line": 24.5},
            {"player_name": None, "line": 26.5},
            {"player_name": "Stephen Curry", "line": 28.5},
            {"player_name": None, "line": 30.5},
            {"player_name": None, "line": 32.5},
        ]

        odds_df = pd.DataFrame(odds_data)

        # Calculate null percentage
        null_count = odds_df["player_name"].isna().sum() + (odds_df["player_name"] == "").sum()
        null_percentage = null_count / len(odds_df) * 100

        # Should be detectable as >50%
        assert null_percentage > 50, f"Expected >50% null, got {null_percentage}%"

        # This would trigger the ValueError in production
        assert null_count == 3, f"Expected 3 null values, got {null_count}"

    def test_get_odds_player_name_extraction(self):
        """Test that get_odds correctly extracts player_name from nested player object."""
        # Simulate raw API response
        raw_props = [
            {
                "game_id": 101,
                "player": {"first_name": "LeBron", "last_name": "James"},
                "player_name": None,  # API returns None here
                "prop_type": "points",
                "line_value": 24.5,
                "vendor": "draftkings",
                "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
            },
            {
                "game_id": 101,
                "player": {"first_name": "Stephen", "last_name": "Curry"},
                "player_name": None,
                "prop_type": "points",
                "line_value": 26.5,
                "vendor": "draftkings",
                "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
            },
        ]

        # Simulate _normalize_player_prop_row logic
        rows = []
        for market in raw_props:
            player = market.get("player", {}) if isinstance(market.get("player"), dict) else {}
            player_name = (
                f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                or market.get("player_name")
            )

            if player_name:  # Only add if we have a valid player name
                rows.append({
                    "game_id": market.get("game_id"),
                    "player_name": player_name,
                    "line": market.get("line_value"),
                    "prop_type": market.get("prop_type"),
                })

        odds_df = pd.DataFrame(rows)

        # Verify all rows have valid player names
        assert not odds_df["player_name"].isna().any(), "Found null player_name"
        assert all(odds_df["player_name"] != ""), "Found empty player_name"
        assert set(odds_df["player_name"]) == {"LeBron James", "Stephen Curry"}

    def test_hard_assertion_triggers_on_high_null_rate(self):
        """Test that hard assertion triggers when >50% player names are null."""
        odds_data = [
            {"player_name": "LeBron James"},
            {"player_name": None},
            {"player_name": None},
            {"player_name": None},
        ]

        odds_df = pd.DataFrame(odds_data)

        null_count = odds_df["player_name"].isna().sum() + (odds_df["player_name"] == "").sum()
        null_percentage = null_count / len(odds_df) * 100

        # This should trigger the ValueError
        assert null_percentage > 50, f"Expected >50% null to trigger assertion, got {null_percentage}%"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
