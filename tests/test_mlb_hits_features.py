"""Network-blocked fixtures for provenance-backed Hits features and candidates."""
from __future__ import annotations

import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
import inspect
import io
import json
from pathlib import Path
import socket
import urllib.request

import pytest
import requests

from courtvision.core.candidates import CandidateProvenance, IdentityStatus, MarketCandidate
from courtvision.sports.mlb.batter_hits import (
    BatterHitsBaselineFeatures,
    assemble_batter_hits_candidate,
    batter_hits_source_evidence_from_market_record,
    compute_batter_hits_probability,
)
from courtvision.sports.mlb.data.prospective_context_acquisition import parse_mlb_schedule
from courtvision.sports.mlb.hits_features import (
    BatterAtBatProjectionEvidence,
    BatterLineupEvidence,
    BatterSeasonHittingEvidence,
    assemble_batter_hits_features,
    extract_batter_lineup_evidence,
    parse_batter_season_hitting_evidence,
)
from courtvision.sports.mlb.hits_identity import bind_mlb_event, resolve_mlb_batter_identity
from courtvision.sports.mlb.providers.the_odds_api_market_adapter import normalize_mlb_event_odds


OBSERVED = datetime(2026, 9, 20, 15, tzinfo=timezone.utc)
CUTOFF = OBSERVED + timedelta(hours=1)
START = datetime(2026, 9, 20, 23, tzinfo=timezone.utc)
GAME_ID = "823184"
PLAYER_ID = "700001"
EVENT_ID = "provider-hits-fixture-001"
PROVIDER_NAME = "Jose Ramirez"
CANONICAL_NAME = "José Ramírez"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network is forbidden in offline Hits feature tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(socket, "gethostbyname", blocked)
    monkeypatch.setattr(socket, "socket", blocked)


def _odds(price=-150, *, collected_at=CUTOFF, observed_name=PROVIDER_NAME):
    payload = {
        "id": EVENT_ID, "sport_key": "baseball_mlb", "sport_title": "MLB",
        "commence_time": START.isoformat(),
        "home_team": "Cleveland Guardians", "away_team": "Minnesota Twins",
        "bookmakers": [{
            "key": "fixture-book", "title": "Fixture Book",
            "last_update": (collected_at - timedelta(minutes=1)).isoformat(),
            "markets": [{
                "key": "batter_hits",
                "last_update": (collected_at - timedelta(minutes=1)).isoformat(),
                "outcomes": [{"description": observed_name, "name": "Over",
                              "point": 0.5, "price": price}],
            }],
        }],
    }
    batch = normalize_mlb_event_odds(
        payload, collected_at=collected_at, source_refs=("fixture:odds",),
    )
    assert not batch.diagnostics
    assert len(batch.records) == len(batch.quotes) == 1
    return batch.records[0], batch.quotes[0]


def _schedule():
    payload = {"dates": [{"games": [{
        "gamePk": int(GAME_ID), "officialDate": "2026-09-20",
        "gameDate": START.isoformat(),
        "teams": {
            "home": {"team": {"id": 114, "name": "Cleveland Guardians"}},
            "away": {"team": {"id": 142, "name": "Minnesota Twins"}},
        },
        "venue": {"id": 5, "name": "Synthetic Park"},
        "status": {"detailedState": "Scheduled"},
    }]}]}
    return parse_mlb_schedule(json.dumps(payload).encode(), operating_date=date(2026, 9, 20))


def _feed():
    people = {
        "ID700001": {"id": 700001, "fullName": CANONICAL_NAME,
                     "currentTeam": {"id": 114}},
        "ID700002": {"id": 700002, "fullName": "Home Teammate",
                     "currentTeam": {"id": 114}},
        "ID600001": {"id": 600001, "fullName": "Away Batter",
                     "currentTeam": {"id": 142}},
    }
    return {
        "gamePk": int(GAME_ID),
        "gameData": {
            "datetime": {"dateTime": START.isoformat()},
            "teams": {"home": {"id": 114}, "away": {"id": 142}},
            "venue": {"id": 5}, "players": people,
        },
        "liveData": {"boxscore": {"teams": {
            "home": {
                "team": {"id": 114}, "battingOrder": [700002, 700001],
                "players": {key: {"person": {"id": person["id"], "fullName": person["fullName"]}}
                            for key, person in people.items() if person["currentTeam"]["id"] == 114},
            },
            "away": {
                "team": {"id": 142}, "battingOrder": [600001],
                "players": {"ID600001": {"person": {"id": 600001, "fullName": "Away Batter"}}},
            },
        }}},
    }


