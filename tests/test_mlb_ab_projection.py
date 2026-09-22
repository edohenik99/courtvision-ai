"""Offline contracts for CourtVision-owned MLB at-bat projection v1."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import inspect
import json

import pytest

from courtvision.sports.mlb.ab_projection import (
    AB_PROJECTION_METHOD,
    AB_PROJECTION_MODEL_VERSION,
    AB_PROJECTION_SOURCE,
    AtBatProjectionError,
    assemble_acquired_batter_hits_features,
    project_batter_at_bats,
)
from courtvision.sports.mlb.hits_acquisition import AcquiredBatterHitsEvidence
from courtvision.sports.mlb.hits_features import (
    BatterLineupEvidence,
    BatterSeasonHittingEvidence,
)
from courtvision.sports.mlb.hits_identity import bind_mlb_event, resolve_mlb_batter_identity
from courtvision.sports.mlb.data.prospective_context_acquisition import parse_mlb_schedule
from courtvision.sports.mlb.providers.the_odds_api_market_adapter import normalize_mlb_event_odds


OBSERVED = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)
GENERATED = datetime(2026, 9, 20, 16, 5, tzinfo=timezone.utc)
START = datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)
GAME_ID = "823184"
PLAYER_ID = "700001"
CANONICAL_NAME = "José Ramírez"


def _record():
    payload = {
        "id": "provider-hits-001",
        "sport_key": "baseball_mlb",
        "sport_title": "MLB",
        "commence_time": START.isoformat(),
        "home_team": "Cleveland Guardians",
        "away_team": "Minnesota Twins",
        "bookmakers": [{
            "key": "fixture-book",
            "title": "Fixture Book",
            "last_update": OBSERVED.isoformat(),
            "markets": [{
                "key": "batter_hits",
                "last_update": OBSERVED.isoformat(),
                "outcomes": [{
                    "description": "Jose Ramirez",
                    "name": "Over",
                    "point": 0.5,
                    "price": -150,
                }],
            }],
        }],
    }
    batch = normalize_mlb_event_odds(
        payload,
        collected_at=CUTOFF,
        source_refs=("fixture:odds",),
    )
    assert len(batch.records) == 1
    return batch.records[0]


def _schedule():
    payload = {"dates": [{"games": [{
        "gamePk": int(GAME_ID),
        "officialDate": "2026-09-20",
        "gameDate": START.isoformat(),
        "teams": {
            "home": {"team": {"id": 114, "name": "Cleveland Guardians"}},
            "away": {"team": {"id": 142, "name": "Minnesota Twins"}},
        },
        "venue": {"id": 5, "name": "Synthetic Park"},
        "status": {"detailedState": "Scheduled"},
    }]}]}
    return parse_mlb_schedule(
        json.dumps(payload).encode("utf-8"),
        operating_date=date(2026, 9, 20),
    )


def _feed():
    people = {
        "ID700001": {
            "id": 700001,
            "fullName": CANONICAL_NAME,
            "currentTeam": {"id": 114},
        },
        "ID700002": {
            "id": 700002,
            "fullName": "Home Teammate",
            "currentTeam": {"id": 114},
        },
    }
    return {
        "gamePk": int(GAME_ID),
        "gameData": {
            "datetime": {"dateTime": START.isoformat()},
            "teams": {"home": {"id": 114}, "away": {"id": 142}},
            "venue": {"id": 5},
            "players": people,
        },
        "liveData": {"boxscore": {"teams": {
            "home": {
                "team": {"id": 114},
                "battingOrder": [700002, 700001],
                "players": {
                    key: {"person": {"id": value["id"], "fullName": value["fullName"]}}
                    for key, value in people.items()
                },
            },
            "away": {"team": {"id": 142}, "battingOrder": [], "players": {}},
        }}},
    }


def _player_binding():
    record = _record()
    event = bind_mlb_event(
        record,
        _schedule(),
        observed_at=OBSERVED,
        evidence_cutoff=CUTOFF,
        source_refs=("fixture:schedule",),
    )
    return resolve_mlb_batter_identity(
        record,
        event,
        _feed(),
        observed_at=OBSERVED,
        evidence_cutoff=CUTOFF,
        source_refs=("fixture:roster",),
    )


def _acquired(
    *,
    at_bats=500,
    games_played=130,
    hits=125,
    lineup_status="statsapi_batting_order_present",
    batting_order_position=2,
):
    player = _player_binding()
    season = BatterSeasonHittingEvidence(
        season=2026,
        mlbam_player_id=PLAYER_ID,
        player_name=CANONICAL_NAME,
        hits=hits,
        at_bats=at_bats,
        observed_at=OBSERVED,
        evidence_cutoff=CUTOFF,
        source="mlb_statsapi",
        source_refs=("fixture:season",),
        games_played=games_played,
    )
    lineup = BatterLineupEvidence(
        mlbam_game_id=GAME_ID,
        mlbam_player_id=PLAYER_ID,
        player_name=CANONICAL_NAME,
        team_side="home",
        batting_order_position=batting_order_position,
        lineup_status=lineup_status,
        observed_at=OBSERVED,
        evidence_cutoff=CUTOFF,
        source_refs=("fixture:lineup",),
    )
    return AcquiredBatterHitsEvidence(
        player_binding=player,
        season_evidence=season,
        lineup_evidence=lineup,
        evidence_cutoff=CUTOFF,
    )


def test_v1_projects_player_observed_season_at_bats_per_game():
    acquired = _acquired()
    projection = project_batter_at_bats(acquired, generated_at=GENERATED)

    assert projection.projected_at_bats == pytest.approx(500 / 130)
    assert projection.projection_method == AB_PROJECTION_METHOD
    assert projection.model_version == AB_PROJECTION_MODEL_VERSION
    assert projection.source == AB_PROJECTION_SOURCE
    assert projection.mlbam_game_id == GAME_ID
    assert projection.mlbam_player_id == PLAYER_ID
    assert projection.player_name == CANONICAL_NAME
    assert projection.evidence_cutoff == CUTOFF
    assert projection.generated_at == GENERATED
    assert any(ref.startswith("cv_ab_projection_provenance=") for ref in projection.source_refs)


def test_v1_requires_confirmed_batting_order_and_never_defaults_opportunity():
    unavailable = _acquired(
        lineup_status="unavailable",
        batting_order_position=None,
    )
    not_listed = _acquired(
        lineup_status="not_listed",
        batting_order_position=None,
    )
    for acquired in (unavailable, not_listed):
        with pytest.raises(AtBatProjectionError, match="confirmed batting-order"):
            project_batter_at_bats(acquired, generated_at=GENERATED)

    missing_history = _acquired()
    missing_history = replace(
        missing_history,
        season_evidence=replace(missing_history.season_evidence, games_played=None),
    )
    with pytest.raises(AtBatProjectionError, match="games_played"):
        project_batter_at_bats(missing_history, generated_at=GENERATED)


def test_v1_uses_opportunity_history_not_hit_rate_or_market_inputs():
    low_hits = project_batter_at_bats(
        _acquired(hits=1), generated_at=GENERATED
    )
    high_hits = project_batter_at_bats(
        _acquired(hits=400), generated_at=GENERATED
    )
    assert low_hits.projected_at_bats == high_hits.projected_at_bats

    parameters = inspect.signature(project_batter_at_bats).parameters
    prohibited = {
        "odds", "american_odds", "decimal_odds", "implied_probability",
        "sportsbook", "edge", "kelly", "official_pick", "market_movement",
    }
    assert not prohibited.intersection(parameters)


def test_v1_slot_is_provenance_only_until_calibrated_v2():
    second = project_batter_at_bats(
        _acquired(batting_order_position=2), generated_at=GENERATED
    )
    ninth = project_batter_at_bats(
        _acquired(batting_order_position=9), generated_at=GENERATED
    )
    assert second.projected_at_bats == ninth.projected_at_bats
    assert any('"batting_order_position":2' in ref for ref in second.source_refs)
    assert any('"batting_order_position":9' in ref for ref in ninth.source_refs)


@pytest.mark.parametrize(
    "generated",
    [
        CUTOFF - timedelta(seconds=1),
        START,
        START + timedelta(seconds=1),
        GENERATED.replace(tzinfo=None),
    ],
)
def test_projection_clock_must_be_post_evidence_and_strictly_pregame(generated):
    with pytest.raises(AtBatProjectionError):
        project_batter_at_bats(_acquired(), generated_at=generated)


def test_projection_integrates_with_existing_hits_feature_assembler():
    acquired = _acquired()
    features = assemble_acquired_batter_hits_features(
        acquired,
        generated_at=GENERATED,
    )

    assert features.projected_at_bats == pytest.approx(500 / 130)
    assert features.season_hits == 125
    assert features.season_at_bats == 500
    assert features.lineup_status == "statsapi_batting_order_present"
    assert features.evidence_cutoff == GENERATED
    assert any("cv_ab_projection_v1" in ref for ref in features.source_refs)


@pytest.mark.parametrize("games_played", [0, -1, 1.5, True, "130"])
def test_games_played_contract_never_coerces_invalid_values(games_played):
    with pytest.raises(ValueError, match="games_played"):
        _acquired(games_played=games_played)
