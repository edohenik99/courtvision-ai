"""Deterministic offline tests for MLB event and player identity boundaries."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import json
import socket
import urllib.request

import pytest
import requests

from courtvision.core.candidates import IdentityStatus
from courtvision.sports.mlb.data.prospective_context_acquisition import (
    ScheduledEvent, parse_mlb_schedule,
)
from courtvision.sports.mlb.hits_identity import (
    bind_mlb_event, bind_mlb_events, event_roster_players,
    resolve_mlb_batter_identity, validate_bound_game_feed,
)
from courtvision.sports.mlb.market_data import MLBPlayerPropSourceRecord


START = datetime(2026, 9, 20, 20, tzinfo=timezone.utc)
OBSERVED = START - timedelta(hours=2)
CUTOFF = START - timedelta(hours=1)


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("identity tests must not perform network or DNS activity")

    for module, name in (
        (socket, "socket"), (socket, "create_connection"), (socket, "getaddrinfo"),
        (socket, "gethostbyname"), (urllib.request, "urlopen"),
        (requests.sessions.Session, "request"),
    ):
        monkeypatch.setattr(module, name, forbidden)


def source(**updates):
    fields = dict(
        provider="the_odds_api", provider_sport_key="baseball_mlb",
        provider_event_id="odds-event-1", home_team="Cleveland Guardians",
        away_team="Minnesota Twins", commence_time=START, bookmaker_key="fixture",
        bookmaker_name="Fixture Book", provider_market_key="batter_hits",
        canonical_market_type="batter_hits", market_variant="MAIN",
        participant_name="Jose Ramirez", side="OVER", line=0.5, american_odds=-150,
        market_updated_at=OBSERVED, collected_at=OBSERVED,
        source_refs=("fixture://quote",), provider_outcome_id=999999,
    )
    fields.update(updates)
    return MLBPlayerPropSourceRecord(**fields)


def event(**updates):
    fields = dict(
        event_id="823184", operating_date=date(2026, 9, 20),
        scheduled_start_utc=START, away_team_id="142", away_team="Minnesota Twins",
        home_team_id="114", home_team="Cleveland Guardians",
        venue_id="5", venue_name="Progressive Field", status="Pre-Game",
    )
    fields.update(updates)
    return ScheduledEvent(**fields)


def bind(record=None, events=None, **updates):
    kwargs = dict(observed_at=OBSERVED, evidence_cutoff=CUTOFF,
                  source_refs=("fixture://schedule",))
    kwargs.update(updates)
    return bind_mlb_event(source() if record is None else record,
                          (event(),) if events is None else events, **kwargs)


def feed():
    return {
        "gamePk": 823184,
        "gameData": {
            "datetime": {"dateTime": START.isoformat()},
            "teams": {"home": {"id": 114}, "away": {"id": 142}},
            "venue": {"id": 5},
            "players": {
                "ID608070": {"id": 608070, "fullName": "José Ramírez",
                             "currentTeam": {"id": 114}},
            },
        },
        "liveData": {"boxscore": {"teams": {
            "home": {
                "team": {"id": 114},
                "players": {"ID608070": {"person": {"id": 608070, "fullName": "José Ramírez"}}},
                "battingOrder": [608070],
            },
            "away": {"team": {"id": 142}, "players": {}, "battingOrder": []},
        }}},
    }


def resolve(record=None, binding=None, payload=None, **updates):
    kwargs = dict(observed_at=OBSERVED, evidence_cutoff=CUTOFF,
                  source_refs=("fixture://game-feed",))
    kwargs.update(updates)
    return resolve_mlb_batter_identity(
        source() if record is None else record, bind() if binding is None else binding,
        feed() if payload is None else payload, **kwargs,
    )


def test_schedule_parser_reuse_and_exact_binding():
    payload = {"dates": [{"games": [{
        "gamePk": 823184, "officialDate": "2026-09-20", "gameDate": START.isoformat(),
        "teams": {
            "home": {"team": {"id": 114, "name": "Cleveland Guardians"}},
            "away": {"team": {"id": 142, "name": "Minnesota Twins"}},
        },
        "venue": {"id": 5, "name": "Progressive Field"},
        "status": {"detailedState": "Pre-Game"},
    }]}]}
    scheduled, = parse_mlb_schedule(json.dumps(payload).encode(), operating_date=date(2026, 9, 20))
    binding = bind(events=(scheduled,))
    assert binding.scheduled_event is scheduled
    assert binding.mlbam_game_id == "823184"
    assert binding.identity_status is IdentityStatus.RESOLVED
    assert binding.event_identity.event_id == "odds-event-1"
    assert binding.event_identity.canonical_event_id == "823184"
    assert binding.canonical_source == "mlb_statsapi"
    assert binding.source_refs == ("fixture://quote", "fixture://schedule")
    assert binding.official_home_team == "Cleveland Guardians"
    assert binding.official_away_team == "Minnesota Twins"


@pytest.mark.parametrize("drift", [0, 60, -60, 120, -120])
def test_event_drift_bound_is_inclusive(drift):
    binding = bind(source(commence_time=START + timedelta(seconds=drift)))
    assert binding.identity_status is IdentityStatus.RESOLVED
    assert binding.schedule_start_drift_seconds == drift


@pytest.mark.parametrize("drift", [121, -121, 3600])
def test_event_drift_outside_bound_remains_unresolved(drift):
    binding = bind(source(commence_time=START + timedelta(seconds=drift)))
    assert binding.identity_status is IdentityStatus.UNRESOLVED
    assert binding.mlbam_game_id is None


@pytest.mark.parametrize("updates", [
    {"home_team": "Cleveland Guardians "},
    {"home_team": "CLEVELAND GUARDIANS"},
    {"away_team": "Minnesota Twin"},
    {"home_team": "Minnesota Twins", "away_team": "Cleveland Guardians"},
    {"operating_date": date(2026, 9, 21)},
])
def test_event_teams_and_date_must_agree(updates):
    if updates.get("home_team", "").endswith(" "):
        with pytest.raises(ValueError):
            bind(events=(event(**updates),))
    else:
        assert bind(events=(event(**updates),)).identity_status is IdentityStatus.UNRESOLVED


def test_toronto_date_cannot_be_inferred_from_utc_date():
    before_midnight = datetime(2026, 9, 21, 3, 59, 30, tzinfo=timezone.utc)
    record = source(commence_time=before_midnight)
    official = event(scheduled_start_utc=before_midnight + timedelta(seconds=60))
    assert bind(record, (official,)).identity_status is IdentityStatus.UNRESOLVED


def test_empty_schedule_does_not_promote_numeric_provider_event_id():
    binding = bind(source(provider_event_id="823184"), ())
    assert binding.identity_status is IdentityStatus.UNRESOLVED
    assert binding.event_identity.event_id == "823184"
    assert binding.event_identity.canonical_event_id is None


def test_doubleheader_resolves_by_exact_time_and_ambiguous_schedule_never_chooses_first():
    first = event()
    later = event(event_id="823185", scheduled_start_utc=START + timedelta(hours=3))
    for events in ((first, later), (later, first)):
        assert bind(events=events).mlbam_game_id == "823184"
    malformed = replace(later, scheduled_start_utc=START + timedelta(seconds=60))
    for events in ((first, malformed), (malformed, first)):
        binding = bind(events=events)
        assert binding.identity_status is IdentityStatus.AMBIGUOUS
        assert binding.mlbam_game_id is None
        assert binding.scheduled_event is None


@pytest.mark.parametrize("value", ["0", "-1", "abc", "001", "12.5", "１２", True, 823184])
def test_direct_schedule_contract_requires_explicit_positive_decimal_string(value):
    with pytest.raises(ValueError):
        bind(events=(event(event_id=value),))


@pytest.mark.parametrize("field,value", [
    ("home_team_id", "0"), ("away_team_id", "bad"), ("venue_id", True),
    ("scheduled_start_utc", START.replace(tzinfo=None)), ("away_team_id", "114"),
    ("status", []), ("home_probable_pitcher_id", []), ("operating_date", "2026-09-20"),
])
def test_direct_schedule_instances_are_validated(field, value):
    with pytest.raises(ValueError):
        bind(events=(event(**{field: value}),))


def test_non_mlb_source_rejected():
    with pytest.raises(ValueError, match="baseball_mlb"):
        bind(source(provider_sport_key="baseball_other"))


def test_batch_rejects_provider_conflicts_and_canonical_collisions():
    kwargs = dict(observed_at=OBSERVED, evidence_cutoff=CUTOFF, source_refs=("fixture://schedule",))
    first, second = event(), event(event_id="823185", scheduled_start_utc=START + timedelta(hours=3))
    with pytest.raises(ValueError, match="provider event conflict"):
        bind_mlb_events((source(), source(commence_time=second.scheduled_start_utc)),
                        (first, second), **kwargs)
    with pytest.raises(ValueError, match="canonical game collision"):
        bind_mlb_events((source(), source(provider_event_id="odds-event-2")), (first,), **kwargs)
    repeated = bind_mlb_events((source(), source(american_odds=150)), (first,), **kwargs)
    assert repeated[0] == repeated[1]


def test_bindings_immutable_and_tampered_event_contract_rejected():
    binding = bind()
    with pytest.raises(FrozenInstanceError):
        binding.provider_event_id = "changed"
    with pytest.raises(ValueError):
        replace(binding, scheduled_event=event(home_team="Wrong Team"))
    with pytest.raises(ValueError):
        replace(binding, identity_status=IdentityStatus.AMBIGUOUS)
    with pytest.raises(ValueError):
        replace(binding, source_refs=["mutable"])


@pytest.mark.parametrize("kwargs", [
    {"observed_at": CUTOFF + timedelta(seconds=1)}, {"evidence_cutoff": START},
    {"observed_at": OBSERVED.replace(tzinfo=None)}, {"source_refs": ()},
    {"source_refs": ("",)}, {"source_refs": ["mutable"]},
])
def test_event_provenance_required_and_pregame(kwargs):
    with pytest.raises(ValueError):
        bind(**kwargs)


def test_unique_player_resolution_preserves_both_names_and_event_team():
    result = resolve()
    identity = result.participant_identity
    assert identity.identity_status is IdentityStatus.RESOLVED
    assert identity.participant_name == "Jose Ramirez"
    assert identity.canonical_participant_name == "José Ramírez"
    assert identity.canonical_participant_id == "608070"
    assert (result.team_side, result.team_id) == ("home", "114")
    assert result.mlbam_game_id == "823184"
    assert result.provider_event_id == "odds-event-1"
    assert result.source_refs == ("fixture://quote", "fixture://game-feed")
    assert result.observed_at == OBSERVED
    assert result.evidence_cutoff == CUTOFF


@pytest.mark.parametrize("observed,canonical", [
    ("Jose Ramirez", "José Ramírez"),
    ("Mike Trout", "Michael Trout"),
    ("Vladimir Guerrero Jr.", "Vladimir Guerrero"),
])
def test_shared_name_normalization_aliases_and_accents(observed, canonical):
    payload = feed()
    payload["gameData"]["players"]["ID608070"]["fullName"] = canonical
    payload["liveData"]["boxscore"]["teams"]["home"]["players"]["ID608070"]["person"]["fullName"] = canonical
    result = resolve(source(participant_name=observed), payload=payload)
    assert result.participant_identity.identity_status is IdentityStatus.RESOLVED
    assert result.participant_identity.participant_name == observed
    assert result.participant_identity.canonical_participant_name == canonical


def test_no_match_retains_name_only_without_outcome_id_or_hash_substitution():
    result = resolve(source(participant_name="Unavailable Player", provider_outcome_id=608070))
    assert result.participant_identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH
    assert result.mlbam_player_id is None
    assert result.participant_identity.canonical_participant_name is None
    assert result.roster_player is None


def test_duplicate_normalized_names_are_explicitly_ambiguous_in_every_order():
    payload = feed()
    payload["gameData"]["players"]["ID700001"] = {
        "id": 700001, "fullName": "Jose Ramirez", "currentTeam": {"id": 142},
    }
    for players in (payload["gameData"]["players"], dict(reversed(list(payload["gameData"]["players"].items())))):
        payload["gameData"]["players"] = players
        result = resolve(payload=payload)
        assert result.participant_identity.identity_status is IdentityStatus.AMBIGUOUS
        assert result.mlbam_player_id is None
        assert result.roster_player is None


@pytest.mark.parametrize("player_id", [0, -1, True, 608070.0, "0", "abc", "0608070", "６０８０７０"])
def test_invalid_explicit_player_id_fails_instead_of_using_roster_key(player_id):
    payload = feed()
    payload["gameData"]["players"]["ID608070"]["id"] = player_id
    with pytest.raises(ValueError):
        resolve(payload=payload)


@pytest.mark.parametrize("case", ["game", "start", "home", "away", "venue"])
def test_game_feed_identity_must_match_authoritative_event_before_roster_read(case):
    payload = feed()
    if case == "game":
        payload["gamePk"] = 823185
    elif case == "start":
        payload["gameData"]["datetime"]["dateTime"] = (START + timedelta(seconds=1)).isoformat()
    elif case == "venue":
        payload["gameData"]["venue"]["id"] = 6
    else:
        payload["gameData"]["teams"][case]["id"] = 999
    with pytest.raises(ValueError):
        resolve(payload=payload)


def test_player_feed_uses_official_start_even_when_provider_start_drifts():
    record = source(commence_time=START + timedelta(seconds=120))
    binding = bind(record)
    assert resolve(record, binding).mlbam_player_id == "608070"
    with pytest.raises(ValueError):
        resolve(binding=bind(events=()))


@pytest.mark.parametrize("case", ["outside", "both_sides", "boxscore_team", "conflicting_name", "key"])
def test_roster_identity_and_membership_conflicts_fail_closed(case):
    payload = feed()
    player = payload["gameData"]["players"]["ID608070"]
    teams = payload["liveData"]["boxscore"]["teams"]
    if case == "outside":
        player["currentTeam"]["id"] = 999
    elif case == "both_sides":
        teams["away"]["players"]["ID608070"] = teams["home"]["players"]["ID608070"]
    elif case == "boxscore_team":
        teams["home"]["team"]["id"] = 999
    elif case == "conflicting_name":
        teams["home"]["players"]["ID608070"]["person"]["fullName"] = "Another Player"
    else:
        payload["gameData"]["players"] = {"ID999": player}
    with pytest.raises(ValueError):
        resolve(payload=payload)


def test_boxscore_only_roster_is_sufficient_and_payload_is_detached():
    payload = feed()
    del payload["gameData"]["players"]
    original = deepcopy(payload)
    result = resolve(payload=payload)
    assert payload == original
    payload["liveData"]["boxscore"]["teams"]["home"]["players"]["ID608070"]["person"]["id"] = 999
    assert result.mlbam_player_id == "608070"
    with pytest.raises(FrozenInstanceError):
        result.roster_player.mlbam_player_id = "999"


def test_roster_missing_team_membership_remains_explicitly_unknown():
    payload = feed()
    del payload["gameData"]["players"]["ID608070"]["currentTeam"]
    del payload["liveData"]
    result = resolve(payload=payload)
    assert result.mlbam_player_id == "608070"
    assert result.team_side is None
    assert result.team_id is None


def test_player_binding_rejects_tampered_canonical_player_or_event():
    result = resolve()
    with pytest.raises(ValueError):
        replace(result, participant_identity=replace(
            result.participant_identity, canonical_participant_id="999",
        ))
    with pytest.raises(ValueError):
        replace(result, event_binding=bind(events=(event(event_id="823185"),)))
    with pytest.raises(ValueError):
        replace(result, roster_player=replace(result.roster_player, team_id="999"))


@pytest.mark.parametrize("kwargs", [
    {"observed_at": CUTOFF + timedelta(seconds=1)}, {"evidence_cutoff": START},
    {"observed_at": OBSERVED.replace(tzinfo=None)}, {"source_refs": ()},
])
def test_player_provenance_clocks_fail_closed(kwargs):
    with pytest.raises(ValueError):
        resolve(**kwargs)


def test_quote_receipt_can_follow_independent_identity_evidence_cutoff():
    record = source(market_updated_at=CUTOFF + timedelta(minutes=10),
                    collected_at=CUTOFF + timedelta(minutes=10))
    binding = bind(record)
    assert resolve(record, binding).evidence_cutoff == CUTOFF


@pytest.mark.parametrize("updates", [
    {"provider_event_id": "other"}, {"provider": "other"},
    {"commence_time": START + timedelta(seconds=1)},
    {"home_team": "Other Team"}, {"away_team": "Other Team"},
])
def test_player_source_must_match_bound_provider_context(updates):
    with pytest.raises(ValueError):
        resolve(source(**updates))


def test_feed_bytes_reject_duplicate_keys_and_nonfinite_values():
    raw = json.dumps(feed()).encode()
    assert resolve(payload=raw).mlbam_player_id == "608070"
    duplicate = raw.replace(b'"gamePk": 823184', b'"gamePk": 999, "gamePk": 823184')
    with pytest.raises(ValueError, match="duplicate"):
        validate_bound_game_feed(duplicate, bind())
    payload = feed()
    payload["bad"] = float("nan")
    with pytest.raises(ValueError):
        validate_bound_game_feed(payload, bind())
    with pytest.raises(ValueError):
        validate_bound_game_feed(json.dumps(payload).encode(), bind())


def test_supplied_reference_labels_are_never_dereferenced(monkeypatch):
    import builtins

    def forbidden(*args, **kwargs):
        raise AssertionError("pure identity code cannot open source references")

    monkeypatch.setattr(builtins, "open", forbidden)
    binding = bind(source_refs=("https://invalid.example/schedule", "C:/nonexistent/file"))
    result = resolve(binding=binding, source_refs=("https://invalid.example/roster",))
    assert result.mlbam_player_id == "608070"
    assert event_roster_players(feed(), binding)[0].player_name == "José Ramírez"


def test_duplicate_canonical_schedule_id_rejected_even_outside_match_window():
    with pytest.raises(ValueError, match="duplicate canonical schedule"):
        bind(events=(event(), event(scheduled_start_utc=START + timedelta(hours=3))))


@pytest.mark.parametrize("quote_offset", [0, 1, 60, 119])
def test_quote_cannot_be_observed_at_or_after_earlier_official_start(quote_offset):
    record = source(commence_time=START + timedelta(seconds=120),
                    market_updated_at=START + timedelta(seconds=quote_offset),
                    collected_at=START + timedelta(seconds=quote_offset))
    with pytest.raises(ValueError, match="official game start"):
        bind(record)
    valid_record = source(commence_time=record.commence_time)
    with pytest.raises(ValueError, match="official game start"):
        resolve(record, bind(valid_record))


def test_batting_order_supplies_team_when_roster_has_no_team():
    payload = feed()
    del payload["gameData"]["players"]["ID608070"]["currentTeam"]
    payload["liveData"]["boxscore"]["teams"]["home"]["players"] = {}
    result = resolve(payload=payload)
    assert (result.team_side, result.team_id) == ("home", "114")


@pytest.mark.parametrize("case", ["duplicate", "cross_side", "wrong_side", "bool", "too_long"])
def test_batting_order_membership_requires_consistent_unique_ids(case):
    payload = feed()
    teams = payload["liveData"]["boxscore"]["teams"]
    if case == "duplicate":
        teams["home"]["battingOrder"] = [608070, 608070]
    elif case == "cross_side":
        teams["away"]["battingOrder"] = [608070]
    elif case == "wrong_side":
        teams["home"]["battingOrder"] = []
        teams["away"]["battingOrder"] = [608070]
    elif case == "bool":
        teams["home"]["battingOrder"] = [True]
    else:
        teams["home"]["battingOrder"] = list(range(700001, 700011))
    with pytest.raises(ValueError):
        resolve(payload=payload)


@pytest.mark.parametrize("order", [None, []])
def test_unavailable_or_empty_batting_order_does_not_remove_roster_membership(order):
    payload = feed()
    payload["liveData"]["boxscore"]["teams"]["home"]["battingOrder"] = order
    result = resolve(payload=payload)
    assert result.mlbam_player_id == "608070"
    assert result.team_side == "home"


def test_repeated_provider_game_quotes_keep_each_observation_reference():
    kwargs = dict(observed_at=OBSERVED, evidence_cutoff=CUTOFF, source_refs=("fixture://schedule",))
    first, second = bind_mlb_events(
        (source(source_refs=("fixture://quote-a",)), source(source_refs=("fixture://quote-b",))),
        (event(),), **kwargs,
    )
    assert first.mlbam_game_id == second.mlbam_game_id == "823184"
    assert first.source_refs == ("fixture://quote-a", "fixture://schedule")
    assert second.source_refs == ("fixture://quote-b", "fixture://schedule")