def _bindings(*, record=None, feed=None):
    record = record if record is not None else _odds()[0]
    event = bind_mlb_event(
        record, _schedule(), observed_at=OBSERVED,
        evidence_cutoff=CUTOFF, source_refs=("fixture:schedule",),
    )
    player = resolve_mlb_batter_identity(
        record, event, _feed() if feed is None else feed, observed_at=OBSERVED,
        evidence_cutoff=CUTOFF, source_refs=("fixture:roster",),
    )
    return event, player


def _season_payload():
    return {"people": [{
        "id": int(PLAYER_ID), "fullName": CANONICAL_NAME,
        "stats": [{
            "type": {"displayName": "season"}, "group": {"displayName": "hitting"},
            "splits": [{"season": "2026",
                        "player": {"id": int(PLAYER_ID), "fullName": CANONICAL_NAME},
                        "stat": {"hits": 125, "atBats": 500}}],
        }],
    }]}


def _season(**updates):
    values = dict(
        season=2026, mlbam_player_id=PLAYER_ID, player_name=CANONICAL_NAME,
        hits=125, at_bats=500, observed_at=OBSERVED,
        evidence_cutoff=CUTOFF, source="mlb_statsapi", source_refs=("fixture:season",),
    )
    values.update(updates)
    return BatterSeasonHittingEvidence(**values)


def _projection(**updates):
    values = dict(
        mlbam_game_id=GAME_ID, mlbam_player_id=PLAYER_ID, player_name=CANONICAL_NAME,
        projected_at_bats=3.7, projection_method="supplied_offline_opportunity_model",
        model_version="fixture-v1", source="fixture-projection",
        generated_at=CUTOFF, evidence_cutoff=OBSERVED,
        source_refs=("fixture:explicit-projection",),
    )
    values.update(updates)
    return BatterAtBatProjectionEvidence(**values)


def _lineup(**updates):
    values = dict(
        mlbam_game_id=GAME_ID, mlbam_player_id=PLAYER_ID, player_name=CANONICAL_NAME,
        team_side="home", batting_order_position=2,
        lineup_status="statsapi_batting_order_present", observed_at=OBSERVED,
        evidence_cutoff=CUTOFF, source_refs=("fixture:lineup",),
    )
    values.update(updates)
    return BatterLineupEvidence(**values)


def _assemble(**updates):
    event, player = _bindings()
    values = dict(event_binding=event, player_binding=player, season_evidence=_season(),
                  lineup_evidence=_lineup(), projection_evidence=_projection(),
                  evidence_cutoff=CUTOFF)
    values.update(updates)
    return assemble_batter_hits_features(**values)


def _extract(feed=None, **updates):
    event, player = _bindings()
    values = dict(observed_at=OBSERVED, evidence_cutoff=CUTOFF,
                  source_refs=("fixture:lineup",))
    values.update(updates)
    return extract_batter_lineup_evidence(_feed() if feed is None else feed, event, player, **values)


def _parse(payload=None, **updates):
    _, player = _bindings()
    values = dict(season=2026, player_binding=player, observed_at=OBSERVED,
                  evidence_cutoff=CUTOFF, source_refs=("fixture:season",))
    values.update(updates)
    return parse_batter_season_hitting_evidence(
        _season_payload() if payload is None else payload, **values,
    )


@pytest.mark.parametrize("constructor", [_season, _projection, _lineup])
def test_evidence_is_immutable_with_immutable_nonempty_references(constructor):
    evidence = constructor()
    with pytest.raises(FrozenInstanceError):
        evidence.source_refs = ("changed",)
    for refs in ([], ["mutable"], (), ("",), (None,)):
        with pytest.raises((TypeError, ValueError)):
            constructor(source_refs=refs)


