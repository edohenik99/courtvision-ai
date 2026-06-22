"""Leakage and provenance audit for fixture-backed MLB HR dataset rows.

This module is deliberately read-only.  It audits Phase 4A row contracts and
mapping-shaped test cases without fetching data, changing builder behavior, or
granting any production or wagering approval.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Final, Iterable, Mapping

from courtvision.sports.mlb.training.hr_dataset_schema import (
    MLB_HR_MARKET_TYPE,
    MLB_LEAGUE,
    MLB_SPORT,
    NOT_APPROVED,
    OUTCOME_LABEL_FIELD_NAMES,
    PREGAME_FEATURE_FIELD_NAMES,
    MLBHRBatterGameRow,
)


class MLBHRLeakageAuditError(ValueError):
    """Raised when an audit input or opt-in report write is invalid."""


class MLBHRLeakageAuditSeverity(str, Enum):
    """Supported audit issue severities."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class MLBHRLeakageAuditIssue:
    """One deterministic, actionable finding for a dataset row."""

    issue_id: str
    row_id: str | None
    game_id: str | None
    player_id: str | None
    severity: MLBHRLeakageAuditSeverity
    category: str
    message: str
    field_name: str | None
    expected: str | None
    actual: str | None
    recommended_fix: str


@dataclass(frozen=True, slots=True)
class MLBHRLeakageAuditReport:
    """Immutable default-deny summary for a collection of audited rows."""

    row_count: int
    checked_at: str
    issue_count: int
    error_count: int
    warning_count: int
    info_count: int
    passed: bool
    issues: tuple[MLBHRLeakageAuditIssue, ...]
    sport: str = MLB_SPORT
    league: str = MLB_LEAGUE
    market_type: str = MLB_HR_MARKET_TYPE
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    approval_status: str = NOT_APPROVED

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.eligible_for_betting is not False:
            raise MLBHRLeakageAuditError("audit reports cannot be betting eligible")
        if self.kelly_eligible is not False:
            raise MLBHRLeakageAuditError("audit reports cannot be Kelly eligible")
        if self.approval_status != NOT_APPROVED:
            raise MLBHRLeakageAuditError(
                "audit reports must retain not_approved status"
            )


_REQUIRED_FIELDS: Final = (
    "game_id",
    "player_id",
    "game_date",
    "row_id",
    "schema_version",
)
_WEATHER_FIELDS: Final = (
    "weather_temperature",
    "weather_wind_speed",
    "weather_wind_direction",
    "weather_wind_out_to_field",
    "weather_humidity",
    "roof_status",
)
_BALLPARK_FIELDS: Final = (
    "park_factor_hr",
    "park_factor_lhb",
    "park_factor_rhb",
    "altitude",
)
_PITCHER_FEATURE_FIELDS: Final = tuple(
    name for name in PREGAME_FEATURE_FIELD_NAMES if name.startswith("pitcher_")
)
_FEATURE_NAMESPACE_NAMES: Final = frozenset(
    {
        "feature",
        "features",
        "feature_values",
        "pregame_feature",
        "pregame_features",
        "model_features",
    }
)
_SAME_GAME_STATCAST_FIELDS: Final = frozenset(
    {
        "events",
        "is_home_run",
        "launch_angle",
        "launch_speed",
        "hit_distance_sc",
        "estimated_ba_using_speedangle",
        "estimated_woba_using_speedangle",
        "barrel",
    }
)
_FIXTURE_SAME_GAME_DERIVED_FIELDS: Final = (
    "hitter_recent_barrel_rate",
    "hitter_recent_hard_hit_rate",
    "hitter_recent_fly_ball_rate",
    "hitter_recent_pull_rate",
    "hitter_avg_exit_velocity",
    "hitter_max_exit_velocity",
    "batter_hand",
    "pitcher_hand",
    "primary_pitch_matchup_score",
)
_NON_COMPLETED_STATUSES: Final = frozenset(
    {"postponed", "suspended", "unknown"}
)
_OPTIONAL_NON_CONTEXT_FEATURES: Final = tuple(
    name
    for name in PREGAME_FEATURE_FIELD_NAMES
    if name not in {*_WEATHER_FIELDS, *_BALLPARK_FIELDS}
)


