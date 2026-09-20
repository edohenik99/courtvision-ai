"""Offline behavioral checks for the research-only Hits specialist and adapter."""

from __future__ import annotations

import builtins
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
import io
import os
from pathlib import Path
import socket
import subprocess

import pytest
import requests

from courtvision.core.candidates import (
    CandidateProvenance,
    EventIdentity,
    IdentityStatus,
    MarketCandidate,
    ParticipantIdentity,
)
from courtvision.core.odds import (
    NormalizedOddsQuote,
    OddsMarketIdentity,
    OddsSelection,
    OddsSourceMetadata,
)
from courtvision.core.probability import (
    DistributionType,
    ProbabilityOutput,
    ThresholdDirection,
)
from courtvision.core.reasoning import EvidenceAvailability
from courtvision.sports.mlb.batter_hits import (
    BatterHitsBaselineFeatures,
    BatterHitsSourceEvidence,
    assemble_batter_hits_candidate,
    compute_batter_hits_probability,
)


SNAPSHOT = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
CUTOFF = SNAPSHOT + timedelta(minutes=1)
GENERATED = CUTOFF + timedelta(minutes=1)
START = SNAPSHOT + timedelta(hours=7)
EVENT_ID = "offline-hits-game"
BATTER = "Example Batter"


def _features(**updates: object) -> BatterHitsBaselineFeatures:
    values = {
        "season_hits": 125,
        "season_at_bats": 500,
        "projected_at_bats": 4.0,
        "lineup_status": "unknown",
        "evidence_cutoff": CUTOFF,
        "source_refs": ("fixture-season-through-cutoff", "fixture-ab-projection"),
        "batter_name": BATTER,
        "event_id": EVENT_ID,
    }
    values.update(updates)
    return BatterHitsBaselineFeatures(**values)


def _source(**updates: object) -> BatterHitsSourceEvidence:
    values = {
        "market": "batter_hits",
        "side": "Over",
        "point": 0.5,
        "batter_name": BATTER,
        "snapshot_timestamp": SNAPSHOT,
        "event_id": EVENT_ID,
        "provider": "offline-source",
        "sportsbook": "offline-book",
        "source_refs": ("fixture-source-over-half-hit",),
    }
    values.update(updates)
    return BatterHitsSourceEvidence(**values)


def _quote(**updates: object) -> NormalizedOddsQuote:
    values = {
        "market_identity": OddsMarketIdentity(
            sport="MLB", league="MLB", event_id=EVENT_ID,
            event_date=date(2026, 9, 20), home_team="NYY", away_team="BOS",
            market_type="batter_hits",
        ),
        "selection": OddsSelection(selection_name=BATTER, line=0.5),
        "source_metadata": OddsSourceMetadata(
            sportsbook="offline-book", provider="offline-source",
            mode="research", source_type="mock",
        ),
        "american_odds": -150,
        "quote_timestamp": SNAPSHOT,
        "collected_at": CUTOFF,
        "event_start_time": START,
        "is_live": False,
    }
    values.update(updates)
    return NormalizedOddsQuote(**values)


def _identity(**updates: object) -> ParticipantIdentity:
    values = {
        "participant_name": BATTER,
        "identity_method": "explicit_fixture_name_only",
        "identity_status": IdentityStatus.NAME_ONLY_RESEARCH,
    }
    values.update(updates)
    return ParticipantIdentity(**values)


def _candidate(**updates: object) -> MarketCandidate:
    features = updates.get("features", _features())
    values = {
        "candidate_id": "offline-hits-candidate",
        "quote": _quote(),
        "source_evidence": _source(),
        "features": features,
        "probability": compute_batter_hits_probability(features, generated_at=GENERATED),
        "participant_identity": _identity(),
        "event_identity": EventIdentity(EVENT_ID, "explicit_fixture_event_reference"),
        "provenance": CandidateProvenance("offline-hits-test", ("fixture-candidate-ref",)),
    }
    values.update(updates)
    return assemble_batter_hits_candidate(**values)


def test_features_and_source_evidence_are_immutable() -> None:
    for obj, field, changed in (
        (_features(), "season_hits", 150),
        (_features(), "source_refs", ("changed",)),
        (_source(), "side", "Under"),
        (_source(), "source_refs", ("changed",)),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, changed)
    feature_names = {field.name for field in fields(BatterHitsBaselineFeatures)}
    assert not feature_names & {
        "quote", "odds", "american_odds", "decimal_odds", "implied_probability",
        "market_probability", "sportsbook",
    }