@pytest.mark.parametrize("field,value", [
    ("hits", -1), ("hits", 501), ("hits", 1.2), ("hits", True), ("hits", "125"),
    ("hits", float("nan")), ("hits", float("inf")), ("at_bats", 0),
    ("at_bats", -1), ("at_bats", 500.5), ("at_bats", True), ("at_bats", "500"),
    ("at_bats", float("nan")), ("at_bats", float("inf")),
])
def test_season_counts_never_coerce_invalid_or_missing_data(field, value):
    with pytest.raises((TypeError, ValueError)):
        _season(**{field: value})


def test_zero_hits_is_observed_evidence_and_produces_zero_probability():
    features = _assemble(season_evidence=_season(hits=0))
    assert features.season_hits == 0
    assert compute_batter_hits_probability(features, generated_at=CUTOFF).model_probability == 0


@pytest.mark.parametrize("as_bytes", [False, True])
def test_season_adapter_accepts_explicit_supplied_hitting_split(as_bytes):
    payload = _season_payload()
    before = deepcopy(payload)
    parsed = _parse(json.dumps(payload).encode() if as_bytes else payload)
    assert parsed == _season()
    assert payload == before


def test_season_split_can_inherit_explicit_people_identity():
    payload = _season_payload()
    del payload["people"][0]["stats"][0]["splits"][0]["player"]
    assert _parse(payload) == _season()


@pytest.mark.parametrize("change", [
    "missing_hits", "missing_at_bats", "wrong_player", "wrong_split_player", "wrong_name",
    "wrong_split_name", "wrong_season", "pitching", "career", "extra_split",
    "conflicting_split", "extra_person", "extra_stat_group", "missing_stats",
])
def test_season_adapter_rejects_missing_conflicting_or_ambiguous_stats(change):
    payload = _season_payload()
    person = payload["people"][0]
    group = person["stats"][0]
    split = group["splits"][0]
    if change == "missing_hits":
        del split["stat"]["hits"]
    elif change == "missing_at_bats":
        del split["stat"]["atBats"]
    elif change == "wrong_player":
        person["id"] = 700002
    elif change == "wrong_split_player":
        split["player"]["id"] = 700002
    elif change == "wrong_name":
        person["fullName"] = "Other Batter"
    elif change == "wrong_split_name":
        split["player"]["fullName"] = "Other Batter"
    elif change == "wrong_season":
        split["season"] = "2025"
    elif change == "pitching":
        group["group"]["displayName"] = "pitching"
    elif change == "career":
        group["type"]["displayName"] = "career"
    elif change in {"extra_split", "conflicting_split"}:
        group["splits"].append(deepcopy(split))
        if change == "conflicting_split":
            group["splits"][1]["stat"]["hits"] = 126
    elif change == "extra_person":
        payload["people"].append(deepcopy(person))
    elif change == "extra_stat_group":
        person["stats"].append(deepcopy(group))
    else:
        del person["stats"]
    with pytest.raises((TypeError, ValueError)):
        _parse(payload)


@pytest.mark.parametrize("key,value", [
    ("hits", "125"), ("hits", 125.0), ("hits", True), ("hits", -1),
    ("atBats", "500"), ("atBats", 500.0), ("atBats", False), ("atBats", 0),
])
def test_season_adapter_preserves_strict_integer_count_contract(key, value):
    payload = _season_payload()
    payload["people"][0]["stats"][0]["splits"][0]["stat"][key] = value
    with pytest.raises((TypeError, ValueError)):
        _parse(payload)


@pytest.mark.parametrize("field", ["observed_at", "evidence_cutoff"])
def test_season_requires_aware_clocks(field):
    with pytest.raises((TypeError, ValueError)):
        _season(**{field: CUTOFF.replace(tzinfo=None)})


def test_season_post_cutoff_capture_is_rejected_by_contract_and_parser():
    with pytest.raises((TypeError, ValueError)):
        _season(observed_at=CUTOFF + timedelta(seconds=1))
    with pytest.raises((TypeError, ValueError)):
        _parse(observed_at=CUTOFF + timedelta(seconds=1))


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf"), True, None, "3.7"])
def test_projection_requires_explicit_finite_positive_opportunity(value):
    with pytest.raises((TypeError, ValueError)):
        _projection(projected_at_bats=value)


