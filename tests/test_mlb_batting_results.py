"""Offline factual Hits evidence: finality and identity always fail closed."""

from __future__ import annotations

import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import socket
import subprocess

import pytest
import requests

from courtvision.core.candidates import (
    CandidateProvenance, EventIdentity, IdentityStatus, MarketCandidate, ParticipantIdentity,
)
from courtvision.core.market_taxonomy import resolve_market_taxonomy
from courtvision.core.odds import NormalizedOddsQuote, OddsMarketIdentity, OddsSelection, OddsSourceMetadata
from courtvision.core.probability import (
    CalibrationProvenance, DistributionType, ProbabilityOutput, ThresholdDirection,
)
from courtvision.core.reasoning import EvidenceAvailability, EvidenceKind, ReasoningAssessment, ReasoningDimension
from courtvision.sports.mlb.batter_hits import (
    BatterHitsBaselineFeatures, BatterHitsSourceEvidence,
    assemble_batter_hits_candidate, compute_batter_hits_probability,
)
from courtvision.sports.mlb.batting_results import (
    BatterBattingResult, BattingResolutionState as State,
    extract_batting_results, resolve_batter_hits_result,
)


PREGAME = datetime(2026, 9, 20, 18, tzinfo=timezone.utc)
START = PREGAME + timedelta(hours=2)
OBSERVED = PREGAME + timedelta(hours=6)
EVENT = EventIdentity("fixture-event", "caller_supplied_fixture_binding")


def _identity(*, canonical_id=None, name="Aaron Judge", status=None):
    return ParticipantIdentity(
        participant_name=name, identity_method="explicit_fixture_identity",
        identity_status=status or (IdentityStatus.RESOLVED if canonical_id else IdentityStatus.NAME_ONLY_RESEARCH),
        canonical_participant_id=canonical_id,
        canonical_participant_name=name if canonical_id else None,
    )


def _candidate(*, identity=None, event=EVENT):
    identity = identity or _identity()
    quote = NormalizedOddsQuote(
        market_identity=OddsMarketIdentity("MLB", "MLB", event.event_id, PREGAME.date(), "NYY", "BOS", "batter_hits"),
        selection=OddsSelection(identity.participant_name, line=0.5),
        source_metadata=OddsSourceMetadata("fixture-book", "fixture-source", source_type="sample"),
        american_odds=-150, quote_timestamp=PREGAME, event_start_time=START,
    )
    probability = ProbabilityOutput(
        model_probability=0.76, target_statistic="hits", threshold=1,
        direction=ThresholdDirection.AT_LEAST, distribution_type=DistributionType.BERNOULLI_EVENT,
        model_id="fixture", model_version="fixture-v1", feature_schema_version="fixture-v1",
        calibration_provenance=CalibrationProvenance(
            availability=EvidenceAvailability.UNAVAILABLE, reason="Fixture has no calibration.",
        ), generated_at=PREGAME, evidence_cutoff=PREGAME,
    )
    reasoning = ReasoningAssessment(
        model_probability=ReasoningDimension.available(probability.model_probability, EvidenceKind.MODEL_OUTPUT),
        market_implied_probability=ReasoningDimension.available(
            quote.implied_probability, EvidenceKind.DERIVED_REASONING_FEATURE,
        ),
    )
    return MarketCandidate(
        "fixture-hits-candidate", quote, resolve_market_taxonomy("MLB", "batter_hits"),
        probability, reasoning, event, identity, CandidateProvenance("fixture", ("fixture://candidate",)),
    )


def _player(name="Aaron Judge", *, player_id=592450, hits=1, at_bats=4):
    return {"person": {"id": player_id, "fullName": name}, "stats": {"batting": {"atBats": at_bats, "hits": hits}}}


def _payload(*players):
    return {"teams": {"away": {"players": {f"ID{index}": player for index, player in enumerate(players)}},
                      "home": {"players": {}}}}