@pytest.mark.parametrize("key,value", [
    ("season_hits", -1), ("season_hits", 501), ("season_hits", 1.5),
    ("season_hits", True), ("season_hits", float("nan")),
    ("season_hits", float("inf")), ("season_at_bats", 0),
    ("season_at_bats", -1), ("season_at_bats", 500.5),
    ("season_at_bats", float("nan")), ("season_at_bats", float("inf")),
    ("projected_at_bats", 0), ("projected_at_bats", -1),
    ("projected_at_bats", float("nan")), ("projected_at_bats", float("inf")),
    ("projected_at_bats", float("-inf")), ("projected_at_bats", True),
    ("projected_at_bats", None), ("source_refs", ()),
    ("source_refs", ["mutable-ref"]), ("source_refs", ("",)),
    ("source_refs", None), ("evidence_cutoff", CUTOFF.replace(tzinfo=None)),
    ("lineup_status", ""), ("batter_name", ""), ("event_id", ""),
])
def test_features_reject_missing_invalid_or_mutable_evidence(key, value) -> None:
    with pytest.raises(ValueError):
        _features(**{key: value})


@pytest.mark.parametrize("hits,at_bats,projected", [
    (0, 500, 4.0), (125, 500, 4.0), (125, 500, 3.7), (500, 500, 4.0),
])
def test_probability_is_exact_independent_at_bat_formula(hits, at_bats, projected) -> None:
    features = _features(season_hits=hits, season_at_bats=at_bats, projected_at_bats=projected)
    output = compute_batter_hits_probability(features, generated_at=GENERATED)
    expected = 1 - (1 - hits / at_bats) ** projected
    assert output.model_probability == expected
    assert 0 <= output.model_probability <= 1
    assert isinstance(output, ProbabilityOutput)
    assert output.target_statistic == "hits"
    assert output.threshold == 1
    assert output.direction is ThresholdDirection.AT_LEAST
    assert output.distribution_type is DistributionType.BERNOULLI_EVENT
    assert output.model_id == "mlb-batter-hits-independent-at-bat-baseline"
    assert output.model_version == "research-v1"
    assert output.feature_schema_version
    assert output.generated_at == GENERATED
    assert output.evidence_cutoff == CUTOFF
    for evidence in (output.calibration_provenance, output.uncertainty):
        assert evidence.availability is EvidenceAvailability.UNAVAILABLE
        assert evidence.reason
    assert output.research_only is True
    assert output.eligible_for_betting is False
    assert output.eligible_for_official_pick is False
    assert output.approval_status == "not_approved"


def test_lineup_status_does_not_invent_a_probability_multiplier() -> None:
    unknown = compute_batter_hits_probability(_features(), generated_at=GENERATED)
    supplied = compute_batter_hits_probability(
        _features(lineup_status="confirmed_by_supplied_fixture"), generated_at=GENERATED,
    )
    assert supplied.model_probability == unknown.model_probability


@pytest.mark.parametrize("generated_at", [
    GENERATED.replace(tzinfo=None), CUTOFF - timedelta(seconds=1),
])
def test_probability_rejects_naive_or_pre_evidence_generation(generated_at) -> None:
    with pytest.raises(ValueError):
        compute_batter_hits_probability(_features(), generated_at=generated_at)


def test_model_probability_is_independent_of_sportsbook_price() -> None:
    features = _features()
    first_probability = compute_batter_hits_probability(features, generated_at=GENERATED)
    second_probability = compute_batter_hits_probability(features, generated_at=GENERATED)
    first = _candidate(features=features, probability=first_probability, quote=_quote(american_odds=-150))
    second = _candidate(features=features, probability=second_probability, quote=_quote(american_odds=120))
    assert first.probability.model_probability == second.probability.model_probability
    assert first.probability is first_probability
    assert second.probability is second_probability
    assert first.reasoning.market_implied_probability.value != second.reasoning.market_implied_probability.value
    assert first.reasoning.edge.value != second.reasoning.edge.value
    assert first.probability.model_probability != first.quote.implied_probability


