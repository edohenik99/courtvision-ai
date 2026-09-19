"""Offline contract checks for analytical candidates and identity boundaries."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import courtvision.core.candidates as candidate_module
from courtvision.core.candidates import (
    CandidateProvenance,
    EventIdentity,
    IdentityStatus,
    MarketCandidate,
    ParticipantIdentity,
)
from courtvision.core.market_taxonomy import resolve_market_taxonomy
from courtvision.core.odds import (
    NormalizedOddsQuote,
    OddsMarketIdentity,
    OddsSelection,
    OddsSourceMetadata,
)
from courtvision.core.probability import (
    CalibrationProvenance,
    DistributionType,
    ProbabilityOutput,
    ThresholdDirection,
)
from courtvision.core.reasoning import (
    EvidenceAvailability,
    EvidenceKind,
    ReasoningAssessment,
    ReasoningDimension,
)


NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def _quote(**updates: object) -> NormalizedOddsQuote:
    values = {
        "market_identity": OddsMarketIdentity(
            sport="MLB", league="MLB", event_id="fixture-game-1",
            event_date=date(2026, 9, 19), home_team="NYY", away_team="BOS",
            market_type="batter_home_runs",
        ),
        "selection": OddsSelection(selection_name="Example Batter", line=0.5),
        "source_metadata": OddsSourceMetadata(
            sportsbook="fixture-book", provider="fixture-source", mode="research",
            source_type="sample",
        ),
        "american_odds": 350,
        "quote_timestamp": NOW,
    }
    values.update(updates)
    return NormalizedOddsQuote(**values)


def _probability(**updates: object) -> ProbabilityOutput:
    values = {
        "model_probability": 0.25,
        "target_statistic": "home_runs",
        "threshold": 1,
        "direction": ThresholdDirection.AT_LEAST,
        "distribution_type": DistributionType.BERNOULLI_EVENT,
        "model_id": "fixture-hr-model",
        "model_version": "v1",
        "feature_schema_version": "fixture-features-v1",
        "calibration_provenance": CalibrationProvenance(
            availability=EvidenceAvailability.UNAVAILABLE,
            reason="Fixture has no calibration evidence.",
        ),
        "generated_at": NOW,
        "evidence_cutoff": NOW,
    }
    values.update(updates)
    return ProbabilityOutput(**values)


def _reasoning(quote: NormalizedOddsQuote, probability: ProbabilityOutput) -> ReasoningAssessment:
    return ReasoningAssessment(
        model_probability=ReasoningDimension.available(
            probability.model_probability, EvidenceKind.MODEL_OUTPUT
        ),
        market_implied_probability=ReasoningDimension.available(
            quote.implied_probability, EvidenceKind.DERIVED_REASONING_FEATURE
        ),
    )


def _candidate(**updates: object) -> MarketCandidate:
    quote = updates.get("quote", _quote())
    probability = updates.get("probability", _probability())
    values = {
        "candidate_id": "analytical-fixture-1",
        "quote": quote,
        "taxonomy": resolve_market_taxonomy("MLB", "batter_home_runs"),
        "probability": probability,
        "reasoning": _reasoning(quote, probability),
        "event_identity": EventIdentity(
            event_id=quote.event_id,
            event_identity_method="fixture_event_reference_only",
        ),
        "participant_identity": ParticipantIdentity(
            participant_name="Example Batter",
            identity_method="legacy_name_only",
            identity_status=IdentityStatus.NAME_ONLY_RESEARCH,
        ),
        "provenance": CandidateProvenance(
            source="fixture-research",
            source_refs=("prediction_run_id=fixture-run-1",),
        ),
    }
    values.update(updates)
    return MarketCandidate(**values)


def test_candidate_preserves_bound_objects_exactly() -> None:
    quote = _quote()
    probability = _probability()
    reasoning = _reasoning(quote, probability)
    candidate = _candidate(quote=quote, probability=probability, reasoning=reasoning)

    assert candidate.quote is quote
    assert candidate.probability is probability
    assert candidate.reasoning is reasoning
    assert candidate.quote.implied_probability == quote.implied_probability
    assert candidate.probability.threshold == 1
    assert candidate.quote.line == 0.5


def test_candidate_and_nested_identity_provenance_are_immutable() -> None:
    candidate = _candidate()
    for obj, name, value in (
        (candidate, "candidate_id", "changed"),
        (candidate.event_identity, "event_id", "changed"),
        (candidate.participant_identity, "participant_name", "changed"),
        (candidate.provenance, "source", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)
    with pytest.raises(ValueError, match="immutable tuple"):
        CandidateProvenance("fixture", ["mutable-reference"])


def test_name_only_research_does_not_create_canonical_identity() -> None:
    identity = _candidate().participant_identity
    assert identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH
    assert identity.canonical_participant_id is None
    assert identity.canonical_player_id is None
    assert identity.canonical_player_name is None
    assert identity.player_identity_status == "name_only_research"
    assert identity.player_identity_method == "legacy_name_only"


@pytest.mark.parametrize("status", [
    IdentityStatus.UNRESOLVED, IdentityStatus.NAME_ONLY_RESEARCH,
    IdentityStatus.AMBIGUOUS, IdentityStatus.CONFLICTING, IdentityStatus.QUARANTINED,
])
def test_unresolved_states_reject_canonical_player_claims(status: IdentityStatus) -> None:
    with pytest.raises(ValueError, match="cannot claim canonical"):
        ParticipantIdentity(
            participant_name="Example Batter", identity_method="not_resolved",
            identity_status=status, canonical_participant_id="invented-player-id",
        )


def test_resolved_identity_requires_both_id_and_name() -> None:
    with pytest.raises(ValueError, match="canonical id and name"):
        ParticipantIdentity("Example Player", "reviewed_mapping", IdentityStatus.RESOLVED)
    identity = ParticipantIdentity(
        "Provider Name", "reviewed_mapping", IdentityStatus.RESOLVED,
        canonical_participant_id="canonical-23", canonical_participant_name="Canonical Name",
    )
    assert identity.canonical_player_id == "canonical-23"
    assert identity.canonical_player_name == "Canonical Name"
    assert identity.player_identity_status == "resolved"


@pytest.mark.parametrize("identifier", ["unknown", "unresolved", "none", "n/a", " "])
def test_resolved_identity_rejects_placeholder_identifiers(identifier: str) -> None:
    with pytest.raises(ValueError):
        ParticipantIdentity(
            "Example Player", "reviewed_mapping", IdentityStatus.RESOLVED,
            canonical_participant_id=identifier, canonical_participant_name="Example Player",
        )


def test_event_resolution_never_infers_canonical_id_from_quote() -> None:
    identity = _candidate().event_identity
    assert identity.event_identity_status == "unresolved"
    assert identity.canonical_event_id is None
    with pytest.raises(ValueError, match="requires resolved"):
        replace(identity, canonical_event_id=identity.event_id)
    with pytest.raises(ValueError, match="requires resolved"):
        replace(identity, identity_status=IdentityStatus.RESOLVED)


def test_candidate_is_research_only_without_promotion_or_sizing_fields() -> None:
    candidate = _candidate()
    assert candidate.record_kind == "MODEL_CANDIDATE"
    assert candidate.record_kind not in {"MARKET_OBSERVATION", "OFFICIAL_PICK", "OFFICIAL_PICK_CANDIDATE_REVIEW"}
    assert candidate.research_only is True
    assert candidate.eligible_for_betting is False
    assert candidate.eligible_for_official_pick is False
    assert candidate.approval_status == "not_approved"
    names = {item.name for item in fields(candidate)}
    assert not names & {"stake", "stake_fraction", "kelly", "bankroll", "official_pick_id", "promotion"}
    for name, value in (
        ("research_only", False), ("eligible_for_betting", True),
        ("eligible_for_official_pick", True), ("approval_status", "approved"),
        ("record_kind", "OFFICIAL_PICK"),
    ):
        with pytest.raises((ValueError, TypeError), match="init=False"):
            replace(candidate, **{name: value})


def test_candidate_module_has_no_publication_or_provider_imports() -> None:
    tree = ast.parse(Path(candidate_module.__file__).read_text(encoding="utf-8"))
    imports = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imports.update(
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert all(not module.startswith((
        "courtvision.official_picks", "courtvision.lifecycle", "courtvision.providers",
        "courtvision.portfolio", "courtvision.sports",
    )) for module in imports)


@pytest.mark.parametrize("updates,match", [
    ({"candidate_id": " "}, "candidate_id"),
    ({"taxonomy": resolve_market_taxonomy("NBA", "player_points")}, "sport"),
    ({"taxonomy": resolve_market_taxonomy("MLB", "batter_hits")}, "market_type"),
    ({"event_identity": EventIdentity("different-game", "fixture")}, "event identity"),
    ({"participant_identity": ParticipantIdentity("Other Batter", "fixture")}, "named quote selection"),
    ({"participant_identity": ParticipantIdentity(None, "game_market", IdentityStatus.NOT_APPLICABLE)}, "requires participant"),
    ({"provenance": {}}, "CandidateProvenance"),
])
def test_mismatched_bindings_fail_closed(updates: dict[str, object], match: str) -> None:
    with pytest.raises((ValueError, TypeError), match=match):
        _candidate(**updates)


def test_probability_statistic_and_threshold_must_match_quote() -> None:
    with pytest.raises(ValueError, match="target_statistic"):
        _candidate(probability=_probability(target_statistic="hits"))
    with pytest.raises(ValueError, match="threshold does not match"):
        _candidate(probability=_probability(threshold=100))


def test_direct_strict_threshold_is_supported_without_rewriting_model_target() -> None:
    probability = _probability(threshold=0.5, direction=ThresholdDirection.GREATER_THAN)
    candidate = _candidate(probability=probability)
    assert candidate.probability is probability
    assert candidate.probability.threshold == 0.5
    with pytest.raises(ValueError, match="threshold does not match"):
        _candidate(probability=_probability(threshold=1, direction=ThresholdDirection.GREATER_THAN))


@pytest.mark.parametrize("side,line,threshold,direction", [
    ("Under", 0.5, 0.5, ThresholdDirection.GREATER_THAN),
    ("Over", 0.5, 0.5, ThresholdDirection.LESS_THAN),
    ("Over", 1, 1, ThresholdDirection.AT_LEAST),
    ("Under", 1, 1, ThresholdDirection.AT_MOST),
    ("Over", 0.5, 0, ThresholdDirection.AT_MOST),
    ("Under", 0.5, 1, ThresholdDirection.AT_LEAST),
    ("Over", 0.5, 0.5, ThresholdDirection.EQUAL),
])
def test_explicit_quote_side_rejects_opposite_direction_and_push_inclusion(
    side: str, line: float, threshold: float, direction: ThresholdDirection,
) -> None:
    with pytest.raises(ValueError, match="quoted side and line"):
        _candidate(
            quote=_quote(selection=OddsSelection(side, line=line)),
            probability=_probability(threshold=threshold, direction=direction),
        )


@pytest.mark.parametrize("side,line,threshold,direction", [
    ("Over", 0.5, 0.5, ThresholdDirection.GREATER_THAN),
    ("Under", 0.5, 0.5, ThresholdDirection.LESS_THAN),
    ("Over", 1, 2, ThresholdDirection.AT_LEAST),
    ("Under", 1, 0, ThresholdDirection.AT_MOST),
    ("Over", 0.5, 1, ThresholdDirection.AT_LEAST),
    ("Under", 0.5, 0, ThresholdDirection.AT_MOST),
    (" OVER ", 0.5, 1, ThresholdDirection.AT_LEAST),
])
def test_explicit_quote_side_accepts_exact_strict_or_discrete_equivalent_target(
    side: str, line: float, threshold: float, direction: ThresholdDirection,
) -> None:
    probability = _probability(threshold=threshold, direction=direction)
    candidate = _candidate(
        quote=_quote(selection=OddsSelection(side, line=line)),
        probability=probability,
    )
    assert candidate.probability is probability
    assert candidate.probability.threshold == threshold
    assert candidate.probability.direction is direction


@pytest.mark.parametrize("name,kind", [
    ("model_probability", EvidenceKind.MODEL_OUTPUT),
    ("market_implied_probability", EvidenceKind.DERIVED_REASONING_FEATURE),
])
def test_reasoning_probability_must_bind_to_available_source(name: str, kind: EvidenceKind) -> None:
    candidate = _candidate()
    for dimension in (
        ReasoningDimension.available(0.9, kind),
        ReasoningDimension.unavailable(kind, "Not supplied."),
    ):
        with pytest.raises(ValueError, match=name):
            replace(candidate, reasoning=replace(candidate.reasoning, **{name: dimension}))


def test_edge_remains_optional_and_cannot_disagree_with_bound_probabilities() -> None:
    candidate = _candidate()
    assert candidate.reasoning.edge.availability is EvidenceAvailability.UNAVAILABLE
    expected = candidate.probability.model_probability - candidate.quote.implied_probability
    bound = replace(candidate, reasoning=replace(
        candidate.reasoning,
        edge=ReasoningDimension.available(expected, EvidenceKind.DERIVED_REASONING_FEATURE),
    ))
    assert bound.reasoning.edge.value == expected
    with pytest.raises(ValueError, match="edge"):
        replace(candidate, reasoning=replace(
            candidate.reasoning,
            edge=ReasoningDimension.available(0.8, EvidenceKind.DERIVED_REASONING_FEATURE),
        ))


def test_empty_or_mutable_provenance_fails_closed() -> None:
    with pytest.raises(ValueError, match="source_refs"):
        CandidateProvenance("fixture", ())
    with pytest.raises(ValueError, match="source reference"):
        CandidateProvenance("fixture", (" ",))


def test_game_scoped_market_uses_explicit_not_applicable_participant() -> None:
    quote = _quote(
        market_identity=OddsMarketIdentity(
            sport="MLB", league="MLB", event_id="fixture-game-1",
            event_date=date(2026, 9, 19), home_team="NYY", away_team="BOS", market_type="game_total",
        ),
        selection=OddsSelection("Over", line=8.5),
    )
    taxonomy = resolve_market_taxonomy("MLB", "game_total")
    probability = _probability(
        target_statistic=taxonomy.statistic.value,
        threshold=8.5, direction=ThresholdDirection.GREATER_THAN,
    )
    candidate = _candidate(
        quote=quote, taxonomy=taxonomy, probability=probability,
        participant_identity=ParticipantIdentity(None, "game_scope", IdentityStatus.NOT_APPLICABLE),
    )
    assert candidate.participant_identity.identity_status is IdentityStatus.NOT_APPLICABLE
    with pytest.raises(ValueError, match="game-scoped"):
        replace(candidate, participant_identity=ParticipantIdentity("A Player", "fixture"))


def test_required_quote_line_cannot_be_omitted() -> None:
    taxonomy = resolve_market_taxonomy("MLB", "game_total")
    quote = _quote(
        market_identity=replace(_quote().market_identity, market_type="game_total"),
        selection=OddsSelection("Over"),
    )
    with pytest.raises(ValueError, match="requires a quote line"):
        _candidate(
            quote=quote, taxonomy=taxonomy,
            probability=_probability(target_statistic=taxonomy.statistic.value),
            participant_identity=ParticipantIdentity(None, "game_scope", IdentityStatus.NOT_APPLICABLE),
        )


def _team_candidate(
    selection: str, participant: ParticipantIdentity, market_type: str = "moneyline",
) -> MarketCandidate:
    taxonomy = resolve_market_taxonomy("NBA", market_type)
    line = 110.5 if market_type == "team_total" else None
    quote = _quote(
        market_identity=OddsMarketIdentity(
            sport="NBA", league="NBA", event_id="fixture-boston-at-new-york",
            event_date=date(2026, 9, 19), home_team="New York", away_team="Boston",
            market_type=market_type,
        ),
        selection=OddsSelection(selection, line=line),
    )
    direction = ThresholdDirection.NOT_APPLICABLE
    if line is not None:
        direction = (ThresholdDirection.LESS_THAN if selection.casefold() == "under"
                     else ThresholdDirection.GREATER_THAN)
    return _candidate(
        quote=quote, taxonomy=taxonomy, participant_identity=participant,
        probability=_probability(
            target_statistic=taxonomy.statistic.value, threshold=line, direction=direction,
        ),
    )


@pytest.mark.parametrize("selection,team", [
    ("Home", "New York"), ("Away", "Boston"),
    ("New York", "New York"), ("Boston", "Boston"),
    (" HOME ", "  new   YORK  "), (" away ", " BOSTON "),
])
def test_team_identity_binds_to_event_and_selection(selection: str, team: str) -> None:
    identity = ParticipantIdentity(team, "fixture_event_team")
    assert _team_candidate(selection, identity).participant_identity is identity


@pytest.mark.parametrize("selection,team,match", [
    ("Home", "Boston", "home/away selection"),
    ("Away", "New York", "home/away selection"),
    ("Home", "Los Angeles", "event team"),
    ("Away", "Los Angeles", "event team"),
    ("Los Angeles", "Los Angeles", "event team"),
    ("Home", "New", "event team"),
    ("New York", "Boston", "named quote selection"),
])
def test_wrong_team_identity_fails_closed(selection: str, team: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _team_candidate(selection, ParticipantIdentity(team, "fixture_event_team"))


def test_team_from_another_event_fails_closed() -> None:
    candidate = _team_candidate("Home", ParticipantIdentity("New York", "fixture_event_team"))
    other_quote = replace(candidate.quote, market_identity=replace(
        candidate.quote.market_identity, event_id="fixture-chicago-at-los-angeles",
        home_team="Los Angeles", away_team="Chicago",
    ))
    with pytest.raises(ValueError, match="event team"):
        replace(candidate, quote=other_quote, event_identity=EventIdentity(other_quote.event_id, "fixture"))


@pytest.mark.parametrize("side", ["Over", "Under"])
@pytest.mark.parametrize("team", ["New York", "Boston"])
def test_team_total_side_accepts_either_event_team(side: str, team: str) -> None:
    identity = ParticipantIdentity(team, "adapter_bound_team_total")
    assert _team_candidate(side, identity, "team_total").participant_identity is identity


@pytest.mark.parametrize("side", ["Over", "Under"])
def test_team_total_rejects_unrelated_team(side: str) -> None:
    with pytest.raises(ValueError, match="event team"):
        _team_candidate(side, ParticipantIdentity("Los Angeles", "fixture"), "team_total")


def test_resolved_team_can_bind_using_canonical_name() -> None:
    identity = ParticipantIdentity(
        "NYK", "adapter_resolved_team", IdentityStatus.RESOLVED,
        canonical_participant_id="fixture-nyk", canonical_participant_name="New York",
    )
    assert _team_candidate("Home", identity).participant_identity is identity
    with pytest.raises(ValueError, match="home/away selection"):
        _team_candidate("Away", identity)


def test_conflicting_event_team_names_fail_closed() -> None:
    identity = ParticipantIdentity(
        "Boston", "fixture_conflicting_mapping", IdentityStatus.RESOLVED,
        canonical_participant_id="fixture-nyk", canonical_participant_name="New York",
    )
    with pytest.raises(ValueError, match="exactly one event team"):
        _team_candidate("Home", identity)


@pytest.mark.parametrize("sport,market_type,statistic", [
    ("NBA", "player_points", "points"),
    ("MLB", "batter_home_runs", "home_runs"),
    ("MLB", "pitcher_strikeouts", "strikeouts"),
])
def test_player_side_binding_remains_adapter_responsibility(
    sport: str, market_type: str, statistic: str,
) -> None:
    quote = _quote(
        market_identity=replace(_quote().market_identity, sport=sport, league=sport, market_type=market_type),
        selection=OddsSelection("Over", line=0.5),
    )
    candidate = _candidate(
        quote=quote, taxonomy=resolve_market_taxonomy(sport, market_type),
        probability=_probability(target_statistic=statistic),
    )
    assert candidate.participant_identity.participant_name == "Example Batter"
    with pytest.raises(ValueError, match="named quote selection"):
        replace(candidate, quote=replace(quote, selection=OddsSelection("Other Player", line=0.5)))