def _extract(payload=None, **updates):
    arguments = dict(event_identity=EVENT, game_status="Final", observed_at=OBSERVED,
                     source_refs=("fixture://boxscore", "fixture://explicit-final-status"))
    arguments.update(updates)
    return extract_batting_results(_payload(_player()) if payload is None else payload, **arguments)


@pytest.mark.parametrize("hits,outcome", [(0, False), (1, True), (2, True)])
def test_extract_and_resolve_factual_hits(hits, outcome):
    evidence = _extract(_payload(_player(hits=hits)))
    batter, = evidence.batters
    assert (batter.mlb_player_id, batter.player_name, batter.normalized_player_name) == (
        "592450", "Aaron Judge", "aaron judge",
    )
    assert (batter.side, batter.at_bats, batter.hits) == ("away", 4, hits)
    result = resolve_batter_hits_result(_candidate(), evidence)
    assert result.state is State.RESOLVED
    assert result.actual_hits == hits
    assert result.event_outcome is outcome
    assert result.boxscore is evidence
    assert result.batter is batter


def test_evidence_is_immutable_and_detached_from_mutable_payload():
    payload = _payload(_player())
    original = deepcopy(payload)
    evidence = _extract(payload)
    result = resolve_batter_hits_result(_candidate(), evidence)
    assert payload == original
    for obj, attribute, value in [(evidence, "game_status", "live"), (evidence.batters[0], "hits", 0),
                                  (result, "state", State.PLAYER_MISSING)]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, attribute, value)
    payload["teams"]["away"]["players"]["ID0"]["stats"]["batting"]["hits"] = 0
    assert result.actual_hits == 1


@pytest.mark.parametrize("updates", [
    {"observed_at": OBSERVED.replace(tzinfo=None)}, {"source_refs": ()},
    {"source_refs": ["mutable"]}, {"source_refs": ("",)}, {"game_status": ""},
])
def test_evidence_requires_aware_time_and_immutable_provenance(updates):
    with pytest.raises(ValueError):
        _extract(**updates)


def test_canonical_id_exact_match_takes_precedence_over_duplicate_names():
    evidence = _extract(_payload(_player(hits=2), _player(player_id=123456, hits=0)))
    result = resolve_batter_hits_result(_candidate(identity=_identity(canonical_id="592450")), evidence)
    assert result.state is State.RESOLVED
    assert result.actual_hits == 2


def test_missing_canonical_id_never_falls_back_to_matching_name():
    result = resolve_batter_hits_result(
        _candidate(identity=_identity(canonical_id="999999")), _extract(_payload(_player())),
    )
    assert result.state is State.PLAYER_MISSING
    assert result.actual_hits is None and result.event_outcome is None


def test_duplicate_canonical_id_is_ambiguous_even_for_identical_rows():
    result = resolve_batter_hits_result(
        _candidate(identity=_identity(canonical_id="592450")), _extract(_payload(_player(), _player())),
    )
    assert result.state is State.IDENTITY_AMBIGUOUS
    assert result.event_outcome is None


def test_matching_canonical_id_with_conflicting_name_fails_closed():
    result = resolve_batter_hits_result(
        _candidate(identity=_identity(canonical_id="592450")), _extract(_payload(_player("Different Batter"))),
    )
    assert result.state is State.IDENTITY_UNRESOLVED
    assert result.event_outcome is None


def test_generic_candidate_with_conflicting_observed_and_canonical_names_cannot_resolve():
    identity = replace(_identity(canonical_id="592450"), canonical_participant_name="Different Batter")
    result = resolve_batter_hits_result(_candidate(identity=identity), _extract(_payload(_player("Different Batter"))))
    assert result.state is State.IDENTITY_UNRESOLVED
    assert result.event_outcome is None


@pytest.mark.parametrize("name", [None, ""])
def test_matching_canonical_id_without_name_evidence_is_unresolved(name):
    result = resolve_batter_hits_result(
        _candidate(identity=_identity(canonical_id="592450")), _extract(_payload(_player(name))),
    )
    assert result.state is State.IDENTITY_UNRESOLVED


