"""Inspectable research reasoning dimensions, with no aggregate scoring logic."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
import math


class EvidenceAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class EvidenceKind(str, Enum):
    FACTUAL_EVIDENCE = "factual_evidence"
    MODEL_OUTPUT = "model_output"
    DERIVED_REASONING_FEATURE = "derived_reasoning_feature"
    EVALUATIVE_CLASSIFICATION = "evaluative_classification"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _finite_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number")


@dataclass(frozen=True, slots=True)
class ReasoningDimension:
    """One independently attributed value or an explained absence of evidence."""

    availability: EvidenceAvailability
    kind: EvidenceKind
    value: float | str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        availability = EvidenceAvailability(self.availability)
        object.__setattr__(self, "availability", availability)
        object.__setattr__(self, "kind", EvidenceKind(self.kind))
        if availability is EvidenceAvailability.AVAILABLE:
            if isinstance(self.value, str):
                object.__setattr__(self, "value", _required_text(self.value, "value"))
            else:
                _finite_number(self.value, "value")
        else:
            if self.value is not None:
                raise ValueError("Unavailable or inapplicable evidence cannot carry a value")
            _required_text(self.reason, "reason")
        if self.reason is not None:
            object.__setattr__(self, "reason", _required_text(self.reason, "reason"))

    @classmethod
    def available(
        cls, value: float | str, kind: EvidenceKind, *, reason: str | None = None
    ) -> ReasoningDimension:
        return cls(EvidenceAvailability.AVAILABLE, kind, value, reason)

    @classmethod
    def unavailable(cls, kind: EvidenceKind, reason: str) -> ReasoningDimension:
        return cls(EvidenceAvailability.UNAVAILABLE, kind, reason=reason)

    @classmethod
    def not_applicable(cls, kind: EvidenceKind, reason: str) -> ReasoningDimension:
        return cls(EvidenceAvailability.NOT_APPLICABLE, kind, reason=reason)


class RelationshipType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    SHARED_GAME_SCRIPT = "shared_game_script"
    SAME_PLAYER_REDUNDANCY = "same_player_redundancy"
    SAME_STAT_REDUNDANCY = "same_stat_redundancy"
    PORTFOLIO_CONCENTRATION = "portfolio_concentration"


@dataclass(frozen=True, slots=True)
class RelationshipFlag:
    """A supplied relationship reference; no coefficient or scoring is inferred."""

    relationship: RelationshipType
    related_candidate_ids: tuple[str, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relationship", RelationshipType(self.relationship))
        if not isinstance(self.related_candidate_ids, (tuple, list)):
            raise ValueError("related_candidate_ids must be an ordered collection")
        identifiers = tuple(
            _required_text(value, "related_candidate_id")
            for value in self.related_candidate_ids
        )
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("related_candidate_ids must be unique")
        object.__setattr__(self, "related_candidate_ids", identifiers)
        if self.detail is not None:
            object.__setattr__(self, "detail", _required_text(self.detail, "detail"))


def _missing_fact() -> ReasoningDimension:
    return ReasoningDimension.unavailable(EvidenceKind.FACTUAL_EVIDENCE, "No evidence supplied.")


def _missing_model_output() -> ReasoningDimension:
    return ReasoningDimension.unavailable(EvidenceKind.MODEL_OUTPUT, "No model output supplied.")


def _missing_derived_feature() -> ReasoningDimension:
    return ReasoningDimension.unavailable(
        EvidenceKind.DERIVED_REASONING_FEATURE, "No derived feature supplied."
    )


def _missing_classification() -> ReasoningDimension:
    return ReasoningDimension.unavailable(
        EvidenceKind.EVALUATIVE_CLASSIFICATION, "No classification supplied."
    )


@dataclass(frozen=True, slots=True)
class ReasoningAssessment:
    """Independent dimensions that remain visible without an overall score.

    Empty relationship tuples mean no findings were supplied; they do not certify
    independence or the absence of correlation. This contract performs no scoring.
    """

    projection_confidence: ReasoningDimension = field(default_factory=_missing_model_output)
    threshold_cushion: ReasoningDimension = field(default_factory=_missing_derived_feature)
    role_certainty: ReasoningDimension = field(default_factory=_missing_classification)
    opportunity: ReasoningDimension = field(default_factory=_missing_fact)
    matchup: ReasoningDimension = field(default_factory=_missing_fact)
    environment: ReasoningDimension = field(default_factory=_missing_fact)
    volatility: ReasoningDimension = field(default_factory=_missing_model_output)
    market_movement: ReasoningDimension = field(default_factory=_missing_derived_feature)
    model_probability: ReasoningDimension = field(default_factory=_missing_model_output)
    market_implied_probability: ReasoningDimension = field(default_factory=_missing_derived_feature)
    edge: ReasoningDimension = field(default_factory=_missing_derived_feature)
    identity_quality: ReasoningDimension = field(default_factory=_missing_fact)
    evidence_quality: ReasoningDimension = field(default_factory=_missing_classification)
    correlation_flags: tuple[RelationshipFlag, ...] = ()
    redundancy_flags: tuple[RelationshipFlag, ...] = ()
    classification: ReasoningDimension | None = None

    def __post_init__(self) -> None:
        for descriptor in fields(self):
            name = descriptor.name
            value = getattr(self, name)
            if name in {"correlation_flags", "redundancy_flags"}:
                if not isinstance(value, (tuple, list)) or not all(
                    isinstance(flag, RelationshipFlag) for flag in value
                ):
                    raise ValueError(f"{name} must contain RelationshipFlag instances")
                object.__setattr__(self, name, tuple(value))
            elif name == "classification" and value is None:
                continue
            elif not isinstance(value, ReasoningDimension):
                raise ValueError(f"{name} must be a ReasoningDimension")
        for name in ("model_probability", "market_implied_probability"):
            dimension = getattr(self, name)
            if dimension.availability is EvidenceAvailability.AVAILABLE:
                _finite_number(dimension.value, name)
                if not 0 <= dimension.value <= 1:
                    raise ValueError(f"{name} must be within [0, 1]")
        required_kinds = (
            ("model_probability", EvidenceKind.MODEL_OUTPUT),
            ("market_implied_probability", EvidenceKind.DERIVED_REASONING_FEATURE),
            ("edge", EvidenceKind.DERIVED_REASONING_FEATURE),
            ("threshold_cushion", EvidenceKind.DERIVED_REASONING_FEATURE),
            ("classification", EvidenceKind.EVALUATIVE_CLASSIFICATION),
        )
        for name, kind in required_kinds:
            dimension = getattr(self, name)
            if dimension is not None and dimension.kind is not kind:
                raise ValueError(f"{name} requires evidence kind {kind.value}")
        for name in ("edge", "threshold_cushion"):
            dimension = getattr(self, name)
            if dimension.availability is EvidenceAvailability.AVAILABLE:
                _finite_number(dimension.value, name)
