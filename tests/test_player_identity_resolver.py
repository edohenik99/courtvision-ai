from __future__ import annotations

import pandas as pd

from courtvision.context.game_context import (
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_STALE_TEAM_REASON,
)
from courtvision.context.player_identity import (
    BASELINE_PROVIDER_TEAM_CONFLICT_REASON,
    PLAYER_ID_TEAM_CONFLICT_REASON,
    PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON,
    build_canonical_player_identity_resolver,
)


def test_resolver_detects_player_id_multiple_teams_and_marks_noncanonical_candidate() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 1,
                "home_team_abbr": "CLE",
                "visitor_team_abbr": "DET",
            }
        ]
    )
    baselines = pd.DataFrame(
        [
            {"player_id": 192, "player_name": "James Harden", "team_abbr": "LAC"},
            {"player_id": 192, "player_name": "James Harden", "team_abbr": "CLE"},
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 1,
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "CLE",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_records(
        [
            {
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "LAC",
                "game_id": 1,
                "game_home_team_abbr": "CLE",
                "game_away_team_abbr": "DET",
            },
            {
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "CLE",
                "game_id": 1,
                "game_home_team_abbr": "CLE",
                "game_away_team_abbr": "DET",
            },
        ]
    )
    by_team = {row["team_abbr"]: row for row in annotated}

    assert resolver.summary()["counts_by_reason"] == {PLAYER_ID_TEAM_CONFLICT_REASON: 1}
    assert by_team["LAC"]["canonical_team_abbr"] == "CLE"
    assert by_team["LAC"]["player_identity_conflict_reason"] == PLAYER_ID_TEAM_CONFLICT_REASON
    assert by_team["LAC"]["identity_quarantine_reason"] == IDENTITY_OUTSIDE_TEAM_REASON
    assert by_team["LAC"]["candidate_team_not_in_game"] is True
    assert by_team["CLE"]["player_identity_valid"] is True
    assert by_team["CLE"]["player_identity_conflict_reason"] == ""


def test_resolver_detects_provider_player_id_multiple_teams() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 11,
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "NYK",
            }
        ]
    )
    baselines = pd.DataFrame(
        [{"player_id": 99, "player_name": "Jayson Tatum", "team_abbr": "BOS"}]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 11,
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "BOS",
                "provider_team_abbr": "BOS",
                "market_type": "player_points",
            },
            {
                "game_id": 11,
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "NYK",
                "provider_team_abbr": "NYK",
                "market_type": "player_rebounds",
            },
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_records(
        [
            {
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "BOS",
                "provider_team_abbr": "BOS",
                "game_id": 11,
                "game_home_team_abbr": "BOS",
                "game_away_team_abbr": "NYK",
            },
            {
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "NYK",
                "provider_team_abbr": "NYK",
                "game_id": 11,
                "game_home_team_abbr": "BOS",
                "game_away_team_abbr": "NYK",
            },
        ]
    )
    by_team = {row["team_abbr"]: row for row in annotated}

    assert resolver.summary()["counts_by_reason"] == {
        BASELINE_PROVIDER_TEAM_CONFLICT_REASON: 1,
        PLAYER_ID_TEAM_CONFLICT_REASON: 1,
    }
    assert by_team["BOS"]["player_identity_valid"] is True
    assert by_team["NYK"]["player_identity_conflict_reason"] == PLAYER_ID_TEAM_CONFLICT_REASON
    assert by_team["NYK"]["identity_quarantine_reason"] == IDENTITY_STALE_TEAM_REASON


def test_resolver_detects_stale_baseline_provider_team_conflict() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 2,
                "home_team_abbr": "CLE",
                "visitor_team_abbr": "LAC",
            }
        ]
    )
    baselines = pd.DataFrame(
        [{"player_id": 192, "player_name": "James Harden", "team_abbr": "CLE"}]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 2,
                "player_id": 192,
                "player_name": "James Harden",
                "team_abbr": "LAC",
                "provider_team_abbr": "LAC",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_record(
        {
            "player_id": 192,
            "player_name": "James Harden",
            "team_abbr": "CLE",
            "baseline_team_abbr": "CLE",
            "provider_team_abbr": "LAC",
            "game_id": 2,
            "game_home_team_abbr": "CLE",
            "game_away_team_abbr": "LAC",
        }
    )

    assert resolver.summary()["counts_by_reason"] == {BASELINE_PROVIDER_TEAM_CONFLICT_REASON: 1}
    assert annotated["player_identity_valid"] is False
    assert annotated["player_identity_conflict_reason"] == BASELINE_PROVIDER_TEAM_CONFLICT_REASON
    assert annotated["identity_quarantine_reason"] == IDENTITY_STALE_TEAM_REASON


def test_resolver_keeps_clean_valid_player_clear() -> None:
    games = pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-18",
                "game_id": 3,
                "home_team_abbr": "BOS",
                "visitor_team_abbr": "NYK",
            }
        ]
    )
    baselines = pd.DataFrame(
        [{"player_id": 99, "player_name": "Jayson Tatum", "team_abbr": "BOS"}]
    )
    odds = pd.DataFrame(
        [
            {
                "game_id": 3,
                "player_id": 99,
                "player_name": "Jayson Tatum",
                "team_abbr": "BOS",
                "provider_team_abbr": "BOS",
                "market_type": "player_points",
            }
        ]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )
    annotated = resolver.annotate_record(
        {
            "player_id": 99,
            "player_name": "Jayson Tatum",
            "team_abbr": "BOS",
            "baseline_team_abbr": "BOS",
            "provider_team_abbr": "BOS",
            "game_id": 3,
            "game_home_team_abbr": "BOS",
            "game_away_team_abbr": "NYK",
        }
    )

    assert resolver.summary()["status"] == "ok"
    assert annotated["canonical_team_abbr"] == "BOS"
    assert annotated["player_identity_valid"] is True
    assert annotated["player_identity_conflict_reason"] == ""
    assert "identity_quarantine_reason" not in annotated


def test_resolver_detects_provider_team_outside_active_game() -> None:
    games = pd.DataFrame(
        [{"prediction_date": "2026-05-18", "game_id": 4, "home_team_abbr": "CLE", "visitor_team_abbr": "DET"}]
    )
    baselines = pd.DataFrame(
        [{"player_id": 17, "player_name": "Dennis Schroder", "team_abbr": "SAC"}]
    )
    odds = pd.DataFrame(
        [{"game_id": 4, "player_id": 17, "player_name": "Dennis Schroder", "team_abbr": "SAC"}]
    )

    resolver = build_canonical_player_identity_resolver(
        prediction_date="2026-05-18",
        player_baselines=baselines,
        odds=odds,
        games=games,
    )

    assert resolver.summary()["counts_by_reason"] == {PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON: 1}
