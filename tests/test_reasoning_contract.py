from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from courtvision.core.reasoning import (
    EvidenceAvailability,
    EvidenceKind,
    ReasoningAssessment,
    ReasoningDimension,
    RelationshipFlag,
    RelationshipType,
)


def _derived(value: float) -> ReasoningDimension:
    return ReasoningDimension.available(value, EvidenceKind.DERIVED_REASONING_FEATURE)


def test_independent_dimensions_preserve_their_values_and_kinds() -> None:
    reasoning = ReasoningAssessment(
        projection_confidence=ReasoningDimension.available(0.6, EvidenceKind.MODEL_OUTPUT),
        threshold_cushion=_derived(24.5),
        opportunity=ReasoningDimension.available(32, EvidenceKind.FACTUAL_EVIDENCE),
        model_probability=ReasoningDimension.available(0.7, EvidenceKind.MODEL_OUTPUT),
        market_implied_probability=_derived(0.55),
        edge=_derived(0.15),
        classification=ReasoningDimension.available("research_watchlist", EvidenceKind.EVALUATIVE_CLASSIFICATION),
    )
    assert reasoning.model_probability.value == 0.7
    assert reasoning.market_implied_probability.value == 0.55
    assert reasoning.edge.value == 0.15
    assert reasoning.threshold_cushion.value == 24.5
    assert reasoning.opportunity.kind is EvidenceKind.FACTUAL_EVIDENCE
    assert reasoning.projection_confidence.kind is EvidenceKind.MODEL_OUTPUT
    assert reasoning.edge.kind is EvidenceKind.DERIVED_REASONING_FEATURE
    assert reasoning.classification.kind is EvidenceKind.EVALUATIVE_CLASSIFICATION
    # Changing one dimension does not recalculate or overwrite the others.
    changed = replace(reasoning, threshold_cushion=_derived(30))
    assert changed.model_probability is reasoning.model_probability
    assert changed.edge is reasoning.edge


def test_missing_dimensions_are_explicit_without_an_aggregate_score() -> None:
    reasoning = ReasoningAssessment()
    assert reasoning.classification is None
    assert not hasattr(reasoning, "overall_score")
    assert not hasattr(reasoning, "score")
    for descriptor in fields(reasoning):
        dimension = getattr(reasoning, descriptor.name)
        if isinstance(dimension, ReasoningDimension):
            assert dimension.availability is EvidenceAvailability.UNAVAILABLE
            assert dimension.value is None
            assert dimension.reason
    not_applicable = ReasoningDimension.not_applicable(
        EvidenceKind.DERIVED_REASONING_FEATURE, "This event has no numeric threshold."
    )
    assert replace(reasoning, threshold_cushion=not_applicable).threshold_cushion is not_applicable
    assert not_applicable.availability is EvidenceAvailability.NOT_APPLICABLE


@pytest.mark.parametrize("availability", [EvidenceAvailability.UNAVAILABLE, EvidenceAvailability.NOT_APPLICABLE])
def test_absent_evidence_cannot_carry_a_value_or_omit_its_reason(availability: EvidenceAvailability) -> None:
    with pytest.raises(ValueError, match="cannot carry"):
        ReasoningDimension(availability, EvidenceKind.FACTUAL_EVIDENCE, 0.0, "No evidence")
    with pytest.raises(ValueError, match="reason"):
        ReasoningDimension(availability, EvidenceKind.FACTUAL_EVIDENCE)


@pytest.mark.parametrize("invalid", [None, True, float("nan"), float("inf"), -float("inf"), "", [], {}])
def test_available_dimensions_require_finite_immutable_values(invalid: object) -> None:
    with pytest.raises(ValueError):
        ReasoningDimension.available(invalid, EvidenceKind.FACTUAL_EVIDENCE)


@pytest.mark.parametrize("name", ["model_probability", "market_implied_probability"])
@pytest.mark.parametrize("invalid", [-0.1, 1.1, "high"])
def test_reasoning_probability_dimensions_are_bounded(name: str, invalid: object) -> None:
    kind = EvidenceKind.MODEL_OUTPUT if name == "model_probability" else EvidenceKind.DERIVED_REASONING_FEATURE
    with pytest.raises(ValueError, match=name):
        ReasoningAssessment(**{name: ReasoningDimension.available(invalid, kind)})


@pytest.mark.parametrize("name", ["model_probability", "market_implied_probability", "edge", "threshold_cushion", "classification"])
def test_required_evidence_roles_cannot_be_mislabeled(name: str) -> None:
    with pytest.raises(ValueError, match="evidence kind"):
        ReasoningAssessment(**{name: ReasoningDimension.available(0.2, EvidenceKind.FACTUAL_EVIDENCE)})


def test_unavailable_edge_is_not_implicitly_computed_from_probabilities() -> None:
    reasoning = ReasoningAssessment(
        model_probability=ReasoningDimension.available(0.7, EvidenceKind.MODEL_OUTPUT),
        market_implied_probability=_derived(0.55),
    )
    assert reasoning.edge.availability is EvidenceAvailability.UNAVAILABLE
    assert reasoning.edge.value is None


def test_relationship_references_are_deeply_immutable_without_scoring() -> None:
    related = ["candidate-2"]
    flag = RelationshipFlag(RelationshipType.SHARED_GAME_SCRIPT, related, "Supplied research finding")
    flags = [flag]
    redundancy = [RelationshipFlag(RelationshipType.SAME_STAT_REDUNDANCY, ["candidate-3"])]
    reasoning = ReasoningAssessment(correlation_flags=flags, redundancy_flags=redundancy)
    related.append("candidate-4")
    flags.clear()
    redundancy.clear()
    assert reasoning.correlation_flags == (flag,)
    assert flag.related_candidate_ids == ("candidate-2",)
    assert len(reasoning.redundancy_flags) == 1
    assert not hasattr(flag, "correlation_coefficient")
    with pytest.raises(FrozenInstanceError):
        flag.detail = "changed"
    with pytest.raises(FrozenInstanceError):
        reasoning.edge = _derived(1)
    with pytest.raises(FrozenInstanceError):
        reasoning.edge.value = 1


def test_untyped_or_unknown_reasoning_data_fails_closed() -> None:
    with pytest.raises(ValueError, match="ReasoningDimension"):
        ReasoningAssessment(environment={"value": "sunny"})
    with pytest.raises(ValueError, match="RelationshipFlag"):
        ReasoningAssessment(correlation_flags=[{"relationship": "positive"}])
    with pytest.raises(ValueError):
        RelationshipFlag("unrecognized")
    with pytest.raises(ValueError, match="unique"):
        RelationshipFlag(RelationshipType.POSITIVE, ["candidate-2", "candidate-2"])
    with pytest.raises(ValueError, match="ordered"):
        RelationshipFlag(RelationshipType.POSITIVE, {"candidate-2"})
    with pytest.raises(ValueError):
        ReasoningDimension("unknown", EvidenceKind.FACTUAL_EVIDENCE)
    with pytest.raises(ValueError):
        ReasoningDimension.available("known", "unknown_kind")
