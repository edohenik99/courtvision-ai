from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.training.hr_dataset_builder import (
    build_fixture_hr_batter_game_dataset,
)
from courtvision.sports.mlb.training.hr_dataset_readiness import (
    MLBHRDatasetReadinessError,
    MLBHRDatasetReadinessSeverity,
    build_hr_dataset_readiness_report,
    readiness_report_to_dict,
    readiness_report_to_json,
    readiness_report_to_txt,
    write_readiness_report_json,
    write_readiness_report_txt,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mlb"
GENERATED_AT = datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def fixture_result():
    return build_fixture_hr_batter_game_dataset(
        FIXTURE_DIR,
        generated_at=GENERATED_AT,
    )


def _mapping(row, **overrides: object) -> dict[str, object]:
    payload = {item.name: getattr(row, item.name) for item in fields(row)}
    payload.update(overrides)
    return payload


def _report(rows, fixture_result, **kwargs):
    return build_hr_dataset_readiness_report(
        rows,
        metadata=fixture_result.metadata,
        **kwargs,
    )


def test_fixture_report_is_immutable_default_deny_and_only_ready_for_larger_build(
    fixture_result,
) -> None:
    report = _report(fixture_result.rows, fixture_result)

    assert report.dataset_row_count == 4
    assert report.label_available_count == 4
    assert report.hr_positive_count == 2
    assert report.hr_negative_count == 2
    assert report.game_completed_count == 2
    assert report.training_eligible_count == 2
    assert report.backtest_eligible_count == 2
    assert report.readiness_status == "READY_FOR_LARGER_HISTORICAL_BUILD"
    assert report.blocking_issue_count == 0
    assert report.warning_issue_count > 0
    assert report.approval_status == "not_approved"
    assert report.eligible_for_betting is False
    assert report.kelly_eligible is False
    with pytest.raises(FrozenInstanceError):
        report.readiness_score = 100  # type: ignore[misc]


def test_context_and_odds_coverage_counts_are_exact(fixture_result) -> None:
    rows = tuple(
        replace(
            row,
            sportsbook="fixture-book" if index < 2 else None,
            odds_provider="local-fixture" if index < 2 else None,
            american_odds=250 if index < 2 else None,
            decimal_odds=3.5 if index < 2 else None,
            odds_collected_at=(
                "2025-04-01T12:00:00+00:00" if index < 2 else None
            ),
            odds_is_fresh_for_pregame=True if index < 2 else None,
        )
        for index, row in enumerate(fixture_result.rows)
    )
    report = _report(
        rows,
        fixture_result,
        pairing_summary={"unmatched_odds_rows": 1},
    )

    assert report.weather_attached_count == 2
    assert report.ballpark_attached_count == 2
    assert report.full_context_count == 2
    assert report.odds_attached_count == 2
    assert report.full_context_plus_odds_count == 2
    assert report.missing_odds_count == 2
    assert report.unmatched_odds_count == 1
    assert report.odds_coverage_rate == 0.5
    assert report.full_context_rate == 0.5
    assert report.full_context_plus_odds_rate == 0.5
    odds_metric = next(
        metric for metric in report.metrics if metric.metric_id == "odds_coverage"
    )
    assert odds_metric.rate == 0.5


def test_leakage_errors_and_duplicate_ids_block_readiness(fixture_result) -> None:
    first = fixture_result.rows[0]
    unsafe = replace(first, feature_as_of=first.event_start_time)
    leakage_report = _report((unsafe,), fixture_result)
    duplicate_report = _report((first, first), fixture_result)

    assert leakage_report.leakage_error_count > 0
    assert leakage_report.readiness_status == "NOT_READY"
    assert duplicate_report.duplicate_row_id_count == 1
    assert duplicate_report.duplicate_player_game_count == 1
    assert duplicate_report.readiness_status == "NOT_READY"


@pytest.mark.parametrize(
    ("overrides", "issue_id"),
    [
        ({"label_available": False}, "label.missing"),
        ({"row_id": ""}, "identity.row_id_missing"),
        ({"game_id": ""}, "identity.game_id_missing"),
        ({"player_id": None}, "identity.player_id_missing"),
        ({"game_date": None}, "identity.game_date_missing"),
        ({"schema_version": ""}, "identity.schema_version_missing"),
    ],
)
def test_missing_labels_and_identity_fields_block_readiness(
    fixture_result, overrides: dict[str, object], issue_id: str
) -> None:
    row = _mapping(fixture_result.rows[0], **overrides)
    report = _report((row,), fixture_result)

    assert report.readiness_status == "NOT_READY"
    assert any(
        issue.issue_id == issue_id
        and issue.severity is MLBHRDatasetReadinessSeverity.BLOCKING
        for issue in report.issues
    )


def test_missing_context_and_odds_warn_without_fabrication(fixture_result) -> None:
    report = _report((fixture_result.rows[2],), fixture_result)

    assert report.missing_weather_count == 1
    assert report.missing_ballpark_count == 1
    assert report.missing_odds_count == 1
    assert report.weather_attached_count == 0
    assert report.ballpark_attached_count == 0
    assert report.odds_attached_count == 0
    assert {issue.issue_id for issue in report.issues} >= {
        "context.weather_missing",
        "context.ballpark_missing",
        "odds.missing",
    }


def test_provenance_checksums_and_row_counts_are_reported(fixture_result) -> None:
    source_manifest = {
        "sources": [
            {"source_name": "one", "sha256": "a" * 64, "parsed_row_count": 3},
            {"source_name": "two", "parsed_row_count": 2},
        ]
    }
    report = _report(
        fixture_result.rows,
        fixture_result,
        source_manifest=source_manifest,
    )

    assert report.source_manifest_count == 2
    assert report.source_checksum_count == 1
    assert report.source_row_count_count == 2
    assert any(issue.issue_id == "provenance.checksum_missing" for issue in report.issues)


def test_json_and_txt_serialization_and_opt_in_writers(
    fixture_result, tmp_path
) -> None:
    report = _report(fixture_result.rows, fixture_result)
    json_path = tmp_path / "readiness.json"
    txt_path = tmp_path / "readiness.txt"

    assert json.loads(readiness_report_to_json(report)) == readiness_report_to_dict(
        report
    )
    summary = readiness_report_to_txt(report)
    assert "readiness_status = READY_FOR_LARGER_HISTORICAL_BUILD" in summary
    assert "approval_status = not_approved" in summary
    assert write_readiness_report_json(report, json_path) == json_path.resolve()
    assert write_readiness_report_txt(report, txt_path) == txt_path.resolve()
    assert json.loads(json_path.read_text(encoding="utf-8"))["eligible_for_betting"] is False
    assert "historical research only" in txt_path.read_text(encoding="utf-8")

    with pytest.raises(MLBHRDatasetReadinessError):
        write_readiness_report_json(report, json_path)
    with pytest.raises(MLBHRDatasetReadinessError):
        write_readiness_report_txt(report, txt_path)