def test_projection_has_no_opportunity_default_and_assembler_cannot_supply_one():
    parameter = inspect.signature(BatterAtBatProjectionEvidence).parameters["projected_at_bats"]
    assert parameter.default is inspect.Parameter.empty
    parameter = inspect.signature(assemble_batter_hits_features).parameters["projection_evidence"]
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises((TypeError, ValueError)):
        _assemble(projection_evidence=None)
    features = _assemble(projection_evidence=_projection(projected_at_bats=2.625))
    assert features.projected_at_bats == 2.625


@pytest.mark.parametrize("field", ["projection_method", "model_version", "source"])
def test_projection_requires_descriptive_provenance(field):
    with pytest.raises((TypeError, ValueError)):
        _projection(**{field: ""})


def test_projection_generation_cannot_precede_its_evidence_cutoff():
    with pytest.raises((TypeError, ValueError)):
        _projection(generated_at=OBSERVED - timedelta(seconds=1))


@pytest.mark.parametrize("as_bytes", [False, True])
def test_lineup_presence_retains_position_without_claiming_confirmation(as_bytes):
    feed = _feed()
    before = deepcopy(feed)
    evidence = _extract(json.dumps(feed).encode() if as_bytes else feed)
    assert evidence == _lineup()
    assert evidence.lineup_status == "statsapi_batting_order_present"
    assert evidence.batting_order_position == 2
    assert evidence.team_side == "home"
    assert feed == before


@pytest.mark.parametrize("order,status", [([700002], "not_listed"), ([], "not_listed"), (None, "unavailable")])
def test_absence_is_honest_lineup_evidence_without_dnp_inference(order, status):
    feed = _feed()
    feed["liveData"]["boxscore"]["teams"]["home"]["battingOrder"] = order
    evidence = _extract(feed)
    assert evidence.lineup_status == status
    assert evidence.batting_order_position is None
    assert evidence.lineup_status not in {"DNP", "inactive", "scratch", "bench"}


def test_missing_batting_order_is_unavailable():
    feed = _feed()
    del feed["liveData"]["boxscore"]["teams"]["home"]["battingOrder"]
    assert _extract(feed).lineup_status == "unavailable"


@pytest.mark.parametrize("change", ["duplicate_home", "duplicate_away", "cross_side", "wrong_game", "wrong_player", "order_non_array"])
def test_lineup_rejects_conflicting_feed_or_batting_order(change):
    feed = _feed()
    teams = feed["liveData"]["boxscore"]["teams"]
    if change == "duplicate_home":
        teams["home"]["battingOrder"] = [700001, 700001]
    elif change == "duplicate_away":
        teams["away"]["battingOrder"] = [600001, 600001]
    elif change == "cross_side":
        teams["away"]["battingOrder"] = [700001]
    elif change == "wrong_game":
        feed["gamePk"] = 823185
    elif change == "wrong_player":
        feed["gameData"]["players"]["ID700001"]["fullName"] = "Other Batter"
        teams["home"]["players"]["ID700001"]["person"]["fullName"] = "Other Batter"
    else:
        teams["home"]["battingOrder"] = "700001"
    with pytest.raises((TypeError, ValueError)):
        _extract(feed)


def test_lineup_rejects_post_cutoff_observation():
    with pytest.raises((TypeError, ValueError)):
        _extract(observed_at=CUTOFF + timedelta(seconds=1))
    with pytest.raises((TypeError, ValueError)):
        _lineup(observed_at=CUTOFF + timedelta(seconds=1))


def test_assembly_retains_provider_reference_exact_values_and_all_provenance():
    features = _assemble()
    assert isinstance(features, BatterHitsBaselineFeatures)
    assert (features.season_hits, features.season_at_bats, features.projected_at_bats) == (125, 500, 3.7)
    assert features.event_id == EVENT_ID
    assert features.event_id != GAME_ID
    assert features.batter_name == PROVIDER_NAME
    assert features.lineup_status == "statsapi_batting_order_present"
    assert features.evidence_cutoff == CUTOFF
    assert set(features.source_refs) >= {
        "fixture:odds", "fixture:schedule", "fixture:roster", "fixture:season",
        "fixture:lineup", "fixture:explicit-projection",
    }