def _row_values(row: MLBHRBatterGameRow | Mapping[str, object]) -> dict[str, object]:
    if isinstance(row, MLBHRBatterGameRow):
        return {item.name: getattr(row, item.name) for item in fields(row)}
    if isinstance(row, Mapping):
        return {str(key): value for key, value in row.items()}
    raise TypeError("row must be an MLBHRBatterGameRow or mapping")


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _context_text(value: object) -> str | None:
    return None if _missing(value) else str(value)


def _actual_text(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    raise TypeError("value must be a datetime or ISO datetime string")


def _nested_mapping(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return parsed


def _namespace_findings(values: Mapping[str, object]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []

    def walk(value: object, path: tuple[str, ...], in_feature: bool) -> None:
        value = _nested_mapping(value)
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key)
                normalized = key.strip().lower()
                current_path = (*path, key)
                current_in_feature = in_feature or normalized in _FEATURE_NAMESPACE_NAMES
                if current_in_feature and normalized in OUTCOME_LABEL_FIELD_NAMES:
                    findings.append(("label", ".".join(current_path)))
                if current_in_feature and normalized in _SAME_GAME_STATCAST_FIELDS:
                    findings.append(("same_game_statcast", ".".join(current_path)))
                if "same_game" in normalized and "statcast" in normalized and nested:
                    findings.append(("same_game_statcast", ".".join(current_path)))
                walk(nested, current_path, current_in_feature)
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value):
                walk(nested, (*path, str(index)), in_feature)

    for field_name, value in values.items():
        normalized = field_name.strip().lower()
        is_feature = (
            normalized in PREGAME_FEATURE_FIELD_NAMES
            or normalized in _FEATURE_NAMESPACE_NAMES
        )
        walk(value, (field_name,), is_feature)
    return list(dict.fromkeys(findings))


def audit_hr_batter_game_row(
    row: MLBHRBatterGameRow | Mapping[str, object],
) -> list[MLBHRLeakageAuditIssue]:
    """Audit one row without mutating it or invoking schema serialization."""

    values = _row_values(row)
    row_id = _context_text(values.get("row_id"))
    game_id = _context_text(values.get("game_id"))
    player_id = _context_text(values.get("player_id"))
    issues: list[MLBHRLeakageAuditIssue] = []

    def add(
        issue_id: str,
        severity: MLBHRLeakageAuditSeverity,
        category: str,
        message: str,
        *,
        field_name: str | None = None,
        expected: str | None = None,
        actual: object = None,
        recommended_fix: str,
    ) -> None:
        issues.append(
            MLBHRLeakageAuditIssue(
                issue_id=issue_id,
                row_id=row_id,
                game_id=game_id,
                player_id=player_id,
                severity=severity,
                category=category,
                message=message,
                field_name=field_name,
                expected=expected,
                actual=_actual_text(actual),
                recommended_fix=recommended_fix,
            )
        )

    eligible = values.get("eligible_for_training") is True

    for field_name in _REQUIRED_FIELDS:
        if _missing(values.get(field_name)):
            add(
                f"required_field.{field_name}_missing",
                MLBHRLeakageAuditSeverity.ERROR,
                "required_field",
                f"Required field {field_name} is missing.",
                field_name=field_name,
                expected="non-empty value",
                actual=values.get(field_name),
                recommended_fix=f"Populate {field_name} from the fixture source contract.",
            )

    feature_as_of = values.get("feature_as_of")
    event_start = values.get("event_start_time")
    if _missing(feature_as_of):
        add(
            "feature_timestamp.feature_as_of_missing",
            (
                MLBHRLeakageAuditSeverity.ERROR
                if eligible
                else MLBHRLeakageAuditSeverity.WARNING
            ),
            "feature_timestamp",
            "Feature cutoff time is missing.",
            field_name="feature_as_of",
            expected="strictly before event_start_time when known",
            actual=feature_as_of,
            recommended_fix="Record the pregame feature cutoff and keep the row ineligible until it is known.",
        )
    if not _missing(feature_as_of) and not _missing(event_start):
        try:
            feature_time = _parse_datetime(feature_as_of)
            event_time = _parse_datetime(event_start)
            if feature_time >= event_time:
                add(
                    "feature_timestamp.not_before_event_start",
                    MLBHRLeakageAuditSeverity.ERROR,
                    "feature_timestamp",
                    "Feature cutoff is not strictly before game start.",
                    field_name="feature_as_of",
                    expected=f"before {_actual_text(event_start)}",
                    actual=feature_as_of,
                    recommended_fix="Rebuild features using only observations available before game start.",
                )
        except (TypeError, ValueError):
            add(
                "feature_timestamp.invalid_or_incomparable",
                MLBHRLeakageAuditSeverity.ERROR,
                "feature_timestamp",
                "Feature and event timestamps are invalid or not comparable.",
                field_name="feature_as_of",
                expected="compatible ISO datetimes",
                actual=feature_as_of,
                recommended_fix="Normalize both timestamps to valid compatible datetimes.",
            )
    elif eligible and _missing(event_start):
        add(
            "feature_timestamp.event_start_missing_for_eligible_row",
            MLBHRLeakageAuditSeverity.ERROR,
            "feature_timestamp",
            "A training-eligible row has no game start time for cutoff verification.",
            field_name="event_start_time",
            expected="known event start time",
            actual=event_start,
            recommended_fix="Keep the row ineligible until event start and feature cutoff can be verified.",
        )

    for finding_type, field_path in _namespace_findings(values):
        if finding_type == "label":
            add(
                "label_separation.label_in_feature_namespace",
                MLBHRLeakageAuditSeverity.ERROR,
                "label_separation",
                "An outcome label appears inside a feature namespace.",
                field_name=field_path,
                expected="label-only namespace",
                actual="label field embedded in features",
                recommended_fix="Remove the label from all pregame feature payloads.",
            )
        else:
            add(
                "label_separation.same_game_statcast_in_features",
                MLBHRLeakageAuditSeverity.ERROR,
                "label_separation",
                "Same-game Statcast output appears inside a pregame feature namespace.",
                field_name=field_path,
                expected="prior-game observations only",
                actual="same-game Statcast field",
                recommended_fix="Exclude current-game batted-ball outputs from pregame features.",
            )

    dataset_version = str(values.get("dataset_version", "")).strip().lower()
    label_source = str(values.get("label_source", "")).strip().lower()
    if dataset_version.startswith("phase4b-fixture") and "statcast" in label_source:
        for field_name in _FIXTURE_SAME_GAME_DERIVED_FIELDS:
            if not _missing(values.get(field_name)):
                add(
                    "label_separation.same_game_statcast_in_fixture_feature",
                    MLBHRLeakageAuditSeverity.ERROR,
                    "label_separation",
                    "A same-game Statcast-derived value populated a fixture pregame feature.",
                    field_name=field_name,
                    expected="null in the Phase 4B fixture join",
                    actual=values.get(field_name),
                    recommended_fix="Keep same-game Statcast outputs label-only in fixture rows.",
                )

    game_completed = values.get("game_completed")
    label_available = values.get("label_available")
    game_status = str(values.get("game_status", "")).strip().lower()
    if eligible and game_completed is not True:
        add(
            "outcome_integrity.incomplete_game_training_eligible",
            MLBHRLeakageAuditSeverity.ERROR,
            "outcome_integrity",
            "A row without a completed game is marked training eligible.",
            field_name="eligible_for_training",
            expected="false unless game_completed is true",
            actual=values.get("eligible_for_training"),
            recommended_fix="Mark the row training ineligible until the game is completed and labeled.",
        )
    if eligible and game_status in _NON_COMPLETED_STATUSES:
        add(
            "outcome_integrity.game_status_training_eligible",
            MLBHRLeakageAuditSeverity.ERROR,
            "outcome_integrity",
            f"A {game_status} game is marked training eligible.",
            field_name="game_status",
            expected="completed",
            actual=game_status,
            recommended_fix="Keep postponed, suspended, and unknown games training ineligible.",
        )
    if eligible and label_available is not True:
        add(
            "outcome_integrity.label_unavailable_for_eligible_row",
            MLBHRLeakageAuditSeverity.ERROR,
            "outcome_integrity",
            "A training-eligible completed row does not have an available label.",
            field_name="label_available",
            expected="true",
            actual=label_available,
            recommended_fix="Attach a completed-game label or keep the row training ineligible.",
        )
    if eligible and any(
        _missing(values.get(name))
        for name in ("hit_hr_today", "home_run_count", "label_source", "label_as_of")
    ):
        missing_labels = [
            name
            for name in ("hit_hr_today", "home_run_count", "label_source", "label_as_of")
            if _missing(values.get(name))
        ]
        add(
            "outcome_integrity.eligible_label_fields_incomplete",
            MLBHRLeakageAuditSeverity.ERROR,
            "outcome_integrity",
            "A training-eligible row has incomplete label fields.",
            field_name=",".join(missing_labels),
            expected="complete label values and provenance",
            actual="missing",
            recommended_fix="Complete all label fields from postgame sources before eligibility.",
        )

    manifest_values = [
        values.get("source_manifest_ids"),
        values.get("statcast_manifest_id"),
        values.get("retrosheet_manifest_id"),
        values.get("weather_manifest_id"),
        values.get("ballpark_manifest_id"),
        values.get("odds_manifest_id"),
    ]
    has_manifest = any(
        bool(value) if isinstance(value, (tuple, list, set, Mapping)) else not _missing(value)
        for value in manifest_values
    )
    if not has_manifest:
        add(
            "provenance.source_manifest_missing",
            MLBHRLeakageAuditSeverity.WARNING,
            "provenance",
            "No source manifest identifier is recorded for this fixture row.",
            field_name="source_manifest_ids",
            expected="one or more fixture manifest IDs",
            actual=values.get("source_manifest_ids"),
            recommended_fix="Attach the fixture source manifest IDs before historical use.",
        )

    if any(not _missing(values.get(name)) for name in _WEATHER_FIELDS) and _missing(
        values.get("weather_source_type")
    ):
        add(
            "provenance.weather_source_type_missing",
            MLBHRLeakageAuditSeverity.WARNING,
            "provenance",
            "Weather values are present without a visible source type.",
            field_name="weather_source_type",
            expected="explicit source type",
            actual=values.get("weather_source_type"),
            recommended_fix="Record the weather source type used for the fixture join.",
        )
    if any(not _missing(values.get(name)) for name in _BALLPARK_FIELDS) and _missing(
        values.get("ballpark_source_type")
    ):
        add(
            "provenance.ballpark_source_type_missing",
            MLBHRLeakageAuditSeverity.WARNING,
            "provenance",
            "Ballpark values are present without a visible source type.",
            field_name="ballpark_source_type",
            expected="explicit source type",
            actual=values.get("ballpark_source_type"),
            recommended_fix="Record the ballpark source type used for the fixture join.",
        )
    if any(not _missing(values.get(name)) for name in _PITCHER_FEATURE_FIELDS) and _missing(
        values.get("pitcher_source_type")
    ):
        add(
            "provenance.pitcher_source_type_missing",
            MLBHRLeakageAuditSeverity.WARNING,
            "provenance",
            "Pitcher features are present without a visible source type.",
            field_name="pitcher_source_type",
            expected="explicit source type",
            actual=values.get("pitcher_source_type"),
            recommended_fix="Record the source type for pregame pitcher features.",
        )

    if values.get("eligible_for_betting") is not False:
        add(
            "approval_safety.betting_eligibility_claimed",
            MLBHRLeakageAuditSeverity.ERROR,
            "approval_safety",
            "Fixture row does not explicitly deny betting eligibility.",
            field_name="eligible_for_betting",
            expected="false",
            actual=values.get("eligible_for_betting"),
            recommended_fix="Set eligible_for_betting to false.",
        )
    if values.get("kelly_eligible") is not False:
        add(
            "approval_safety.kelly_eligibility_claimed",
            MLBHRLeakageAuditSeverity.ERROR,
            "approval_safety",
            "Fixture row does not explicitly deny Kelly eligibility.",
            field_name="kelly_eligible",
            expected="false",
            actual=values.get("kelly_eligible"),
            recommended_fix="Set kelly_eligible to false.",
        )
    if values.get("approval_status") != NOT_APPROVED:
        add(
            "approval_safety.production_approval_claimed",
            MLBHRLeakageAuditSeverity.ERROR,
            "approval_safety",
            "Fixture row does not retain the required not-approved status.",
            field_name="approval_status",
            expected=NOT_APPROVED,
            actual=values.get("approval_status"),
            recommended_fix="Set approval_status to not_approved.",
        )

    if all(_missing(values.get(name)) for name in _WEATHER_FIELDS):
        add(
            "data_quality.weather_context_missing",
            MLBHRLeakageAuditSeverity.WARNING,
            "data_quality",
            "Optional weather context is missing.",
            field_name="weather_temperature",
            expected="fixture weather context when available",
            actual=None,
            recommended_fix="Join a timestamped weather fixture or keep the missing context explicit.",
        )
    if all(_missing(values.get(name)) for name in _BALLPARK_FIELDS):
        add(
            "data_quality.ballpark_context_missing",
            MLBHRLeakageAuditSeverity.WARNING,
            "data_quality",
            "Optional ballpark context is missing.",
            field_name="park_factor_hr",
            expected="fixture ballpark context when available",
            actual=None,
            recommended_fix="Join fixture ballpark context or keep the missing context explicit.",
        )
    if eligible:
        missing_optional = [
            name for name in _OPTIONAL_NON_CONTEXT_FEATURES if _missing(values.get(name))
        ]
        if missing_optional:
            add(
                "data_quality.optional_training_features_missing",
                MLBHRLeakageAuditSeverity.WARNING,
                "data_quality",
                "A training-eligible fixture row has missing optional pregame features.",
                field_name=",".join(missing_optional),
                expected="documented feature coverage",
                actual=f"{len(missing_optional)} fields missing",
                recommended_fix="Document fixture coverage and fill only from pregame source data.",
            )
    for status_field in ("lineup_status", "probable_pitcher_status"):
        status = str(values.get(status_field, "unknown") or "unknown").strip().lower()
        if status == "unknown":
            add(
                f"data_quality.{status_field}_unknown",
                MLBHRLeakageAuditSeverity.WARNING,
                "data_quality",
                f"{status_field} is unknown.",
                field_name=status_field,
                expected="known pregame status when available",
                actual=status,
                recommended_fix="Keep the uncertainty visible and do not infer a confirmed status.",
            )

    return issues


def _checked_at_text(value: datetime | str | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    parsed = _parse_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def audit_hr_batter_game_rows(
    rows: Iterable[MLBHRBatterGameRow | Mapping[str, object]],
    *,
    checked_at: datetime | str | None = None,
) -> MLBHRLeakageAuditReport:
    """Audit rows and return an error-failing, warning-visible report."""

    materialized = tuple(rows)
    issues: list[MLBHRLeakageAuditIssue] = []
    seen_row_ids: set[str] = set()
    for row in materialized:
        values = _row_values(row)
        issues.extend(audit_hr_batter_game_row(row))
        row_id = _context_text(values.get("row_id"))
        if row_id is not None and row_id in seen_row_ids:
            issues.append(
                MLBHRLeakageAuditIssue(
                    issue_id="required_field.duplicate_row_id",
                    row_id=row_id,
                    game_id=_context_text(values.get("game_id")),
                    player_id=_context_text(values.get("player_id")),
                    severity=MLBHRLeakageAuditSeverity.ERROR,
                    category="required_field",
                    message="Duplicate row_id detected in audit input.",
                    field_name="row_id",
                    expected="unique row ID",
                    actual=row_id,
                    recommended_fix="Regenerate stable row IDs from unique game and player identities.",
                )
            )
        if row_id is not None:
            seen_row_ids.add(row_id)

    ordered = tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.row_id or "",
                issue.game_id or "",
                issue.player_id or "",
                issue.issue_id,
                issue.field_name or "",
            ),
        )
    )
    error_count = sum(
        issue.severity is MLBHRLeakageAuditSeverity.ERROR for issue in ordered
    )
    warning_count = sum(
        issue.severity is MLBHRLeakageAuditSeverity.WARNING for issue in ordered
    )
    info_count = sum(
        issue.severity is MLBHRLeakageAuditSeverity.INFO for issue in ordered
    )
    return MLBHRLeakageAuditReport(
        row_count=len(materialized),
        checked_at=_checked_at_text(checked_at),
        issue_count=len(ordered),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        passed=error_count == 0,
        issues=ordered,
    )