@pytest.mark.parametrize("canonical_id", ["fake-player", "0", "001", "-1"])
def test_invalid_canonical_mlb_id_is_not_used(canonical_id):
    result = resolve_batter_hits_result(_candidate(identity=_identity(canonical_id=canonical_id)), _extract())
    assert result.state is State.IDENTITY_UNRESOLVED


def test_roster_dictionary_key_never_becomes_canonical_player_id():
    payload = {"teams": {"away": {"players": {"ID592450": _player(player_id=None)}}}}
    evidence = _extract(payload)
    assert evidence.batters[0].mlb_player_id is None
    assert resolve_batter_hits_result(_candidate(identity=_identity(canonical_id="592450")), evidence).state is State.PLAYER_MISSING
    assert resolve_batter_hits_result(_candidate(), evidence).state is State.RESOLVED


def test_unique_name_only_match_uses_existing_deterministic_normalization():
    candidate = _candidate(identity=_identity(name="Jose Ramirez"))
    result = resolve_batter_hits_result(candidate, _extract(_payload(_player("José Ramírez", player_id=None))))
    assert result.state is State.RESOLVED
    assert candidate.participant_identity.canonical_player_id is None
    assert result.batter.mlb_player_id is None


def test_duplicate_normalized_names_across_both_sides_are_ambiguous():
    payload = _payload(_player("Rafael Flores", player_id=123))
    payload["teams"]["home"]["players"]["other"] = _player("Rafael Flores Jr.", player_id=456)
    result = resolve_batter_hits_result(_candidate(identity=_identity(name="Rafael Flores")), _extract(payload))
    assert result.state is State.IDENTITY_AMBIGUOUS
    assert result.actual_hits is None and result.event_outcome is None


def test_duplicate_name_with_missing_stat_still_prevents_resolution():
    result = resolve_batter_hits_result(_candidate(), _extract(_payload(_player(), _player(hits=None))))
    assert result.state is State.IDENTITY_AMBIGUOUS


@pytest.mark.parametrize("name", ["Aron Judge", "Other Batter"])
def test_near_or_missing_name_is_never_a_first_or_fuzzy_match(name):
    result = resolve_batter_hits_result(_candidate(identity=_identity(name=name)), _extract())
    assert result.state is State.PLAYER_MISSING
    assert result.actual_hits is None and result.event_outcome is None


@pytest.mark.parametrize("status", [IdentityStatus.UNRESOLVED, IdentityStatus.AMBIGUOUS,
                                   IdentityStatus.CONFLICTING, IdentityStatus.QUARANTINED])
def test_unqualified_participant_identity_cannot_resolve(status):
    result = resolve_batter_hits_result(_candidate(identity=_identity(status=status)), _extract())
    assert result.state is State.IDENTITY_UNRESOLVED


@pytest.mark.parametrize("hits", [None, -1, True, False, 1.5, "1", float("nan"), float("inf"), 5])
def test_missing_or_invalid_hits_is_never_zero_or_resolved(hits):
    result = resolve_batter_hits_result(_candidate(), _extract(_payload(_player(hits=hits))))
    assert result.state is State.STAT_MISSING
    assert result.batter.hits is None
    assert result.actual_hits is None and result.event_outcome is None


def test_absent_batting_and_hits_keys_remain_missing():
    for row in [{"person": {"fullName": "Aaron Judge"}},
                {"person": {"fullName": "Aaron Judge"}, "stats": {"batting": {"atBats": 4}}}]:
        result = resolve_batter_hits_result(_candidate(), _extract(_payload(row)))
        assert result.state is State.STAT_MISSING
        assert result.event_outcome is None


def test_no_players_is_missing_not_zero():
    result = resolve_batter_hits_result(_candidate(), _extract(_payload()))
    assert result.state is State.PLAYER_MISSING
    assert result.actual_hits is None and result.event_outcome is None