@pytest.mark.parametrize("component,field,value", [
    ("season", "mlbam_player_id", "700002"), ("season", "player_name", "Other Batter"),
    ("lineup", "mlbam_player_id", "700002"), ("lineup", "mlbam_game_id", "823185"),
    ("lineup", "player_name", "Other Batter"), ("lineup", "team_side", "away"),
    ("projection", "mlbam_player_id", "700002"), ("projection", "mlbam_game_id", "823185"),
    ("projection", "player_name", "Other Batter"),
])
def test_assembly_rejects_cross_subject_evidence(component, field, value):
    constructor = {"season": _season, "lineup": _lineup, "projection": _projection}[component]
    with pytest.raises((TypeError, ValueError)):
        _assemble(**{f"{component}_evidence": constructor(**{field: value})})


@pytest.mark.parametrize("component", ["season", "lineup", "projection"])
def test_assembly_rejects_sources_with_later_individual_cutoffs(component):
    constructor = {"season": _season, "lineup": _lineup, "projection": _projection}[component]
    updates = {"evidence_cutoff": CUTOFF + timedelta(seconds=1)}
    if component == "projection":
        updates["generated_at"] = CUTOFF + timedelta(seconds=1)
    with pytest.raises((TypeError, ValueError)):
        _assemble(**{f"{component}_evidence": constructor(**updates)})


@pytest.mark.parametrize("component", ["season", "lineup", "projection"])
def test_assembly_rejects_feature_observations_after_final_cutoff(component):
    constructor = {"season": _season, "lineup": _lineup, "projection": _projection}[component]
    observed_key = "generated_at" if component == "projection" else "observed_at"
    updates = {observed_key: CUTOFF + timedelta(seconds=1)}
    if component != "projection":
        updates["evidence_cutoff"] = CUTOFF + timedelta(seconds=2)
    with pytest.raises((TypeError, ValueError)):
        _assemble(**{f"{component}_evidence": constructor(**updates)})


@pytest.mark.parametrize("cutoff", [START, START + timedelta(seconds=1)])
def test_feature_cutoff_must_be_strictly_before_first_pitch(cutoff):
    with pytest.raises((TypeError, ValueError)):
        _assemble(evidence_cutoff=cutoff)


def test_postgame_statistics_cannot_be_used_for_pregame_features():
    with pytest.raises((TypeError, ValueError)):
        _assemble(season_evidence=_season(observed_at=START + timedelta(hours=3),
                                         evidence_cutoff=START + timedelta(hours=3)))


def test_name_only_identity_cannot_attach_canonical_season_stats():
    source, _ = _odds(observed_name="Unlisted Batter")
    event, player = _bindings(record=source)
    assert player.participant_identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH
    assert player.participant_identity.canonical_player_id is None
    with pytest.raises((TypeError, ValueError)):
        _assemble(event_binding=event, player_binding=player)
    with pytest.raises((TypeError, ValueError)):
        _parse(player_binding=player)


def test_lineup_status_is_provenance_and_never_a_probability_multiplier():
    present = _assemble()
    absent = _assemble(lineup_evidence=_lineup(lineup_status="not_listed", batting_order_position=None))
    assert present.lineup_status != absent.lineup_status
    assert compute_batter_hits_probability(present, generated_at=CUTOFF).model_probability == (
        compute_batter_hits_probability(absent, generated_at=CUTOFF).model_probability
    )


def _vertical_slice(price=-150, *, collected_at=CUTOFF):
    record, quote = _odds(price, collected_at=collected_at)
    event, player = _bindings(record=record)
    lineup = extract_batter_lineup_evidence(
        _feed(), event, player, observed_at=OBSERVED,
        evidence_cutoff=CUTOFF, source_refs=("fixture:lineup",),
    )
    season = parse_batter_season_hitting_evidence(
        json.dumps(_season_payload()).encode(), season=2026, player_binding=player,
        observed_at=OBSERVED, evidence_cutoff=CUTOFF, source_refs=("fixture:season",),
    )
    features = assemble_batter_hits_features(
        event_binding=event, player_binding=player, season_evidence=season,
        lineup_evidence=lineup, projection_evidence=_projection(), evidence_cutoff=CUTOFF,
    )
    probability = compute_batter_hits_probability(features, generated_at=CUTOFF + timedelta(minutes=1))
    candidate = assemble_batter_hits_candidate(
        candidate_id="fixture-candidate", quote=quote,
        source_evidence=batter_hits_source_evidence_from_market_record(record),
        features=features, probability=probability,
        participant_identity=player.participant_identity, event_identity=event.event_identity,
        provenance=CandidateProvenance("offline-hits-features-fixture", ("fixture:candidate",)),
    )
    return features, candidate


