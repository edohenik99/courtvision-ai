"""Regression test for player-prop team resolution.

Ensures resolved player identity lookup is source of truth for team assignment,
preventing stale API team data from causing bad assignments like James Harden LAC -> CLE.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPlayerPropTeamResolution:
    """Test that player props use resolved identity for team assignment."""

    def test_resolved_identity_team_priority(self):
        """
        PROOF: Stale raw team + correct resolved identity returns lookup team_abbr.

        This test proves the fix in _normalize_player_prop_row() where:
        - Raw API data has stale team (LAC)
        - Resolved identity lookup has correct team (CLE)
        - Result must be CLE (lookup wins)

        Prevents bad assignments like James Harden showing as LAC instead of CLE.
        """
        # Stale API data says LAC, resolved identity says CLE
        player_lookup = {
            123: {"player_name": "James Harden", "team_abbr": "CLE"}  # Correct team
        }

        market = {
            "player_id": 123,
            "player": {"first_name": "James", "last_name": "Harden"},
            "team": {"abbreviation": "LAC", "abbr": "LAC"},  # Stale API data
            "team_abbr": "LAC",  # Stale API data
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": 24.5,
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }

        from courtvision_ai import CourtVisionAI

        ai = CourtVisionAI(api_key="test_key")
        result = ai._normalize_player_prop_row(market, player_lookup=player_lookup)

        assert result is not None, "Should return a valid row"
        assert result["team"] == "CLE", f"Expected CLE from resolved identity, got {result['team']}"

    def test_fallback_to_api_team_when_no_resolved_identity(self):
        """When no resolved identity, fall back to API team data."""
        player_lookup = {}  # No resolved identity

        market = {
            "player_id": 456,
            "player": {"first_name": "LeBron", "last_name": "James"},
            "team": {"abbreviation": "LAL", "abbr": "LAL"},
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": 26.5,
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }

        from courtvision_ai import CourtVisionAI

        ai = CourtVisionAI(api_key="test_key")
        result = ai._normalize_player_prop_row(market, player_lookup=player_lookup)

        assert result is not None
        assert result["team"] == "LAL", f"Expected LAL from API, got {result['team']}"

    def test_no_resolved_identity_no_api_team(self):
        """When no resolved identity and no team data, team should be None/empty."""
        player_lookup = {}

        market = {
            "player_id": 789,
            "player": {"first_name": "Kevin", "last_name": "Durant"},
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": 28.5,
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }

        from courtvision_ai import CourtVisionAI

        ai = CourtVisionAI(api_key="test_key")
        result = ai._normalize_player_prop_row(market, player_lookup=player_lookup)

        assert result is not None
        assert result["team"] is None or result["team"] == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
