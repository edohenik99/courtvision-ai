from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from courtvision.core.probability import (
    CalibrationProvenance,
    DistributionType,
    ProbabilityOutput,
    ProbabilityUncertainty,
    ThresholdDirection,
)
from courtvision.core.reasoning import EvidenceAvailability


def _probability() -> ProbabilityOutput:
    return ProbabilityOutput(
        model_probability=0.1739123456789,
        target_statistic="home_runs",
        threshold=1,
        direction=ThresholdDirection.AT_LEAST,
        distribution_type=DistributionType.BERNOULLI_EVENT,
        model_id="fixture-hr-specialist",
        model_version="v1",
        feature_schema_version="hr-features-v1",
        calibration_provenance=CalibrationProvenance(
            EvidenceAvailability.UNAVAILABLE,
            reason="Fixture model supplies no calibration evidence.",
        ),
        generated_at=datetime(2026, 9, 19, 15, tzinfo=timezone.utc),
        evidence_cutoff=datetime(2026, 9, 19, 14, tzinfo=timezone.utc),
    )


def test_hr_probability_represents_one_or_more_without_a_count_distribution() -> None:
    result = _probability()
    assert result.model_probability == 0.1739123456789
    assert result.target_statistic == "home_runs"
    assert result.threshold == 1
    assert result.direction is ThresholdDirection.AT_LEAST
    assert result.distribution_type is DistributionType.BERNOULLI_EVENT
    assert not hasattr(result, "count_probabilities")
    assert result.uncertainty.availability is EvidenceAvailability.UNAVAILABLE
    assert result.uncertainty.lower_bound is None
    assert result.uncertainty.upper_bound is None


@pytest.mark.parametrize("probability", [0, 1, 0.0, 1.0, 0.125])
def test_probability_including_endpoints_is_preserved_exactly(probability: float) -> None:
    assert replace(_probability(), model_probability=probability).model_probability == probability


@pytest.mark.parametrize("probability", [-0.01, 1.01, 24.5, float("nan"), float("inf"), -float("inf"), True, "0.3", None])
def test_invalid_probability_fails_closed(probability: object) -> None:
    with pytest.raises(ValueError, match="model_probability"):
        replace(_probability(), model_probability=probability)


def test_threshold_and_cushion_cannot_substitute_for_probability() -> None:
    result = replace(_probability(), target_statistic="passing_yards", threshold=225, model_probability=0.7)
    assert result.threshold == 225
    assert result.model_probability == 0.7
    with pytest.raises(ValueError, match="model_probability"):
        replace(result, model_probability=249.5 - 225)
    assert not hasattr(result, "threshold_cushion")


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), True, "1"])
def test_invalid_threshold_fails_closed(threshold: object) -> None:
    with pytest.raises(ValueError, match="threshold"):
        replace(_probability(), threshold=threshold)


def test_threshold_direction_is_explicit_and_consistent() -> None:
    with pytest.raises(ValueError, match="requires a threshold"):
        replace(_probability(), threshold=None)
    with pytest.raises(ValueError, match="applicable direction"):
        replace(_probability(), direction=ThresholdDirection.NOT_APPLICABLE)
    result = replace(_probability(), threshold=None, direction=ThresholdDirection.NOT_APPLICABLE)
    assert result.threshold is None
    with pytest.raises(ValueError):
        replace(result, direction="unrecognized")
    with pytest.raises(ValueError):
        replace(result, distribution_type="invented_poisson")