def test_full_offline_vertical_slice_creates_one_resolved_research_candidate():
    features, candidate = _vertical_slice()
    candidates = [candidate]
    assert len(candidates) == 1
    assert isinstance(candidate, MarketCandidate)
    assert candidate.research_only is True
    assert candidate.eligible_for_betting is False
    assert candidate.eligible_for_official_pick is False
    assert candidate.approval_status == "not_approved"
    assert candidate.quote.mode == "research"
    assert candidate.quote.is_live is False
    assert candidate.quote.eligible_for_betting is False
    assert candidate.quote.kelly_eligible is False
    assert candidate.quote.approval_status == "not_approved"
    assert candidate.event_identity.identity_status is IdentityStatus.RESOLVED
    assert candidate.event_identity.event_id == EVENT_ID
    assert candidate.event_identity.canonical_event_id == GAME_ID
    assert candidate.participant_identity.identity_status is IdentityStatus.RESOLVED
    assert candidate.participant_identity.participant_name == PROVIDER_NAME
    assert candidate.participant_identity.canonical_player_id == PLAYER_ID
    assert candidate.participant_identity.canonical_player_name == CANONICAL_NAME
    assert candidate.probability.model_probability == 1 - (1 - 125 / 500) ** 3.7
    assert candidate.probability.evidence_cutoff == features.evidence_cutoff == CUTOFF
    assert set(features.source_refs).issubset(set(candidate.provenance.source_refs))


def test_changing_only_odds_changes_comparison_without_changing_baseball_features():
    first_features, first = _vertical_slice(-150)
    second_features, second = _vertical_slice(120)
    assert first_features == second_features
    assert first.probability == second.probability
    assert first.quote.implied_probability != second.quote.implied_probability
    assert first.reasoning.market_implied_probability.value != second.reasoning.market_implied_probability.value
    assert first.reasoning.edge.value != second.reasoning.edge.value


@pytest.mark.parametrize("offset", [-30, 30])
def test_quote_collection_and_feature_evidence_have_independent_clocks(offset):
    features, candidate = _vertical_slice(collected_at=CUTOFF + timedelta(minutes=offset))
    assert features.evidence_cutoff == CUTOFF
    assert candidate.quote.collected_at == CUTOFF + timedelta(minutes=offset)
    assert candidate.probability.model_probability == 1 - (1 - 125 / 500) ** 3.7


def test_baseball_feature_interfaces_do_not_accept_market_price_inputs():
    prohibited = {"american_odds", "decimal_odds", "implied_probability", "sportsbook",
                  "line_price", "edge", "kelly", "official_pick", "market_movement", "quote"}
    for evidence_type in (BatterSeasonHittingEvidence, BatterLineupEvidence, BatterAtBatProjectionEvidence):
        assert not prohibited.intersection(field.name for field in fields(evidence_type))
    assert not prohibited.intersection(inspect.signature(assemble_batter_hits_features).parameters)


def test_source_refs_are_retained_without_opening_paths_or_urls(monkeypatch):
    event, player = _bindings()
    refs = ("https://offline.invalid/never-open", "C:/not-present/never-open.json")
    season = _season(source_refs=refs)
    lineup = _lineup(source_refs=refs)
    projection = _projection(source_refs=refs)

    def blocked(*args, **kwargs):
        raise AssertionError("pure feature assembly must never dereference provenance")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", blocked)
        patch.setattr(io, "open", blocked)
        patch.setattr(Path, "open", blocked)
        features = assemble_batter_hits_features(
            event_binding=event, player_binding=player, season_evidence=season,
            lineup_evidence=lineup, projection_evidence=projection, evidence_cutoff=CUTOFF,
        )
    assert set(refs).issubset(features.source_refs)


