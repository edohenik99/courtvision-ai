"""Offline contracts for two-phase MLB Hits evidence acquisition."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import socket
from types import MappingProxyType
import urllib.request

import pytest
import requests

from courtvision.core.candidates import IdentityStatus
from courtvision.sports.mlb.data.prospective_context_acquisition import ProviderResponse
from courtvision.sports.mlb.hits_acquisition import (
    HitsAcquisitionError,
    acquire_hits_game_feed,
    acquire_hits_season_hitting,
    captured_hits_source,
    hits_game_feed_request,
    hits_season_hitting_request,
    hits_season_hitting_requests,
    materialize_acquired_hits_evidence,
    resolve_hits_player_from_capture,
)
from courtvision.sports.mlb.hits_identity import bind_mlb_event
from courtvision.sports.mlb.data.prospective_context_acquisition import parse_mlb_schedule
from courtvision.sports.mlb.providers.the_odds_api_market_adapter import normalize_mlb_event_odds


START = datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)
IDENTITY_OBSERVED = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
IDENTITY_CUTOFF = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)
ACQUIRE_AT = START - timedelta(minutes=60)
SEASON_ACQUIRE_AT = START - timedelta(minutes=55)
ACQUISITION_CUTOFF = START - timedelta(minutes=45)
GAME_ID = "823184"
PLAYER_ID = "700001"
COMMIT = "d31858bb982df1e4a2d8806bcb4391a54c0a7d03"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Hits acquisition tests must stay offline")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(socket, "gethostbyname", blocked)


class MockProvider:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, request):
        self.calls.append(request.request_id)
        result = self.responses[request.request_id]
        if isinstance(result, BaseException):
            raise result
        return result


def _record(*, name="Jose Ramirez"):
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
            "last_update": IDENTITY_OBSERVED.isoformat(),
            "markets": [{
                "key": "batter_hits",
                "last_update": IDENTITY_OBSERVED.isoformat(),
                "outcomes": [{
                    "description": name,
                    "name": "Over",
                    "point": 0.5,
                    "price": -150,
                }],
            }],
        }],
    }
    batch = normalize_mlb_event_odds(
        payload,
        collected_at=IDENTITY_CUTOFF,
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


def _event_binding():
    return bind_mlb_event(
        _record(),
        _schedule(),
        observed_at=IDENTITY_OBSERVED,
        evidence_cutoff=IDENTITY_CUTOFF,
        source_refs=("fixture:schedule",),
    )


def _feed(*, duplicate_name=False):
    people = {
        "ID700001": {
            "id": 700001,
            "fullName": "José Ramírez",
            "currentTeam": {"id": 114},
        },
        "ID700002": {
            "id": 700002,
            "fullName": "Home Teammate",
            "currentTeam": {"id": 114},
        },
        "ID600001": {
            "id": 600001,
            "fullName": "Away Batter",
            "currentTeam": {"id": 142},
        },
    }
    if duplicate_name:
        people["ID600002"] = {
            "id": 600002,
            "fullName": "Jose Ramirez",
            "currentTeam": {"id": 142},
        }
    home_players = {
        key: {"person": {"id": value["id"], "fullName": value["fullName"]}}
        for key, value in people.items()
        if value["currentTeam"]["id"] == 114
    }
    away_players = {
        key: {"person": {"id": value["id"], "fullName": value["fullName"]}}
        for key, value in people.items()
        if value["currentTeam"]["id"] == 142
    }
    payload = {
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
                "players": home_players,
            },
            "away": {
                "team": {"id": 142},
                "battingOrder": [600001],
                "players": away_players,
            },
        }}},
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _season_payload(
    *,
    player_id=700001,
    name="José Ramírez",
    hits=125,
    at_bats=500,
    season="2026",
):
    return json.dumps({
        "people": [{
            "id": player_id,
            "fullName": name,
            "stats": [{
                "type": {"displayName": "season"},
                "group": {"displayName": "hitting"},
                "splits": [{
                    "season": season,
                    "player": {"id": player_id, "fullName": name},
                    "stat": {"hits": hits, "atBats": at_bats},
                }],
            }],
        }],
    }, sort_keys=True).encode("utf-8")


def _response(body: bytes, observed: datetime):
    return ProviderResponse(
        body=body,
        status_code=200,
        headers=MappingProxyType({"Content-Type": "application/json"}),
        first_observed_at_utc=observed,
        captured_at_utc=observed + timedelta(seconds=1),
    )


def _identity_capture(tmp_path: Path, *, feed=None, provider=None):
    event = _event_binding()
    request = hits_game_feed_request(event)
    provider = provider or MockProvider({
        request.request_id: _response(feed or _feed(), ACQUIRE_AT)
    })
    capture = acquire_hits_game_feed(
        event,
        observed_at_utc=ACQUIRE_AT,
        evidence_cutoff=ACQUISITION_CUTOFF,
        provider=provider,
        acquisition_root=tmp_path,
        git_commit=COMMIT,
    )
    return event, capture, provider


def _resolved_player(tmp_path: Path):
    event, capture, _ = _identity_capture(tmp_path)
    player = resolve_hits_player_from_capture(_record(), event, capture)
    assert player.participant_identity.identity_status is IdentityStatus.RESOLVED
    return event, player, capture


def test_declared_requests_use_canonical_ids_and_no_name_search():
    event = _event_binding()
    feed_request = hits_game_feed_request(event)
    assert feed_request.request_id == "hits-game-feed-823184"
    assert feed_request.event_id == GAME_ID
    assert feed_request.source_name == "mlb_statsapi_hits_game_feed"
    assert feed_request.url.endswith("/api/v1.1/game/823184/feed/live")
    assert "search" not in feed_request.url.casefold()


def test_game_feed_capture_resolves_unique_player_and_preserves_raw(tmp_path: Path):
    event, capture, provider = _identity_capture(tmp_path)
    request = hits_game_feed_request(event)

    assert capture.capture_state == "completed"
    assert provider.calls == [request.request_id]
    player = resolve_hits_player_from_capture(_record(), event, capture)

    assert player.mlbam_player_id == PLAYER_ID
    assert player.participant_identity.canonical_participant_name == "José Ramírez"
    source = captured_hits_source(capture, request_id=request.request_id)
    assert source.body == _feed()
    assert source.first_observed_at_utc == ACQUIRE_AT
    assert source.evidence_cutoff == ACQUISITION_CUTOFF
    assert source.source_refs[0].startswith(f"capture:{capture.capture_id}:")


def test_early_feed_without_boxscore_remains_usable_for_hits(tmp_path: Path):
    payload = json.loads(_feed().decode("utf-8"))
    payload.pop("liveData")
    feed = json.dumps(payload, sort_keys=True).encode("utf-8")
    event, capture, provider = _identity_capture(tmp_path, feed=feed)

    assert capture.capture_state == "completed"
    assert provider.calls == [hits_game_feed_request(event).request_id]

    player = resolve_hits_player_from_capture(_record(), event, capture)
    assert player.mlbam_player_id == PLAYER_ID

    request = hits_season_hitting_request(player, season=2026)
    season_provider = MockProvider({
        request.request_id: _response(_season_payload(), SEASON_ACQUIRE_AT)
    })
    season_capture = acquire_hits_season_hitting(
        event,
        (player,),
        season=2026,
        observed_at_utc=SEASON_ACQUIRE_AT,
        evidence_cutoff=ACQUISITION_CUTOFF,
        provider=season_provider,
        acquisition_root=tmp_path,
        git_commit=COMMIT,
    )
    acquired = materialize_acquired_hits_evidence(
        player,
        season=2026,
        game_feed_capture=capture,
        season_capture=season_capture,
    )
    assert acquired.lineup_evidence.lineup_status == "unavailable"
    assert acquired.lineup_evidence.batting_order_position is None


def test_wrong_game_hits_feed_is_rejected_before_capture_reuse(tmp_path: Path):
    payload = json.loads(_feed().decode("utf-8"))
    payload["gamePk"] = 823185
    wrong_feed = json.dumps(payload, sort_keys=True).encode("utf-8")
    event = _event_binding()
    request = hits_game_feed_request(event)
    provider = MockProvider({
        request.request_id: _response(wrong_feed, ACQUIRE_AT)
    })

    capture = acquire_hits_game_feed(
        event,
        observed_at_utc=ACQUIRE_AT,
        evidence_cutoff=ACQUISITION_CUTOFF,
        provider=provider,
        acquisition_root=tmp_path,
        git_commit=COMMIT,
    )

    assert capture.capture_state == "rejected"
    manifest = json.loads(capture.manifest_path.read_text(encoding="utf-8"))
    source = manifest["sources"][0]
    assert source["availability_status"] == "rejected"
    assert "game" in source["availability_note"].casefold()
    with pytest.raises(HitsAcquisitionError, match="not completed"):
        captured_hits_source(capture, request_id=request.request_id)


@pytest.mark.parametrize(
    "malformed",
    [
        b'{"gamePk":823185,"gamePk":823184,"gameData":{"datetime":{"dateTime":"2026-09-20T23:00:00+00:00"},"teams":{"home":{"id":114},"away":{"id":142}},"venue":{"id":5}}}',
        b'{"gamePk":823184,"gameData":{"datetime":{"dateTime":"2026-09-20T23:00:00+00:00"},"teams":{"home":{"id":114},"away":{"id":142}},"venue":{"id":5}},"bad":NaN}',
    ],
)
def test_ambiguous_or_nonfinite_hits_feed_is_rejected_before_capture_reuse(
    tmp_path: Path, malformed: bytes
):
    event = _event_binding()
    request = hits_game_feed_request(event)
    provider = MockProvider({
        request.request_id: _response(malformed, ACQUIRE_AT)
    })

    capture = acquire_hits_game_feed(
        event,
        observed_at_utc=ACQUIRE_AT,
        evidence_cutoff=ACQUISITION_CUTOFF,
        provider=provider,
        acquisition_root=tmp_path,
        git_commit=COMMIT,
    )

    assert capture.capture_state == "rejected"
    manifest = json.loads(capture.manifest_path.read_text(encoding="utf-8"))
    source = manifest["sources"][0]
    assert source["availability_status"] == "rejected"
    note = source["availability_note"].casefold()
    assert "duplicate json field" in note or "non-finite json value" in note
    with pytest.raises(HitsAcquisitionError, match="not completed"):
        captured_hits_source(capture, request_id=request.request_id)


def test_ambiguous_roster_never_creates_player_season_request(tmp_path: Path):
    event, capture, _ = _identity_capture(tmp_path, feed=_feed(duplicate_name=True))
    player = resolve_hits_player_from_capture(_record(), event, capture)

    assert player.participant_identity.identity_status is IdentityStatus.AMBIGUOUS
    assert player.mlbam_player_id is None
    with pytest.raises(HitsAcquisitionError, match="uniquely resolved"):
        hits_season_hitting_request(player, season=2026)


def test_season_request_is_id_bound_and_uses_hydrated_hitting_stats(tmp_path: Path):
    _, player, _ = _resolved_player(tmp_path)
    request = hits_season_hitting_request(player, season=2026)

    assert request.player_id == PLAYER_ID
    assert request.event_id == GAME_ID
    assert request.season == 2026
    assert request.request_id == "hits-season-2026-700001"
    assert "/api/v1/people/700001?" in request.url
    assert "hydrate=" in request.url
    assert "hitting" in request.url
    assert "season%5D" in request.url or "season" in request.url
    assert "Jose" not in request.url
    assert "Ramirez" not in request.url


def test_two_phase_capture_materializes_typed_baseball_evidence(tmp_path: Path):
    event, player, feed_capture = _resolved_player(tmp_path)
    request = hits_season_hitting_request(player, season=2026)
    provider = MockProvider({
        request.request_id: _response(_season_payload(), SEASON_ACQUIRE_AT)
    })
    season_capture = acquire_hits_season_hitting(
        event,
        (player,),
        season=2026,
        observed_at_utc=SEASON_ACQUIRE_AT,
        evidence_cutoff=ACQUISITION_CUTOFF,
        provider=provider,
        acquisition_root=tmp_path,
        git_commit=COMMIT,
    )

    acquired = materialize_acquired_hits_evidence(
        player,
        season=2026,
        game_feed_capture=feed_capture,
        season_capture=season_capture,
    )

    assert season_capture.capture_state == "completed"
    assert provider.calls == [request.request_id]
    assert acquired.season_evidence.hits == 125
    assert acquired.season_evidence.at_bats == 500
    assert acquired.season_evidence.source == "mlb_statsapi"
    assert acquired.lineup_evidence.lineup_status == "statsapi_batting_order_present"
    assert acquired.lineup_evidence.batting_order_position == 2
    assert acquired.evidence_cutoff == ACQUISITION_CUTOFF
    assert "projection" not in type(acquired).__annotations__
    assert "projected_at_bats" not in type(acquired).__annotations__


def test_multiple_player_requests_are_deterministic_and_deduplicated(tmp_path: Path):
    event, player, capture = _resolved_player(tmp_path)
    repeated = hits_season_hitting_requests((player, player), season=2026)

    assert len(repeated) == 1
    assert repeated[0].player_id == PLAYER_ID
    assert capture.capture_state == "completed"
    assert event.mlbam_game_id == GAME_ID


def test_unavailable_season_response_fails_closed_at_materialization(tmp_path: Path):
    event, player, feed_capture = _resolved_player(tmp_path)
    request = hits_season_hitting_request(player, season=2026)
    provider = MockProvider({request.request_id: TimeoutError("provider timeout")})
    season_capture = acquire_hits_season_hitting(
        event,
        (player,),
        season=2026,
        observed_at_utc=SEASON_ACQUIRE_AT,
        evidence_cutoff=ACQUISITION_CUTOFF,
        provider=provider,
        acquisition_root=tmp_path,
        git_commit=COMMIT,
    )

    assert season_capture.capture_state == "unavailable"
    with pytest.raises(HitsAcquisitionError, match="not completed"):
        materialize_acquired_hits_evidence(
            player,
            season=2026,
            game_feed_capture=feed_capture,
            season_capture=season_capture,
        )


def test_tampered_preserved_body_is_rejected_before_feature_parsing(tmp_path: Path):
    event, capture, _ = _identity_capture(tmp_path)
    request = hits_game_feed_request(event)
    manifest = json.loads(capture.manifest_path.read_text(encoding="utf-8"))
    record = next(item for item in manifest["sources"] if item["request_id"] == request.request_id)
    body_path = capture.capture_dir / record["body_path"]
    body_path.write_bytes(b'{"tampered":true}')

    with pytest.raises(HitsAcquisitionError, match="raw response digest"):
        captured_hits_source(capture, request_id=request.request_id)


@pytest.mark.parametrize(
    "body,note_fragment",
    [
        (_season_payload(player_id=700999, name="Wrong Batter"), "wrong player"),
        (_season_payload(season="2025"), "wrong season"),
        (_season_payload(hits="125"), "integer counts"),
        (b'{"people":[],"people":[]}', "duplicate json field"),
        (b'{"people":NaN}', "non-finite json value"),
    ],
)
def test_invalid_season_stats_rejected_before_immutable_reuse(
    tmp_path: Path, body: bytes, note_fragment: str
):
    event, player, feed_capture = _resolved_player(tmp_path)
    request = hits_season_hitting_request(player, season=2026)
    provider = MockProvider({
        request.request_id: _response(body, SEASON_ACQUIRE_AT)
    })
    season_capture = acquire_hits_season_hitting(
        event,
        (player,),
        season=2026,
        observed_at_utc=SEASON_ACQUIRE_AT,
        evidence_cutoff=ACQUISITION_CUTOFF,
        provider=provider,
        acquisition_root=tmp_path,
        git_commit=COMMIT,
    )

    assert season_capture.capture_state == "rejected"
    manifest = json.loads(season_capture.manifest_path.read_text(encoding="utf-8"))
    source = manifest["sources"][0]
    assert source["availability_status"] == "rejected"
    assert note_fragment in source["availability_note"].casefold()
    with pytest.raises(HitsAcquisitionError, match="not completed"):
        materialize_acquired_hits_evidence(
            player,
            season=2026,
            game_feed_capture=feed_capture,
            season_capture=season_capture,
        )


def test_module_does_not_activate_projection_candidate_or_wagering():
    import courtvision.sports.mlb.hits_acquisition as acquisition

    source = Path(acquisition.__file__).read_text(encoding="utf-8").casefold()
    for prohibited in (
        "assemble_batter_hits_candidate",
        "compute_batter_hits_probability",
        "officialpick",
        "official_pick",
        "bankroll",
        "kelly",
        "assemble_batter_hits_features(",
    ):
        assert prohibited not in source
