from __future__ import annotations

import csv
from pathlib import Path

import pytest

from courtvision.evaluation.model_records import (
    FEEDBACK_EVALUATION_POPULATION,
    Outcome,
)
from courtvision.evaluation.model_sources import (
    DuplicateRecordConflictError,
    FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS,
    FEEDBACK_REQUIRED_COLUMNS,
    LEGACY_PICK_HISTORY_COLUMNS,
    SourceNotAllowedError,
    SourceRowError,
    SourceSchemaError,
    SourceState,
    load_feedback_records,
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


def _feedback_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {column: "" for column in FEEDBACK_REQUIRED_COLUMNS}
    row.update({column: "" for column in FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS})
    row.update(
        {
            "prediction_date": "2026-05-01",
            "market_type": "player_points",
            "entity_name": "Sample Player",
            "team": "TOR",
            "opponent": "BOS",
            "selection": "over",
            "sportsbook_line": "20.5",
            "actual_value": "24",
            "result": "win",
            "graded_result": "win",
            "player_id": "player-1",
            "canonical_player_id": "canonical-1",
            "game_id": "game-1",
            "model_projection": "23.0",
            "projection": "23.0",
            "odds": "-110",
            "confidence": "0.75",
            "edge": "2.5",
        }
    )
    row.update(overrides)
    if "grade_key" not in overrides:
        row["grade_key"] = "|".join(
            str(row[column])
            for column in (
                "prediction_date",
                "market_type",
                "entity_name",
                "selection",
                "sportsbook_line",
            )
        )
    effective_result = str(row.get("result") or row.get("graded_result") or "").strip().casefold()
    for column, outcome in (
        ("is_win", "win"),
        ("is_push", "push"),
        ("is_loss", "loss"),
    ):
        if column not in overrides:
            row[column] = 1 if effective_result == outcome else 0
    return row


def _write_feedback(
    path: Path,
    rows: list[dict[str, object]],
    *,
    columns: tuple[str, ...] | None = None,
) -> None:
    fieldnames = list(
        columns
        or (*FEEDBACK_REQUIRED_COLUMNS, *FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS)
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(
            {column: row.get(column, "") for column in fieldnames} for row in rows
        )


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


def test_feedback_adapter_requires_core_allows_extras_and_maps_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "result_feedback.csv"
    columns = (
        *FEEDBACK_REQUIRED_COLUMNS,
        *FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS,
        "irrelevant_runtime_metadata",
    )
    row = _feedback_row(irrelevant_runtime_metadata="ignored")
    _write_feedback(path, [row], columns=columns)

    result = load_feedback_records(path)

    assert result.state is SourceState.LOADED
    assert result.coverage.source_row_count == 1
    assert result.coverage.unique_record_count == 1
    assert result.feedback_coverage is not None
    assert result.feedback_coverage.unique_grade_key_count == 1
    record = result.records[0]
    assert record.evaluation_population == FEEDBACK_EVALUATION_POPULATION
    assert record.participant_name == "Sample Player"
    assert record.participant_id == "player-1"
    assert record.team == "TOR"
    assert record.opponent == "BOS"
    assert record.event_id == "game-1"
    assert record.market == "player_points"
    assert record.selection == "over"
    assert record.line == pytest.approx(20.5)
    assert record.prediction_value == pytest.approx(23.0)
    assert record.odds_american == -110
    assert record.confidence == pytest.approx(0.75)
    assert record.edge_value == pytest.approx(2.5)
    assert record.actual_value == pytest.approx(24.0)
    assert not hasattr(record, "irrelevant_runtime_metadata")


def test_feedback_optional_analytical_columns_may_be_absent(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(path, [_feedback_row()], columns=FEEDBACK_REQUIRED_COLUMNS)

    result = load_feedback_records(path)

    record = result.records[0]
    assert record.participant_id is None
    assert record.event_id is None
    assert record.prediction_value is None
    assert record.odds_american is None
    assert record.confidence is None
    assert record.edge_value is None
    assert result.feedback_coverage is not None
    assert result.feedback_coverage.valid_odds_count == 0
    assert result.feedback_coverage.prediction_value_present_count == 0


@pytest.mark.parametrize(
    ("raw_result", "graded_result", "expected"),
    [
        (" WIN ", "win", Outcome.WIN),
        ("Loss", "loss", Outcome.LOSS),
        ("PUSH", "push", Outcome.PUSH),
        ("void", "void", Outcome.VOID),
        ("unresolved", "unresolved", Outcome.PENDING),
        ("ungraded", "ungraded", Outcome.UNSUPPORTED),
        ("unsupported", "unsupported", Outcome.UNSUPPORTED),
        ("unknown", "unknown", Outcome.UNSUPPORTED),
        ("unexpected", "unexpected", Outcome.UNSUPPORTED),
        ("", "", Outcome.UNSUPPORTED),
    ],
)
def test_feedback_outcome_mappings(
    tmp_path: Path,
    raw_result: str,
    graded_result: str,
    expected: Outcome,
) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(
        path,
        [_feedback_row(result=raw_result, graded_result=graded_result)],
    )

    record = load_feedback_records(path).records[0]

    assert record.outcome is expected


def test_feedback_result_falls_back_to_graded_result(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(path, [_feedback_row(result="", graded_result="win")])

    record = load_feedback_records(path).records[0]

    assert record.outcome is Outcome.WIN
    assert record.hit_rate_eligible is True
    assert record.roi_eligible is True


def test_feedback_result_disagreement_fails_row(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(
        path,
        [_feedback_row(result="win", graded_result="loss")],
    )

    with pytest.raises(SourceRowError, match="different outcomes"):
        load_feedback_records(path)


def test_feedback_meaningful_flag_disagreement_fails_row(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(path, [_feedback_row(result="win", is_win=0)])

    with pytest.raises(SourceRowError, match="is_win contradicts"):
        load_feedback_records(path)


def test_feedback_blank_flags_are_allowed(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(
        path,
        [_feedback_row(is_win="", is_push="", is_loss="")],
    )

    assert load_feedback_records(path).records[0].outcome is Outcome.WIN


def test_feedback_invalid_grade_key_fails_row(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(path, [_feedback_row(grade_key="wrong")])

    with pytest.raises(SourceRowError, match="grade_key must exactly match"):
        load_feedback_records(path)


def test_feedback_projection_alias_disagreement_fails_row(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(
        path,
        [_feedback_row(model_projection="23.0", projection="24.0")],
    )

    with pytest.raises(SourceRowError, match="materially disagree"):
        load_feedback_records(path)


def test_feedback_record_ids_are_stable_across_location_and_order(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left" / "result_feedback.csv"
    right = tmp_path / "right" / "result_feedback.csv"
    left.parent.mkdir()
    right.parent.mkdir()
    first = _feedback_row()
    second = _feedback_row(
        entity_name="Second Player",
        player_id="player-2",
        sportsbook_line="18.5",
    )
    _write_feedback(left, [first, second])
    _write_feedback(right, [second, first])

    left_ids = {record.participant_name: record.record_id for record in load_feedback_records(left).records}
    right_ids = {record.participant_name: record.record_id for record in load_feedback_records(right).records}

    assert left_ids == right_ids
    assert all(record_id.startswith("cv1_") for record_id in left_ids.values())


def test_feedback_compatible_duplicates_are_canonicalized_and_counted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "result_feedback.csv"
    unresolved = _feedback_row(
        result="unresolved",
        graded_result="unresolved",
        actual_value="",
        odds="",
        confidence="",
        edge="",
    )
    final = _feedback_row(result="win", graded_result="win", actual_value="24")
    _write_feedback(path, [unresolved, final])

    result = load_feedback_records(path)

    assert len(result.records) == 1
    assert result.records[0].outcome is Outcome.WIN
    assert result.records[0].source_row_number == 3
    assert result.feedback_coverage is not None
    assert result.feedback_coverage.duplicate_grade_key_group_count == 1
    assert result.feedback_coverage.duplicate_source_row_count == 2
    assert result.feedback_coverage.duplicate_rows_excluded_count == 1


def test_feedback_conflicting_final_outcomes_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(
        path,
        [
            _feedback_row(result="win", graded_result="win"),
            _feedback_row(result="loss", graded_result="loss"),
        ],
    )

    with pytest.raises(DuplicateRecordConflictError, match="Conflicting final outcomes"):
        load_feedback_records(path)


def test_feedback_missing_source_is_nonfatal_and_creates_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing" / "result_feedback.csv"

    result = load_feedback_records(path)

    assert result.state is SourceState.MISSING
    assert result.records == ()
    assert result.feedback_coverage is not None
    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.parametrize("header_only", [False, True])
def test_feedback_empty_source_is_nonfatal(
    tmp_path: Path, header_only: bool
) -> None:
    path = tmp_path / "result_feedback.csv"
    if header_only:
        _write_feedback(path, [])
    else:
        path.write_text("", encoding="utf-8")

    result = load_feedback_records(path)

    assert result.state is SourceState.EMPTY
    assert result.records == ()


def test_feedback_missing_required_column_raises_schema_error(tmp_path: Path) -> None:
    path = tmp_path / "result_feedback.csv"
    columns = tuple(
        column for column in FEEDBACK_REQUIRED_COLUMNS if column != "grade_key"
    )
    _write_feedback(path, [_feedback_row()], columns=columns)

    with pytest.raises(SourceSchemaError, match="missing required"):
        load_feedback_records(path)


@pytest.mark.parametrize(
    "filename",
    [
        "prediction_history.csv",
        "pick_history.csv",
        "market_shadow_history.csv",
        "paper_kelly_history.csv",
        "result_feedback_backup.csv",
        "official_settlement.csv",
        "mlb_result_feedback.csv",
        "feature_feedback.csv",
        "rehearsal_result_feedback.csv",
        "unrelated.csv",
    ],
)
def test_feedback_loader_rejects_every_non_allowlisted_filename(
    tmp_path: Path, filename: str
) -> None:
    path = tmp_path / filename
    _write_feedback(path, [_feedback_row()])

    with pytest.raises(SourceNotAllowedError, match="permits only"):
        load_feedback_records(path)