def audit_report_to_dict(report: MLBHRLeakageAuditReport) -> dict[str, object]:
    """Return a stable JSON-ready report mapping."""

    if not isinstance(report, MLBHRLeakageAuditReport):
        raise TypeError("report must be an MLBHRLeakageAuditReport")
    return {
        "sport": report.sport,
        "league": report.league,
        "market_type": report.market_type,
        "row_count": report.row_count,
        "checked_at": report.checked_at,
        "issue_count": report.issue_count,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "info_count": report.info_count,
        "passed": report.passed,
        "issues": [
            {
                "issue_id": issue.issue_id,
                "row_id": issue.row_id,
                "game_id": issue.game_id,
                "player_id": issue.player_id,
                "severity": issue.severity.value,
                "category": issue.category,
                "message": issue.message,
                "field_name": issue.field_name,
                "expected": issue.expected,
                "actual": issue.actual,
                "recommended_fix": issue.recommended_fix,
            }
            for issue in report.issues
        ],
        "eligible_for_betting": report.eligible_for_betting,
        "kelly_eligible": report.kelly_eligible,
        "approval_status": report.approval_status,
    }


def audit_report_to_json(report: MLBHRLeakageAuditReport) -> str:
    """Serialize a report deterministically for a fixed checked_at value."""

    return json.dumps(
        audit_report_to_dict(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def write_audit_report_json(
    report: MLBHRLeakageAuditReport,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write an audit report only to an explicit path, refusing overwrite by default."""

    destination = Path(path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise MLBHRLeakageAuditError(
            f"output parent directory does not exist: {destination.parent}"
        )
    if destination.exists() and not overwrite:
        raise MLBHRLeakageAuditError(f"output file already exists: {destination}")
    destination.write_text(audit_report_to_json(report) + "\n", encoding="utf-8")
    return destination


__all__ = [
    "MLBHRLeakageAuditError",
    "MLBHRLeakageAuditIssue",
    "MLBHRLeakageAuditReport",
    "MLBHRLeakageAuditSeverity",
    "audit_hr_batter_game_row",
    "audit_hr_batter_game_rows",
    "audit_report_to_dict",
    "audit_report_to_json",
    "write_audit_report_json",
]