@pytest.mark.parametrize("field_name", ["target_statistic", "model_id", "model_version", "feature_schema_version"])
@pytest.mark.parametrize("invalid", [None, "", "   ", 1])
def test_missing_or_invalid_provenance_is_rejected(field_name: str, invalid: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        replace(_probability(), **{field_name: invalid})


def test_provenance_arguments_are_required() -> None:
    with pytest.raises(TypeError):
        ProbabilityOutput(model_probability=0.2, target_statistic="home_runs", threshold=1)
    with pytest.raises(ValueError, match="calibration_provenance"):
        replace(_probability(), calibration_provenance=None)


@pytest.mark.parametrize("field_name", ["generated_at", "evidence_cutoff"])
@pytest.mark.parametrize("invalid", [None, "2026-09-19", datetime(2026, 9, 19)])
def test_provenance_requires_aware_timestamps(field_name: str, invalid: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        replace(_probability(), **{field_name: invalid})


def test_evidence_cannot_be_newer_than_generation() -> None:
    result = _probability()
    with pytest.raises(ValueError, match="later"):
        replace(result, evidence_cutoff=result.generated_at + timedelta(seconds=1))
    assert replace(result, evidence_cutoff=result.generated_at).evidence_cutoff == result.generated_at


def test_calibration_is_explicit_without_fabricated_metadata() -> None:
    calibration = _probability().calibration_provenance
    assert calibration.availability is EvidenceAvailability.UNAVAILABLE
    assert calibration.calibration_id is None
    assert calibration.method is None
    with pytest.raises(ValueError, match="reason"):
        CalibrationProvenance(EvidenceAvailability.UNAVAILABLE)
    with pytest.raises(ValueError, match="metadata"):
        CalibrationProvenance(EvidenceAvailability.UNAVAILABLE, calibration_id="fake", reason="Missing")
    with pytest.raises(ValueError, match="method"):
        CalibrationProvenance(EvidenceAvailability.AVAILABLE, calibration_id="cal-1")
    supplied = CalibrationProvenance(EvidenceAvailability.AVAILABLE, "cal-1", "isotonic")
    assert replace(_probability(), calibration_provenance=supplied).calibration_provenance is supplied


def test_uncertainty_is_used_only_when_explicitly_supplied() -> None:
    uncertainty = ProbabilityUncertainty(
        EvidenceAvailability.AVAILABLE,
        lower_bound=0.1,
        upper_bound=0.25,
        method="fixture-bootstrap",
        confidence_level=0.95,
    )
    assert replace(_probability(), uncertainty=uncertainty).uncertainty is uncertainty
    with pytest.raises(ValueError, match="contain"):
        replace(_probability(), model_probability=0.4, uncertainty=uncertainty)
    with pytest.raises(ValueError, match="reason"):
        ProbabilityUncertainty(EvidenceAvailability.UNAVAILABLE)
    with pytest.raises(ValueError, match="interval values"):
        ProbabilityUncertainty(EvidenceAvailability.UNAVAILABLE, lower_bound=0.0, reason="Missing")
    with pytest.raises(ValueError, match="exceed"):
        ProbabilityUncertainty(EvidenceAvailability.AVAILABLE, 0.3, 0.2, "fixture-bootstrap")
    with pytest.raises(ValueError, match="method"):
        ProbabilityUncertainty(EvidenceAvailability.AVAILABLE, 0.1, 0.2)
    with pytest.raises(ValueError, match="confidence_level"):
        ProbabilityUncertainty(EvidenceAvailability.AVAILABLE, 0.1, 0.2, "fixture-bootstrap", 0.0)


@pytest.mark.parametrize("field_name,value", [
    ("research_only", False), ("research_only", 1),
    ("eligible_for_betting", True), ("eligible_for_betting", 0),
    ("eligible_for_official_pick", True), ("approval_status", "approved"),
])
def test_research_boundary_cannot_be_widened(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match="research-only"):
        replace(_probability(), **{field_name: value})


def test_probability_and_nested_provenance_are_immutable() -> None:
    result = _probability()
    assert result.research_only is True
    assert result.eligible_for_betting is False
    assert result.eligible_for_official_pick is False
    assert result.approval_status == "not_approved"
    with pytest.raises(FrozenInstanceError):
        result.model_probability = 0.9
    with pytest.raises(FrozenInstanceError):
        result.calibration_provenance.reason = "replaced"
    with pytest.raises(FrozenInstanceError):
        result.uncertainty.lower_bound = 0.0