@pytest.mark.parametrize("key,value", [
    ("market", "batter_total_bases"), ("market", "batter_home_runs"),
    ("market", "batter_hits_alternate"), ("market", "2+ hits"),
    ("market", "unknown"), ("side", "Under"), ("side", ""),
    ("side", "Yes"), ("side", "2+"), ("point", 1.5),
    ("point", 1), ("point", 2), ("point", None),
    ("point", float("nan")), ("point", float("inf")),
    ("batter_name", ""), ("snapshot_timestamp", SNAPSHOT.replace(tzinfo=None)),
    ("event_id", ""), ("provider", ""), ("sportsbook", ""),
    ("source_refs", ()), ("source_refs", ["mutable-source"]),
])
def test_source_evidence_requires_explicit_over_half_hit_semantics(key, value) -> None:
    with pytest.raises(ValueError):
        _source(**{key: value})


def test_candidate_retains_exact_objects_and_generic_research_contract() -> None:
    quote = _quote()
    probability = compute_batter_hits_probability(_features(), generated_at=GENERATED)
    identity = _identity()
    event = EventIdentity(EVENT_ID, "supplied_fixture_reference")
    provenance = CandidateProvenance("offline-hits-test", ("original-candidate-ref",))
    candidate = _candidate(
        quote=quote, probability=probability, participant_identity=identity,
        event_identity=event, provenance=provenance,
    )
    assert type(candidate) is MarketCandidate
    assert candidate.quote is quote
    assert candidate.probability is probability
    assert candidate.participant_identity is identity
    assert candidate.event_identity is event
    assert candidate.taxonomy.sport == "MLB"
    assert candidate.taxonomy.market_type == "batter_hits"
    assert candidate.taxonomy.market_family == "player_prop"
    assert candidate.taxonomy.participant_scope == "batter"
    assert candidate.taxonomy.statistic == "hits"
    assert candidate.taxonomy.period == "full_game"
    assert candidate.probability.threshold == 1
    assert candidate.probability.direction is ThresholdDirection.AT_LEAST
    assert candidate.quote.line == 0.5
    assert candidate.record_kind == "MODEL_CANDIDATE"
    assert candidate.research_only is True
    assert candidate.eligible_for_betting is False
    assert candidate.eligible_for_official_pick is False
    assert candidate.approval_status == "not_approved"
    assert "original-candidate-ref" in candidate.provenance.source_refs
    assert set(_features().source_refs) <= set(candidate.provenance.source_refs)
    assert set(_source().source_refs) <= set(candidate.provenance.source_refs)
    assert candidate.provenance.source == provenance.source
    names = {field.name for field in fields(candidate)}
    assert not names & {"stake", "stake_units", "kelly", "kelly_fraction", "bankroll", "promotion", "tier", "pick_score"}


def test_reasoning_exposes_exact_values_and_explained_absence() -> None:
    candidate = _candidate()
    reasoning = candidate.reasoning
    assert reasoning.model_probability.value == candidate.probability.model_probability
    assert reasoning.market_implied_probability.value == candidate.quote.implied_probability
    assert reasoning.edge.value == candidate.probability.model_probability - candidate.quote.implied_probability
    assert reasoning.opportunity.value == _features().projected_at_bats
    assert reasoning.identity_quality.value == IdentityStatus.NAME_ONLY_RESEARCH.value
    for name in (
        "threshold_cushion", "projection_confidence", "matchup", "environment",
        "volatility", "market_movement",
    ):
        dimension = getattr(reasoning, name)
        assert dimension.availability is EvidenceAvailability.UNAVAILABLE
        assert dimension.value is None
        assert dimension.reason
    assert "alternate" in reasoning.threshold_cushion.reason.casefold()
    assert reasoning.classification is None
    assert not {field.name for field in fields(reasoning)} & {"score", "pick_score", "aggregate_score", "tier"}


@pytest.mark.parametrize("line", [None, 0, 1, 1.5, 2.5])
def test_adapter_rejects_unsupported_quote_line(line) -> None:
    with pytest.raises(ValueError):
        _candidate(quote=_quote(selection=OddsSelection(BATTER, line=line)))


@pytest.mark.parametrize("market", ["batter_home_runs", "batter_total_bases", "batter_hits_alternate", "unknown"])
def test_adapter_rejects_unsupported_normalized_market(market) -> None:
    quote = _quote()
    with pytest.raises(ValueError):
        _candidate(quote=replace(quote, market_identity=replace(quote.market_identity, market_type=market)))


@pytest.mark.parametrize("source_market", ["batter_home_runs", "batter_total_bases", "batter_hits_alternate"])
def test_raw_quote_market_cannot_conflict_with_source_evidence(source_market) -> None:
    quote = _quote()
    with pytest.raises(ValueError):
        _candidate(quote=replace(
            quote, source_metadata=replace(quote.source_metadata, raw_provider_market_id=source_market),
        ))


