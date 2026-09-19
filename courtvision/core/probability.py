"""Immutable specialist event probabilities with explicit research provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math

from courtvision.core.reasoning import EvidenceAvailability


class ThresholdDirection(str, Enum):
    AT_LEAST = "at_least"
    GREATER_THAN = "greater_than"
    AT_MOST = "at_most"
    LESS_THAN = "less_than"
    EQUAL = "equal"
    NOT_APPLICABLE = "not_applicable"


class DistributionType(str, Enum):
    # A Bernoulli event probability does not assert a full count distribution.
    BERNOULLI_EVENT = "bernoulli_event"


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


def _probability(value: object, name: str) -> None:
    _finite_number(value, name)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be within [0, 1]")


def _aware_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


@dataclass(frozen=True, slots=True)
class CalibrationProvenance:
    availability: EvidenceAvailability
    calibration_id: str | None = None
    method: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        availability = EvidenceAvailability(self.availability)
        object.__setattr__(self, "availability", availability)
        if availability is EvidenceAvailability.AVAILABLE:
            for name in ("calibration_id", "method"):
                object.__setattr__(self, name, _required_text(getattr(self, name), name))
        else:
            if self.calibration_id is not None or self.method is not None:
                raise ValueError("Unavailable calibration cannot carry calibration metadata")
            _required_text(self.reason, "reason")
        if self.reason is not None:
            object.__setattr__(self, "reason", _required_text(self.reason, "reason"))


@dataclass(frozen=True, slots=True)
class ProbabilityUncertainty:
    """A supplied probability interval, or an explicit absence of uncertainty."""

    availability: EvidenceAvailability
    lower_bound: float | None = None
    upper_bound: float | None = None
    method: str | None = None
    confidence_level: float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        availability = EvidenceAvailability(self.availability)
        object.__setattr__(self, "availability", availability)
        if availability is EvidenceAvailability.AVAILABLE:
            _probability(self.lower_bound, "lower_bound")
            _probability(self.upper_bound, "upper_bound")
            if self.lower_bound > self.upper_bound:
                raise ValueError("lower_bound cannot exceed upper_bound")
            object.__setattr__(self, "method", _required_text(self.method, "method"))
            if self.confidence_level is not None:
                _probability(self.confidence_level, "confidence_level")
                if self.confidence_level == 0:
                    raise ValueError("confidence_level must be greater than zero")
        else:
            if any(value is not None for value in (
                self.lower_bound, self.upper_bound, self.method, self.confidence_level
            )):
                raise ValueError("Unavailable uncertainty cannot carry interval values")
            _required_text(self.reason, "reason")
        if self.reason is not None:
            object.__setattr__(self, "reason", _required_text(self.reason, "reason"))


def _unavailable_uncertainty() -> ProbabilityUncertainty:
    return ProbabilityUncertainty(
        EvidenceAvailability.UNAVAILABLE,
        reason="The specialist model supplied no uncertainty estimate.",
    )


@dataclass(frozen=True, slots=True)
class ProbabilityOutput:
    """Probability of the stated event, never a threshold cushion or rank score.

    ``home_runs``, threshold ``1``, and ``AT_LEAST`` express P(home_runs >= 1).
    BERNOULLI_EVENT describes only that event's binary outcome and does not invent
    the probabilities of other home-run counts. This phase is research-only.
    """

    model_probability: float
    target_statistic: str
    threshold: float | None
    direction: ThresholdDirection
    distribution_type: DistributionType
    model_id: str
    model_version: str
    feature_schema_version: str
    calibration_provenance: CalibrationProvenance
    generated_at: datetime
    evidence_cutoff: datetime
    uncertainty: ProbabilityUncertainty = field(default_factory=_unavailable_uncertainty)
    research_only: bool = True
    eligible_for_betting: bool = False
    eligible_for_official_pick: bool = False
    approval_status: str = "not_approved"

    def __post_init__(self) -> None:
        _probability(self.model_probability, "model_probability")
        for name in ("target_statistic", "model_id", "model_version", "feature_schema_version"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        direction = ThresholdDirection(self.direction)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "distribution_type", DistributionType(self.distribution_type))
        if self.threshold is None:
            if direction is not ThresholdDirection.NOT_APPLICABLE:
                raise ValueError("A threshold direction requires a threshold")
        else:
            _finite_number(self.threshold, "threshold")
            if direction is ThresholdDirection.NOT_APPLICABLE:
                raise ValueError("A threshold requires an applicable direction")
        if not isinstance(self.calibration_provenance, CalibrationProvenance):
            raise ValueError("calibration_provenance must be CalibrationProvenance")
        if not isinstance(self.uncertainty, ProbabilityUncertainty):
            raise ValueError("uncertainty must be ProbabilityUncertainty")
        _aware_datetime(self.generated_at, "generated_at")
        _aware_datetime(self.evidence_cutoff, "evidence_cutoff")
        if self.evidence_cutoff > self.generated_at:
            raise ValueError("evidence_cutoff cannot be later than generated_at")
        if self.uncertainty.availability is EvidenceAvailability.AVAILABLE:
            if not self.uncertainty.lower_bound <= self.model_probability <= self.uncertainty.upper_bound:
                raise ValueError("The uncertainty interval must contain model_probability")
        if (
            self.research_only is not True
            or self.eligible_for_betting is not False
            or self.eligible_for_official_pick is not False
            or self.approval_status != "not_approved"
        ):
            raise ValueError("ProbabilityOutput must remain unapproved research-only output")
