from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone

import pytest

from courtvision.core.research_artifact import (
    ResearchArtifact,
    ResearchArtifactMetadata,
    ResearchArtifactRow,
    ResearchArtifactValidationError,
    validate_artifact,
    write_artifact_csv,
    write_artifact_json,
)
from courtvision.sports.mlb.hr_report import (
    build_hr_report,
    hr_assessments_to_research_artifact,
)


RUN_DATE = date(2026, 6, 19)
GENERATED_AT = datetime(2026, 6, 19, 16, 30, tzinfo=timezone.utc)


def _metadata(**overrides: object) -> ResearchArtifactMetadata:
    values: dict[str, object] = {
        "artifact_id": "mlb-hr-research-2026-06-19",
        "sport": "MLB",
        "league": "MLB",
        "market_type": "batter_home_runs",
        "mode": "research",
        "artifact_type": "watchlist",
        "run_date": RUN_DATE,
        "generated_at": GENERATED_AT,
        "provider_names": ("sample",),
        "source_types": ("sample",),
    }
    values.update(overrides)
    return ResearchArtifactMetadata(**values)  # type: ignore[arg-type]


def _row(**overrides: object) -> ResearchArtifactRow:
    values: dict[str, object] = {
        "row_id": "mlb-hr-2026-06-19-1",
        "sport": "MLB",
        "league": "MLB",
        "player_name": "Sample Player",
        "team": "TOR",
        "opponent": "NYY",
        "event_date": RUN_DATE,
        "market_type": "batter_home_runs",
        "research_score": 72,
        "status": "Candidate",
        "data_quality": "Sample data",
        "reasons": ("Research reason",),
        "warnings": ("Not approved",),
        "source_refs": ("sample",),
        "mode": "research",
    }
    values.update(overrides)
    return ResearchArtifactRow(**values)  # type: ignore[arg-type]


def _artifact(
    *,
    metadata: ResearchArtifactMetadata | None = None,
    row: ResearchArtifactRow | None = None,
) -> ResearchArtifact:
    return ResearchArtifact(metadata=metadata or _metadata(), rows=(row or _row(),))


@pytest.mark.parametrize("mode", ["research", "sample"])
def test_research_and_sample_artifacts_default_to_not_approved(mode: str) -> None:
    artifact = _artifact(
        metadata=_metadata(mode=mode),
        row=_row(mode=mode),
    )

    assert artifact.metadata.approval_status == "not_approved"
    assert artifact.metadata.eligible_for_betting is False
    assert artifact.metadata.kelly_eligible is False
    assert artifact.rows[0].approval_status == "not_approved"
    assert artifact.rows[0].eligible_for_betting is False
    assert artifact.rows[0].kelly_eligible is False
    assert validate_artifact(artifact).is_valid


def test_research_artifact_is_immutable() -> None:
    artifact = _artifact()

    with pytest.raises(FrozenInstanceError):
        artifact.metadata.approval_status = "approved"  # type: ignore[misc]


def test_research_and_sample_artifacts_cannot_serialize_as_betting_eligible() -> None:
    for mode in ("research", "sample"):
        unsafe = _artifact(
            metadata=_metadata(mode=mode, eligible_for_betting=True),
            row=_row(mode=mode, eligible_for_betting=True),
        )

        result = validate_artifact(unsafe)
        assert not result.is_valid
        with pytest.raises(ResearchArtifactValidationError, match="must be false"):
            unsafe.to_dict()


def test_safety_flags_require_literal_false_not_merely_falsy() -> None:
    unsafe = _artifact(metadata=_metadata(eligible_for_betting=0))

    result = validate_artifact(unsafe)

    assert not result.is_valid
    with pytest.raises(ResearchArtifactValidationError, match="must be false"):
        unsafe.to_json()


def test_row_level_flags_cannot_override_artifact_safety() -> None:
    unsafe = _artifact(row=_row(eligible_for_betting=True, kelly_eligible=True))

    result = validate_artifact(unsafe)

    assert not result.is_valid
    assert any("conflicts with artifact metadata" in error for error in result.errors)
    with pytest.raises(ResearchArtifactValidationError):
        unsafe.to_json()


def test_missing_required_metadata_fails_validation() -> None:
    result = validate_artifact(_artifact(metadata=_metadata(artifact_id="")))

    assert not result.is_valid
    assert "metadata.artifact_id is required" in result.errors


@pytest.mark.parametrize(
    ("field_name", "value", "expected"),
    [
        ("mode", "production", "unsupported artifact mode"),
        ("artifact_type", "picks", "unsupported artifact type"),
    ],
)
def test_unsupported_contract_values_fail_validation(
    field_name: str, value: str, expected: str
) -> None:
    metadata = replace(_metadata(), **{field_name: value})
    row = _row(mode=value) if field_name == "mode" else _row()

    result = validate_artifact(_artifact(metadata=metadata, row=row))

    assert not result.is_valid
    assert any(expected in error for error in result.errors)


def test_json_serialization_is_deterministic(tmp_path) -> None:
    artifact = _artifact()

    first = artifact.to_json()
    second = artifact.to_json()
    output_path = write_artifact_json(artifact, tmp_path / "artifact.json")

    assert first == second
    assert output_path.read_text(encoding="utf-8") == f"{first}\n"
    assert first.index('"metadata"') < first.index('"rows"')


def test_csv_export_omits_production_recommendation_and_sizing_fields(tmp_path) -> None:
    artifact = _artifact()

    rows = artifact.to_csv_rows()
    output_path = write_artifact_csv(artifact, tmp_path / "artifact.csv")

    forbidden = {
        "stake",
        "stake_amount",
        "units",
        "unit_size",
        "recommendation",
        "bet_recommendation",
    }
    assert forbidden.isdisjoint(rows[0])
    assert forbidden.isdisjoint(output_path.read_text(encoding="utf-8").splitlines()[0].split(","))


def test_mlb_hr_assessment_maps_to_default_deny_research_artifact() -> None:
    assessments = build_hr_report(RUN_DATE, provider="sample")

    artifact = hr_assessments_to_research_artifact(
        RUN_DATE,
        assessments[:1],
        generated_at=GENERATED_AT,
    )
    payload = artifact.to_dict()

    assert validate_artifact(artifact).is_valid
    assert artifact.metadata.sport == "MLB"
    assert artifact.metadata.artifact_type == "watchlist"
    assert artifact.metadata.approval_status == "not_approved"
    assert artifact.metadata.eligible_for_betting is False
    assert artifact.rows[0].player_name == assessments[0].player
    assert artifact.rows[0].research_score == assessments[0].research_score
    assert artifact.rows[0].approval_status == "not_approved"
    assert artifact.rows[0].eligible_for_betting is False
    assert payload["metadata"]["kelly_eligible"] is False  # type: ignore[index]
