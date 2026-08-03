from __future__ import annotations

import csv
from pathlib import Path

import pytest

from courtvision.evaluation.model_records import Outcome
from courtvision.evaluation.model_sources import (
    DuplicateRecordConflictError,
    LEGACY_PICK_HISTORY_COLUMNS,
    SourceNotAllowedError,
    SourceSchemaError,
    SourceState,
    load_phase1_records,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {column: "" for column in LEGACY_PICK_HISTORY_COLUMNS}
    row.update(
        {
            "prediction_date": "2026-05-01",
            "run_timestamp": "2026-05-01T12:00:00+00:00",
            "player_name": "Sample Player",
            "player_id": "player-1",
            "team": "TOR",
            "opponent": "BOS",
            "game_id": "game-1",
            "market": "player_points",
            "selection": "over",
            "line": "20.5",
            "projection": "23.0",
            "edge": "2.5",
            "abs_edge": "2.5",
            "odds": "-110",
            "confidence": "0.75",
            "result_status": "hit",
            "actual_value": "24",
        }
    )
    row.update(overrides)
    return row


def _write_source(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEGACY_PICK_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_adapter_requires_exact_legacy_32_column_contract(tmp_path: Path) -> None:
    assert len(LEGACY_PICK_HISTORY_COLUMNS) == 32
    path = tmp_path / "pick_history.csv"
    _write_source(path, [_row()])

    result = load_phase1_records(path)

    assert result.state is SourceState.LOADED
    assert result.coverage.source_row_count == 1
    assert result.coverage.unique_record_count == 1


def test_missing_source_returns_state_without_creating_any_path(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist" / "pick_history.csv"

    result = load_phase1_records(path)

    assert result.state is SourceState.MISSING
    assert result.records == ()
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.parametrize("header_only", [False, True])
def test_empty_source_is_nonfatal(tmp_path: Path, header_only: bool) -> None:
    path = tmp_path / "pick_history.csv"
    if header_only:
        _write_source(path, [])
    else:
        path.write_text("", encoding="utf-8")

    result = load_phase1_records(path)

    assert result.state is SourceState.EMPTY
    assert result.records == ()


def test_malformed_schema_raises_clear_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "pick_history.csv"
    path.write_text("prediction_date,player_name\n2026-05-01,Player\n", encoding="utf-8")

    with pytest.raises(SourceSchemaError, match="exact 32-column"):
        load_phase1_records(path)


def test_identical_duplicate_ids_are_deduplicated_and_reported(tmp_path: Path) -> None:
    path = tmp_path / "pick_history.csv"
    _write_source(path, [_row(), _row()])

    result = load_phase1_records(path)

    assert len(result.records) == 1
    assert result.coverage.source_row_count == 2
    assert result.coverage.duplicate_identical_count == 1
    assert result.coverage.duplicate_status == "IDENTICAL_DUPLICATES_DEDUPED"
    assert result.records[0].source_row_number == 2


def test_conflicting_duplicate_ids_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "pick_history.csv"
    _write_source(path, [_row(), _row(result_status="miss", actual_value="10")])

    with pytest.raises(DuplicateRecordConflictError, match="rows 2 and 3"):
        load_phase1_records(path)


def test_outcomes_row_provenance_and_coverage_are_exact(tmp_path: Path) -> None:
    path = tmp_path / "pick_history.csv"
    values = [" HIT ", "Miss", "PUSH", "void", "pending", "cancelled"]
    rows = [
        _row(player_name=f"Player {index}", player_id=f"p-{index}", result_status=value)
        for index, value in enumerate(values)
    ]
    _write_source(path, rows)

    result = load_phase1_records(path)

    assert [record.outcome for record in result.records] == list(Outcome)
    assert [record.source_row_number for record in result.records] == list(range(2, 8))
    assert result.coverage.hit_rate_eligible_count == 2
    assert result.coverage.roi_eligible_count == 3
    assert result.coverage.fully_excluded_count == 3


def test_record_ids_are_deterministic_across_source_locations(tmp_path: Path) -> None:
    left = tmp_path / "left" / "pick_history.csv"
    right = tmp_path / "right" / "pick_history.csv"
    left.parent.mkdir()
    right.parent.mkdir()
    _write_source(left, [_row()])
    _write_source(right, [_row()])

    left_id = load_phase1_records(left).records[0].record_id
    right_id = load_phase1_records(right).records[0].record_id

    assert left_id == right_id
    assert left_id.startswith("cv1_")


@pytest.mark.parametrize(
    "filename",
    [
        "prediction_history.csv",
        "result_feedback.csv",
        "market_shadow_history.csv",
        "performance_summary.csv",
        "pick_history_backup.csv",
    ],
)
def test_excluded_sources_cannot_be_loaded(tmp_path: Path, filename: str) -> None:
    path = tmp_path / filename
    _write_source(path, [_row()])

    with pytest.raises(SourceNotAllowedError, match="permits only"):
        load_phase1_records(path)