@pytest.mark.parametrize("source_market", [None, "batter_hits"])
def test_quote_accepts_absent_or_compatible_raw_market_reference(source_market) -> None:
    quote = _quote()
    candidate = _candidate(quote=replace(
        quote, source_metadata=replace(quote.source_metadata, raw_provider_market_id=source_market),
    ))
    assert candidate.taxonomy.market_type == "batter_hits"


@pytest.mark.parametrize("selection", ["Over", "Under", "Yes", "No", "2+ hits"])
def test_unnamed_quote_cannot_prove_the_price_belongs_to_supplied_batter(selection) -> None:
    with pytest.raises(ValueError):
        _candidate(quote=_quote(selection=OddsSelection(selection, line=0.5)))


@pytest.mark.parametrize("approval", [
    {"eligible_for_betting": True},
    {"eligible_for_betting": True, "kelly_eligible": True},
    {"approval_status": "approved"},
])
def test_adapter_rejects_operationally_approved_quote(approval) -> None:
    metadata = OddsSourceMetadata(
        sportsbook="offline-book", provider="offline-source", mode="production", source_type="manual",
    )
    quote = _quote(source_metadata=metadata, **approval)
    with pytest.raises(ValueError):
        _candidate(quote=quote)


def test_name_only_identity_does_not_synthesize_canonical_player_id() -> None:
    candidate = _candidate()
    assert candidate.participant_identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH
    assert candidate.participant_identity.canonical_player_id is None
    assert candidate.participant_identity.canonical_player_name is None


def test_explicit_resolved_player_identity_is_retained() -> None:
    identity = _identity(
        identity_status=IdentityStatus.RESOLVED,
        canonical_participant_id="123456", canonical_participant_name=BATTER,
        identity_method="caller_supplied_mlb_id",
    )
    candidate = _candidate(participant_identity=identity)
    assert candidate.participant_identity is identity
    assert candidate.participant_identity.canonical_player_id == "123456"
    assert candidate.reasoning.identity_quality.value == "resolved"


@pytest.mark.parametrize("missing", ["canonical_participant_id", "canonical_participant_name"])
def test_resolved_player_identity_requires_explicit_id_and_name(missing) -> None:
    supplied = {
        "identity_status": IdentityStatus.RESOLVED,
        "canonical_participant_id": "123456", "canonical_participant_name": BATTER,
    }
    supplied.pop(missing)
    with pytest.raises(ValueError):
        _identity(**supplied)


@pytest.mark.parametrize("canonical_id", ["name-derived-id", "0", "-1", "123.5", "hash-123456", "0665599"])
def test_resolved_hits_identity_rejects_non_mlb_identifier_shapes(canonical_id) -> None:
    identity = _identity(
        identity_status=IdentityStatus.RESOLVED,
        canonical_participant_id=canonical_id, canonical_participant_name=BATTER,
    )
    with pytest.raises(ValueError):
        _candidate(participant_identity=identity)


@pytest.mark.parametrize("status", [
    IdentityStatus.UNRESOLVED, IdentityStatus.AMBIGUOUS, IdentityStatus.CONFLICTING,
    IdentityStatus.QUARANTINED,
])
def test_adapter_rejects_other_identity_resolution_states(status) -> None:
    with pytest.raises(ValueError):
        _candidate(participant_identity=_identity(identity_status=status))


def test_deterministic_mlb_name_normalization_preserves_supplied_identity() -> None:
    quote = _quote(selection=OddsSelection("Ronald Acuna", line=0.5))
    features = _features(batter_name="Ronald Acuña Jr.")
    identity = _identity(
        participant_name="Ronald Acuna", identity_status=IdentityStatus.RESOLVED,
        canonical_participant_id="660670", canonical_participant_name="Ronald Acuña Jr.",
    )
    candidate = _candidate(
        quote=quote, features=features, source_evidence=_source(batter_name="Acuna, Ronald"),
        participant_identity=identity,
    )
    assert candidate.participant_identity is identity
    assert candidate.quote is quote


