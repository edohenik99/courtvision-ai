"""Historical dataset readiness reporting for local MLB HR row builds.

The report is a default-deny research gate.  It summarizes row construction,
labels, context, local odds references, provenance, and the Phase 4C leakage
audit without training a model or changing any dataset row.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Final, Iterable, Mapping, Sequence

from courtvision.sports.mlb.training.hr_dataset_schema import (
    MLB_HR_MARKET_TYPE,
    MLB_LEAGUE,
    MLB_SPORT,
    NOT_APPROVED,
    MLBHRBatterGameRow,
)
from courtvision.sports.mlb.training.hr_leakage_audit import (
    MLBHRLeakageAuditReport,
    audit_hr_batter_game_rows,
)


MIN_RESEARCH_ROW_COUNT: Final = 1_000
MIN_CONTEXT_COVERAGE_RATE: Final = 0.80
MIN_ODDS_COVERAGE_RATE: Final = 0.80


class MLBHRDatasetReadinessError(ValueError):
    """Raised for an invalid readiness report or opt-in write."""


class MLBHRDatasetReadinessStatus(str, Enum):
    """Research-only readiness states, ordered by the checks they support."""

    NOT_READY = "NOT_READY"
    READY_FOR_LARGER_HISTORICAL_BUILD = "READY_FOR_LARGER_HISTORICAL_BUILD"
    READY_FOR_TRAINING_RESEARCH = "READY_FOR_TRAINING_RESEARCH"
    READY_FOR_BACKTEST_RESEARCH = "READY_FOR_BACKTEST_RESEARCH"


class MLBHRDatasetReadinessSeverity(str, Enum):
    """Readiness issue severity."""

    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class MLBHRDatasetReadinessMetric:
    """One stable count or coverage measurement."""

    metric_id: str
    value: int
    total_count: int | None = None
    rate: float | None = None


@dataclass(frozen=True, slots=True)
class MLBHRDatasetReadinessIssue:
    """One actionable readiness finding."""

    issue_id: str
    severity: MLBHRDatasetReadinessSeverity
    category: str
    message: str
    count: int


@dataclass(frozen=True, slots=True)
class MLBHRDatasetReadinessReport:
    """Immutable, default-deny readiness summary for historical research."""

    generated_at: str
    dataset_row_count: int
    label_available_count: int
    hr_positive_count: int
    hr_negative_count: int
    game_completed_count: int
    training_eligible_count: int
    backtest_eligible_count: int
    weather_attached_count: int
    ballpark_attached_count: int
    odds_attached_count: int
    full_context_count: int
    full_context_plus_odds_count: int
    duplicate_row_id_count: int
    missing_game_id_count: int
    missing_player_id_count: int
    missing_game_date_count: int
    missing_label_count: int
    missing_weather_count: int
    missing_ballpark_count: int
    missing_odds_count: int
    leakage_error_count: int
    leakage_warning_count: int
    leakage_passed: bool
    source_manifest_count: int
    readiness_status: str
    readiness_score: int
    blocking_issue_count: int
    warning_issue_count: int
    issues: tuple[MLBHRDatasetReadinessIssue, ...] = field(default_factory=tuple)
    metrics: tuple[MLBHRDatasetReadinessMetric, ...] = field(default_factory=tuple)
    duplicate_player_game_count: int = 0
    missing_row_id_count: int = 0
    missing_schema_version_count: int = 0
    labels_in_incomplete_game_count: int = 0
    invalid_game_date_count: int = 0
    missing_team_count: int = 0
    missing_opponent_count: int = 0
    missing_venue_count: int = 0
    unmatched_odds_count: int = 0
    odds_timestamp_unsafe_count: int = 0
    source_checksum_count: int = 0
    source_row_count_count: int = 0
    label_coverage_rate: float = 0.0
    weather_coverage_rate: float = 0.0
    ballpark_coverage_rate: float = 0.0
    full_context_rate: float = 0.0
    odds_coverage_rate: float = 0.0
    full_context_plus_odds_rate: float = 0.0
    sport: str = MLB_SPORT
    league: str = MLB_LEAGUE
    market_type: str = MLB_HR_MARKET_TYPE
    approval_status: str = NOT_APPROVED
    eligible_for_betting: bool = False
    kelly_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "metrics", tuple(self.metrics))
        valid_statuses = {status.value for status in MLBHRDatasetReadinessStatus}
        if self.readiness_status not in valid_statuses:
            raise MLBHRDatasetReadinessError("unsupported readiness status")
        if not 0 <= self.readiness_score <= 100:
            raise MLBHRDatasetReadinessError("readiness_score must be from 0 to 100")
        if self.approval_status != NOT_APPROVED:
            raise MLBHRDatasetReadinessError("readiness must remain not_approved")
        if self.eligible_for_betting is not False or self.kelly_eligible is not False:
            raise MLBHRDatasetReadinessError("readiness must remain default-deny")


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
def _values(row: MLBHRBatterGameRow | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(row, MLBHRBatterGameRow):
        return {item.name: getattr(row, item.name) for item in fields(row)}
    if isinstance(row, Mapping):
        return row
    raise TypeError("rows must contain MLBHRBatterGameRow instances or mappings")


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _attached(values: Mapping[str, object], fields_: Sequence[str]) -> bool:
    return any(not _missing(values.get(name)) for name in fields_)


def _odds_attached(values: Mapping[str, object]) -> bool:
    return any(
        not _missing(values.get(name)) for name in ("american_odds", "decimal_odds")
    )


def _valid_date(value: object) -> bool:
    if isinstance(value, datetime):
        return False
    if isinstance(value, date):
        return True
    if isinstance(value, str):
        try:
            date.fromisoformat(value.strip())
        except ValueError:
            return False
        return True
    return False


def _utc_text(value: object) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise MLBHRDatasetReadinessError("generated_at must be an ISO datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _object_value(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _manifest_entries(source_manifest: object | None) -> tuple[Mapping[str, object], ...]:
    def entry(value: object) -> Mapping[str, object] | None:
        if isinstance(value, Mapping):
            return value
        if hasattr(value, "source_name"):
            return {
                "source_name": getattr(value, "source_name", None),
                "sha256": getattr(value, "checksum", None),
                "parsed_row_count": getattr(value, "row_count", None),
            }
        return None

    if source_manifest is None:
        return ()
    sources = _object_value(source_manifest, "sources")
    if sources is None and isinstance(source_manifest, Sequence) and not isinstance(
        source_manifest, (str, bytes)
    ):
        sources = source_manifest
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
        return tuple(converted for item in sources if (converted := entry(item)))
    single = entry(source_manifest)
    return (single,) if single is not None else ()


def _pairing_count(pairing_summary: object | None, *names: str) -> int:
    for name in names:
        value = _object_value(pairing_summary, name)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
    return 0


def _metadata_time(metadata: object | None, audit: MLBHRLeakageAuditReport) -> object:
    return _object_value(metadata, "generated_at", audit.checked_at)


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def build_hr_dataset_readiness_report(
    rows: Iterable[MLBHRBatterGameRow | Mapping[str, object]],
    metadata: object | None = None,
    audit_report: MLBHRLeakageAuditReport | None = None,
    source_manifest: object | None = None,
    pairing_summary: object | None = None,
) -> MLBHRDatasetReadinessReport:
    """Evaluate a materialized local-file dataset without writing or training."""

    materialized = tuple(rows)
    row_values = tuple(_values(row) for row in materialized)
    audit = audit_report or audit_hr_batter_game_rows(materialized)
    if not isinstance(audit, MLBHRLeakageAuditReport):
        raise TypeError("audit_report must be an MLBHRLeakageAuditReport")

    row_count = len(row_values)
    label_count = sum(values.get("label_available") is True for values in row_values)
    positive_count = sum(
        values.get("label_available") is True and values.get("hit_hr_today") is True
        for values in row_values
    )
    negative_count = sum(
        values.get("label_available") is True and values.get("hit_hr_today") is False
        for values in row_values
    )
    completed_count = sum(values.get("game_completed") is True for values in row_values)
    training_count = sum(
        values.get("eligible_for_training") is True for values in row_values
    )
    backtest_count = sum(
        values.get("eligible_for_backtest") is True for values in row_values
    )
    weather_flags = tuple(_attached(values, _WEATHER_FIELDS) for values in row_values)
    ballpark_flags = tuple(_attached(values, _BALLPARK_FIELDS) for values in row_values)
    odds_flags = tuple(_odds_attached(values) for values in row_values)
    weather_count = sum(weather_flags)
    ballpark_count = sum(ballpark_flags)
    odds_count = sum(odds_flags)
    full_context_count = sum(
        weather and ballpark
        for weather, ballpark in zip(weather_flags, ballpark_flags, strict=True)
    )
    full_context_odds_count = sum(
        weather and ballpark and odds
        for weather, ballpark, odds in zip(
            weather_flags, ballpark_flags, odds_flags, strict=True
        )
    )

    row_ids = [str(values.get("row_id", "")).strip() for values in row_values]
    nonempty_row_ids = [value for value in row_ids if value]
    duplicate_row_ids = len(nonempty_row_ids) - len(set(nonempty_row_ids))
    player_games = [
        (str(values.get("game_id")).strip(), str(values.get("player_id")).strip())
        for values in row_values
        if not _missing(values.get("game_id")) and not _missing(values.get("player_id"))
    ]
    duplicate_player_games = len(player_games) - len(set(player_games))
    missing_row_ids = sum(not value for value in row_ids)
    missing_game_ids = sum(_missing(values.get("game_id")) for values in row_values)
    missing_player_ids = sum(_missing(values.get("player_id")) for values in row_values)
    missing_dates = sum(_missing(values.get("game_date")) for values in row_values)
    invalid_dates = sum(
        not _missing(values.get("game_date")) and not _valid_date(values.get("game_date"))
        for values in row_values
    )
    missing_schema = sum(_missing(values.get("schema_version")) for values in row_values)
    missing_teams = sum(_missing(values.get("team")) for values in row_values)
    missing_opponents = sum(_missing(values.get("opponent")) for values in row_values)
    missing_venues = sum(_missing(values.get("venue_name")) for values in row_values)
    labels_in_incomplete_games = sum(
        values.get("label_available") is True and values.get("game_completed") is not True
        for values in row_values
    )
    unsafe_odds_timestamps = sum(
        odds and values.get("odds_is_fresh_for_pregame") is False
        for odds, values in zip(odds_flags, row_values, strict=True)
    )

    entries = _manifest_entries(source_manifest)
    fallback_manifest_ids: set[str] = set()
    for values in row_values:
        identifiers = values.get("source_manifest_ids") or ()
        if isinstance(identifiers, str):
            identifiers = (identifiers,)
        fallback_manifest_ids.update(
            str(identifier).strip()
            for identifier in identifiers
            if str(identifier).strip()
        )
    metadata_manifest_ids = _object_value(metadata, "source_manifest_ids", ()) or ()
    if isinstance(metadata_manifest_ids, str):
        metadata_manifest_ids = (metadata_manifest_ids,)
    fallback_manifest_ids.update(
        str(identifier).strip()
        for identifier in metadata_manifest_ids
        if str(identifier).strip()
    )
    manifest_count = len(entries) if entries else len(fallback_manifest_ids)
    checksum_count = sum(
        not _missing(entry.get("sha256", entry.get("checksum"))) for entry in entries
    )
    source_row_count_count = sum(
        isinstance(entry.get("parsed_row_count", entry.get("row_count")), int)
        for entry in entries
    )
    unmatched_odds_count = _pairing_count(
        pairing_summary, "unmatched_odds_rows", "unmatched_odds_count"
    )
    unsafe_odds_timestamps = max(
        unsafe_odds_timestamps,
        _pairing_count(
            pairing_summary,
            "odds_timestamp_after_event_start_time",
            "odds_timestamp_unsafe_count",
        ),
    )

    issues: list[MLBHRDatasetReadinessIssue] = []

    def add(
        issue_id: str,
        severity: MLBHRDatasetReadinessSeverity,
        category: str,
        message: str,
        count: int,
    ) -> None:
        if count:
            issues.append(
                MLBHRDatasetReadinessIssue(
                    issue_id=issue_id,
                    severity=severity,
                    category=category,
                    message=message,
                    count=count,
                )
            )

    add(
        "dataset.empty",
        MLBHRDatasetReadinessSeverity.BLOCKING,
        "dataset",
        "No batter-game rows were built.",
        int(row_count == 0),
    )
    for issue_id, message, count in (
        ("identity.row_id_missing", "Required row IDs are missing.", missing_row_ids),
        ("identity.game_id_missing", "Required game IDs are missing.", missing_game_ids),
        ("identity.player_id_missing", "Required player IDs are missing.", missing_player_ids),
        ("identity.game_date_missing", "Required game dates are missing.", missing_dates),
        ("identity.game_date_invalid", "Game dates are invalid.", invalid_dates),
        ("identity.schema_version_missing", "Schema versions are missing.", missing_schema),
        ("duplicate.row_id", "Duplicate row IDs were detected.", duplicate_row_ids),
        (
            "duplicate.player_game",
            "Duplicate player-game identities were detected.",
            duplicate_player_games,
        ),
        ("label.missing", "Rows without available labels were detected.", row_count - label_count),
        ("leakage.errors", "The Phase 4C leakage audit reported errors.", audit.error_count),
    ):
        add(
            issue_id,
            MLBHRDatasetReadinessSeverity.BLOCKING,
            "leakage" if issue_id.startswith("leakage") else issue_id.split(".")[0],
            message,
            count,
        )
    add(
        "label.hr_positive_missing",
        MLBHRDatasetReadinessSeverity.BLOCKING,
        "label",
        "No HR-positive labels are present.",
        int(row_count > 0 and positive_count == 0),
    )
    add(
        "label.hr_negative_missing",
        MLBHRDatasetReadinessSeverity.BLOCKING,
        "label",
        "No HR-negative labels are present.",
        int(row_count > 0 and negative_count == 0),
    )

    for issue_id, category, message, count in (
        (
            "sample.too_small",
            "sample",
            "Dataset is too small for real research runs.",
            int(0 < row_count < MIN_RESEARCH_ROW_COUNT),
        ),
        (
            "label.incomplete_game",
            "label",
            "Available labels are attached to games not confirmed completed.",
            labels_in_incomplete_games,
        ),
        (
            "context.weather_missing",
            "context",
            "Rows are missing weather context.",
            row_count - weather_count,
        ),
        (
            "context.ballpark_missing",
            "context",
            "Rows are missing ballpark context.",
            row_count - ballpark_count,
        ),
        ("context.team_missing", "context", "Rows are missing team identity.", missing_teams),
        (
            "context.opponent_missing",
            "context",
            "Rows are missing opponent identity.",
            missing_opponents,
        ),
        ("context.venue_missing", "context", "Rows are missing venue identity.", missing_venues),
        (
            "odds.missing",
            "odds",
            "Rows are missing local odds market references.",
            row_count - odds_count,
        ),
        ("odds.unmatched", "odds", "Local odds rows were not matched.", unmatched_odds_count),
        (
            "odds.timestamp_unsafe",
            "odds",
            "Odds timestamps are not safely pregame.",
            unsafe_odds_timestamps,
        ),
        (
            "provenance.manifest_missing",
            "provenance",
            "No source manifests are recorded.",
            int(manifest_count == 0),
        ),
        (
            "provenance.checksum_missing",
            "provenance",
            "Some source checksums are unavailable.",
            max(0, len(entries) - checksum_count),
        ),
        (
            "provenance.row_count_missing",
            "provenance",
            "Some source row counts are unavailable.",
            max(0, len(entries) - source_row_count_count),
        ),
    ):
        add(
            issue_id,
            MLBHRDatasetReadinessSeverity.WARNING,
            category,
            message,
            count,
        )

    ordered_issues = tuple(
        sorted(issues, key=lambda issue: (issue.severity.value, issue.issue_id))
    )
    blocking_count = sum(
        issue.severity is MLBHRDatasetReadinessSeverity.BLOCKING
        for issue in ordered_issues
    )
    warning_count = sum(
        issue.severity is MLBHRDatasetReadinessSeverity.WARNING
        for issue in ordered_issues
    )
    weather_rate = _rate(weather_count, row_count)
    ballpark_rate = _rate(ballpark_count, row_count)
    context_rate = _rate(full_context_count, row_count)
    odds_rate = _rate(odds_count, row_count)
    context_odds_rate = _rate(full_context_odds_count, row_count)
    completed_rate = _rate(completed_count, row_count)
    training_rate = _rate(training_count, row_count)
    backtest_rate = _rate(backtest_count, row_count)

    score = 100
    score -= min(60, blocking_count * 20)
    if 0 < row_count < MIN_RESEARCH_ROW_COUNT:
        score -= 25
    score -= round((1 - weather_rate) * 8)
    score -= round((1 - ballpark_rate) * 8)
    score -= round((1 - odds_rate) * 8)
    score -= min(10, warning_count)
    score = max(0, min(100, score))

    if blocking_count:
        status = MLBHRDatasetReadinessStatus.NOT_READY
    elif row_count < MIN_RESEARCH_ROW_COUNT:
        status = MLBHRDatasetReadinessStatus.READY_FOR_LARGER_HISTORICAL_BUILD
    elif (
        completed_rate < 0.95
        or training_rate < 0.95
        or manifest_count == 0
        or missing_teams
        or missing_opponents
        or missing_venues
    ):
        status = MLBHRDatasetReadinessStatus.READY_FOR_LARGER_HISTORICAL_BUILD
    elif (
        context_odds_rate >= MIN_CONTEXT_COVERAGE_RATE
        and odds_rate >= MIN_ODDS_COVERAGE_RATE
        and backtest_rate >= 0.95
        and unsafe_odds_timestamps == 0
    ):
        status = MLBHRDatasetReadinessStatus.READY_FOR_BACKTEST_RESEARCH
    elif context_rate >= MIN_CONTEXT_COVERAGE_RATE:
        status = MLBHRDatasetReadinessStatus.READY_FOR_TRAINING_RESEARCH
    else:
        status = MLBHRDatasetReadinessStatus.READY_FOR_LARGER_HISTORICAL_BUILD

    metric_values = (
        ("label_coverage", label_count),
        ("weather_coverage", weather_count),
        ("ballpark_coverage", ballpark_count),
        ("full_context_coverage", full_context_count),
        ("odds_coverage", odds_count),
        ("full_context_plus_odds_coverage", full_context_odds_count),
        ("completed_game_coverage", completed_count),
        ("training_eligible_coverage", training_count),
        ("backtest_eligible_coverage", backtest_count),
    )
    metrics = tuple(
        MLBHRDatasetReadinessMetric(
            metric_id=name,
            value=value,
            total_count=row_count,
            rate=_rate(value, row_count),
        )
        for name, value in metric_values
    )

    return MLBHRDatasetReadinessReport(
        generated_at=_utc_text(_metadata_time(metadata, audit)),
        dataset_row_count=row_count,
        label_available_count=label_count,
        hr_positive_count=positive_count,
        hr_negative_count=negative_count,
        game_completed_count=completed_count,
        training_eligible_count=training_count,
        backtest_eligible_count=backtest_count,
        weather_attached_count=weather_count,
        ballpark_attached_count=ballpark_count,
        odds_attached_count=odds_count,
        full_context_count=full_context_count,
        full_context_plus_odds_count=full_context_odds_count,
        duplicate_row_id_count=duplicate_row_ids,
        missing_game_id_count=missing_game_ids,
        missing_player_id_count=missing_player_ids,
        missing_game_date_count=missing_dates,
        missing_label_count=row_count - label_count,
        missing_weather_count=row_count - weather_count,
        missing_ballpark_count=row_count - ballpark_count,
        missing_odds_count=row_count - odds_count,
        leakage_error_count=audit.error_count,
        leakage_warning_count=audit.warning_count,
        leakage_passed=audit.passed,
        source_manifest_count=manifest_count,
        readiness_status=status.value,
        readiness_score=score,
        blocking_issue_count=blocking_count,
        warning_issue_count=warning_count,
        issues=ordered_issues,
        metrics=metrics,
        duplicate_player_game_count=duplicate_player_games,
        missing_row_id_count=missing_row_ids,
        missing_schema_version_count=missing_schema,
        labels_in_incomplete_game_count=labels_in_incomplete_games,
        invalid_game_date_count=invalid_dates,
        missing_team_count=missing_teams,
        missing_opponent_count=missing_opponents,
        missing_venue_count=missing_venues,
        unmatched_odds_count=unmatched_odds_count,
        odds_timestamp_unsafe_count=unsafe_odds_timestamps,
        source_checksum_count=checksum_count,
        source_row_count_count=source_row_count_count,
        label_coverage_rate=_rate(label_count, row_count),
        weather_coverage_rate=weather_rate,
        ballpark_coverage_rate=ballpark_rate,
        full_context_rate=context_rate,
        odds_coverage_rate=odds_rate,
        full_context_plus_odds_rate=context_odds_rate,
    )


def readiness_report_to_dict(
    report: MLBHRDatasetReadinessReport,
) -> dict[str, object]:
    """Return a deterministic JSON-ready report mapping."""

    if not isinstance(report, MLBHRDatasetReadinessReport):
        raise TypeError("report must be an MLBHRDatasetReadinessReport")
    result: dict[str, object] = {}
    for item in fields(report):
        value = getattr(report, item.name)
        if item.name == "issues":
            result[item.name] = [
                {
                    "issue_id": issue.issue_id,
                    "severity": issue.severity.value,
                    "category": issue.category,
                    "message": issue.message,
                    "count": issue.count,
                }
                for issue in value
            ]
        elif item.name == "metrics":
            result[item.name] = [
                {
                    "metric_id": metric.metric_id,
                    "value": metric.value,
                    "total_count": metric.total_count,
                    "rate": metric.rate,
                }
                for metric in value
            ]
        else:
            result[item.name] = value
    return result


def readiness_report_to_json(report: MLBHRDatasetReadinessReport) -> str:
    """Serialize a readiness report deterministically."""

    return json.dumps(
        readiness_report_to_dict(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def readiness_report_to_txt(report: MLBHRDatasetReadinessReport) -> str:
    """Render a compact, human-facing research-only readiness summary."""

    lines = (
        "CourtVision MLB HR historical dataset readiness report",
        "historical research only",
        "not production approved",
        "default-deny",
        f"readiness_status = {report.readiness_status}",
        f"readiness_score = {report.readiness_score}",
        f"blocking_issue_count = {report.blocking_issue_count}",
        f"warning_issue_count = {report.warning_issue_count}",
        f"dataset_row_count = {report.dataset_row_count}",
        f"label_available_count = {report.label_available_count}",
        f"full_context_count = {report.full_context_count}",
        f"odds_attached_count = {report.odds_attached_count}",
        f"leakage_error_count = {report.leakage_error_count}",
        f"leakage_warning_count = {report.leakage_warning_count}",
        f"approval_status = {report.approval_status}",
    )
    issue_lines = tuple(
        f"{issue.severity.value}: {issue.issue_id} ({issue.count}) - {issue.message}"
        for issue in report.issues
    )
    return "\n".join((*lines, *issue_lines, ""))


def _write_text(path: str | Path, payload: str, *, overwrite: bool) -> Path:
    destination = Path(path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise MLBHRDatasetReadinessError(
            f"output parent directory does not exist: {destination.parent}"
        )
    if destination.exists() and not overwrite:
        raise MLBHRDatasetReadinessError(f"output file already exists: {destination}")
    destination.write_text(payload, encoding="utf-8", newline="\n")
    return destination


def write_readiness_report_json(
    report: MLBHRDatasetReadinessReport,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write JSON only to an explicitly supplied path."""

    return _write_text(
        path, readiness_report_to_json(report) + "\n", overwrite=overwrite
    )


def write_readiness_report_txt(
    report: MLBHRDatasetReadinessReport,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write the text summary only to an explicitly supplied path."""

    return _write_text(path, readiness_report_to_txt(report), overwrite=overwrite)


__all__ = [
    "MIN_CONTEXT_COVERAGE_RATE",
    "MIN_ODDS_COVERAGE_RATE",
    "MIN_RESEARCH_ROW_COUNT",
    "MLBHRDatasetReadinessError",
    "MLBHRDatasetReadinessIssue",
    "MLBHRDatasetReadinessMetric",
    "MLBHRDatasetReadinessReport",
    "MLBHRDatasetReadinessSeverity",
    "MLBHRDatasetReadinessStatus",
    "build_hr_dataset_readiness_report",
    "readiness_report_to_dict",
    "readiness_report_to_json",
    "readiness_report_to_txt",
    "write_readiness_report_json",
    "write_readiness_report_txt",
]
