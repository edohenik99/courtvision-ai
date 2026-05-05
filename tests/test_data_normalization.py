from __future__ import annotations

import pandas as pd

from courtvision.data.normalization import (
    infer_opponent_from_game,
    normalize_games_frame,
    normalize_injuries_frame,
    normalize_odds_frame,
    normalize_stats_frame,
    parse_minutes,
    safe_float,
)


def test_parse_minutes_handles_clock_and_numeric_values() -> None:
    assert parse_minutes("31:30") == 31.5
    assert parse_minutes("18") == 18.0
    assert parse_minutes(None) == 0.0


def test_safe_float_handles_noise() -> None:
    assert safe_float("1,234.5") == 1234.5
    assert safe_float("nan") is None
    assert safe_float(True) == 1.0


def test_infer_opponent_from_game_uses_team_abbreviation() -> None:
    game = {
        "home_team": {"abbreviation": "LAL"},
        "visitor_team": {"abbreviation": "BOS"},
    }
    assert infer_opponent_from_game(game, "LAL") == "BOS"
    assert infer_opponent_from_game(game, "BOS") == "LAL"


def test_normalize_stats_frame_creates_clean_projection_inputs() -> None:
    raw = pd.DataFrame(
        [
            {
                "player": {"id": 1, "first_name": "Jalen", "last_name": "Brunson"},
                "game": {
                    "id": 99,
                    "date": "2026-04-15T00:00:00.000Z",
                    "home_team": {"abbreviation": "NYK"},
                    "visitor_team": {"abbreviation": "MIA"},
                },
                "team": {"id": 10, "abbreviation": "NYK"},
                "min": "35:30",
                "pts": "28",
                "reb": 4,
                "ast": 7,
                "stl": 1,
                "blk": 0,
                "fg3m": 3,
            }
        ]
    )

    out = normalize_stats_frame(raw)
    assert list(out["player_name"]) == ["Jalen Brunson"]
    assert list(out["team_abbr"]) == ["NYK"]
    assert list(out["opponent_abbr"]) == ["MIA"]
    assert float(out.iloc[0]["min"]) == 35.5


def test_normalize_games_frame_returns_expected_columns() -> None:
    raw = pd.DataFrame(
        [
            {
                "id": 12,
                "home_team": {"abbreviation": "DEN"},
                "visitor_team": {"abbreviation": "PHX"},
                "status": "Final",
            }
        ]
    )
    out = normalize_games_frame(raw)
    assert out.iloc[0]["home_team_abbr"] == "DEN"
    assert out.iloc[0]["visitor_team_abbr"] == "PHX"


def test_normalize_games_frame_iso_datetime_in_status_becomes_unknown() -> None:
    """BallDontLie sometimes puts the game datetime in the status field."""
    raw = pd.DataFrame(
        [
            {
                "id": 12,
                "home_team": {"abbreviation": "LAL"},
                "visitor_team": {"abbreviation": "GSW"},
                "status": "2026-05-05T01:30:00Z",
                "date": "2026-05-05",
                "datetime": "2026-05-05T01:30:00.000Z",
            }
        ]
    )
    out = normalize_games_frame(raw)
    assert out.iloc[0]["status"] == "unknown"
    assert out.iloc[0]["datetime"] == "2026-05-05T01:30:00.000Z"
    # datetime should be preserved even if it already existed


def test_normalize_games_frame_iso_datetime_in_status_preserves_when_datetime_missing() -> None:
    """If status is a datetime and the explicit datetime field is missing, use status value."""
    raw = pd.DataFrame(
        [
            {
                "id": 12,
                "home_team": {"abbreviation": "LAL"},
                "visitor_team": {"abbreviation": "GSW"},
                "status": "2026-05-05T01:30:00Z",
                "date": "2026-05-05",
            }
        ]
    )
    out = normalize_games_frame(raw)
    assert out.iloc[0]["status"] == "unknown"
    assert out.iloc[0]["datetime"] == "2026-05-05T01:30:00Z"


def test_normalize_odds_frame_maps_market_and_numeric_columns() -> None:
    raw = pd.DataFrame(
        [
            {
                "raw_market_name": "player_points",
                "player_id": "7",
                "line": "24.5",
                "over_odds": "-115",
                "under_odds": "-105",
                "odds": "-115",
            }
        ]
    )
    out = normalize_odds_frame(
        raw,
        map_market_type=lambda value: str(value),
        market_to_stat_key=lambda value: "pts" if value == "player_points" else None,
    )
    assert out.iloc[0]["market_type"] == "player_points"
    assert out.iloc[0]["raw_stat_key"] == "pts"
    assert float(out.iloc[0]["line"]) == 24.5


def test_normalize_injuries_frame_builds_player_name_when_missing() -> None:
    raw = pd.DataFrame(
        [
            {
                "player.first_name": "Tyrese",
                "player.last_name": "Haliburton",
                "player.id": 44,
                "status": "Out",
                "description": "Hamstring",
            }
        ]
    )
    out = normalize_injuries_frame(raw)
    assert out.iloc[0]["player_name"] == "Tyrese Haliburton"
    assert out.iloc[0]["player_id"] == 44