@pytest.mark.parametrize("status", ["pregame", "scheduled", "in_progress", "delayed", "suspended",
                                   "unknown", "Live", "Game Over", "cancelled", "postponed"])
def test_only_explicit_final_status_can_resolve_despite_scores_innings_stats(status):
    payload = _payload(_player(hits=2))
    payload.update({"runs": 10, "inning": 15})
    result = resolve_batter_hits_result(_candidate(), _extract(payload, game_status=status))
    assert result.state is State.NOT_FINAL
    assert result.actual_hits is None and result.event_outcome is None


@pytest.mark.parametrize("observed_at", [PREGAME - timedelta(seconds=1), PREGAME, START])
def test_final_evidence_at_or_before_start_cannot_resolve(observed_at):
    result = resolve_batter_hits_result(_candidate(), _extract(observed_at=observed_at))
    assert result.state is State.NOT_FINAL


def test_final_evidence_cannot_predate_candidate_when_start_is_unavailable():
    candidate = _candidate()
    candidate = replace(candidate, quote=replace(candidate.quote, event_start_time=None))
    result = resolve_batter_hits_result(candidate, _extract(observed_at=PREGAME - timedelta(seconds=1)))
    assert result.state is State.NOT_FINAL


@pytest.mark.parametrize("attribute,value", [
    ("quote_timestamp", None), ("quote_timestamp", PREGAME.replace(tzinfo=None)),
    ("event_start_time", None), ("event_start_time", START.replace(tzinfo=None)),
])
def test_generic_candidate_with_missing_or_naive_time_fails_closed(attribute, value):
    candidate = _candidate()
    candidate = replace(candidate, quote=replace(candidate.quote, **{attribute: value}))
    result = resolve_batter_hits_result(candidate, _extract())
    assert result.state is State.NOT_FINAL
    assert result.event_outcome is None


def test_event_reference_mismatch_cannot_resolve():
    evidence = _extract(event_identity=EventIdentity("other-event", "caller_supplied"))
    result = resolve_batter_hits_result(_candidate(), evidence)
    assert result.state is State.EVENT_MISMATCH
    assert result.actual_hits is None


@pytest.mark.parametrize("evidence_canonical_id", [None, "123456"])
def test_resolved_event_requires_matching_canonical_game_id(evidence_canonical_id):
    event = EventIdentity(EVENT.event_id, "explicit_mapping", IdentityStatus.RESOLVED, "777777")
    evidence_event = EVENT if evidence_canonical_id is None else replace(event, canonical_event_id=evidence_canonical_id)
    result = resolve_batter_hits_result(_candidate(event=event), _extract(event_identity=evidence_event))
    assert result.state is State.EVENT_MISMATCH


def test_embedded_game_pk_must_match_explicit_canonical_event_binding():
    payload = _payload(_player())
    payload["gamePk"] = 777777
    with pytest.raises(ValueError, match="canonical event"):
        _extract(payload)
    event = EventIdentity(EVENT.event_id, "explicit_mapping", IdentityStatus.RESOLVED, "777777")
    evidence = _extract(payload, event_identity=event)
    assert resolve_batter_hits_result(_candidate(event=event), evidence).state is State.RESOLVED
    with pytest.raises(ValueError, match="canonical event"):
        _extract(payload, event_identity=replace(event, canonical_event_id="123456"))


@pytest.mark.parametrize("embedded_status", ["In Progress", {"abstractGameState": "Live"},
                                           {"abstractGameState": "Final", "detailedState": "Suspended"}])
def test_embedded_nonfinal_status_cannot_be_overridden(embedded_status):
    payload = _payload(_player())
    payload["status"] = embedded_status
    with pytest.raises(ValueError, match="conflicts"):
        _extract(payload)


@pytest.mark.parametrize("status", [{}, {"statusCode": "I"}, {"codedGameState": "S"}])
def test_unrecognized_embedded_status_requires_explicit_status_text(status):
    payload = _payload(_player())
    payload["status"] = status
    with pytest.raises(ValueError, match="explicit supported text status"):
        _extract(payload)