@pytest.mark.parametrize("part", ["features", "source", "participant", "canonical", "quote"])
def test_conflicting_batter_names_fail_closed(part) -> None:
    overrides = {}
    if part == "features":
        overrides["features"] = _features(batter_name="Wrong Batter")
    elif part == "source":
        overrides["source_evidence"] = _source(batter_name="Wrong Batter")
    elif part == "participant":
        overrides["participant_identity"] = _identity(participant_name="Wrong Batter")
    elif part == "canonical":
        overrides["participant_identity"] = _identity(
            identity_status=IdentityStatus.RESOLVED, canonical_participant_id="123456",
            canonical_participant_name="Wrong Batter",
        )
    else:
        overrides["quote"] = _quote(selection=OddsSelection("Wrong Batter", line=0.5))
    with pytest.raises(ValueError):
        _candidate(**overrides)


@pytest.mark.parametrize("part", ["features", "source", "identity"])
def test_conflicting_event_identity_fails_closed(part) -> None:
    overrides = {
        "features": {"features": _features(event_id="different-event")},
        "source": {"source_evidence": _source(event_id="different-event")},
        "identity": {"event_identity": EventIdentity("different-event", "supplied")},
    }[part]
    with pytest.raises(ValueError):
        _candidate(**overrides)


@pytest.mark.parametrize("key,value", [
    ("provider", "different-provider"), ("sportsbook", "different-book"),
    ("snapshot_timestamp", SNAPSHOT - timedelta(seconds=1)),
])
def test_source_observation_must_bind_to_quote(key, value) -> None:
    with pytest.raises(ValueError):
        _candidate(source_evidence=_source(**{key: value}))


@pytest.mark.parametrize("key,value", [
    ("quote_timestamp", None), ("quote_timestamp", SNAPSHOT.replace(tzinfo=None)),
    ("collected_at", None), ("collected_at", CUTOFF.replace(tzinfo=None)),
    ("collected_at", SNAPSHOT - timedelta(seconds=1)),
    ("collected_at", GENERATED + timedelta(seconds=1)),
    ("event_start_time", None), ("event_start_time", START.replace(tzinfo=None)),
    ("event_start_time", GENERATED), ("event_start_time", GENERATED - timedelta(seconds=1)),
    ("is_live", True),
])
def test_candidate_rejects_unproven_or_postgame_point_in_time_evidence(key, value) -> None:
    with pytest.raises(ValueError):
        _candidate(quote=_quote(**{key: value}))


def test_source_snapshot_cannot_be_later_than_feature_evidence_cutoff() -> None:
    later = CUTOFF + timedelta(seconds=1)
    with pytest.raises(ValueError):
        _candidate(
            quote=_quote(quote_timestamp=later, collected_at=later),
            source_evidence=_source(snapshot_timestamp=later),
        )


@pytest.mark.parametrize("key,value", [
    ("model_probability", 0.9), ("model_id", "different-model"),
    ("model_version", "different-version"), ("feature_schema_version", "different-schema"),
    ("target_statistic", "home_runs"), ("threshold", 2),
    ("direction", ThresholdDirection.AT_MOST),
    ("evidence_cutoff", CUTOFF - timedelta(seconds=1)),
])
def test_adapter_rejects_probability_not_derived_from_supplied_baseline(key, value) -> None:
    probability = compute_batter_hits_probability(_features(), generated_at=GENERATED)
    with pytest.raises(ValueError):
        _candidate(probability=replace(probability, **{key: value}))


def test_specialist_and_candidate_assembly_perform_no_io_or_environment_mutation(monkeypatch) -> None:
    features = _features()
    quote = _quote()
    source = _source()
    identity = _identity()
    event = EventIdentity(EVENT_ID, "offline-explicit-reference")
    provenance = CandidateProvenance("offline-test", ("source-fixture",))
    environment_before = dict(os.environ)

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure Hits functions attempted I/O")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(io, "open", forbidden)
        for name in ("open", "write_text", "write_bytes", "mkdir", "unlink"):
            patch.setattr(Path, name, forbidden)
        patch.setattr(requests.sessions.Session, "request", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        for name in ("Popen", "run", "call", "check_call", "check_output"):
            patch.setattr(subprocess, name, forbidden)
        probability = compute_batter_hits_probability(features, generated_at=GENERATED)
        candidate = assemble_batter_hits_candidate(
            candidate_id="offline-pure-test", quote=quote, source_evidence=source,
            features=features, probability=probability, participant_identity=identity,
            event_identity=event, provenance=provenance,
        )
    assert candidate.quote is quote
    assert candidate.probability is probability
    assert dict(os.environ) == environment_before