@pytest.mark.parametrize("official_offset", [-120, 120])
def test_cutoff_cannot_fall_between_provider_and_official_starts(official_offset):
    source, _ = _odds()
    official_start = START + timedelta(seconds=official_offset)
    schedule = (replace(_schedule()[0], scheduled_start_utc=official_start),)
    event = bind_mlb_event(source, schedule, observed_at=OBSERVED,
                           evidence_cutoff=CUTOFF, source_refs=("fixture:schedule",))
    feed = _feed()
    feed["gameData"]["datetime"]["dateTime"] = official_start.isoformat()
    player = resolve_mlb_batter_identity(source, event, feed, observed_at=OBSERVED,
                                         evidence_cutoff=CUTOFF, source_refs=("fixture:roster",))
    cutoff = min(START, official_start) + timedelta(seconds=60)
    assert cutoff < max(START, official_start)
    with pytest.raises((TypeError, ValueError)):
        _assemble(event_binding=event, player_binding=player, evidence_cutoff=cutoff)


@pytest.mark.parametrize("binding_name", ["event", "player"])
def test_identity_evidence_cutoffs_cannot_exceed_final_feature_cutoff(binding_name):
    event, player = _bindings()
    if binding_name == "event":
        event = replace(event, evidence_cutoff=CUTOFF + timedelta(seconds=1))
        player = replace(player, event_binding=event)
    else:
        player = replace(player, evidence_cutoff=CUTOFF + timedelta(seconds=1))
    with pytest.raises((TypeError, ValueError)):
        _assemble(event_binding=event, player_binding=player)


@pytest.mark.parametrize("constructor", [_season, _lineup, _projection])
@pytest.mark.parametrize("player_id", ["0", "-1", "0700001", "outcome-700001", "７００００１", True, 700001])
def test_feature_evidence_ids_are_canonical_decimal_strings(constructor, player_id):
    with pytest.raises((TypeError, ValueError)):
        constructor(mlbam_player_id=player_id)


@pytest.mark.parametrize("field,value", [
    ("batting_order_position", 0), ("batting_order_position", 10),
    ("batting_order_position", True), ("batting_order_position", 2.0),
    ("batting_order_position", None), ("team_side", None),
    ("lineup_status", "confirmed"), ("lineup_status", "DNP"),
])
def test_lineup_contract_does_not_overstate_evidence(field, value):
    with pytest.raises((TypeError, ValueError)):
        _lineup(**{field: value})


@pytest.mark.parametrize("payload", [b'{"people": [], "people": []}', b'{"people": NaN}', b'{'])
def test_season_parser_rejects_ambiguous_or_malformed_json(payload):
    with pytest.raises((TypeError, ValueError)):
        _parse(payload)


def test_ambiguous_player_cannot_assemble_candidate_features():
    feed = _feed()
    feed["gameData"]["players"]["ID700002"]["fullName"] = PROVIDER_NAME
    feed["liveData"]["boxscore"]["teams"]["home"]["players"]["ID700002"]["person"]["fullName"] = PROVIDER_NAME
    event, player = _bindings(feed=feed)
    assert player.participant_identity.identity_status is IdentityStatus.AMBIGUOUS
    assert player.mlbam_player_id is None
    with pytest.raises(ValueError, match="resolved canonical player"):
        _assemble(event_binding=event, player_binding=player)


def test_ambiguous_event_cannot_resolve_a_player_or_assemble_candidate_features():
    source, _ = _odds()
    schedule = _schedule()
    second = replace(schedule[0], event_id="823185", scheduled_start_utc=START + timedelta(seconds=60))
    ambiguous = bind_mlb_event(source, (*schedule, second), observed_at=OBSERVED,
                               evidence_cutoff=CUTOFF, source_refs=("fixture:ambiguous-schedule",))
    assert ambiguous.identity_status is IdentityStatus.AMBIGUOUS
    assert ambiguous.mlbam_game_id is None
    with pytest.raises(ValueError, match="resolved event"):
        resolve_mlb_batter_identity(source, ambiguous, _feed(), observed_at=OBSERVED,
                                   evidence_cutoff=CUTOFF, source_refs=("fixture:roster",))
    with pytest.raises(ValueError, match="resolved event"):
        _assemble(event_binding=ambiguous)
