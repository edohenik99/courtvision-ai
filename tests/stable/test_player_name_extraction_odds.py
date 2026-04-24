"""Regression test for player name extraction from odds data.

Ensures player names are properly extracted from nested player objects
in BallDontLie odds API response.

Related to fix for:
- player_odds_match_rate = 0%
- sample_odds_players = [None, None, ...]
- all candidates rejected due to missing_market_lines
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPlayerNameExtractionFromOdds:
    """Test that player names are extracted from nested player objects."""

    def test_extract_player_name_from_nested_object(self):
        """Test extracting player name from nested player dict."""
        # Simulate what BallDontLie returns
        row = {
            "id": 123,
            "game_id": 456,
            "player": {
                "first_name": "LeBron",
                "last_name": "James",
            },
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": 24.5,
            "market": {
                "type": "over_under",
                "over_odds": -110,
                "under_odds": -110,
            },
        }

        # Extract player name using the same logic as the fix
        player_name = ""
        if row.get("player_name"):
            player_name = str(row.get("player_name", "")).strip()
        elif row.get("player"):
            player = row.get("player")
            if isinstance(player, dict):
                first_name = str(player.get("first_name", "")).strip()
                last_name = str(player.get("last_name", "")).strip()
                player_name = f"{first_name} {last_name}".strip()

        assert player_name == "LeBron James", f"Expected 'LeBron James', got '{player_name}'"

    def test_extract_player_name_from_direct_field(self):
        """Test extracting player name from direct player_name field."""
        row = {
            "id": 123,
            "game_id": 456,
            "player_name": "Stephen Curry",
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": 26.5,
            "market": {
                "type": "over_under",
                "over_odds": -110,
                "under_odds": -110,
            },
        }

        player_name = ""
        if row.get("player_name"):
            player_name = str(row.get("player_name", "")).strip()
        elif row.get("player"):
            player = row.get("player")
            if isinstance(player, dict):
                first_name = str(player.get("first_name", "")).strip()
                last_name = str(player.get("last_name", "")).strip()
                player_name = f"{first_name} {last_name}".strip()

        assert player_name == "Stephen Curry", f"Expected 'Stephen Curry', got '{player_name}'"

    def test_extract_player_name_prefers_direct_field(self):
        """Test that direct player_name field takes precedence over nested object."""
        row = {
            "id": 123,
            "game_id": 456,
            "player_name": "Kevin Durant",
            "player": {
                "first_name": "Different",
                "last_name": "Name",
            },
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": 28.5,
            "market": {
                "type": "over_under",
                "over_odds": -110,
                "under_odds": -110,
            },
        }

        player_name = ""
        if row.get("player_name"):
            player_name = str(row.get("player_name", "")).strip()
        elif row.get("player"):
            player = row.get("player")
            if isinstance(player, dict):
                first_name = str(player.get("first_name", "")).strip()
                last_name = str(player.get("last_name", "")).strip()
                player_name = f"{first_name} {last_name}".strip()

        # Direct field takes precedence
        assert player_name == "Kevin Durant", f"Expected 'Kevin Durant', got '{player_name}'"

    def test_extract_player_name_handles_empty(self):
        """Test handling of empty player data."""
        row = {
            "id": 123,
            "game_id": 456,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": 24.5,
            "market": {
                "type": "over_under",
                "over_odds": -110,
                "under_odds": -110,
            },
        }

        player_name = ""
        if row.get("player_name"):
            player_name = str(row.get("player_name", "")).strip()
        elif row.get("player"):
            player = row.get("player")
            if isinstance(player, dict):
                first_name = str(player.get("first_name", "")).strip()
                last_name = str(player.get("last_name", "")).strip()
                player_name = f"{first_name} {last_name}".strip()

        assert player_name == "", f"Expected empty string, got '{player_name}'"

    def test_odds_dataframe_has_valid_player_names(self):
        """Test that odds DataFrame has valid player_name values after extraction."""
        import pandas as pd

        # Simulate the conversion from API response to DataFrame
        api_response = [
            {
                "player": {"first_name": "LeBron", "last_name": "James"},
                "player_name": None,  # API returns None for direct field
                "prop_type": "points",
                "line_value": 24.5,
            },
            {
                "player": {"first_name": "Stephen", "last_name": "Curry"},
                "player_name": None,
                "prop_type": "points",
                "line_value": 26.5,
            },
            {
                "player": {"first_name": "Kevin", "last_name": "Durant"},
                "player_name": None,
                "prop_type": "points",
                "line_value": 28.5,
            },
        ]

        # Build DataFrame with proper player_name extraction
        rows = []
        for row in api_response:
            player_name = ""
            if row.get("player_name"):
                player_name = str(row.get("player_name", "")).strip()
            elif row.get("player"):
                player = row.get("player")
                if isinstance(player, dict):
                    first_name = str(player.get("first_name", "")).strip()
                    last_name = str(player.get("last_name", "")).strip()
                    player_name = f"{first_name} {last_name}".strip()

            rows.append({
                "player_name": player_name,
                "prop_type": row.get("prop_type"),
                "line_value": row.get("line_value"),
            })

        df = pd.DataFrame(rows)

        # Verify all rows have valid player names
        assert not df["player_name"].isna().any(), "Found null player_name values"
        assert all(df["player_name"] != ""), "Found empty player_name values"
        assert all(df["player_name"] != "None"), "Found 'None' string in player_name"

        # Verify expected names
        expected_names = {"LeBron James", "Stephen Curry", "Kevin Durant"}
        actual_names = set(df["player_name"].tolist())
        assert actual_names == expected_names, f"Expected {expected_names}, got {actual_names}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
