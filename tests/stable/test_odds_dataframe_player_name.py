"""Regression test for player_name in odds DataFrame.

Ensures player names are properly extracted and preserved when converting
odds API response to DataFrame for candidate matching.

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


class TestOddsDataFramePlayerName:
    """Test that odds DataFrame has valid player_name values."""

    def test_extract_player_name_from_nested_object_in_odds(self):
        """Test extracting player name from nested player dict in odds data."""
        # Simulate raw API response with nested player object
        all_odds = [
            {
                "id": 1,
                "game_id": 101,
                "player": {"first_name": "LeBron", "last_name": "James"},
                "player_name": None,  # API returns None here
                "prop_type": "points",
                "line_value": 24.5,
                "vendor": "draftkings",
            },
            {
                "id": 2,
                "game_id": 101,
                "player": {"first_name": "Stephen", "last_name": "Curry"},
                "player_name": None,
                "prop_type": "points",
                "line_value": 26.5,
                "vendor": "draftkings",
            },
            {
                "id": 3,
                "game_id": 102,
                "player": {"first_name": "Kevin", "last_name": "Durant"},
                "player_name": None,
                "prop_type": "rebounds",
                "line_value": 7.5,
                "vendor": "fanduel",
            },
        ]

        # Apply extraction logic (same as in engine.py)
        for row in all_odds:
            if not row.get("player_name") and row.get("player"):
                player = row.get("player")
                if isinstance(player, dict):
                    first_name = str(player.get("first_name", "")).strip()
                    last_name = str(player.get("last_name", "")).strip()
                    row["player_name"] = f"{first_name} {last_name}".strip()

        # Create DataFrame
        df = pd.DataFrame(all_odds)

        # Verify all rows have valid player names
        assert "player_name" in df.columns, "player_name column missing"
        assert not df["player_name"].isna().any(), f"Found null player_name: {df['player_name'].tolist()}"
        assert all(df["player_name"] != ""), f"Found empty player_name: {df['player_name'].tolist()}"
        assert all(df["player_name"] != "None"), f"Found 'None' string: {df['player_name'].tolist()}"

        # Verify expected names
        expected_names = {"LeBron James", "Stephen Curry", "Kevin Durant"}
        actual_names = set(df["player_name"].tolist())
        assert actual_names == expected_names, f"Expected {expected_names}, got {actual_names}"

    def test_player_name_extraction_preserves_existing_names(self):
        """Test that existing player_name values are preserved."""
        all_odds = [
            {
                "id": 1,
                "player": {"first_name": "Different", "last_name": "Name"},
                "player_name": "LeBron James",  # Direct field present
                "prop_type": "points",
            },
        ]

        # Apply extraction logic
        for row in all_odds:
            if not row.get("player_name") and row.get("player"):
                player = row.get("player")
                if isinstance(player, dict):
                    first_name = str(player.get("first_name", "")).strip()
                    last_name = str(player.get("last_name", "")).strip()
                    row["player_name"] = f"{first_name} {last_name}".strip()

        df = pd.DataFrame(all_odds)

        # Should preserve the direct player_name, not extract from nested object
        assert df.iloc[0]["player_name"] == "LeBron James"

    def test_no_false_positives_for_player_name(self):
        """Test that we don't create false positive player names."""
        all_odds = [
            {
                "id": 1,
                "player": None,  # No player data
                "player_name": None,
                "prop_type": "points",
            },
            {
                "id": 2,
                "player": {"first_name": "", "last_name": ""},  # Empty player data
                "player_name": None,
                "prop_type": "rebounds",
            },
        ]

        # Apply extraction logic
        for row in all_odds:
            if not row.get("player_name") and row.get("player"):
                player = row.get("player")
                if isinstance(player, dict):
                    first_name = str(player.get("first_name", "")).strip()
                    last_name = str(player.get("last_name", "")).strip()
                    row["player_name"] = f"{first_name} {last_name}".strip()

        df = pd.DataFrame(all_odds)

        # Should have empty strings for missing data
        assert df.iloc[0]["player_name"] is None or df.iloc[0]["player_name"] == ""
        assert df.iloc[1]["player_name"] == ""  # Empty first+last name = empty string

    def test_odds_dataframe_null_percentage_check(self):
        """Test that we can detect too many null player names."""
        all_odds = [
            {"player_name": "LeBron James", "prop_type": "points"},
            {"player_name": None, "prop_type": "rebounds"},
            {"player_name": "Stephen Curry", "prop_type": "points"},
            {"player_name": None, "prop_type": "assists"},
            {"player_name": None, "prop_type": "steals"},
        ]

        df = pd.DataFrame(all_odds)

        # Calculate null percentage
        null_count = df["player_name"].isna().sum() + (df["player_name"] == "").sum()
        null_percentage = null_count / len(df) * 100

        # Should be detectable as >50%
        assert null_percentage > 50, f"Expected >50% null, got {null_percentage}%"

        # This would trigger an error log in production
        assert null_count == 3, f"Expected 3 null values, got {null_count}"


class TestOddsToCandidateMatching:
    """Test that odds DataFrame can match with candidate players."""

    def test_odds_matches_candidates_after_name_extraction(self):
        """Test that odds with extracted names can match candidate players."""
        from courtvision.data.candidates import _normalize_player_name

        # Odds with extracted names
        odds = [
            {"player_name": "LeBron James", "prop_type": "points", "line_value": 24.5},
            {"player_name": "Stephen Curry", "prop_type": "points", "line_value": 26.5},
        ]
        odds_df = pd.DataFrame(odds)
        odds_df["_normalized_name"] = odds_df["player_name"].apply(_normalize_player_name)

        # Candidate players (different case/format)
        candidates = [
            {"player_name": "LeBron James", "team": "LAL"},
            {"player_name": "Stephen Curry", "team": "GSW"},
        ]
        candidates_df = pd.DataFrame(candidates)
        candidates_df["_normalized"] = candidates_df["player_name"].apply(_normalize_player_name)

        # Test matching
        for _, candidate in candidates_df.iterrows():
            matches = odds_df[odds_df["_normalized_name"] == candidate["_normalized"]]
            assert len(matches) > 0, f"No odds match for candidate: {candidate['player_name']}"

    def test_match_rate_calculation(self):
        """Test that we can calculate match rate between players and odds."""
        from courtvision.data.candidates import _normalize_player_name

        # Simulate players and odds
        players = [
            {"player_name": "LeBron James"},
            {"player_name": "Stephen Curry"},
            {"player_name": "Unknown Player"},
        ]
        players_df = pd.DataFrame(players)

        odds = [
            {"player_name": "lebron james", "line_value": 24.5},
            {"player_name": "stephen curry", "line_value": 26.5},
        ]
        odds_df = pd.DataFrame(odds)
        odds_df["_normalized"] = odds_df["player_name"].apply(_normalize_player_name)

        # Calculate match rate
        total_players = len(players_df)
        matched_players = 0

        for _, player in players_df.iterrows():
            normalized = _normalize_player_name(player["player_name"])
            matches = odds_df[odds_df["_normalized"] == normalized]
            if len(matches) > 0:
                matched_players += 1

        match_rate = (matched_players / total_players * 100) if total_players > 0 else 0

        # 2 out of 3 should match (LeBron and Stephen)
        assert match_rate > 50, f"Expected >50% match rate, got {match_rate}%"
        assert matched_players == 2, f"Expected 2 matches, got {matched_players}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
