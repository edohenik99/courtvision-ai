from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.training.hr_dataset_builder import (
    build_fixture_hr_batter_game_dataset,
)
from courtvision.sports.mlb.training.hr_dataset_schema import MLBHRBatterGameRow
from courtvision.sports.mlb.training.hr_leakage_audit import (
    MLBHRLeakageAuditError,
    MLBHRLeakageAuditSeverity,
    audit_hr_batter_game_row,
    audit_hr_batter_game_rows,
    audit_report_to_dict,
    audit_report_to_json,
    write_audit_report_json,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mlb"
CHECKED_AT = datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def fixture_rows() -> tuple[MLBHRBatterGameRow, ...]:
    return build_fixture_hr_batter_game_dataset(
        FIXTURE_DIR,
        generated_at=CHECKED_AT,
    ).rows


@pytest.fixture(scope="module")
def complete_row(fixture_rows) -> MLBHRBatterGameRow:
    return next(row for row in fixture_rows if row.eligible_for_training)


def _mapping(row: MLBHRBatterGameRow, **overrides: object) -> dict[str, object]:
    values = {item.name: getattr(row, item.name) for item in fields(row)}
    values.update(overrides)
    return values


def _issue_ids(row: MLBHRBatterGameRow | dict[str, object]) -> set[str]:
    return {issue.issue_id for issue in audit_hr_batter_game_row(row)}


def test_complete_fixture_rows_pass_critical_checks_and_report_is_default_deny(
    fixture_rows,
) -> None:
    report = audit_hr_batter_game_rows(fixture_rows, checked_at=CHECKED_AT)

    assert report.row_count == 4
    assert report.error_count == 0
    assert report.passed is True
    assert report.eligible_for_betting is False
    assert report.kelly_eligible is False
    assert report.approval_status == "not_approved"
    assert all(issue.severity is not MLBHRLeakageAuditSeverity.ERROR for issue in report.issues)


def test_incomplete_fixture_rows_emit_visible_warnings(fixture_rows) -> None:
    incomplete = tuple(row for row in fixture_rows if not row.eligible_for_training)
    report = audit_hr_batter_game_rows(incomplete, checked_at=CHECKED_AT)

    assert report.warning_count > 0
    assert any(issue.category == "data_quality" for issue in report.issues)
    assert report.passed is True


def test_audit_structures_are_immutable_and_serialization_is_deterministic(
    complete_row,
) -> None:
    first = audit_hr_batter_game_rows((complete_row,), checked_at=CHECKED_AT)
    second = audit_hr_batter_game_rows((complete_row,), checked_at=CHECKED_AT)

    with pytest.raises(FrozenInstanceError):
        first.passed = False  # type: ignore[misc]
    assert audit_report_to_json(first) == audit_report_to_json(second)
    assert json.loads(audit_report_to_json(first)) == audit_report_to_dict(first)


def test_feature_timestamp_after_game_start_is_error(complete_row) -> None:
    row = replace(complete_row, feature_as_of="2025-04-02T00:00:00+00:00")

    issues = audit_hr_batter_game_row(row)

    assert any(
        issue.issue_id == "feature_timestamp.not_before_event_start"
        and issue.severity is MLBHRLeakageAuditSeverity.ERROR
        for issue in issues
    )
    assert audit_hr_batter_game_rows((row,), checked_at=CHECKED_AT).passed is False


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("game_id", ""),
        ("player_id", None),
        ("game_date", None),
        ("row_id", ""),
        ("schema_version", ""),
    ],
)
def test_missing_required_fields_are_errors(
    complete_row, field_name: str, value: object
) -> None:
    row = _mapping(complete_row, **{field_name: value})

    issues = audit_hr_batter_game_row(row)

    assert any(
        issue.issue_id == f"required_field.{field_name}_missing"
        and issue.severity is MLBHRLeakageAuditSeverity.ERROR
        for issue in issues
    )


@pytest.mark.parametrize(
    ("field_name", "value", "issue_id"),
    [
        (
            "eligible_for_betting",
            True,
            "approval_safety.betting_eligibility_claimed",
        ),
        ("kelly_eligible", True, "approval_safety.kelly_eligibility_claimed"),
        (
            "approval_status",
            "approved",
            "approval_safety.production_approval_claimed",
        ),
    ],
)
def test_unsafe_approval_claims_are_errors(
    complete_row, field_name: str, value: object, issue_id: str
) -> None:
    row = _mapping(complete_row, **{field_name: value})

    assert issue_id in _issue_ids(row)