@pytest.mark.parametrize("key", ["codedGameState", "statusCode"])
@pytest.mark.parametrize("code", ["I", "S", "unknown", "", None])
def test_final_text_does_not_override_nonfinal_or_unknown_embedded_code(key, code):
    payload = _payload(_player())
    payload["status"] = {"abstractGameState": "Final", key: code}
    with pytest.raises(ValueError, match="status code conflicts"):
        _extract(payload)


def test_corroborating_final_text_and_codes_allow_factual_resolution():
    payload = _payload(_player())
    payload["status"] = {
        "abstractGameState": "Final", "detailedState": "Final",
        "codedGameState": "F", "statusCode": "F",
    }
    assert resolve_batter_hits_result(_candidate(), _extract(payload)).state is State.RESOLVED


@pytest.mark.parametrize("actual_hits", [0, 1, 2])
def test_complete_offline_vertical_slice_through_final_result(actual_hits):
    quote = replace(_candidate().quote, collected_at=PREGAME)
    features = BatterHitsBaselineFeatures(
        season_hits=150, season_at_bats=500, projected_at_bats=4.2,
        lineup_status="unconfirmed", evidence_cutoff=PREGAME,
        source_refs=("fixture://pregame-season-totals", "fixture://supplied-ab-projection"),
        batter_name="Aaron Judge", event_id=EVENT.event_id,
    )
    probability = compute_batter_hits_probability(features, generated_at=PREGAME)
    source = BatterHitsSourceEvidence(
        market="batter_hits", side="Over", point=0.5, batter_name="Aaron Judge",
        snapshot_timestamp=PREGAME, event_id=EVENT.event_id, provider=quote.provider,
        sportsbook=quote.sportsbook, source_refs=("fixture://explicit-over-half-hits",),
    )
    candidate = assemble_batter_hits_candidate(
        candidate_id="assembled-fixture", quote=quote, source_evidence=source, features=features,
        probability=probability, participant_identity=_identity(), event_identity=EVENT,
        provenance=CandidateProvenance("fixture", ("fixture://vertical-slice",)),
    )
    evidence = _extract(_payload(_player(hits=actual_hits)))
    result = resolve_batter_hits_result(candidate, evidence)
    assert candidate.quote is quote and candidate.probability is probability
    assert probability.model_probability == 1 - (1 - 150 / 500) ** 4.2
    assert candidate.research_only and not candidate.eligible_for_betting
    assert not candidate.eligible_for_official_pick
    assert result.state is State.RESOLVED
    assert result.actual_hits == actual_hits
    assert result.event_outcome is (actual_hits >= 1)


@pytest.mark.parametrize("updates", [{"hits": -1}, {"at_bats": True}, {"hits": 5},
                                     {"mlb_player_id": "fake"}, {"side": "unknown"}])
def test_direct_factual_evidence_rejects_invalid_values(updates):
    values = dict(mlb_player_id="592450", player_name="Aaron Judge", side="away", at_bats=4, hits=1)
    values.update(updates)
    with pytest.raises(ValueError):
        BatterBattingResult(**values)


def test_extractor_and_resolver_are_pure_with_io_blocked(monkeypatch):
    candidate, payload = _candidate(), _payload(_player())
    original = deepcopy(payload)

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure result functions attempted I/O")

    with monkeypatch.context() as block:
        for obj, name in [(builtins, "open"), (io, "open"), (os, "open"), (Path, "write_text"),
                          (Path, "write_bytes"), (socket, "socket"), (socket, "create_connection"),
                          (subprocess, "Popen"), (subprocess, "run"), (requests.sessions.Session, "request")]:
            block.setattr(obj, name, forbidden)
        evidence = _extract(payload)
        result = resolve_batter_hits_result(candidate, evidence)
    assert result.state is State.RESOLVED
    assert payload == original
