"""Strict read-only source adapters for legacy model-evaluation data."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, fields
from datetime import date
from enum import Enum
import math
from pathlib import Path
from typing import Any, Mapping

from courtvision.evaluation.model_records import (
    FEEDBACK_EVALUATION_POPULATION,
    ModelEvaluationRecord,
    Outcome,
    create_model_evaluation_record,
    normalize_outcome,
)


LEGACY_PICK_HISTORY_COLUMNS = (
    "prediction_date",
    "run_timestamp",
    "player_name",
    "player_id",
    "team",
    "opponent",
    "game_id",
    "market",
    "selection",
    "line",
    "projection",
    "edge",
    "abs_edge",
    "odds",
    "confidence",
    "quality_score",
    "qualification_reason",
    "provider_used",
    "result_status",
    "actual_value",
    "grading_skip_reason",
    "kelly_eligible",
    "skip_reason",
    "context_caution_level",
    "context_pick_alignment",
    "line_source",
    "fragility_score",
    "fragility_bucket",
    "fragility_reasons",
    "survivability_score",
    "survivability_bucket",
    "survivability_reasons",
)

PHASE1_SOURCE_FILENAME = "pick_history.csv"
PHASE1_SOURCE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "history" / PHASE1_SOURCE_FILENAME
)

FEEDBACK_SOURCE_FILENAME = "result_feedback.csv"
FEEDBACK_SOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "outputs"
    / "runtime"
    / "history"
    / FEEDBACK_SOURCE_FILENAME
)
FEEDBACK_REQUIRED_COLUMNS = (
    "grade_key",
    "prediction_date",
    "market_type",
    "entity_name",
    "team",
    "opponent",
    "selection",
    "sportsbook_line",
    "actual_value",
    "result",
    "graded_result",
    "is_win",
    "is_push",
    "is_loss",
)
FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS = (
    "player_id",
    "canonical_player_id",
    "game_id",
    "model_projection",
    "projection",
    "odds",
    "confidence",
    "edge",
)


class SourceState(str, Enum):
    LOADED = "LOADED"
    MISSING = "MISSING"
    EMPTY = "EMPTY"


class ModelSourceError(RuntimeError):
    """Base error for fail-closed source validation."""


class SourceNotAllowedError(ModelSourceError):
    """Raised when a path is not the single Phase 1 source filename."""


class SourceSchemaError(ModelSourceError):
    """Raised when the CSV header is not the exact legacy contract."""


class SourceRowError(ModelSourceError):
    """Raised when a source row cannot be normalized safely."""


class DuplicateRecordConflictError(ModelSourceError):
    """Raised when one stable identity maps to conflicting normalized values."""


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    source_row_count: int
    unique_record_count: int
    duplicate_identical_count: int
    duplicate_conflict_count: int
    earliest_date: date | None
    latest_date: date | None
    unique_slates: int
    hit_rate_eligible_count: int
    roi_eligible_count: int
    fully_excluded_count: int
    exclusion_reason_counts: tuple[tuple[str, int], ...]

    @property
    def duplicate_status(self) -> str:
        if self.duplicate_conflict_count:
            return "CONFLICT"
        if self.duplicate_identical_count:
            return "IDENTICAL_DUPLICATES_DEDUPED"
        return "NONE"


@dataclass(frozen=True, slots=True)
class SourceLoadResult:
    state: SourceState
    source_path: str
    records: tuple[ModelEvaluationRecord, ...]
    coverage: SourceCoverage
    message: str
    feedback_coverage: FeedbackCoverage | None = None


@dataclass(frozen=True, slots=True)
class FeedbackCoverage:
    unique_grade_key_count: int
    duplicate_grade_key_group_count: int
    duplicate_source_row_count: int
    duplicate_rows_excluded_count: int
    conflicting_grade_key_count: int
    final_graded_count: int
    unresolved_count: int
    unsupported_count: int
    actual_value_present_count: int
    valid_odds_count: int
    prediction_value_present_count: int
    confidence_present_count: int
    edge_present_count: int
    participant_id_present_count: int
    event_id_present_count: int


def _empty_coverage() -> SourceCoverage:
    return SourceCoverage(
        source_row_count=0,
        unique_record_count=0,
        duplicate_identical_count=0,
        duplicate_conflict_count=0,
        earliest_date=None,
        latest_date=None,
        unique_slates=0,
        hit_rate_eligible_count=0,
        roi_eligible_count=0,
        fully_excluded_count=0,
        exclusion_reason_counts=(),
    )


def _empty_feedback_coverage() -> FeedbackCoverage:
    return FeedbackCoverage(
        unique_grade_key_count=0,
        duplicate_grade_key_group_count=0,
        duplicate_source_row_count=0,
        duplicate_rows_excluded_count=0,
        conflicting_grade_key_count=0,
        final_graded_count=0,
        unresolved_count=0,
        unsupported_count=0,
        actual_value_present_count=0,
        valid_odds_count=0,
        prediction_value_present_count=0,
        confidence_present_count=0,
        edge_present_count=0,
        participant_id_present_count=0,
        event_id_present_count=0,
    )


def _optional_text(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _required_text(value: Any, *, column: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{column} is required")
    return text


def _optional_float(value: Any, *, column: str) -> float | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column} must be numeric when present") from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ValueError(f"{column} must be finite when present")
    return parsed


def _normalize_row(
    row: Mapping[str, Any], *, source_path: str, source_row_number: int
) -> ModelEvaluationRecord:
    try:
        prediction_date = date.fromisoformat(
            _required_text(row.get("prediction_date"), column="prediction_date")
        )
        return create_model_evaluation_record(
            source_name=PHASE1_SOURCE_FILENAME,
            source_path=source_path,
            source_row_number=source_row_number,
            prediction_date=prediction_date,
            participant_id=_optional_text(row.get("player_id")),
            participant_name=_required_text(
                row.get("player_name"), column="player_name"
            ),
            team=_optional_text(row.get("team")),
            opponent=_optional_text(row.get("opponent")),
            event_id=_optional_text(row.get("game_id")),
            market=_required_text(row.get("market"), column="market"),
            selection=_required_text(row.get("selection"), column="selection"),
            line=_optional_float(row.get("line"), column="line"),
            prediction_value=_optional_float(
                row.get("projection"), column="projection"
            ),
            raw_odds=row.get("odds"),
            confidence=_optional_float(row.get("confidence"), column="confidence"),
            edge_value=_optional_float(row.get("edge"), column="edge"),
            raw_outcome=row.get("result_status"),
            actual_value=_optional_float(
                row.get("actual_value"), column="actual_value"
            ),
        )
    except (TypeError, ValueError) as exc:
        raise SourceRowError(f"Row {source_row_number}: {exc}") from exc


def _feedback_outcome(row: Mapping[str, Any]) -> tuple[str, Outcome]:
    result = _optional_text(row.get("result"))
    graded_result = _optional_text(row.get("graded_result"))
    if result is not None and graded_result is not None:
        normalized_result = normalize_outcome(result)
        normalized_graded_result = normalize_outcome(graded_result)
        if normalized_result is not normalized_graded_result:
            raise ValueError(
                "result and graded_result normalize to different outcomes"
            )
    selected = result or graded_result or ""
    return selected, normalize_outcome(selected)


def _optional_feedback_flag(value: Any, *, column: str) -> bool | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text.casefold()
    if normalized in {"nan", "none", "null", "<na>"}:
        return None
    if normalized in {"1", "1.0", "true", "yes"}:
        return True
    if normalized in {"0", "0.0", "false", "no"}:
        return False
    raise ValueError(f"{column} must be a boolean flag when present")


def _feedback_prediction_value(row: Mapping[str, Any]) -> float | None:
    model_projection = _optional_float(
        row.get("model_projection"), column="model_projection"
    )
    projection = _optional_float(row.get("projection"), column="projection")
    if (
        model_projection is not None
        and projection is not None
        and not math.isclose(
            model_projection,
            projection,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    ):
        raise ValueError("model_projection and projection materially disagree")
    return model_projection if model_projection is not None else projection


def _normalize_feedback_row(
    row: Mapping[str, Any], *, source_path: str, source_row_number: int
) -> tuple[str, ModelEvaluationRecord]:
    try:
        grade_key = _required_text(row.get("grade_key"), column="grade_key")
        prediction_date_text = _required_text(
            row.get("prediction_date"), column="prediction_date"
        )
        market = _required_text(row.get("market_type"), column="market_type")
        participant_name = _required_text(
            row.get("entity_name"), column="entity_name"
        )
        selection = _required_text(row.get("selection"), column="selection")
        line_text = _required_text(
            row.get("sportsbook_line"), column="sportsbook_line"
        )
        line = _optional_float(line_text, column="sportsbook_line")
        assert line is not None

        canonical_grade_key = "|".join(
            (
                prediction_date_text,
                market,
                participant_name,
                selection,
                line_text,
            )
        )
        if grade_key != canonical_grade_key:
            raise ValueError(
                "grade_key must exactly match "
                "prediction_date|market_type|entity_name|selection|sportsbook_line"
            )

        raw_outcome, outcome = _feedback_outcome(row)
        expected_flags = {
            "is_win": outcome is Outcome.WIN,
            "is_push": outcome is Outcome.PUSH,
            "is_loss": outcome is Outcome.LOSS,
        }
        for column, expected in expected_flags.items():
            flag = _optional_feedback_flag(row.get(column), column=column)
            if flag is not None and flag is not expected:
                raise ValueError(f"{column} contradicts the normalized outcome")

        participant_id = _optional_text(row.get("player_id")) or _optional_text(
            row.get("canonical_player_id")
        )
        return grade_key, create_model_evaluation_record(
            source_name=FEEDBACK_SOURCE_FILENAME,
            source_path=source_path,
            source_row_number=source_row_number,
            prediction_date=date.fromisoformat(prediction_date_text),
            participant_id=participant_id,
            participant_name=participant_name,
            team=_optional_text(row.get("team")),
            opponent=_optional_text(row.get("opponent")),
            event_id=_optional_text(row.get("game_id")),
            market=market,
            selection=selection,
            line=line,
            prediction_value=_feedback_prediction_value(row),
            raw_odds=row.get("odds"),
            confidence=_optional_float(row.get("confidence"), column="confidence"),
            edge_value=_optional_float(row.get("edge"), column="edge"),
            raw_outcome=raw_outcome,
            actual_value=_optional_float(
                row.get("actual_value"), column="actual_value"
            ),
            evaluation_population=FEEDBACK_EVALUATION_POPULATION,
            source_identity=grade_key,
        )
    except (TypeError, ValueError) as exc:
        raise SourceRowError(f"Row {source_row_number}: {exc}") from exc


_FINAL_FEEDBACK_OUTCOMES = {Outcome.WIN, Outcome.LOSS, Outcome.PUSH}


def _feedback_record_score(record: ModelEvaluationRecord) -> tuple[int, ...]:
    analytical_completeness = sum(
        value is not None
        for value in (
            record.participant_id,
            record.team,
            record.opponent,
            record.event_id,
            record.prediction_value,
            record.odds_american,
            record.confidence,
            record.edge_value,
        )
    )
    return (
        int(record.outcome in _FINAL_FEEDBACK_OUTCOMES),
        int(record.outcome is not Outcome.UNSUPPORTED),
        int(record.actual_value is not None),
        analytical_completeness,
        record.source_row_number,
    )


def _canonical_feedback_records(
    records_by_grade_key: Mapping[str, list[ModelEvaluationRecord]],
) -> tuple[tuple[ModelEvaluationRecord, ...], int, int, int]:
    canonical: list[ModelEvaluationRecord] = []
    duplicate_groups = 0
    duplicate_source_rows = 0
    duplicate_rows_excluded = 0
    for grade_key, group in records_by_grade_key.items():
        if len(group) > 1:
            duplicate_groups += 1
            duplicate_source_rows += len(group)
            duplicate_rows_excluded += len(group) - 1
        final_outcomes = {
            record.outcome
            for record in group
            if record.outcome in _FINAL_FEEDBACK_OUTCOMES
        }
        if len(final_outcomes) > 1:
            source_rows = sorted(record.source_row_number for record in group)
            raise DuplicateRecordConflictError(
                "Conflicting final outcomes for feedback grade key "
                f"{grade_key!r} at source rows {source_rows}"
            )
        canonical.append(max(group, key=_feedback_record_score))
    return (
        tuple(canonical),
        duplicate_groups,
        duplicate_source_rows,
        duplicate_rows_excluded,
    )


_DUPLICATE_COMPARISON_FIELDS = tuple(
    field.name
    for field in fields(ModelEvaluationRecord)
    if field.name != "source_row_number"
)


def _records_match(
    left: ModelEvaluationRecord, right: ModelEvaluationRecord
) -> bool:
    return all(
        getattr(left, field_name) == getattr(right, field_name)
        for field_name in _DUPLICATE_COMPARISON_FIELDS
    )


def _coverage(
    records: tuple[ModelEvaluationRecord, ...],
    *,
    source_row_count: int,
    duplicate_identical_count: int,
) -> SourceCoverage:
    dates = {record.prediction_date for record in records}
    exclusion_counts = Counter(
        reason for record in records for reason in record.exclusion_reasons
    )
    return SourceCoverage(
        source_row_count=source_row_count,
        unique_record_count=len(records),
        duplicate_identical_count=duplicate_identical_count,
        duplicate_conflict_count=0,
        earliest_date=min(dates) if dates else None,
        latest_date=max(dates) if dates else None,
        unique_slates=len(dates),
        hit_rate_eligible_count=sum(record.hit_rate_eligible for record in records),
        roi_eligible_count=sum(record.roi_eligible for record in records),
        fully_excluded_count=sum(
            not record.hit_rate_eligible and not record.roi_eligible
            for record in records
        ),
        exclusion_reason_counts=tuple(sorted(exclusion_counts.items())),
    )


def _feedback_coverage(
    records: tuple[ModelEvaluationRecord, ...],
    *,
    duplicate_grade_key_group_count: int,
    duplicate_source_row_count: int,
    duplicate_rows_excluded_count: int,
) -> FeedbackCoverage:
    return FeedbackCoverage(
        unique_grade_key_count=len(records),
        duplicate_grade_key_group_count=duplicate_grade_key_group_count,
        duplicate_source_row_count=duplicate_source_row_count,
        duplicate_rows_excluded_count=duplicate_rows_excluded_count,
        conflicting_grade_key_count=0,
        final_graded_count=sum(
            record.outcome in _FINAL_FEEDBACK_OUTCOMES for record in records
        ),
        unresolved_count=sum(record.outcome is Outcome.PENDING for record in records),
        unsupported_count=sum(
            record.outcome is Outcome.UNSUPPORTED for record in records
        ),
        actual_value_present_count=sum(
            record.actual_value is not None for record in records
        ),
        valid_odds_count=sum(record.odds_valid for record in records),
        prediction_value_present_count=sum(
            record.prediction_value is not None for record in records
        ),
        confidence_present_count=sum(
            record.confidence is not None for record in records
        ),
        edge_present_count=sum(record.edge_value is not None for record in records),
        participant_id_present_count=sum(
            record.participant_id is not None for record in records
        ),
        event_id_present_count=sum(record.event_id is not None for record in records),
    )


def load_phase1_records(path: str | Path = PHASE1_SOURCE_PATH) -> SourceLoadResult:
    """Load only ``pick_history.csv`` without writes or directory creation."""

    source = Path(path)
    if source.name.casefold() != PHASE1_SOURCE_FILENAME.casefold():
        raise SourceNotAllowedError(
            f"Phase 1 permits only {PHASE1_SOURCE_FILENAME!r}; got {source.name!r}"
        )
    source_path = str(source.resolve(strict=False))
    if not source.is_file():
        return SourceLoadResult(
            state=SourceState.MISSING,
            source_path=source_path,
            records=(),
            coverage=_empty_coverage(),
            message="Phase 1 source file is missing; no file was created.",
        )
    if source.stat().st_size == 0:
        return SourceLoadResult(
            state=SourceState.EMPTY,
            source_path=source_path,
            records=(),
            coverage=_empty_coverage(),
            message="Phase 1 source file is empty.",
        )

    by_id: dict[str, ModelEvaluationRecord] = {}
    raw_row_count = 0
    duplicate_identical_count = 0
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            actual_columns = tuple(reader.fieldnames or ())
            if actual_columns != LEGACY_PICK_HISTORY_COLUMNS:
                raise SourceSchemaError(
                    "Malformed pick_history.csv schema: expected exact 32-column "
                    f"contract {LEGACY_PICK_HISTORY_COLUMNS!r}; got {actual_columns!r}"
                )

            for row in reader:
                raw_row_count += 1
                source_row_number = reader.line_num
                if None in row:
                    raise SourceSchemaError(
                        f"Row {source_row_number} has values beyond the 32-column schema"
                    )
                record = _normalize_row(
                    row,
                    source_path=source_path,
                    source_row_number=source_row_number,
                )
                existing = by_id.get(record.record_id)
                if existing is None:
                    by_id[record.record_id] = record
                elif _records_match(existing, record):
                    duplicate_identical_count += 1
                else:
                    raise DuplicateRecordConflictError(
                        "Conflicting duplicate record ID "
                        f"{record.record_id!r} at source rows "
                        f"{existing.source_row_number} and {record.source_row_number}"
                    )
    except UnicodeError as exc:
        raise SourceSchemaError(f"Source is not valid UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise SourceSchemaError(f"Malformed CSV: {exc}") from exc

    records = tuple(by_id.values())
    if raw_row_count == 0:
        return SourceLoadResult(
            state=SourceState.EMPTY,
            source_path=source_path,
            records=(),
            coverage=_empty_coverage(),
            message="Phase 1 source has a valid header but no data rows.",
        )
    coverage = _coverage(
        records,
        source_row_count=raw_row_count,
        duplicate_identical_count=duplicate_identical_count,
    )
    return SourceLoadResult(
        state=SourceState.LOADED,
        source_path=source_path,
        records=records,
        coverage=coverage,
        message="Phase 1 source loaded read-only.",
    )


def load_feedback_records(
    path: str | Path = FEEDBACK_SOURCE_PATH,
) -> SourceLoadResult:
    """Load only ``result_feedback.csv`` without writes or directory creation."""

    source = Path(path)
    if source.name.casefold() != FEEDBACK_SOURCE_FILENAME.casefold():
        raise SourceNotAllowedError(
            "Feedback evaluation permits only "
            f"{FEEDBACK_SOURCE_FILENAME!r}; got {source.name!r}"
        )
    source_path = str(source.resolve(strict=False))
    if not source.is_file():
        return SourceLoadResult(
            state=SourceState.MISSING,
            source_path=source_path,
            records=(),
            coverage=_empty_coverage(),
            message="Feedback source file is missing; no file was created.",
            feedback_coverage=_empty_feedback_coverage(),
        )
    if source.stat().st_size == 0:
        return SourceLoadResult(
            state=SourceState.EMPTY,
            source_path=source_path,
            records=(),
            coverage=_empty_coverage(),
            message="Feedback source file is empty.",
            feedback_coverage=_empty_feedback_coverage(),
        )

    records_by_grade_key: dict[str, list[ModelEvaluationRecord]] = {}
    raw_row_count = 0
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            actual_columns = tuple(reader.fieldnames or ())
            missing_columns = tuple(
                column
                for column in FEEDBACK_REQUIRED_COLUMNS
                if column not in actual_columns
            )
            if missing_columns:
                raise SourceSchemaError(
                    "Malformed result_feedback.csv schema: missing required "
                    f"columns {missing_columns!r}"
                )

            for row in reader:
                raw_row_count += 1
                source_row_number = reader.line_num
                if None in row:
                    raise SourceSchemaError(
                        f"Row {source_row_number} has values beyond its CSV header"
                    )
                grade_key, record = _normalize_feedback_row(
                    row,
                    source_path=source_path,
                    source_row_number=source_row_number,
                )
                records_by_grade_key.setdefault(grade_key, []).append(record)
    except UnicodeError as exc:
        raise SourceSchemaError(f"Source is not valid UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise SourceSchemaError(f"Malformed CSV: {exc}") from exc

    if raw_row_count == 0:
        return SourceLoadResult(
            state=SourceState.EMPTY,
            source_path=source_path,
            records=(),
            coverage=_empty_coverage(),
            message="Feedback source has a valid header but no data rows.",
            feedback_coverage=_empty_feedback_coverage(),
        )

    (
        records,
        duplicate_groups,
        duplicate_source_rows,
        duplicate_rows_excluded,
    ) = _canonical_feedback_records(records_by_grade_key)
    coverage = _coverage(
        records,
        source_row_count=raw_row_count,
        duplicate_identical_count=0,
    )
    feedback_coverage = _feedback_coverage(
        records,
        duplicate_grade_key_group_count=duplicate_groups,
        duplicate_source_row_count=duplicate_source_rows,
        duplicate_rows_excluded_count=duplicate_rows_excluded,
    )
    return SourceLoadResult(
        state=SourceState.LOADED,
        source_path=source_path,
        records=records,
        coverage=coverage,
        message="Feedback source loaded read-only.",
        feedback_coverage=feedback_coverage,
    )


__all__ = [
    "DuplicateRecordConflictError",
    "FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS",
    "FEEDBACK_REQUIRED_COLUMNS",
    "FEEDBACK_SOURCE_FILENAME",
    "FEEDBACK_SOURCE_PATH",
    "FeedbackCoverage",
    "LEGACY_PICK_HISTORY_COLUMNS",
    "ModelSourceError",
    "PHASE1_SOURCE_FILENAME",
    "PHASE1_SOURCE_PATH",
    "SourceCoverage",
    "SourceLoadResult",
    "SourceNotAllowedError",
    "SourceRowError",
    "SourceSchemaError",
    "SourceState",
    "load_feedback_records",
    "load_phase1_records",
]