@pytest.mark.parametrize("game_status", ["postponed", "suspended", "unknown"])
def test_non_completed_game_status_cannot_be_training_eligible(
    complete_row, game_status: str
) -> None:
    row = _mapping(
        complete_row,
        game_status=game_status,
        game_completed=False if game_status != "unknown" else None,
        eligible_for_training=True,
    )

    issue_ids = _issue_ids(row)

    assert "outcome_integrity.incomplete_game_training_eligible" in issue_ids
    assert "outcome_integrity.game_status_training_eligible" in issue_ids


def test_completed_training_row_requires_available_complete_label(complete_row) -> None:
    row = replace(
        complete_row,
        label_available=False,
        label_source=None,
        label_as_of=None,
    )

    issue_ids = _issue_ids(row)

    assert "outcome_integrity.label_unavailable_for_eligible_row" in issue_ids
    assert "outcome_integrity.eligible_label_fields_incomplete" in issue_ids


def test_label_field_in_feature_namespace_is_error(complete_row) -> None:
    row = _mapping(complete_row, features={"hit_hr_today": True})

    assert "label_separation.label_in_feature_namespace" in _issue_ids(row)


def test_same_game_statcast_output_in_feature_namespace_is_error(complete_row) -> None:
    row = _mapping(
        complete_row,
        pregame_features={"launch_speed": 109.2, "launch_angle": 28.0},
    )

    assert "label_separation.same_game_statcast_in_features" in _issue_ids(row)


def test_same_game_statcast_cannot_populate_fixture_feature(complete_row) -> None:
    row = replace(
        complete_row,
        hitter_avg_exit_velocity=109.2,
        label_source="retrosheet+statcast",
    )

    assert (
        "label_separation.same_game_statcast_in_fixture_feature" in _issue_ids(row)
    )


def test_missing_source_provenance_emits_warning(complete_row) -> None:
    row = replace(
        complete_row,
        source_manifest_ids=(),
        statcast_manifest_id=None,
        retrosheet_manifest_id=None,
        weather_manifest_id=None,
        ballpark_manifest_id=None,
        odds_manifest_id=None,
    )

    issues = audit_hr_batter_game_row(row)

    assert any(
        issue.issue_id == "provenance.source_manifest_missing"
        and issue.severity is MLBHRLeakageAuditSeverity.WARNING
        for issue in issues
    )


def test_missing_optional_weather_emits_warning(complete_row) -> None:
    row = replace(
        complete_row,
        weather_temperature=None,
        weather_wind_speed=None,
        weather_wind_direction=None,
        weather_wind_out_to_field=None,
        weather_humidity=None,
        roof_status=None,
        weather_source_type=None,
    )

    assert "data_quality.weather_context_missing" in _issue_ids(row)


def test_missing_optional_ballpark_emits_warning(complete_row) -> None:
    row = replace(
        complete_row,
        park_factor_hr=None,
        park_factor_lhb=None,
        park_factor_rhb=None,
        altitude=None,
        ballpark_source_type=None,
    )

    assert "data_quality.ballpark_context_missing" in _issue_ids(row)


def test_missing_feature_cutoff_is_error_only_for_training_eligible_row(
    complete_row,
) -> None:
    eligible = replace(complete_row, feature_as_of=None)
    ineligible = replace(eligible, eligible_for_training=False)

    eligible_issue = next(
        issue
        for issue in audit_hr_batter_game_row(eligible)
        if issue.issue_id == "feature_timestamp.feature_as_of_missing"
    )
    ineligible_issue = next(
        issue
        for issue in audit_hr_batter_game_row(ineligible)
        if issue.issue_id == "feature_timestamp.feature_as_of_missing"
    )

    assert eligible_issue.severity is MLBHRLeakageAuditSeverity.ERROR
    assert ineligible_issue.severity is MLBHRLeakageAuditSeverity.WARNING


def test_warning_only_report_policy_passes_but_preserves_warnings(complete_row) -> None:
    row = replace(
        complete_row,
        eligible_for_training=False,
        feature_as_of=None,
    )

    report = audit_hr_batter_game_rows((row,), checked_at=CHECKED_AT)

    assert report.error_count == 0
    assert report.warning_count > 0
    assert report.passed is True


def test_optional_json_writer_uses_tmp_and_refuses_overwrite(
    complete_row, tmp_path: Path
) -> None:
    report = audit_hr_batter_game_rows((complete_row,), checked_at=CHECKED_AT)
    destination = tmp_path / "audit.json"

    assert write_audit_report_json(report, destination) == destination
    assert json.loads(destination.read_text(encoding="utf-8"))["passed"] is True
    with pytest.raises(MLBHRLeakageAuditError, match="already exists"):
        write_audit_report_json(report, destination)
