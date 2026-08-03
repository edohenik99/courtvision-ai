"""Strict read-only source adapter for the Phase 1 legacy evaluation data."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, fields
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from courtvision.evaluation.model_records import (
    ModelEvaluationRecord,
    create_model_evaluation_record,
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


__all__ = [
    "DuplicateRecordConflictError",
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
    "load_phase1_records",
]
