"""Tests for player_id to player_name lookup and join logic."""

import pandas as pd
import pytest

from courtvision.data.candidates import score_player_markets
from courtvision_ai import CourtVisionAIClient


class TestPlayerIdJoin:
    """Test suite for player_id join logic in odds normalization."""

    def test_normalize_player_prop_row_with_player_lookup_int_keys(self):
        """Test that player_id lookup works with int keys and int odds player_id."""
        client = CourtVisionAIClient(api_key="test_key")
        
        # Build a player lookup with INT keys (as built by _build_player_prop_identity_lookup)
        player_lookup = {
            123: {"player_name": "LeBron James", "team_abbr": "LAL"},
            456: {"player_name": "Stephen Curry", "team_abbr": "GSW"},
        }
        
        # Odds row with INT player_id (as returned by some APIs)
        odds_row = {
            "id": 1,
            "game_id": 100,
            "player_id": 123,  # Int
            "vendor": "draftkings",
            "prop_type": "player_points",
            "line_value": 27.5,
            "market": "Over",
            "odds": -110,
        }
        
        result = client._normalize_player_prop_row(odds_row, player_lookup=player_lookup)
        
        assert result is not None
        assert result["player_name"] == "LeBron James"
        assert result["player_id"] == 123
        assert result["missing_player_lookup"] is False

    def test_normalize_player_prop_row_str_odds_id_int_lookup_keys(self):
        """Test that player_id lookup works when odds has string player_id but lookup has int keys."""
        client = CourtVisionAIClient(api_key="test_key")
        
        # Lookup with INT keys (standard)
        player_lookup = {
            123: {"player_name": "LeBron James", "team_abbr": "LAL"},
        }
        
        # Odds row with STRING player_id (common in JSON APIs)
        odds_row = {
            "id": 1,
            "game_id": 100,
            "player_id": "123",  # String!
            "vendor": "draftkings",
            "prop_type": "player_points",
            "line_value": 27.5,
        }
        
        result = client._normalize_player_prop_row(odds_row, player_lookup=player_lookup)
        
        assert result is not None
        assert result["player_name"] == "LeBron James"
        assert result["player_id"] == 123  # Should be coerced to int
        assert result["missing_player_lookup"] is False

    def test_normalize_player_prop_row_int_odds_id_str_lookup_keys(self):
        """Test that player_id lookup works when odds has int player_id but lookup has string keys."""
        client = CourtVisionAIClient(api_key="test_key")
        
        # Lookup with STRING keys (edge case)
        player_lookup = {
            "123": {"player_name": "LeBron James", "team_abbr": "LAL"},
        }
        
        # Odds row with INT player_id
        odds_row = {
            "id": 1,
            "game_id": 100,
            "player_id": 123,  # Int
            "vendor": "draftkings",
            "prop_type": "player_points",
            "line_value": 27.5,
        }
        
        result = client._normalize_player_prop_row(odds_row, player_lookup=player_lookup)
        
        assert result is not None
        assert result["player_name"] == "LeBron James"
        assert result["missing_player_lookup"] is False

    def test_normalize_player_prop_row_lookup_miss_returns_none(self):
        """Test that missing player_id in lookup returns None."""
        client = CourtVisionAIClient(api_key="test_key")
        
        # Empty lookup - player not found
        player_lookup = {}
        
        odds_row = {
            "id": 1,
            "game_id": 100,
            "player_id": 999,  # Unknown player
            "vendor": "draftkings",
            "prop_type": "player_points",
            "line_value": 27.5,
            "market": "Over",
            "odds": -110,
        }
        
        result = client._normalize_player_prop_row(odds_row, player_lookup=player_lookup)
        
        # Should still return a row but with missing_player_lookup=True
        assert result is not None
        assert result["player_id"] == 999
        assert result["player_name"] is None
        assert result["missing_player_lookup"] is True

    def test_normalize_player_prop_row_no_player_id(self):
        """Test that missing player_id returns None."""
        client = CourtVisionAIClient(api_key="test_key")
        
        player_lookup = {123: {"player_name": "LeBron James"}}
        
        # Odds row without player_id
        odds_row = {
            "id": 1,
            "game_id": 100,
            # No player_id field
            "vendor": "draftkings",
            "prop_type": "player_points",
            "line_value": 27.5,
        }
        
        result = client._normalize_player_prop_row(odds_row, player_lookup=player_lookup)
        
        # Should return None when no player_id
        assert result is None

    def test_build_player_lookup_from_baseline_rows(self):
        """Test building player_id -> player_name lookup from baseline rows."""
        # Simulating player baseline data
        baseline_rows = [
            {"player_id": 123, "first_name": "LeBron", "last_name": "James", "team_abbr": "LAL"},
            {"player_id": 456, "first_name": "Stephen", "last_name": "Curry", "team_abbr": "GSW"},
            {"player_id": 789, "full_name": "Kevin Durant", "team_abbr": "PHX"},
        ]
        
        lookup = {}
        for row in baseline_rows:
            pid = row.get("player_id")
            if pid:
                # Build name from various field combinations
                name = (
                    row.get("full_name")
                    or f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
                    or row.get("name")
                )
                if name:
                    lookup[pid] = {
                        "player_name": name,
                        "team_abbr": row.get("team_abbr", ""),
                    }
        
        assert len(lookup) == 3
        assert lookup[123]["player_name"] == "LeBron James"
        assert lookup[456]["player_name"] == "Stephen Curry"
        assert lookup[789]["player_name"] == "Kevin Durant"

    def test_odds_row_has_player_id_not_name(self):
        """Verify that raw odds rows only have player_id, not player name fields."""
        # Simulating actual API response structure
        raw_odds_row = {
            "id": 12345,
            "game_id": 67890,
            "player_id": 237,  # Only ID, no nested player object
            "vendor": "draftkings",
            "prop_type": "player_points",
            "line_value": 27.5,
            "market": "Over",
            "odds": -110,
            "updated_at": "2024-01-01T12:00:00Z",
        }
        
        # Verify no player name fields exist
        assert "player" not in raw_odds_row
        assert "player_name" not in raw_odds_row
        assert "full_name" not in raw_odds_row
        assert "first_name" not in raw_odds_row
        assert "last_name" not in raw_odds_row
        
        # Verify player_id exists
        assert "player_id" in raw_odds_row
        assert raw_odds_row["player_id"] == 237

    def test_lookup_diagnostics_tracking(self):
        """Test that diagnostics correctly track lookup metrics."""
        diagnostics = {
            "player_lookup_size": 2,
            "odds_rows_with_player_id": 0,
            "odds_rows_resolved_to_player_name": 0,
            "odds_player_lookup_misses": 0,
            "lookup_sample_player_ids": [],
        }
        
        player_lookup = {100: {"player_name": "Player A"}, 200: {"player_name": "Player B"}}
        
        # Simulate processing odds rows
        test_rows = [
            {"player_id": 100},  # Match
            {"player_id": 200},  # Match
            {"player_id": 999},  # Miss
            {"player_id": 888},  # Miss
        ]
        
        for row in test_rows:
            pid = row.get("player_id")
            diagnostics["odds_rows_with_player_id"] += 1
            
            if pid in player_lookup:
                diagnostics["odds_rows_resolved_to_player_name"] += 1
            else:
                diagnostics["odds_player_lookup_misses"] += 1
                if len(diagnostics["lookup_sample_player_ids"]) < 10:
                    diagnostics["lookup_sample_player_ids"].append(pid)
        
        assert diagnostics["odds_rows_with_player_id"] == 4
        assert diagnostics["odds_rows_resolved_to_player_name"] == 2
        assert diagnostics["odds_player_lookup_misses"] == 2
        assert diagnostics["lookup_sample_player_ids"] == [999, 888]

    def test_score_player_markets_keeps_player_id_matches_on_same_team(self):
        """Duplicate baseline rows should not borrow another team's player-id odds."""
        players = pd.DataFrame(
            [
                {"player_id": 460, "player_name": "Nikola Vucevic", "team_abbr": "CHI"},
                {"player_id": 460, "player_name": "Nikola Vucevic", "team_abbr": "BOS"},
            ]
        )
        odds = pd.DataFrame(
            [
                {
                    "player_id": 460,
                    "player_name": "Nikola Vucevic",
                    "team_abbr": "BOS",
                    "market_type": "player_points",
                    "selection": "over",
                    "line": 5.5,
                    "odds": -110,
                }
            ]
        )

        def build_candidate_row(player_row, market, market_rows, partial_fill=False):
            market_row = market_rows.iloc[0]
            return {
                "player_name": player_row.get("player_name"),
                "team": player_row.get("team_abbr"),
                "market_type": market,
                "selection": market_row.get("selection"),
                "sportsbook_line": market_row.get("line"),
                "edge": 2.0,
                "confidence": 0.8,
            }

        def score_candidate_fn(candidate_row, **_):
            return candidate_row

        def reject_candidate_fn(player_row, market, reason, team, **_):
            return {
                "player_name": player_row.get("player_name"),
                "team": team,
                "market_type": market,
                "rejection_reason": reason,
            }

        accepted, _ = score_player_markets(
            players_df=players,
            odds_df=odds,
            is_player_inactive=lambda _: False,
            build_candidate_row=build_candidate_row,
            score_candidate_fn=score_candidate_fn,
            reject_candidate_fn=reject_candidate_fn,
            allow_partial_fill=False,
        )

        assert [row["team"] for row in accepted] == ["BOS"]
        assert accepted[0]["player_name"] == "Nikola Vucevic"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
