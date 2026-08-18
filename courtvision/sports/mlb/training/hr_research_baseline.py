"""Research-only MLB HR prediction baseline.

This module builds a deterministic live-archive feature table, trains a small
auditable logistic-regression baseline, writes immutable model/prediction
artifacts, and keeps prospective settlements append-only.  It does not fetch
live data, grade bankroll-facing history, change existing collectors, or grant
betting approval.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib import metadata as importlib_metadata
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Final, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from courtvision.sports.mlb.player_name_normalization import (
    normalize_mlb_player_name,
)
from courtvision.prediction import (
    CallbackPredictionPublisher,
    DisabledPredictionLifecycle,
    EnginePrediction,
    NoArtifactPublisher,
    PredictionApplicationService,
    PredictionEngineRegistry,
    PredictionRequest,
    ShadowPredictionLifecycle,
)
from courtvision.prediction.publication import (
    current_publication_metadata,
    publish_csv_rows,
    publish_json,
)


RESEARCH_ONLY_LABEL: Final = "RESEARCH ONLY - NOT A VALIDATED BETTING PICK"
APPROVAL_STATUS: Final = "not_approved"
FEATURE_SCHEMA_VERSION_V1: Final = "mlb-hr-research-feature-v1"
FEATURE_SCHEMA_VERSION: Final = "mlb-hr-research-feature-v2"
MODEL_BUNDLE_SCHEMA_VERSION: Final = "mlb-hr-logistic-baseline-bundle-v1"
PREDICTION_SCHEMA_VERSION: Final = "mlb-hr-research-predictions-v2"
LEDGER_SCHEMA_VERSION: Final = "mlb-hr-prospective-ledger-v1"
IDENTITY_CACHE_SCHEMA_VERSION: Final = "mlb-hr-player-identity-cache-v1"
DEFAULT_IDENTITY_MAPPING_VERSION: Final = "research-identity-v1"
CLOSING_LINE_SCHEMA_VERSION: Final = "mlb-hr-closing-line-evidence-v1"
DAILY_RUN_SCHEMA_VERSION: Final = "mlb-hr-daily-research-run-v2"
PROSPECTIVE_REPORT_SCHEMA_VERSION: Final = "mlb-hr-prospective-trial-report-v1"
DEFAULT_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_LIVE_HR_DIR: Final = (
    DEFAULT_REPOSITORY_ROOT / "data" / "theoddsapi" / "live_hr_snapshots"
)
DEFAULT_ODDS_CSV: Final = DEFAULT_LIVE_HR_DIR / "live_hr_props_master.csv"
DEFAULT_RESULTS_CSV: Final = DEFAULT_LIVE_HR_DIR / "live_hr_results.csv"
DEFAULT_ARTIFACT_ROOT: Final = (
    DEFAULT_REPOSITORY_ROOT / "outputs" / "research" / "mlb_hr_baseline"
)
DEFAULT_MODEL_ROOT: Final = DEFAULT_ARTIFACT_ROOT / "models"
COURTVISION_OPERATING_TIMEZONE_NAME: Final = "America/Toronto"
COURTVISION_OPERATING_TIMEZONE: Final = ZoneInfo(COURTVISION_OPERATING_TIMEZONE_NAME)
COMPATIBLE_FEATURE_SCHEMA_VERSIONS: Final = frozenset(
    {FEATURE_SCHEMA_VERSION_V1, FEATURE_SCHEMA_VERSION}
)
EVENT_REGULAR_SEASON_ELIGIBLE: Final = "regular_season_eligible"
EVENT_SPECIAL_QUARANTINED: Final = "special_event_quarantined"
EVENT_TYPE_UNKNOWN: Final = "event_type_unknown"
EVENT_MANUAL_REVIEW_REQUIRED: Final = "manual_review_required"
SPECIAL_EVENT_EXCLUSION_REASON: Final = "special_event_out_of_distribution"
EVENT_TYPE_UNKNOWN_EXCLUSION_REASON: Final = "event_type_unknown"
REGULAR_SEASON_TEAM_ALLOWLIST_RULE: Final = (
    "both_teams_must_match_current_mlb_club_allowlist"
)
SPECIAL_EVENT_TEAM_RULE: Final = "national_league_vs_american_league_team_names"
MLB_REGULAR_SEASON_TEAM_NAMES: Final = frozenset(
    name.casefold()
    for name in (
        "Arizona Diamondbacks",
        "Athletics",
        "Atlanta Braves",
        "Baltimore Orioles",
        "Boston Red Sox",
        "Chicago Cubs",
        "Chicago White Sox",
        "Cincinnati Reds",
        "Cleveland Guardians",
        "Colorado Rockies",
        "Detroit Tigers",
        "Houston Astros",
        "Kansas City Royals",
        "Los Angeles Angels",
        "Los Angeles Dodgers",
        "Miami Marlins",
        "Milwaukee Brewers",
        "Minnesota Twins",
        "New York Mets",
        "New York Yankees",
        "Oakland Athletics",
        "Philadelphia Phillies",
        "Pittsburgh Pirates",
        "San Diego Padres",
        "San Francisco Giants",
        "Seattle Mariners",
        "St. Louis Cardinals",
        "Tampa Bay Rays",
        "Texas Rangers",
        "Toronto Blue Jays",
        "Washington Nationals",
    )
)
SPECIAL_EVENT_TEAM_PAIRS: Final = frozenset(
    {
        frozenset({"american league", "national league"}),
    }
)

ODDS_REQUIRED_COLUMNS: Final = (
    "snapshot_time",
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "bookmaker_key",
    "bookmaker",
    "market",
    "player",
    "side",
    "price",
    "point",
)
RESULTS_REQUIRED_COLUMNS: Final = (
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
)
NON_GRADEABLE_STATUSES: Final = frozenset(
    {"void", "void_candidate", "manual_review_required"}
)
TRAINING_ELIGIBLE_STATUS: Final = "eligible_for_training"
PREDICTION_ELIGIBLE_STATUS: Final = "eligible_for_prediction"
SNAPSHOT_SELECTION_RULE: Final = (
    "latest_snapshot_strictly_before_game_start_per_event_normalized_player_market_point"
)
PREDICTION_SNAPSHOT_SELECTION_RULE: Final = (
    "latest_snapshot_at_or_before_prediction_timestamp_and_before_game_start"
)
BOOKMAKER_SELECTION_RULE: Final = (
    "best_available_decimal_odds_then_bookmaker_key_then_bookmaker_name"
)

NUMERIC_MODEL_FEATURES: Final = (
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "best_available_american_odds",
    "best_available_decimal_odds",
    "best_available_implied_probability",
    "bookmaker_count",
    "implied_probability_mean",
    "implied_probability_min",
    "implied_probability_max",
    "implied_probability_dispersion",
    "american_odds_min",
    "american_odds_max",
    "american_odds_dispersion",
    "hours_before_game",
)
CATEGORICAL_MODEL_FEATURES: Final = ("sportsbook",)
MODEL_REQUIRED_INPUT_COLUMNS: Final = (
    *NUMERIC_MODEL_FEATURES,
    *CATEGORICAL_MODEL_FEATURES,
)
DEFAULT_THRESHOLDS: Final = (0.05, 0.10, 0.15, 0.20, 0.25)

FEATURE_COLUMNS: Final = (
    "feature_row_id",
    "feature_schema_version",
    "research_label",
    "approval_status",
    "event_id",
    "game_date",
    "commence_time",
    "commence_time_utc",
    "game_date_utc",
    "game_date_operating",
    "operating_timezone",
    "prediction_timestamp",
    "snapshot_time",
    "player_id",
    "player_name",
    "normalized_player_name",
    "team",
    "opponent",
    "home_team",
    "away_team",
    "event_eligibility_status",
    "event_eligibility_reason",
    "event_eligibility_rule",
    "sportsbook",
    "sportsbook_name",
    "market_key",
    "side",
    "point",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "best_available_american_odds",
    "best_available_decimal_odds",
    "best_available_implied_probability",
    "bookmaker_count",
    "implied_probability_mean",
    "implied_probability_min",
    "implied_probability_max",
    "implied_probability_dispersion",
    "american_odds_min",
    "american_odds_max",
    "american_odds_dispersion",
    "hours_before_game",
    "snapshot_selection_rule",
    "bookmaker_selection_rule",
    "lineup_status",
    "eligibility_status",
    "exclusion_reason",
    "identity_match_status",
    "actual_home_runs",
    "hit_hr",
    "game_status",
    "result_reason",
    "label_available",
    "source_odds_path",
    "source_results_path",
    "source_odds_sha256",
    "source_results_sha256",
    "source_manifest_reference",
    "repository_commit_sha",
    "leakage_check_status",
    "eligible_for_betting",
    "kelly_eligible",
)
EXCLUSION_COLUMNS: Final = (
    "feature_row_id",
    "event_id",
    "game_date",
    "game_date_utc",
    "game_date_operating",
    "operating_timezone",
    "home_team",
    "away_team",
    "snapshot_time",
    "player_name",
    "normalized_player_name",
    "event_eligibility_status",
    "event_eligibility_reason",
    "event_eligibility_rule",
    "eligibility_status",
    "exclusion_reason",
    "game_status",
    "result_reason",
)
PREDICTION_COLUMNS: Final = (
    "prediction_id",
    "prediction_run_id",
    "prediction_schema_version",
    "research_label",
    "model_id",
    "model_version",
    "event_id",
    "game_date",
    "commence_time",
    "commence_time_utc",
    "game_date_utc",
    "game_date_operating",
    "operating_timezone",
    "player_id",
    "player_name",
    "normalized_player_name",
    "team",
    "opponent",
    "home_team",
    "away_team",
    "event_eligibility_status",
    "event_eligibility_reason",
    "event_eligibility_rule",
    "sportsbook",
    "sportsbook_name",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "model_probability",
    "probability_edge",
    "prediction_timestamp",
    "snapshot_time",
    "market_key",
    "side",
    "point",
    "lineup_status",
    "eligibility_status",
    "exclusion_reason",
    "source_manifest_reference",
    "feature_schema_version",
    "repository_commit_sha",
    "model_bundle_path",
    "source_odds_sha256",
)
LEDGER_COLUMNS: Final = (
    "ledger_schema_version",
    "ledger_record_id",
    "record_type",
    "prediction_id",
    "prediction_run_id",
    "model_id",
    "game_date",
    "event_id",
    "commence_time",
    "player_id",
    "player_name",
    "normalized_player_name",
    "sportsbook",
    "original_odds",
    "original_decimal_odds",
    "original_implied_probability",
    "model_probability",
    "prediction_timestamp",
    "prediction_artifact_sha256",
    "source_manifest_reference",
    "repository_commit_sha",
    "closing_odds",
    "closing_implied_probability",
    "closing_line_movement",
    "final_result",
    "grade",
    "unit_profit_loss",
    "settlement_timestamp",
    "settlement_source",
    "settlement_status",
    "manual_review_status",
    "integrity_status",
    "research_label",
)
IDENTITY_CACHE_COLUMNS: Final = (
    "cache_schema_version",
    "cache_record_id",
    "sportsbook_player_name",
    "normalized_player_name",
    "mlb_player_id",
    "canonical_mlb_name",
    "identity_status",
    "identity_method",
    "identity_source",
    "resolved_at",
    "reviewed_at",
    "review_status",
    "mapping_version",
    "conflict_reason",
)
CLOSING_LINE_COLUMNS: Final = (
    "closing_line_schema_version",
    "closing_record_id",
    "prediction_id",
    "prediction_run_id",
    "event_id",
    "commence_time",
    "sportsbook",
    "sportsbook_name",
    "normalized_player_name",
    "closing_status",
    "closing_method",
    "closing_source",
    "closing_snapshot_time",
    "closing_sportsbook",
    "closing_sportsbook_name",
    "closing_american_odds",
    "closing_decimal_odds",
    "closing_implied_probability",
    "consensus_bookmaker_count",
    "consensus_implied_probability",
    "original_american_odds",
    "original_implied_probability",
    "closing_line_movement",
    "closing_probability_movement",
    "captured_at",
    "official_evidence_allowed",
    "integrity_status",
    "research_label",
)


class MLBHRResearchBaselineError(ValueError):
    """Raised when a research baseline step must fail closed."""


@dataclass(frozen=True, slots=True)
class FeatureBuildResult:
    """Materialized feature table and deterministic audit manifest."""

    rows: tuple[dict[str, str], ...]
    exclusions: tuple[dict[str, str], ...]
    manifest: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "exclusions", tuple(self.exclusions))
        object.__setattr__(self, "manifest", dict(self.manifest))


@dataclass(frozen=True, slots=True)
class ModelBundle:
    """Validated model bundle loaded from immutable JSON artifacts."""

    bundle_dir: Path
    metadata: Mapping[str, object]
    model: Mapping[str, object]

    @property
    def model_id(self) -> str:
        return str(self.metadata["model_id"])

    @property
    def model_version(self) -> str:
        return str(self.metadata["model_version"])


@dataclass(frozen=True, slots=True)
class TrainingResult:
    """Paths and metadata for a written model bundle."""

    model_id: str
    bundle_dir: Path
    metadata: Mapping[str, object]
    metrics: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PredictionRunResult:
    """Daily research prediction artifact result."""

    prediction_run_id: str
    predictions: tuple[dict[str, str], ...]
    exclusions: tuple[dict[str, str], ...]
    manifest: Mapping[str, object]
    output_dir: Path | None = None
    application_status: str | None = None
    lifecycle_status: str | None = None
    application_manifest_path: Path | None = None
    exclusion_reasons: Mapping[str, int] = field(default_factory=dict)
    input_diagnostics: Mapping[str, int] = field(default_factory=dict)
    artifact_paths: Mapping[str, str] = field(default_factory=dict)
    resolved_model_dir: Path | None = None
    resolved_odds_csv: Path | None = None
    resolved_output_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class LedgerAppendResult:
    """Append-only ledger write summary."""

    ledger_path: Path
    appended_rows: int
    skipped_rows: int = 0


@dataclass(frozen=True, slots=True)
class SettlementResult:
    """Append-only settlement summary."""

    ledger_path: Path
    appended_settlements: int
    pending_predictions: int
    skipped_existing_settlements: int
    conflicting_settlements: int = 0


@dataclass(frozen=True, slots=True)
class IdentityResolutionResult:
    """Deterministic player identity resolution report."""

    records: tuple[dict[str, str], ...]
    report: Mapping[str, object]
    cache_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "report", dict(self.report))


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Prediction artifact integrity check result."""

    passed: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "summary", dict(self.summary))


@dataclass(frozen=True, slots=True)
class ClosingLineCaptureResult:
    """Append-only closing-line evidence capture summary."""

    rows: tuple[dict[str, str], ...]
    output_path: Path | None
    report: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "report", dict(self.report))


@dataclass(frozen=True, slots=True)
class DailyResearchRunResult:
    """Manual daily research runner summary."""

    run_id: str
    status: str
    output_dir: Path | None
    summary: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", dict(self.summary))


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _read_csv(
    path: str | Path,
    *,
    required_columns: Sequence[str],
    label: str,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MLBHRResearchBaselineError(f"{label} CSV does not exist: {source}")
    try:
        with source.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = tuple(reader.fieldnames or ())
            missing = [name for name in required_columns if name not in columns]
            if missing:
                raise MLBHRResearchBaselineError(
                    f"{label} CSV missing required columns: {', '.join(missing)}"
                )
            rows: list[dict[str, str]] = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise MLBHRResearchBaselineError(
                        f"{label} CSV row {row_number} has more values than columns"
                    )
                clean_row = {column: _clean(row.get(column)) for column in columns}
                clean_row["__row_number"] = str(row_number)
                rows.append(clean_row)
    except OSError as exc:
        raise MLBHRResearchBaselineError(
            f"could not read {label} CSV {source}: {exc}"
        ) from exc
    return columns, tuple(rows)


def _write_csv_create_once(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> Path:
    if path.exists():
        raise MLBHRResearchBaselineError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
    except OSError as exc:
        raise MLBHRResearchBaselineError(f"could not write CSV {path}: {exc}") from exc
    return path


def _write_json_create_once(path: Path, payload: Mapping[str, object]) -> Path:
    if path.exists():
        raise MLBHRResearchBaselineError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as exc:
        raise MLBHRResearchBaselineError(f"could not write JSON {path}: {exc}") from exc
    return path


def _write_text_create_once(path: Path, payload: str) -> Path:
    if path.exists():
        raise MLBHRResearchBaselineError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(payload, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise MLBHRResearchBaselineError(f"could not write text {path}: {exc}") from exc
    return path


def _file_sha256(path: str | Path) -> str:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        return ""
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(*parts: object, length: int | None = None) -> str:
    digest = hashlib.sha256(
        "|".join(_clean(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return digest[:length] if length else digest


def _parse_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
        text = value.isoformat()
    else:
        text = _clean(value)
        if not text:
            raise MLBHRResearchBaselineError(f"{field_name} is required")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MLBHRResearchBaselineError(
                f"{field_name} must be an ISO datetime: {text!r}"
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLBHRResearchBaselineError(
            f"{field_name} must include an explicit timezone offset: {text!r}"
        )
    return parsed.astimezone(timezone.utc)


def courtvision_operating_date(timestamp: datetime) -> date:
    """Return the CourtVision Toronto operating date for an aware timestamp."""

    parsed = _parse_datetime(timestamp, "timestamp")
    return parsed.astimezone(COURTVISION_OPERATING_TIMEZONE).date()


def _target_operating_date_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise MLBHRResearchBaselineError(
            f"target_date must be YYYY-MM-DD: {text!r}"
        ) from exc
    return parsed.isoformat()


def _coerce_datetime_utc(
    value: datetime | str | None,
    field_name: str,
    *,
    default_now: bool = True,
) -> datetime:
    if value is None:
        if default_now:
            return _utc_now()
        raise MLBHRResearchBaselineError(f"{field_name} is required")
    return _parse_datetime(value, field_name)


def _event_eligibility_from_row(row: Mapping[str, object]) -> tuple[str, str, str]:
    home_team = _clean(row.get("home_team"))
    away_team = _clean(row.get("away_team"))
    team_pair = frozenset({home_team.casefold(), away_team.casefold()})
    event_type = _clean(
        row.get("event_type") or row.get("game_type") or row.get("event_category")
    ).casefold()

    if event_type:
        if event_type in {"regular", "regular_season", "regular season"}:
            return (
                EVENT_REGULAR_SEASON_ELIGIBLE,
                "",
                "authoritative_event_type_regular_season",
            )
        if event_type in {
            "all_star",
            "all-star",
            "all star",
            "exhibition",
            "special_event",
            "special event",
        }:
            return (
                EVENT_SPECIAL_QUARANTINED,
                SPECIAL_EVENT_EXCLUSION_REASON,
                "authoritative_event_type_special_event",
            )
        return (
            EVENT_MANUAL_REVIEW_REQUIRED,
            EVENT_TYPE_UNKNOWN_EXCLUSION_REASON,
            "authoritative_event_type_unrecognized",
        )

    if team_pair in SPECIAL_EVENT_TEAM_PAIRS:
        return (
            EVENT_SPECIAL_QUARANTINED,
            SPECIAL_EVENT_EXCLUSION_REASON,
            SPECIAL_EVENT_TEAM_RULE,
        )
    if (
        home_team
        and away_team
        and home_team.casefold() in MLB_REGULAR_SEASON_TEAM_NAMES
        and away_team.casefold() in MLB_REGULAR_SEASON_TEAM_NAMES
        and home_team.casefold() != away_team.casefold()
    ):
        return (EVENT_REGULAR_SEASON_ELIGIBLE, "", REGULAR_SEASON_TEAM_ALLOWLIST_RULE)
    return (
        EVENT_TYPE_UNKNOWN,
        EVENT_TYPE_UNKNOWN_EXCLUSION_REASON,
        "team_names_not_in_mlb_club_allowlist",
    )


def _event_eligibility_counts(rows: Sequence[Mapping[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = _clean(row.get("event_eligibility_status")) or EVENT_TYPE_UNKNOWN
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _manifest_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _manifest_int(mapping: Mapping[str, object], key: str) -> int:
    try:
        return int(mapping.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _manifest_dict(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    value = mapping.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _daily_prediction_summary_fields(
    prediction_result: PredictionRunResult,
) -> dict[str, object]:
    feature_manifest = _manifest_mapping(
        prediction_result.manifest.get("feature_manifest")
    )
    return {
        "source_row_count": _manifest_int(feature_manifest, "row_count"),
        "source_excluded_row_count": _manifest_int(
            feature_manifest, "excluded_row_count"
        ),
        "source_eligible_row_count": _manifest_int(
            feature_manifest, "eligible_row_count"
        ),
        "feature_dates": list(feature_manifest.get("dates", []))
        if isinstance(feature_manifest.get("dates"), list)
        else [],
        "exclusion_counts": _manifest_dict(feature_manifest, "exclusion_counts"),
        "event_eligibility_counts": _manifest_dict(
            feature_manifest, "event_eligibility_counts"
        ),
    }


def _normalize_feature_row(row: Mapping[str, str]) -> dict[str, str]:
    normalized = dict(row)
    commence_text = _clean(normalized.get("commence_time"))
    if commence_text:
        try:
            commence_time = _parse_datetime(commence_text, "commence_time")
        except MLBHRResearchBaselineError:
            commence_time = None
        if commence_time is not None:
            if not _clean(normalized.get("commence_time_utc")):
                normalized["commence_time_utc"] = _iso_z(commence_time)
            if not _clean(normalized.get("game_date_utc")):
                normalized["game_date_utc"] = commence_time.date().isoformat()
            if not _clean(normalized.get("game_date_operating")):
                normalized["game_date_operating"] = courtvision_operating_date(
                    commence_time
                ).isoformat()
            if not _clean(normalized.get("operating_timezone")):
                normalized["operating_timezone"] = COURTVISION_OPERATING_TIMEZONE_NAME
    if not _clean(normalized.get("game_date_operating")) and _clean(
        normalized.get("game_date")
    ):
        normalized["game_date_operating"] = _clean(normalized.get("game_date"))
    if not _clean(normalized.get("operating_timezone")):
        normalized["operating_timezone"] = COURTVISION_OPERATING_TIMEZONE_NAME
    if not _clean(normalized.get("event_eligibility_status")):
        normalized["event_eligibility_status"] = EVENT_REGULAR_SEASON_ELIGIBLE
    normalized.setdefault("event_eligibility_reason", "")
    normalized.setdefault("event_eligibility_rule", "legacy_feature_row")
    return normalized


def _normalize_feature_rows(
    rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    return tuple(_normalize_feature_row(row) for row in rows)


def _iso_z(value: datetime) -> str:
    parsed = value.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.isoformat().replace("+00:00", "Z")


def _parse_decimal(value: object, field_name: str, row_number: str = "") -> Decimal:
    text = _clean(value)
    if not text:
        location = f" row {row_number}" if row_number else ""
        raise MLBHRResearchBaselineError(f"{field_name}{location} is required")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise MLBHRResearchBaselineError(
            f"{field_name} must be numeric: {text!r}"
        ) from exc
    if not parsed.is_finite():
        raise MLBHRResearchBaselineError(f"{field_name} must be finite: {text!r}")
    return parsed


def american_to_decimal(american_odds: int | float | Decimal) -> float:
    odds = Decimal(str(american_odds))
    if odds == 0:
        raise MLBHRResearchBaselineError("American odds cannot be zero")
    if odds > 0:
        return float(Decimal(1) + odds / Decimal(100))
    return float(Decimal(1) + Decimal(100) / abs(odds))


def american_to_implied_probability(american_odds: int | float | Decimal) -> float:
    odds = Decimal(str(american_odds))
    if odds == 0:
        raise MLBHRResearchBaselineError("American odds cannot be zero")
    if odds > 0:
        return float(Decimal(100) / (odds + Decimal(100)))
    absolute = abs(odds)
    return float(absolute / (absolute + Decimal(100)))


def win_profit_1u(american_odds: int | float | Decimal) -> float:
    odds = Decimal(str(american_odds))
    if odds == 0:
        raise MLBHRResearchBaselineError("American odds cannot be zero")
    if odds > 0:
        return float(odds / Decimal(100))
    return float(Decimal(100) / abs(odds))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sample_stdev(values: Sequence[float]) -> float:
    if len(values) <= 1:
        return 0.0
    center = _mean(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


def _repository_commit_sha(repository_root: Path = DEFAULT_REPOSITORY_ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_missingness(rows: Sequence[Mapping[str, str]], columns: Sequence[str]) -> dict[str, int]:
    return {
        column: sum(not _clean(row.get(column)) for row in rows)
        for column in columns
    }


def _exclusion_counts(rows: Sequence[Mapping[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = _clean(row.get("exclusion_reason")) or "none"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _load_result_index(
    results_path: str | Path | None,
) -> tuple[
    dict[tuple[str, str], dict[str, str]],
    set[tuple[str, str]],
    str,
    str,
]:
    if results_path is None:
        return {}, set(), "", ""
    source = Path(results_path).expanduser().resolve()
    _, rows = _read_csv(
        source,
        required_columns=RESULTS_REQUIRED_COLUMNS,
        label="results",
    )
    index: dict[tuple[str, str], dict[str, str]] = {}
    duplicates: set[tuple[str, str]] = set()
    for row in rows:
        event_id = _clean(row.get("event_id"))
        player = _clean(row.get("player"))
        normalized = normalize_mlb_player_name(player)
        if not event_id or not normalized:
            continue
        key = (event_id, normalized)
        if key in index:
            duplicates.add(key)
        else:
            index[key] = row
    for key in duplicates:
        index.pop(key, None)
    return index, duplicates, str(source), _file_sha256(source)


def _validate_odds_row(row: Mapping[str, str]) -> tuple[datetime, datetime, int, float, float]:
    row_number = _clean(row.get("__row_number"))
    side = _clean(row.get("side"))
    if side != "Over":
        raise MLBHRResearchBaselineError(
            f"odds CSV row {row_number} is not an Over HR side"
        )
    point = _parse_decimal(row.get("point"), "point", row_number)
    if point != Decimal("0.5"):
        raise MLBHRResearchBaselineError(
            f"odds CSV row {row_number} is not an Over 0.5 HR market"
        )
    price = _parse_decimal(row.get("price"), "price", row_number)
    if price == 0 or price != price.to_integral_value():
        raise MLBHRResearchBaselineError(
            f"odds CSV row {row_number} has invalid American odds"
        )
    snapshot_time = _parse_datetime(row.get("snapshot_time"), "snapshot_time")
    commence_time = _parse_datetime(row.get("commence_time"), "commence_time")
    american = int(price)
    decimal_odds = american_to_decimal(american)
    implied = american_to_implied_probability(american)
    return snapshot_time, commence_time, american, decimal_odds, implied


def _selected_market_row(
    rows: Sequence[Mapping[str, str]],
) -> tuple[Mapping[str, str], dict[str, float | int]]:
    enriched: list[tuple[float, str, str, Mapping[str, str], int, float]] = []
    implied_values: list[float] = []
    american_values: list[float] = []
    for row in rows:
        _, _, american, decimal_odds, implied = _validate_odds_row(row)
        enriched.append(
            (
                decimal_odds,
                _clean(row.get("bookmaker_key")),
                _clean(row.get("bookmaker")),
                row,
                american,
                implied,
            )
        )
        implied_values.append(implied)
        american_values.append(float(american))
    if not enriched:
        raise MLBHRResearchBaselineError("market group has no rows")
    selected = sorted(enriched, key=lambda item: (-item[0], item[1], item[2]))[0]
    stats: dict[str, float | int] = {
        "bookmaker_count": len(
            {
                (
                    _clean(row.get("bookmaker_key")),
                    _clean(row.get("bookmaker")),
                )
                for row in rows
            }
        ),
        "best_available_american_odds": selected[4],
        "best_available_decimal_odds": selected[0],
        "best_available_implied_probability": selected[5],
        "implied_probability_mean": _mean(implied_values),
        "implied_probability_min": min(implied_values),
        "implied_probability_max": max(implied_values),
        "implied_probability_dispersion": _sample_stdev(implied_values),
        "american_odds_min": min(american_values),
        "american_odds_max": max(american_values),
        "american_odds_dispersion": _sample_stdev(american_values),
    }
    return selected[3], stats


def _format_float(value: object, digits: int = 12) -> str:
    if value is None or value == "":
        return ""
    parsed = float(value)
    if not math.isfinite(parsed):
        return ""
    return f"{parsed:.{digits}g}"


def _feature_row_from_group(
    group_rows: Sequence[Mapping[str, str]],
    *,
    results_index: Mapping[tuple[str, str], Mapping[str, str]],
    duplicate_results: set[tuple[str, str]],
    odds_path: str,
    odds_sha256: str,
    results_path: str,
    results_sha256: str,
    repository_commit_sha: str,
    prediction_timestamp: datetime | None,
    mode: str,
) -> dict[str, str]:
    first = group_rows[0]
    snapshot_time, commence_time, american, decimal_odds, implied = _validate_odds_row(
        first
    )
    event_id = _clean(first.get("event_id"))
    player_name = _clean(first.get("player"))
    normalized_player = normalize_mlb_player_name(player_name)
    if not event_id:
        raise MLBHRResearchBaselineError("odds row has blank event_id")
    if not normalized_player:
        raise MLBHRResearchBaselineError("odds row has blank normalized player")

    selected, market_stats = _selected_market_row(group_rows)
    snapshot_time, commence_time, american, decimal_odds, implied = _validate_odds_row(
        selected
    )
    commence_time_utc = _iso_z(commence_time)
    game_date_utc = commence_time.date().isoformat()
    game_date_operating = courtvision_operating_date(commence_time).isoformat()
    game_date = game_date_operating
    (
        event_eligibility_status,
        event_eligibility_reason,
        event_eligibility_rule,
    ) = _event_eligibility_from_row(selected)
    row_prediction_time = prediction_timestamp or snapshot_time
    hours_before_game = (
        commence_time - row_prediction_time
    ).total_seconds() / 3600.0
    row_id = _stable_id(
        FEATURE_SCHEMA_VERSION,
        event_id,
        normalized_player,
        _iso_z(snapshot_time),
        _clean(selected.get("market")),
        _clean(selected.get("point")),
    )
    source_manifest_reference = _stable_id(
        "live_hr_sources",
        odds_sha256,
        results_sha256,
        event_id,
        normalized_player,
        _iso_z(snapshot_time),
        length=24,
    )

    eligibility_status = (
        PREDICTION_ELIGIBLE_STATUS if mode == "prediction" else "excluded"
    )
    exclusion_reason = ""
    actual_home_runs = ""
    hit_hr = ""
    game_status = ""
    result_reason = ""
    label_available = "false"
    identity_match_status = "normalized_name_only"

    if snapshot_time >= commence_time:
        eligibility_status = "excluded"
        exclusion_reason = "snapshot_not_before_game_start"
    elif event_eligibility_status != EVENT_REGULAR_SEASON_ELIGIBLE:
        eligibility_status = event_eligibility_status
        exclusion_reason = event_eligibility_reason or event_eligibility_status
    elif mode == "prediction":
        if prediction_timestamp is None:
            raise MLBHRResearchBaselineError(
                "prediction_timestamp is required in prediction mode"
            )
        if snapshot_time > prediction_timestamp:
            eligibility_status = "excluded"
            exclusion_reason = "snapshot_after_prediction_timestamp"
        elif prediction_timestamp >= commence_time:
            eligibility_status = "excluded"
            exclusion_reason = "game_already_started"
    else:
        key = (event_id, normalized_player)
        if key in duplicate_results:
            exclusion_reason = "ambiguous_result_identity"
            identity_match_status = "ambiguous_normalized_name"
        else:
            result = results_index.get(key)
            if result is None:
                exclusion_reason = "missing_result"
                identity_match_status = "normalized_name_unmatched"
            else:
                identity_match_status = "normalized_name_matched"
                game_status = _clean(result.get("game_status")).casefold()
                result_reason = _clean(result.get("result_reason"))
                if not game_status:
                    exclusion_reason = "unresolved_result"
                elif game_status in NON_GRADEABLE_STATUSES:
                    exclusion_reason = game_status
                elif game_status != "final":
                    exclusion_reason = "non_final_game"
                else:
                    raw_hr = _clean(result.get("actual_home_runs"))
                    try:
                        parsed_hr = int(raw_hr)
                    except ValueError as exc:
                        raise MLBHRResearchBaselineError(
                            "final result has invalid actual_home_runs for "
                            f"{event_id} + {player_name!r}: {raw_hr!r}"
                        ) from exc
                    if parsed_hr < 0:
                        raise MLBHRResearchBaselineError(
                            "final result has negative actual_home_runs for "
                            f"{event_id} + {player_name!r}"
                        )
                    actual_home_runs = str(parsed_hr)
                    hit_hr = "1" if parsed_hr >= 1 else "0"
                    label_available = "true"
                    eligibility_status = TRAINING_ELIGIBLE_STATUS

    row = {
        "feature_row_id": row_id,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "event_id": event_id,
        "game_date": game_date,
        "commence_time": commence_time_utc,
        "commence_time_utc": commence_time_utc,
        "game_date_utc": game_date_utc,
        "game_date_operating": game_date_operating,
        "operating_timezone": COURTVISION_OPERATING_TIMEZONE_NAME,
        "prediction_timestamp": _iso_z(row_prediction_time),
        "snapshot_time": _iso_z(snapshot_time),
        "player_id": "",
        "player_name": player_name,
        "normalized_player_name": normalized_player,
        "team": "",
        "opponent": "",
        "home_team": _clean(selected.get("home_team")),
        "away_team": _clean(selected.get("away_team")),
        "event_eligibility_status": event_eligibility_status,
        "event_eligibility_reason": event_eligibility_reason,
        "event_eligibility_rule": event_eligibility_rule,
        "sportsbook": _clean(selected.get("bookmaker_key")),
        "sportsbook_name": _clean(selected.get("bookmaker")),
        "market_key": _clean(selected.get("market")),
        "side": _clean(selected.get("side")),
        "point": _clean(selected.get("point")),
        "american_odds": str(american),
        "decimal_odds": _format_float(decimal_odds),
        "implied_probability": _format_float(implied),
        "best_available_american_odds": str(
            int(market_stats["best_available_american_odds"])
        ),
        "best_available_decimal_odds": _format_float(
            market_stats["best_available_decimal_odds"]
        ),
        "best_available_implied_probability": _format_float(
            market_stats["best_available_implied_probability"]
        ),
        "bookmaker_count": str(int(market_stats["bookmaker_count"])),
        "implied_probability_mean": _format_float(
            market_stats["implied_probability_mean"]
        ),
        "implied_probability_min": _format_float(
            market_stats["implied_probability_min"]
        ),
        "implied_probability_max": _format_float(
            market_stats["implied_probability_max"]
        ),
        "implied_probability_dispersion": _format_float(
            market_stats["implied_probability_dispersion"]
        ),
        "american_odds_min": _format_float(market_stats["american_odds_min"]),
        "american_odds_max": _format_float(market_stats["american_odds_max"]),
        "american_odds_dispersion": _format_float(
            market_stats["american_odds_dispersion"]
        ),
        "hours_before_game": _format_float(hours_before_game),
        "snapshot_selection_rule": (
            PREDICTION_SNAPSHOT_SELECTION_RULE
            if mode == "prediction"
            else SNAPSHOT_SELECTION_RULE
        ),
        "bookmaker_selection_rule": BOOKMAKER_SELECTION_RULE,
        "lineup_status": "unknown",
        "eligibility_status": eligibility_status,
        "exclusion_reason": exclusion_reason,
        "identity_match_status": identity_match_status,
        "actual_home_runs": actual_home_runs,
        "hit_hr": hit_hr,
        "game_status": game_status,
        "result_reason": result_reason,
        "label_available": label_available,
        "source_odds_path": odds_path,
        "source_results_path": results_path,
        "source_odds_sha256": odds_sha256,
        "source_results_sha256": results_sha256,
        "source_manifest_reference": source_manifest_reference,
        "repository_commit_sha": repository_commit_sha,
        "leakage_check_status": "passed" if not exclusion_reason.startswith("snapshot_") else "failed",
        "eligible_for_betting": "false",
        "kelly_eligible": "false",
    }
    return {column: row.get(column, "") for column in FEATURE_COLUMNS}


def _selected_snapshot_groups(
    odds_rows: Sequence[Mapping[str, str]],
    *,
    mode: str,
    target_date: str | None,
    prediction_timestamp: datetime | None,
) -> tuple[tuple[Mapping[str, str], ...], ...]:
    grouped: dict[tuple[str, str, str, str, str], list[Mapping[str, str]]] = {}
    target_operating_date = _target_operating_date_text(target_date)
    for row in odds_rows:
        snapshot_time, commence_time, _, _, _ = _validate_odds_row(row)
        if (
            target_operating_date
            and courtvision_operating_date(commence_time).isoformat()
            != target_operating_date
        ):
            continue
        event_id = _clean(row.get("event_id"))
        normalized = normalize_mlb_player_name(row.get("player"))
        market = _clean(row.get("market"))
        point = _clean(row.get("point"))
        if not event_id or not normalized:
            raise MLBHRResearchBaselineError(
                "odds CSV contains blank event_id or player after normalization"
            )
        if mode == "prediction":
            if prediction_timestamp is None:
                raise MLBHRResearchBaselineError(
                    "prediction_timestamp is required for prediction features"
                )
            if snapshot_time > prediction_timestamp:
                continue
        if snapshot_time >= commence_time and mode != "prediction":
            # Keep the group so it appears as an explicit excluded leakage row.
            pass
        grouped.setdefault(
            (event_id, normalized, _iso_z(snapshot_time), market, point), []
        ).append(row)

    by_player_game: dict[tuple[str, str, str, str], list[tuple[datetime, tuple[Mapping[str, str], ...]]]] = {}
    for (event_id, normalized, snapshot_text, market, point), rows in grouped.items():
        snapshot = _parse_datetime(snapshot_text, "snapshot_time")
        by_player_game.setdefault((event_id, normalized, market, point), []).append(
            (snapshot, tuple(rows))
        )

    selected_groups: list[tuple[Mapping[str, str], ...]] = []
    for _, candidates in sorted(by_player_game.items(), key=lambda item: item[0]):
        eligible_candidates: list[tuple[datetime, tuple[Mapping[str, str], ...]]] = []
        for snapshot, rows in candidates:
            _, commence_time, _, _, _ = _validate_odds_row(rows[0])
            if mode == "prediction":
                if prediction_timestamp is not None and snapshot <= prediction_timestamp:
                    eligible_candidates.append((snapshot, rows))
            elif snapshot < commence_time:
                eligible_candidates.append((snapshot, rows))
        if not eligible_candidates:
            selected_groups.append(sorted(candidates, key=lambda item: item[0])[-1][1])
            continue
        selected_groups.append(sorted(eligible_candidates, key=lambda item: item[0])[-1][1])
    return tuple(selected_groups)


def _input_stage_diagnostics(
    odds_rows: Sequence[Mapping[str, str]],
    *,
    target_operating_date: str | None,
    selected_groups: Sequence[Sequence[Mapping[str, str]]],
    feature_rows: Sequence[Mapping[str, str]],
    eligible_row_count: int,
) -> dict[str, int]:
    requested_date_row_count = 0
    for row in odds_rows:
        _, commence_time, _, _, _ = _validate_odds_row(row)
        if (
            target_operating_date is None
            or courtvision_operating_date(commence_time).isoformat()
            == target_operating_date
        ):
            requested_date_row_count += 1
    return {
        "input_row_count": len(odds_rows),
        "requested_date_row_count": requested_date_row_count,
        # Unsupported/non-HR rows fail the existing loader contract rather
        # than being silently filtered, so every retained requested-date row
        # has passed the canonical market validation.
        "market_filtered_row_count": requested_date_row_count,
        "deduplicated_row_count": len(selected_groups),
        "feature_validated_row_count": len(feature_rows),
        "eligible_row_count": eligible_row_count,
    }


def build_live_hr_research_features(
    *,
    odds_path: str | Path = DEFAULT_ODDS_CSV,
    results_path: str | Path | None = DEFAULT_RESULTS_CSV,
    target_date: str | None = None,
    prediction_timestamp: datetime | str | None = None,
    mode: str = "training",
    generated_at: datetime | str | None = None,
    repository_root: str | Path = DEFAULT_REPOSITORY_ROOT,
) -> FeatureBuildResult:
    """Build one deterministic research feature table from local live HR files.

    Training mode joins strict outcomes and keeps all non-training rows with an
    exclusion reason. Prediction mode never reads outcomes and excludes games
    that have already started at ``prediction_timestamp``.
    """

    if mode not in {"training", "prediction"}:
        raise MLBHRResearchBaselineError("mode must be 'training' or 'prediction'")
    target_operating_date = _target_operating_date_text(target_date)
    odds_source = Path(odds_path).expanduser().resolve()
    _, odds_rows = _read_csv(
        odds_source,
        required_columns=ODDS_REQUIRED_COLUMNS,
        label="odds",
    )
    odds_sha = _file_sha256(odds_source)
    parsed_generated_at = _coerce_datetime_utc(generated_at, "generated_at")
    parsed_prediction_ts = (
        _coerce_datetime_utc(
            prediction_timestamp,
            "prediction_timestamp",
            default_now=False,
        )
        if prediction_timestamp is not None
        else None
    )
    if mode == "prediction" and parsed_prediction_ts is None:
        parsed_prediction_ts = parsed_generated_at
    result_index: Mapping[tuple[str, str], Mapping[str, str]]
    duplicate_results: set[tuple[str, str]]
    result_path_text: str
    result_sha: str
    if mode == "training":
        result_index, duplicate_results, result_path_text, result_sha = _load_result_index(
            results_path
        )
    else:
        result_index, duplicate_results, result_path_text, result_sha = {}, set(), "", ""

    groups = _selected_snapshot_groups(
        odds_rows,
        mode=mode,
        target_date=target_operating_date,
        prediction_timestamp=parsed_prediction_ts if isinstance(parsed_prediction_ts, datetime) else None,
    )
    commit_sha = _repository_commit_sha(Path(repository_root).expanduser().resolve())
    rows = tuple(
        _feature_row_from_group(
            group,
            results_index=result_index,
            duplicate_results=duplicate_results,
            odds_path=str(odds_source),
            odds_sha256=odds_sha,
            results_path=result_path_text,
            results_sha256=result_sha,
            repository_commit_sha=commit_sha,
            prediction_timestamp=(
                parsed_prediction_ts if isinstance(parsed_prediction_ts, datetime) else None
            ),
            mode=mode,
        )
        for group in groups
    )
    exclusions = tuple(
        {column: row.get(column, "") for column in EXCLUSION_COLUMNS}
        for row in rows
        if row.get("exclusion_reason")
    )
    eligible_status = (
        PREDICTION_ELIGIBLE_STATUS if mode == "prediction" else TRAINING_ELIGIBLE_STATUS
    )
    eligible_rows = [row for row in rows if row.get("eligibility_status") == eligible_status]
    input_diagnostics = _input_stage_diagnostics(
        odds_rows,
        target_operating_date=target_operating_date,
        selected_groups=groups,
        feature_rows=rows,
        eligible_row_count=len(eligible_rows),
    )
    dates = sorted({row["game_date"] for row in rows if row.get("game_date")})
    manifest = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "mode": mode,
        "generated_at": _iso_z(parsed_generated_at),
        "target_date": target_operating_date or "",
        "target_date_semantics": "courtvision_operating_date",
        "operating_timezone": COURTVISION_OPERATING_TIMEZONE_NAME,
        "date_fields": {
            "game_date": "CourtVision operating date in America/Toronto",
            "game_date_operating": "CourtVision operating date in America/Toronto",
            "game_date_utc": "UTC calendar date from commence_time_utc",
            "commence_time": "UTC timestamp preserved from source",
            "commence_time_utc": "UTC timestamp preserved from source",
        },
        "snapshot_selection_rule": (
            PREDICTION_SNAPSHOT_SELECTION_RULE
            if mode == "prediction"
            else SNAPSHOT_SELECTION_RULE
        ),
        "bookmaker_selection_rule": BOOKMAKER_SELECTION_RULE,
        "row_count": len(rows),
        "eligible_row_count": len(eligible_rows),
        "excluded_row_count": len(exclusions),
        "input_diagnostics": input_diagnostics,
        **input_diagnostics,
        "unique_games": len({row["event_id"] for row in rows if row.get("event_id")}),
        "unique_players": len(
            {
                row["normalized_player_name"]
                for row in rows
                if row.get("normalized_player_name")
            }
        ),
        "date_range_start": dates[0] if dates else "",
        "date_range_end": dates[-1] if dates else "",
        "dates": dates,
        "positive_outcomes": sum(row.get("hit_hr") == "1" for row in eligible_rows),
        "exclusion_counts": _exclusion_counts(exclusions),
        "event_eligibility_counts": _event_eligibility_counts(rows),
        "missingness": _row_missingness(rows, FEATURE_COLUMNS),
        "source_files": {
            "odds_csv": str(odds_source),
            "results_csv": result_path_text,
        },
        "source_hashes": {
            "odds_csv_sha256": odds_sha,
            "results_csv_sha256": result_sha,
        },
        "repository_commit_sha": commit_sha,
        "leakage_protections": {
            "snapshot_must_precede_game_start": True,
            "training_features_exclude_result_fields": True,
            "prediction_mode_reads_results": False,
            "one_snapshot_per_event_player_market_point": True,
            "one_best_bookmaker_row_per_feature_row": True,
        },
        "market_vig_note": (
            "Only Over 0.5 HR prices are retained in the live archive; "
            "true two-sided vig removal is unavailable. Market baseline uses "
            "raw implied probabilities and is labelled vig-included."
        ),
    }
    return FeatureBuildResult(rows=rows, exclusions=exclusions, manifest=manifest)


def _audit_summary_markdown(result: FeatureBuildResult) -> str:
    manifest = result.manifest
    lines = [
        "# CourtVision MLB HR Research Feature Audit",
        "",
        RESEARCH_ONLY_LABEL,
        "",
        f"- Mode: {manifest.get('mode')}",
        f"- Rows: {manifest.get('row_count')}",
        f"- Eligible rows: {manifest.get('eligible_row_count')}",
        f"- Excluded rows: {manifest.get('excluded_row_count')}",
        f"- Unique games: {manifest.get('unique_games')}",
        f"- Unique players: {manifest.get('unique_players')}",
        f"- Date range: {manifest.get('date_range_start')} to {manifest.get('date_range_end')}",
        f"- Positive outcomes: {manifest.get('positive_outcomes')}",
        "",
        "## Snapshot Rule",
        "",
        str(manifest.get("snapshot_selection_rule")),
        "",
        "## Market Note",
        "",
        str(manifest.get("market_vig_note")),
        "",
        "## Exclusion Counts",
        "",
    ]
    counts = manifest.get("exclusion_counts", {})
    if isinstance(counts, Mapping) and counts:
        lines.extend(f"- {key}: {value}" for key, value in counts.items())
    else:
        lines.append("- none: 0")
    lines.append("")
    return "\n".join(lines)


def write_feature_artifacts(
    result: FeatureBuildResult,
    output_dir: str | Path,
) -> Mapping[str, str]:
    """Write feature rows, exclusions, manifest, and audit text create-once."""

    root = Path(output_dir).expanduser().resolve()
    if root.exists():
        raise MLBHRResearchBaselineError(f"output directory already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    features_path = _write_csv_create_once(root / "feature_rows.csv", FEATURE_COLUMNS, result.rows)
    exclusions_path = _write_csv_create_once(root / "exclusions.csv", EXCLUSION_COLUMNS, result.exclusions)
    manifest_path = _write_json_create_once(root / "manifest.json", result.manifest)
    audit_path = _write_text_create_once(root / "audit_summary.md", _audit_summary_markdown(result))
    return {
        "feature_rows_csv": str(features_path),
        "exclusions_csv": str(exclusions_path),
        "manifest_json": str(manifest_path),
        "audit_summary_md": str(audit_path),
    }


def _read_identity_provider_rows(path: str | Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MLBHRResearchBaselineError(f"identity source CSV does not exist: {source}")
    try:
        with source.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = tuple(reader.fieldnames or ())
            if "mlb_player_id" not in columns:
                raise MLBHRResearchBaselineError(
                    "identity source CSV missing required column: mlb_player_id"
                )
            if not (
                "canonical_mlb_name" in columns
                or "player_name" in columns
                or "sportsbook_player_name" in columns
            ):
                raise MLBHRResearchBaselineError(
                    "identity source CSV needs canonical_mlb_name, player_name, "
                    "or sportsbook_player_name"
                )
            rows: list[dict[str, str]] = []
            for row_number, row in enumerate(reader, start=2):
                clean_row = {column: _clean(row.get(column)) for column in columns}
                clean_row["__row_number"] = str(row_number)
                clean_row["__source_path"] = str(source)
                rows.append(clean_row)
    except OSError as exc:
        raise MLBHRResearchBaselineError(
            f"could not read identity source CSV {source}: {exc}"
        ) from exc
    return tuple(rows)


def _read_identity_cache(path: str | Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    source = Path(path).expanduser().resolve()
    if not source.exists():
        return ()
    columns, rows = _read_csv(
        source,
        required_columns=IDENTITY_CACHE_COLUMNS,
        label="identity cache",
    )
    if tuple(columns) != IDENTITY_CACHE_COLUMNS:
        raise MLBHRResearchBaselineError(
            "identity cache schema does not match expected columns"
        )
    return rows


def _identity_record(
    *,
    sportsbook_player_name: str,
    normalized_player_name: str,
    mlb_player_id: str,
    canonical_mlb_name: str,
    identity_status: str,
    identity_method: str,
    identity_source: str,
    resolved_at: str,
    mapping_version: str,
    reviewed_at: str = "",
    review_status: str = "not_reviewed",
    conflict_reason: str = "",
) -> dict[str, str]:
    payload = {
        "cache_schema_version": IDENTITY_CACHE_SCHEMA_VERSION,
        "sportsbook_player_name": sportsbook_player_name,
        "normalized_player_name": normalized_player_name,
        "mlb_player_id": mlb_player_id,
        "canonical_mlb_name": canonical_mlb_name,
        "identity_status": identity_status,
        "identity_method": identity_method,
        "identity_source": identity_source,
        "resolved_at": resolved_at,
        "reviewed_at": reviewed_at,
        "review_status": review_status,
        "mapping_version": mapping_version,
        "conflict_reason": conflict_reason,
    }
    payload["cache_record_id"] = _stable_id(
        IDENTITY_CACHE_SCHEMA_VERSION,
        mapping_version,
        sportsbook_player_name,
        normalized_player_name,
        mlb_player_id,
        canonical_mlb_name,
        identity_status,
        identity_method,
        identity_source,
        conflict_reason,
    )
    return {column: payload.get(column, "") for column in IDENTITY_CACHE_COLUMNS}


def _provider_identity_index(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        mlb_id = _clean(row.get("mlb_player_id"))
        canonical_name = (
            _clean(row.get("canonical_mlb_name"))
            or _clean(row.get("player_name"))
            or _clean(row.get("sportsbook_player_name"))
        )
        normalized = _clean(row.get("normalized_player_name")) or normalize_mlb_player_name(
            canonical_name
        )
        if not mlb_id or not canonical_name or not normalized:
            continue
        source = (
            _clean(row.get("identity_source"))
            or _clean(row.get("source"))
            or _clean(row.get("__source_path"))
            or "identity_provider_csv"
        )
        index.setdefault(normalized, []).append(
            {
                "mlb_player_id": mlb_id,
                "canonical_mlb_name": canonical_name,
                "normalized_player_name": normalized,
                "identity_source": source,
            }
        )
    return index


def _resolved_cache_index(
    rows: Sequence[Mapping[str, object]],
    *,
    mapping_version: str,
) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if _clean(row.get("cache_schema_version")) != IDENTITY_CACHE_SCHEMA_VERSION:
            continue
        if _clean(row.get("mapping_version")) != mapping_version:
            continue
        if _clean(row.get("identity_status")) != "resolved":
            continue
        normalized = _clean(row.get("normalized_player_name"))
        mlb_id = _clean(row.get("mlb_player_id"))
        canonical = _clean(row.get("canonical_mlb_name"))
        if not normalized or not mlb_id or not canonical:
            continue
        index.setdefault(normalized, []).append(
            {
                "mlb_player_id": mlb_id,
                "canonical_mlb_name": canonical,
                "identity_source": _clean(row.get("identity_source")) or "identity_cache",
                "reviewed_at": _clean(row.get("reviewed_at")),
                "review_status": _clean(row.get("review_status")) or "not_reviewed",
            }
        )
    return index


def _unique_identity_candidates(
    rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (_clean(row.get("mlb_player_id")), _clean(row.get("canonical_mlb_name")))
        if key[0] and key[1]:
            unique.setdefault(key, dict(row))
    return tuple(unique.values())


def resolve_player_identities(
    *,
    feature_rows: Sequence[Mapping[str, object]],
    identity_provider_rows: Sequence[Mapping[str, object]] = (),
    identity_source_csv: str | Path | None = None,
    identity_cache_csv: str | Path | None = None,
    mapping_version: str = DEFAULT_IDENTITY_MAPPING_VERSION,
    resolved_at: datetime | str | None = None,
    write_cache: bool = False,
) -> IdentityResolutionResult:
    """Resolve sportsbook player names to MLBAM IDs using deterministic evidence.

    The resolver never guesses. Exact normalized-name agreement can resolve a
    row only when it maps to one canonical MLBAM ID. Multiple current cache or
    provider candidates are quarantined for manual review.
    """

    timestamp = _coerce_datetime_utc(resolved_at, "resolved_at")
    resolved_at_text = _iso_z(timestamp)
    provider_rows = tuple(identity_provider_rows) + _read_identity_provider_rows(
        identity_source_csv
    )
    cache_rows = _read_identity_cache(identity_cache_csv)
    provider_index = _provider_identity_index(provider_rows)
    cache_index = _resolved_cache_index(cache_rows, mapping_version=mapping_version)

    names_by_normalized: dict[str, set[str]] = {}
    counts_by_normalized: dict[str, int] = {}
    for row in feature_rows:
        player_name = (
            _clean(row.get("sportsbook_player_name"))
            or _clean(row.get("player_name"))
            or _clean(row.get("canonical_mlb_name"))
        )
        normalized = _clean(row.get("normalized_player_name")) or normalize_mlb_player_name(
            player_name
        )
        if not normalized:
            normalized = "<blank>"
        names_by_normalized.setdefault(normalized, set()).add(player_name)
        counts_by_normalized[normalized] = counts_by_normalized.get(normalized, 0) + 1

    records: list[dict[str, str]] = []
    for normalized in sorted(names_by_normalized):
        sportsbook_name = sorted(name for name in names_by_normalized[normalized] if name)[0]
        if normalized == "<blank>":
            records.append(
                _identity_record(
                    sportsbook_player_name=sportsbook_name,
                    normalized_player_name="",
                    mlb_player_id="",
                    canonical_mlb_name="",
                    identity_status="unresolved",
                    identity_method="name_normalization_failed",
                    identity_source="feature_rows",
                    resolved_at=resolved_at_text,
                    mapping_version=mapping_version,
                    conflict_reason="blank_player_name",
                )
            )
            continue

        cache_candidates = _unique_identity_candidates(cache_index.get(normalized, []))
        if len(cache_candidates) == 1:
            candidate = cache_candidates[0]
            records.append(
                _identity_record(
                    sportsbook_player_name=sportsbook_name,
                    normalized_player_name=normalized,
                    mlb_player_id=candidate["mlb_player_id"],
                    canonical_mlb_name=candidate["canonical_mlb_name"],
                    identity_status="resolved",
                    identity_method="cache",
                    identity_source=candidate.get("identity_source", "identity_cache"),
                    resolved_at=resolved_at_text,
                    mapping_version=mapping_version,
                    reviewed_at=candidate.get("reviewed_at", ""),
                    review_status=candidate.get("review_status", "not_reviewed"),
                )
            )
            continue
        if len(cache_candidates) > 1:
            records.append(
                _identity_record(
                    sportsbook_player_name=sportsbook_name,
                    normalized_player_name=normalized,
                    mlb_player_id="",
                    canonical_mlb_name="",
                    identity_status="quarantined",
                    identity_method="cache_conflict",
                    identity_source="identity_cache",
                    resolved_at=resolved_at_text,
                    mapping_version=mapping_version,
                    conflict_reason="cache_multiple_ids_for_normalized_name",
                )
            )
            continue

        provider_candidates = _unique_identity_candidates(provider_index.get(normalized, []))
        if len(provider_candidates) == 1:
            candidate = provider_candidates[0]
            records.append(
                _identity_record(
                    sportsbook_player_name=sportsbook_name,
                    normalized_player_name=normalized,
                    mlb_player_id=candidate["mlb_player_id"],
                    canonical_mlb_name=candidate["canonical_mlb_name"],
                    identity_status="resolved",
                    identity_method="exact_normalized_name",
                    identity_source=candidate.get("identity_source", "identity_provider_csv"),
                    resolved_at=resolved_at_text,
                    mapping_version=mapping_version,
                )
            )
        elif len(provider_candidates) > 1:
            records.append(
                _identity_record(
                    sportsbook_player_name=sportsbook_name,
                    normalized_player_name=normalized,
                    mlb_player_id="",
                    canonical_mlb_name="",
                    identity_status="quarantined",
                    identity_method="provider_conflict",
                    identity_source="identity_provider_csv",
                    resolved_at=resolved_at_text,
                    mapping_version=mapping_version,
                    conflict_reason="provider_multiple_ids_for_normalized_name",
                )
            )
        else:
            records.append(
                _identity_record(
                    sportsbook_player_name=sportsbook_name,
                    normalized_player_name=normalized,
                    mlb_player_id="",
                    canonical_mlb_name="",
                    identity_status="unresolved",
                    identity_method="no_local_identity_evidence",
                    identity_source="identity_provider_csv",
                    resolved_at=resolved_at_text,
                    mapping_version=mapping_version,
                    conflict_reason="missing_provider_mapping",
                )
            )

    status_counts: dict[str, int] = {}
    for record in records:
        status = record["identity_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    unresolved_counts = [
        {
            "normalized_player_name": record["normalized_player_name"],
            "sportsbook_player_name": record["sportsbook_player_name"],
            "identity_status": record["identity_status"],
            "identity_method": record["identity_method"],
            "conflict_reason": record["conflict_reason"],
            "feature_row_count": counts_by_normalized.get(
                record["normalized_player_name"] or "<blank>", 0
            ),
        }
        for record in records
        if record["identity_status"] != "resolved"
    ]
    unresolved_counts.sort(
        key=lambda item: (-int(item["feature_row_count"]), item["normalized_player_name"])
    )
    cache_path = Path(identity_cache_csv).expanduser().resolve() if identity_cache_csv else None
    if write_cache and cache_path is not None:
        append_identity_cache_records(cache_path=cache_path, records=records)
    report = {
        "identity_cache_schema_version": IDENTITY_CACHE_SCHEMA_VERSION,
        "mapping_version": mapping_version,
        "resolved_at": resolved_at_text,
        "source_provider_row_count": len(provider_rows),
        "existing_cache_row_count": len(cache_rows),
        "input_feature_row_count": len(feature_rows),
        "unique_normalized_players": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "resolved_count": status_counts.get("resolved", 0),
        "unresolved_count": status_counts.get("unresolved", 0),
        "quarantined_count": status_counts.get("quarantined", 0),
        "top_unresolved_by_feature_rows": unresolved_counts[:25],
        "cache_path": str(cache_path) if cache_path else "",
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
    }
    return IdentityResolutionResult(
        records=tuple(records),
        report=report,
        cache_path=cache_path,
    )


def append_identity_cache_records(
    *,
    cache_path: str | Path,
    records: Sequence[Mapping[str, object]],
) -> int:
    """Append new identity evidence rows without mutating prior mappings."""

    target = Path(cache_path).expanduser().resolve()
    existing_record_ids: set[str] = set()
    if target.exists():
        columns, existing_rows = _read_csv(
            target,
            required_columns=IDENTITY_CACHE_COLUMNS,
            label="identity cache",
        )
        if tuple(columns) != IDENTITY_CACHE_COLUMNS:
            raise MLBHRResearchBaselineError(
                "identity cache schema does not match expected columns"
            )
        existing_record_ids = {
            _clean(row.get("cache_record_id")) for row in existing_rows
        }
    new_rows = [
        {column: _clean(record.get(column)) for column in IDENTITY_CACHE_COLUMNS}
        for record in records
        if _clean(record.get("cache_record_id")) not in existing_record_ids
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    file_exists = target.exists()
    try:
        with target.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(IDENTITY_CACHE_COLUMNS))
            if not file_exists:
                writer.writeheader()
            for row in new_rows:
                writer.writerow(row)
    except OSError as exc:
        raise MLBHRResearchBaselineError(
            f"could not append identity cache {target}: {exc}"
        ) from exc
    return len(new_rows)


def _identity_report_markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# MLB HR Research Identity Resolution",
        "",
        RESEARCH_ONLY_LABEL,
        "",
        f"- Mapping version: {report.get('mapping_version')}",
        f"- Input feature rows: {report.get('input_feature_row_count')}",
        f"- Unique normalized players: {report.get('unique_normalized_players')}",
        f"- Resolved: {report.get('resolved_count')}",
        f"- Unresolved: {report.get('unresolved_count')}",
        f"- Quarantined: {report.get('quarantined_count')}",
        "",
        "## Top Unresolved Or Quarantined",
        "",
    ]
    top = report.get("top_unresolved_by_feature_rows", [])
    if isinstance(top, Sequence) and top:
        for item in top:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "- "
                + str(item.get("sportsbook_player_name", ""))
                + " | status="
                + str(item.get("identity_status", ""))
                + " | rows="
                + str(item.get("feature_row_count", ""))
                + " | reason="
                + str(item.get("conflict_reason", ""))
            )
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _float_or_none(value: object) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _training_rows_from_feature_csv(feature_rows_path: str | Path) -> tuple[dict[str, str], ...]:
    _, rows = _read_csv(
        feature_rows_path,
        required_columns=(
            "feature_schema_version",
            "eligibility_status",
            "hit_hr",
            "game_date",
            "event_id",
            "normalized_player_name",
            *MODEL_REQUIRED_INPUT_COLUMNS,
        ),
        label="feature rows",
    )
    invalid_schema = [
        row.get("feature_row_id", f"row {row.get('__row_number')}")
        for row in rows
        if row.get("feature_schema_version") not in COMPATIBLE_FEATURE_SCHEMA_VERSIONS
    ]
    if invalid_schema:
        raise MLBHRResearchBaselineError(
            "feature rows use unsupported schema version"
        )
    return _normalize_feature_rows(rows)


def _eligible_training_rows(rows: Sequence[Mapping[str, str]]) -> tuple[dict[str, str], ...]:
    eligible: list[dict[str, str]] = []
    for row in rows:
        if row.get("eligibility_status") != TRAINING_ELIGIBLE_STATUS:
            continue
        if row.get("hit_hr") not in {"0", "1"}:
            continue
        eligible.append(dict(row))
    return tuple(eligible)


def chronological_split_rows(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[dict[str, str], ...]]:
    """Split rows by ordered game date and keep training strictly earlier."""

    materialized = [dict(row) for row in rows]
    if not materialized:
        return {"train": (), "validation": (), "test": ()}
    dates = sorted({row["game_date"] for row in materialized})
    if len(dates) >= 5:
        train_cut = max(1, int(math.floor(len(dates) * 0.6)))
        validation_cut = max(train_cut + 1, int(math.floor(len(dates) * 0.8)))
        validation_cut = min(validation_cut, len(dates) - 1)
    elif len(dates) >= 3:
        train_cut = len(dates) - 2
        validation_cut = len(dates) - 1
    elif len(dates) == 2:
        train_cut = 1
        validation_cut = 2
    else:
        train_cut = 1
        validation_cut = 1

    def rows_for(date_values: set[str]) -> tuple[dict[str, str], ...]:
        return tuple(row for row in materialized if row["game_date"] in date_values)

    train_dates = set(dates[:train_cut])
    validation_dates = set(dates[train_cut:validation_cut])
    test_dates = set(dates[validation_cut:])

    # Preserve chronology while expanding train if the initial train window is
    # one-class and later dates can make the baseline estimable.
    remaining_dates = dates[train_cut:]
    while remaining_dates:
        train_labels = {row["hit_hr"] for row in rows_for(train_dates)}
        if train_labels == {"0", "1"}:
            break
        next_date = remaining_dates.pop(0)
        train_dates.add(next_date)
        validation_dates.discard(next_date)
        test_dates.discard(next_date)
    return {
        "train": rows_for(train_dates),
        "validation": rows_for(validation_dates),
        "test": rows_for(test_dates),
    }


def _fit_preprocessor(rows: Sequence[Mapping[str, str]]) -> dict[str, object]:
    numeric: dict[str, dict[str, float]] = {}
    for feature in NUMERIC_MODEL_FEATURES:
        values = [
            parsed
            for row in rows
            if (parsed := _float_or_none(row.get(feature))) is not None
        ]
        mean = _mean(values) if values else 0.0
        stdev = _sample_stdev(values) if values else 1.0
        if stdev <= 1e-12:
            stdev = 1.0
        numeric[feature] = {"mean": mean, "stdev": stdev}

    categories: dict[str, tuple[str, ...]] = {}
    for feature in CATEGORICAL_MODEL_FEATURES:
        categories[feature] = tuple(
            sorted({_clean(row.get(feature)) for row in rows if _clean(row.get(feature))})
        )

    feature_order = ["intercept"]
    for feature in NUMERIC_MODEL_FEATURES:
        feature_order.append(f"{feature}__z")
        feature_order.append(f"{feature}__missing")
    for feature in CATEGORICAL_MODEL_FEATURES:
        for level in categories[feature]:
            feature_order.append(f"{feature}__eq__{level}")

    return {
        "numeric": numeric,
        "categorical_levels": {key: list(value) for key, value in categories.items()},
        "feature_order": feature_order,
    }


def _vectorize(row: Mapping[str, str], preprocessor: Mapping[str, object]) -> list[float]:
    values = [1.0]
    numeric = preprocessor["numeric"]
    if not isinstance(numeric, Mapping):
        raise MLBHRResearchBaselineError("invalid numeric preprocessing metadata")
    for feature in NUMERIC_MODEL_FEATURES:
        stats = numeric.get(feature)
        if not isinstance(stats, Mapping):
            raise MLBHRResearchBaselineError("invalid numeric preprocessing metadata")
        parsed = _float_or_none(row.get(feature))
        missing = parsed is None
        if parsed is None:
            parsed = float(stats["mean"])
        values.append((parsed - float(stats["mean"])) / float(stats["stdev"]))
        values.append(1.0 if missing else 0.0)

    levels = preprocessor["categorical_levels"]
    if not isinstance(levels, Mapping):
        raise MLBHRResearchBaselineError("invalid categorical preprocessing metadata")
    for feature in CATEGORICAL_MODEL_FEATURES:
        observed = _clean(row.get(feature))
        feature_levels = levels.get(feature, [])
        if not isinstance(feature_levels, Sequence) or isinstance(feature_levels, str):
            raise MLBHRResearchBaselineError("invalid categorical levels")
        values.extend(1.0 if observed == level else 0.0 for level in feature_levels)
    return values


def _fit_logistic(
    rows: Sequence[Mapping[str, str]],
    *,
    iterations: int = 1200,
    learning_rate: float = 0.08,
    l2_penalty: float = 0.01,
) -> tuple[dict[str, object], list[float]]:
    if not rows:
        raise MLBHRResearchBaselineError("cannot train on empty rows")
    labels = [int(row["hit_hr"]) for row in rows]
    if set(labels) != {0, 1}:
        raise MLBHRResearchBaselineError(
            "logistic baseline training requires at least one positive and one negative"
        )
    preprocessor = _fit_preprocessor(rows)
    vectors = [_vectorize(row, preprocessor) for row in rows]
    weights = [0.0] * len(vectors[0])
    n_rows = len(vectors)
    for _ in range(iterations):
        gradients = [0.0] * len(weights)
        for vector, label in zip(vectors, labels, strict=True):
            score = sum(weight * value for weight, value in zip(weights, vector, strict=True))
            error = _sigmoid(score) - label
            for index, value in enumerate(vector):
                gradients[index] += error * value
        for index in range(len(weights)):
            gradients[index] /= n_rows
            if index > 0:
                gradients[index] += l2_penalty * weights[index]
            weights[index] -= learning_rate * gradients[index]
    return preprocessor, weights


def _predict_probability(
    row: Mapping[str, str],
    *,
    preprocessor: Mapping[str, object],
    weights: Sequence[float],
) -> float:
    vector = _vectorize(row, preprocessor)
    if len(vector) != len(weights):
        raise MLBHRResearchBaselineError("feature vector length does not match model")
    score = sum(weight * value for weight, value in zip(weights, vector, strict=True))
    probability = _sigmoid(score)
    return min(max(probability, 0.0), 1.0)


def _labels_and_probabilities(
    rows: Sequence[Mapping[str, str]],
    probabilities: Sequence[float],
) -> tuple[list[int], list[float]]:
    labels = [int(row["hit_hr"]) for row in rows]
    return labels, list(probabilities)


def _log_loss(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    total = 0.0
    for label, probability in zip(labels, probabilities, strict=True):
        clipped = min(max(probability, 1e-15), 1.0 - 1e-15)
        total -= label * math.log(clipped) + (1 - label) * math.log1p(-clipped)
    return total / len(labels) if labels else math.nan


def _brier(labels: Sequence[int], probabilities: Sequence[float]) -> float:
    return (
        sum((probability - label) ** 2 for label, probability in zip(labels, probabilities, strict=True))
        / len(labels)
        if labels
        else math.nan
    )


def _roc_auc(labels: Sequence[int], probabilities: Sequence[float]) -> float | None:
    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if positive_count == 0 or negative_count == 0:
        return None
    ordered = sorted(zip(probabilities, labels, strict=True), key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (
        rank_sum - positive_count * (positive_count + 1) / 2.0
    ) / (positive_count * negative_count)


def _calibration_buckets(
    labels: Sequence[int], probabilities: Sequence[float], bins: int = 10
) -> tuple[list[dict[str, object]], float | None]:
    if not labels:
        return [], None
    buckets: list[dict[str, object]] = []
    calibration_error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        selected = [
            (label, probability)
            for label, probability in zip(labels, probabilities, strict=True)
            if (low <= probability < high) or (index == bins - 1 and probability == 1.0)
        ]
        if not selected:
            continue
        count = len(selected)
        avg_probability = _mean([probability for _, probability in selected])
        observed_rate = _mean([float(label) for label, _ in selected])
        calibration_error += (count / len(labels)) * abs(observed_rate - avg_probability)
        buckets.append(
            {
                "bin": index,
                "lower": low,
                "upper": high,
                "rows": count,
                "avg_probability": avg_probability,
                "observed_rate": observed_rate,
            }
        )
    return buckets, calibration_error


def _threshold_metrics(
    labels: Sequence[int],
    probabilities: Sequence[float],
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> list[dict[str, object]]:
    rows = []
    for threshold in thresholds:
        predicted_positive = [probability >= threshold for probability in probabilities]
        tp = sum(label == 1 and pred for label, pred in zip(labels, predicted_positive, strict=True))
        fp = sum(label == 0 and pred for label, pred in zip(labels, predicted_positive, strict=True))
        fn = sum(label == 1 and not pred for label, pred in zip(labels, predicted_positive, strict=True))
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        rows.append(
            {
                "threshold": threshold,
                "selected_rows": sum(predicted_positive),
                "precision": precision,
                "recall": recall,
            }
        )
    return rows


def _evaluate_probability_series(
    rows: Sequence[Mapping[str, str]],
    probabilities: Sequence[float],
    *,
    series_name: str,
) -> dict[str, object]:
    if not rows:
        return {
            "series_name": series_name,
            "row_count": 0,
            "status": "empty",
        }
    labels, probs = _labels_and_probabilities(rows, probabilities)
    buckets, calibration_error = _calibration_buckets(labels, probs)
    return {
        "series_name": series_name,
        "row_count": len(rows),
        "unique_games": len({row["event_id"] for row in rows}),
        "unique_players": len({row["normalized_player_name"] for row in rows}),
        "unique_dates": len({row["game_date"] for row in rows}),
        "positive_outcomes": sum(labels),
        "base_home_run_rate": sum(labels) / len(labels),
        "log_loss": _log_loss(labels, probs),
        "brier_score": _brier(labels, probs),
        "roc_auc": _roc_auc(labels, probs),
        "calibration_error": calibration_error,
        "calibration_buckets": buckets,
        "thresholds": _threshold_metrics(labels, probs),
        "status": "ok",
    }


def _market_probabilities(rows: Sequence[Mapping[str, str]]) -> list[float]:
    probabilities: list[float] = []
    for row in rows:
        parsed = _float_or_none(row.get("implied_probability"))
        if parsed is None:
            parsed = _float_or_none(row.get("best_available_implied_probability"))
        probabilities.append(min(max(parsed if parsed is not None else 0.0, 0.0), 1.0))
    return probabilities


def _evaluate_split(
    rows: Sequence[Mapping[str, str]],
    *,
    preprocessor: Mapping[str, object],
    weights: Sequence[float],
) -> dict[str, object]:
    model_probs = [
        _predict_probability(row, preprocessor=preprocessor, weights=weights)
        for row in rows
    ]
    market_probs = _market_probabilities(rows)
    model_metrics = _evaluate_probability_series(
        rows, model_probs, series_name="logistic_baseline"
    )
    market_metrics = _evaluate_probability_series(
        rows, market_probs, series_name="raw_sportsbook_implied_probability"
    )
    comparison = {
        "vig_removed": False,
        "vig_removal_note": (
            "The live archive stores only Over 0.5 HR prices, so two-sided vig "
            "removal is unavailable. Market metrics use raw implied probabilities."
        ),
    }
    for metric in ("log_loss", "brier_score", "calibration_error", "roc_auc"):
        model_value = model_metrics.get(metric)
        market_value = market_metrics.get(metric)
        if isinstance(model_value, (int, float)) and isinstance(market_value, (int, float)):
            if metric == "roc_auc":
                comparison[f"{metric}_model_minus_market"] = model_value - market_value
            else:
                comparison[f"{metric}_market_minus_model"] = market_value - model_value
    return {
        "model": model_metrics,
        "market_baseline": market_metrics,
        "comparison": comparison,
    }


def _walk_forward_summary(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    dates = sorted({row["game_date"] for row in rows})
    windows: list[dict[str, object]] = []
    for index in range(1, len(dates)):
        train_dates = set(dates[:index])
        eval_date = dates[index]
        train_rows = [row for row in rows if row["game_date"] in train_dates]
        eval_rows = [row for row in rows if row["game_date"] == eval_date]
        if {row["hit_hr"] for row in train_rows} != {"0", "1"}:
            windows.append(
                {
                    "eval_date": eval_date,
                    "status": "skipped_one_class_training_window",
                    "train_rows": len(train_rows),
                    "eval_rows": len(eval_rows),
                }
            )
            continue
        preprocessor, weights = _fit_logistic(train_rows)
        metrics = _evaluate_split(eval_rows, preprocessor=preprocessor, weights=weights)
        windows.append(
            {
                "eval_date": eval_date,
                "status": "evaluated",
                "train_rows": len(train_rows),
                "eval_rows": len(eval_rows),
                "metrics": metrics,
            }
        )
    evaluated = [window for window in windows if window["status"] == "evaluated"]
    return {
        "method": "expanding_window_by_game_date",
        "window_count": len(windows),
        "evaluated_window_count": len(evaluated),
        "windows": windows,
        "limitation": (
            "Date-level walk-forward windows are reported for small prospective "
            "archives; underpowered windows are diagnostic only."
        ),
    }


def _dependency_versions() -> dict[str, str]:
    versions = {
        "python": platform.python_version(),
    }
    for package in ("pandas", "pytest"):
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = "not_installed"
    return versions


def train_research_logistic_baseline(
    *,
    feature_rows_path: str | Path,
    output_root: str | Path,
    model_version: str = "research-v1",
    generated_at: datetime | str | None = None,
    repository_root: str | Path = DEFAULT_REPOSITORY_ROOT,
) -> TrainingResult:
    """Train and write an immutable research-only logistic baseline bundle."""

    source = Path(feature_rows_path).expanduser().resolve()
    all_rows = _training_rows_from_feature_csv(source)
    eligible_rows = _eligible_training_rows(all_rows)
    if len(eligible_rows) < 2:
        raise MLBHRResearchBaselineError(
            "at least two eligible training rows are required"
        )
    splits = chronological_split_rows(eligible_rows)
    train_rows = splits["train"]
    if {row["hit_hr"] for row in train_rows} != {"0", "1"}:
        raise MLBHRResearchBaselineError(
            "chronological training split does not contain both classes"
        )
    generated = _coerce_datetime_utc(generated_at, "generated_at")
    preprocessor, weights = _fit_logistic(train_rows)
    metrics = {
        split_name: _evaluate_split(
            split_rows,
            preprocessor=preprocessor,
            weights=weights,
        )
        for split_name, split_rows in splits.items()
    }
    metrics["walk_forward"] = _walk_forward_summary(eligible_rows)
    feature_hash = _file_sha256(source)
    dates = sorted({row["game_date"] for row in eligible_rows})
    model_id = (
        "mlb-hr-logreg-baseline-"
        + generated.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + _stable_id(feature_hash, model_version, len(eligible_rows), length=8)
    )
    bundle_dir = Path(output_root).expanduser().resolve() / model_id
    if bundle_dir.exists():
        raise MLBHRResearchBaselineError(f"model bundle already exists: {bundle_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=False)
    commit_sha = _repository_commit_sha(Path(repository_root).expanduser().resolve())
    model_payload: dict[str, object] = {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "model_id": model_id,
        "model_version": model_version,
        "algorithm": "logistic_regression_gradient_descent",
        "parameters": {
            "iterations": 1200,
            "learning_rate": 0.08,
            "l2_penalty": 0.01,
            "class_weighting": "none",
            "calibration": "identity_logistic_probability",
        },
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "required_input_columns": list(MODEL_REQUIRED_INPUT_COLUMNS),
        "numeric_features": list(NUMERIC_MODEL_FEATURES),
        "categorical_features": list(CATEGORICAL_MODEL_FEATURES),
        "preprocessing": preprocessor,
        "feature_order": preprocessor["feature_order"],
        "weights": weights,
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "eligible_for_betting": False,
        "kelly_eligible": False,
    }
    model_json = _write_json_create_once(bundle_dir / "model.json", model_payload)
    model_sha = _file_sha256(model_json)
    metadata_payload: dict[str, object] = {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "model_id": model_id,
        "model_version": model_version,
        "training_timestamp": _iso_z(generated),
        "training_date_range": {
            "start": dates[0] if dates else "",
            "end": dates[-1] if dates else "",
        },
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(preprocessor["feature_order"]),
        "algorithm": "logistic_regression_gradient_descent",
        "parameters": model_payload["parameters"],
        "preprocessing_configuration": preprocessor,
        "training_data_hash": feature_hash,
        "training_data_path": str(source),
        "row_counts": {
            "all_feature_rows": len(all_rows),
            "eligible_training_rows": len(eligible_rows),
            "excluded_rows": len(all_rows) - len(eligible_rows),
            "train": len(splits["train"]),
            "validation": len(splits["validation"]),
            "test": len(splits["test"]),
        },
        "evaluation_metrics": metrics,
        "calibration_metrics": {
            split: payload["model"].get("calibration_error")
            for split, payload in metrics.items()
            if isinstance(payload, Mapping) and "model" in payload
        },
        "exclusion_counts": _exclusion_counts(
            [row for row in all_rows if row.get("eligibility_status") != TRAINING_ELIGIBLE_STATUS]
        ),
        "source_commit_sha": commit_sha,
        "dependency_versions": _dependency_versions(),
        "model_json_sha256": model_sha,
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "official_pick_status": "not_official_not_validated",
    }
    metadata_json = _write_json_create_once(bundle_dir / "metadata.json", metadata_payload)
    metrics_json = _write_json_create_once(bundle_dir / "metrics.json", metrics)
    card = _model_card_text(metadata_payload)
    _write_text_create_once(bundle_dir / "model_card.md", card)
    _write_json_create_once(
        bundle_dir / "bundle_manifest.json",
        {
            "model_id": model_id,
            "metadata_json_sha256": _file_sha256(metadata_json),
            "model_json_sha256": model_sha,
            "metrics_json_sha256": _file_sha256(metrics_json),
            "research_label": RESEARCH_ONLY_LABEL,
            "approval_status": APPROVAL_STATUS,
        },
    )
    return TrainingResult(
        model_id=model_id,
        bundle_dir=bundle_dir,
        metadata=metadata_payload,
        metrics=metrics,
    )


def _model_card_text(metadata: Mapping[str, object]) -> str:
    row_counts = metadata.get("row_counts", {})
    metrics = metadata.get("evaluation_metrics", {})
    lines = [
        "# CourtVision MLB HR Logistic Baseline Model Card",
        "",
        RESEARCH_ONLY_LABEL,
        "",
        f"- Model ID: {metadata.get('model_id')}",
        f"- Model version: {metadata.get('model_version')}",
        f"- Training timestamp: {metadata.get('training_timestamp')}",
        f"- Algorithm: {metadata.get('algorithm')}",
        f"- Training rows: {row_counts.get('train') if isinstance(row_counts, Mapping) else ''}",
        f"- Validation rows: {row_counts.get('validation') if isinstance(row_counts, Mapping) else ''}",
        f"- Test rows: {row_counts.get('test') if isinstance(row_counts, Mapping) else ''}",
        "",
        "## Features",
        "",
        "Only live-archive market features are used: selected best price, raw implied probability, bookmaker count, market dispersion, and hours before game. Player IDs, lineup, pitcher, Statcast, weather, and park features are not available in the current live archive.",
        "",
        "## Market Baseline",
        "",
        "The sportsbook baseline uses raw implied probability. Vig is not removed because the archive stores only Over 0.5 HR prices.",
        "",
        "## Limitations",
        "",
        "This baseline is not a validated betting model and must not feed official picks, Elite gates, Kelly sizing, bankroll logic, or dashboard recommendations.",
        "",
    ]
    if isinstance(metrics, Mapping):
        validation = metrics.get("validation")
        if isinstance(validation, Mapping):
            model = validation.get("model")
            if isinstance(model, Mapping):
                lines.extend(
                    [
                        "## Validation Snapshot",
                        "",
                        f"- Rows: {model.get('row_count')}",
                        f"- Log loss: {model.get('log_loss')}",
                        f"- Brier score: {model.get('brier_score')}",
                        f"- ROC AUC: {model.get('roc_auc')}",
                        f"- Calibration error: {model.get('calibration_error')}",
                        "",
                    ]
                )
    return "\n".join(lines)


def load_model_bundle(bundle_dir: str | Path) -> ModelBundle:
    """Load and validate a research model bundle."""

    root = Path(bundle_dir).expanduser().resolve()
    metadata_path = root / "metadata.json"
    model_path = root / "model.json"
    if not metadata_path.is_file() or not model_path.is_file():
        raise MLBHRResearchBaselineError(
            f"model bundle missing metadata.json or model.json: {root}"
        )
    try:
        metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        model_payload = json.loads(model_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MLBHRResearchBaselineError(f"could not load model bundle: {exc}") from exc
    if metadata_payload.get("schema_version") != MODEL_BUNDLE_SCHEMA_VERSION:
        raise MLBHRResearchBaselineError("unsupported model metadata schema")
    if model_payload.get("schema_version") != MODEL_BUNDLE_SCHEMA_VERSION:
        raise MLBHRResearchBaselineError("unsupported model schema")
    if metadata_payload.get("model_id") != model_payload.get("model_id"):
        raise MLBHRResearchBaselineError("model_id mismatch inside bundle")
    if model_payload.get("feature_schema_version") not in COMPATIBLE_FEATURE_SCHEMA_VERSIONS:
        raise MLBHRResearchBaselineError("model expects unsupported feature schema")
    if tuple(model_payload.get("required_input_columns", ())) != MODEL_REQUIRED_INPUT_COLUMNS:
        raise MLBHRResearchBaselineError("model required input columns changed")
    recorded_sha = metadata_payload.get("model_json_sha256")
    if not isinstance(recorded_sha, str) or recorded_sha != _file_sha256(model_path):
        raise MLBHRResearchBaselineError("model artifact integrity check failed")
    feature_order = model_payload.get("feature_order")
    weights = model_payload.get("weights")
    if (
        not isinstance(feature_order, list)
        or not isinstance(weights, list)
        or len(feature_order) != len(weights)
    ):
        raise MLBHRResearchBaselineError("model feature order and weights are invalid")
    return ModelBundle(bundle_dir=root, metadata=metadata_payload, model=model_payload)


def resolve_mlb_model_bundle(
    model_bundle_dir: str | Path | None = None,
    *,
    model_root: str | Path = DEFAULT_MODEL_ROOT,
) -> Path:
    """Resolve an explicit bundle or the latest valid local research bundle."""

    if model_bundle_dir is not None:
        return load_model_bundle(model_bundle_dir).bundle_dir
    root = Path(model_root).expanduser().resolve()
    if not root.is_dir():
        raise MLBHRResearchBaselineError(
            f"MLB research model root does not exist: {root}"
        )
    valid: list[tuple[str, str, Path]] = []
    for candidate in root.iterdir():
        if (
            not candidate.is_dir()
            or candidate.name.startswith(".pytest_tmp")
        ):
            continue
        try:
            bundle = load_model_bundle(candidate)
        except MLBHRResearchBaselineError:
            continue
        trained_at = _clean(bundle.metadata.get("training_timestamp"))
        valid.append((trained_at, bundle.model_id, bundle.bundle_dir))
    if not valid:
        raise MLBHRResearchBaselineError(
            f"no valid MLB research model bundle found under {root}"
        )
    return max(valid, key=lambda item: (item[0], item[1], str(item[2])))[2]


def resolve_mlb_odds_csv(
    odds_path: str | Path | None = None,
    *,
    repository_root: str | Path = DEFAULT_REPOSITORY_ROOT,
) -> Path:
    """Resolve the explicit local odds archive or the canonical free source."""

    source = (
        Path(odds_path)
        if odds_path is not None
        else (
            Path(repository_root)
            / "data"
            / "theoddsapi"
            / "live_hr_snapshots"
            / "live_hr_props_master.csv"
        )
    )
    resolved = source.expanduser().resolve()
    if not resolved.is_file():
        raise MLBHRResearchBaselineError(
            f"MLB local odds CSV does not exist: {resolved}"
        )
    return resolved


def resolve_mlb_output_dir(
    output_dir: str | Path | None,
    *,
    target_date: str,
    artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
) -> Path:
    """Resolve the explicit output or the canonical date-specific run path."""

    prediction_date = _target_operating_date_text(target_date)
    if prediction_date is None:
        raise MLBHRResearchBaselineError(
            "target_date is required to resolve the MLB output directory"
        )
    source = (
        Path(output_dir)
        if output_dir is not None
        else Path(artifact_root) / "daily_runs" / prediction_date
    )
    return source.expanduser().resolve()


def predict_model_probability(
    row: Mapping[str, str],
    bundle: ModelBundle,
) -> float:
    for column in MODEL_REQUIRED_INPUT_COLUMNS:
        if column not in row:
            raise MLBHRResearchBaselineError(
                f"prediction row missing required feature: {column}"
            )
    preprocessor = bundle.model.get("preprocessing")
    weights = bundle.model.get("weights")
    if not isinstance(preprocessor, Mapping) or not isinstance(weights, list):
        raise MLBHRResearchBaselineError("invalid loaded model payload")
    return _predict_probability(row, preprocessor=preprocessor, weights=[float(w) for w in weights])


def _prediction_outcome_status(
    *,
    prediction_count: int,
    input_diagnostics: Mapping[str, object],
) -> str:
    if prediction_count > 0:
        return "PASS"
    if _manifest_int(input_diagnostics, "requested_date_row_count") == 0:
        return "NO_DATA"
    return "NO_ELIGIBLE_PREDICTIONS"


def _generate_daily_research_predictions_internal(
    *,
    model_bundle_dir: str | Path,
    odds_path: str | Path = DEFAULT_ODDS_CSV,
    output_dir: str | Path | None = None,
    target_date: str | None = None,
    prediction_timestamp: datetime | str | None = None,
    repository_root: str | Path = DEFAULT_REPOSITORY_ROOT,
    dry_run: bool = False,
    run_nonce: str | None = None,
    prediction_run_id: str | None = None,
) -> PredictionRunResult:
    """Generate immutable current-day research predictions from local odds."""

    bundle = load_model_bundle(model_bundle_dir)
    target_operating_date = _target_operating_date_text(target_date)
    timestamp = _coerce_datetime_utc(prediction_timestamp, "prediction_timestamp")
    feature_result = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=None,
        target_date=target_operating_date,
        prediction_timestamp=timestamp,
        mode="prediction",
        generated_at=timestamp,
        repository_root=repository_root,
    )
    eligible_rows = [
        row
        for row in feature_result.rows
        if row.get("eligibility_status") == PREDICTION_ELIGIBLE_STATUS
        and not row.get("exclusion_reason")
    ]
    run_id = str(
        prediction_run_id
        or (
            "mlb-hr-research-pred-"
            + timestamp.strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + _stable_id(
                bundle.model_id,
                feature_result.manifest.get("source_hashes", {}),
                len(eligible_rows),
                run_nonce or "",
                length=8,
            )
        )
    )
    predictions: list[dict[str, str]] = []
    for row in eligible_rows:
        model_probability = predict_model_probability(row, bundle)
        implied = _float_or_none(row.get("implied_probability")) or 0.0
        prediction_id = _stable_id(
            PREDICTION_SCHEMA_VERSION,
            run_id,
            bundle.model_id,
            row.get("event_id"),
            row.get("normalized_player_name"),
            row.get("sportsbook"),
            row.get("snapshot_time"),
        )
        prediction = {
            "prediction_id": prediction_id,
            "prediction_run_id": run_id,
            "prediction_schema_version": PREDICTION_SCHEMA_VERSION,
            "research_label": RESEARCH_ONLY_LABEL,
            "model_id": bundle.model_id,
            "model_version": bundle.model_version,
            "event_id": row.get("event_id", ""),
            "game_date": row.get("game_date", ""),
            "commence_time": row.get("commence_time", ""),
            "commence_time_utc": row.get("commence_time_utc", row.get("commence_time", "")),
            "game_date_utc": row.get("game_date_utc", ""),
            "game_date_operating": row.get("game_date_operating", row.get("game_date", "")),
            "operating_timezone": row.get(
                "operating_timezone", COURTVISION_OPERATING_TIMEZONE_NAME
            ),
            "player_id": row.get("player_id", ""),
            "player_name": row.get("player_name", ""),
            "normalized_player_name": row.get("normalized_player_name", ""),
            "team": row.get("team", ""),
            "opponent": row.get("opponent", ""),
            "home_team": row.get("home_team", ""),
            "away_team": row.get("away_team", ""),
            "event_eligibility_status": row.get("event_eligibility_status", ""),
            "event_eligibility_reason": row.get("event_eligibility_reason", ""),
            "event_eligibility_rule": row.get("event_eligibility_rule", ""),
            "sportsbook": row.get("sportsbook", ""),
            "sportsbook_name": row.get("sportsbook_name", ""),
            "american_odds": row.get("american_odds", ""),
            "decimal_odds": row.get("decimal_odds", ""),
            "implied_probability": row.get("implied_probability", ""),
            "model_probability": _format_float(model_probability),
            "probability_edge": _format_float(model_probability - implied),
            "prediction_timestamp": _iso_z(timestamp),
            "snapshot_time": row.get("snapshot_time", ""),
            "market_key": row.get("market_key", ""),
            "side": row.get("side", ""),
            "point": row.get("point", ""),
            "lineup_status": row.get("lineup_status", "unknown"),
            "eligibility_status": row.get("eligibility_status", ""),
            "exclusion_reason": row.get("exclusion_reason", ""),
            "source_manifest_reference": row.get("source_manifest_reference", ""),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "repository_commit_sha": row.get("repository_commit_sha", ""),
            "model_bundle_path": str(bundle.bundle_dir),
            "source_odds_sha256": row.get("source_odds_sha256", ""),
        }
        predictions.append({column: prediction.get(column, "") for column in PREDICTION_COLUMNS})

    input_diagnostics = _manifest_dict(
        feature_result.manifest, "input_diagnostics"
    )
    exclusion_reasons = _exclusion_counts(feature_result.exclusions)
    outcome_status = _prediction_outcome_status(
        prediction_count=len(predictions),
        input_diagnostics=input_diagnostics,
    )
    manifest: dict[str, object] = {
        "prediction_schema_version": PREDICTION_SCHEMA_VERSION,
        "prediction_run_id": run_id,
        "model_id": bundle.model_id,
        "model_version": bundle.model_version,
        "prediction_timestamp": _iso_z(timestamp),
        "row_count": len(predictions),
        "excluded_row_count": len(feature_result.exclusions),
        "eligible_row_count": len(eligible_rows),
        "status": outcome_status,
        "exclusion_reasons": exclusion_reasons,
        "input_diagnostics": input_diagnostics,
        **input_diagnostics,
        "target_date": target_operating_date or "",
        "target_date_semantics": "courtvision_operating_date",
        "operating_timezone": COURTVISION_OPERATING_TIMEZONE_NAME,
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "rerun_behavior": "creates a new immutable timestamped prediction snapshot",
        "run_nonce": run_nonce or "",
        "no_post_start_predictions": True,
        "feature_manifest": feature_result.manifest,
    }
    written_dir: Path | None = None
    if output_dir is not None and not dry_run:
        written_dir = Path(output_dir).expanduser().resolve()
        if written_dir.exists():
            raise MLBHRResearchBaselineError(
                f"prediction output directory already exists: {written_dir}"
            )
        predictions_path = publish_csv_rows(
            written_dir / "predictions.csv",
            PREDICTION_COLUMNS,
            predictions,
            prediction_date=target_operating_date
            or courtvision_operating_date(timestamp).isoformat(),
            caller="mlb_hr_research_baseline:internal_engine",
            artifact_label="mlb_predictions",
            create_once=True,
        )
        publish_csv_rows(
            written_dir / "excluded_rows.csv",
            EXCLUSION_COLUMNS,
            feature_result.exclusions,
            prediction_date=target_operating_date
            or courtvision_operating_date(timestamp).isoformat(),
            caller="mlb_hr_research_baseline:internal_engine",
            artifact_label="mlb_prediction_exclusions",
            create_once=True,
        )
        publish_json(
            written_dir / "exclusion_summary.json",
            {
                "prediction_date": target_operating_date
                or courtvision_operating_date(timestamp).isoformat(),
                "status": outcome_status,
                "excluded_row_count": len(feature_result.exclusions),
                "exclusion_reasons": exclusion_reasons,
                "input_diagnostics": input_diagnostics,
            },
            prediction_date=target_operating_date
            or courtvision_operating_date(timestamp).isoformat(),
            caller="mlb_hr_research_baseline:internal_engine",
            artifact_label="mlb_prediction_exclusion_summary",
            create_once=True,
            trailing_newline=True,
            sort_keys=True,
        )
        manifest["predictions_csv_sha256"] = _file_sha256(predictions_path)
        publish_json(
            written_dir / "manifest.json",
            manifest,
            prediction_date=target_operating_date
            or courtvision_operating_date(timestamp).isoformat(),
            caller="mlb_hr_research_baseline:internal_engine",
            artifact_label="mlb_prediction_manifest",
            create_once=True,
            trailing_newline=True,
            sort_keys=True,
        )
    return PredictionRunResult(
        prediction_run_id=run_id,
        predictions=tuple(predictions),
        exclusions=feature_result.exclusions,
        manifest=manifest,
        output_dir=written_dir,
        application_status=outcome_status,
        lifecycle_status="DISABLED",
        exclusion_reasons=exclusion_reasons,
        input_diagnostics={
            str(key): int(value)
            for key, value in input_diagnostics.items()
        },
        resolved_model_dir=bundle.bundle_dir,
        resolved_odds_csv=Path(odds_path).expanduser().resolve(),
        resolved_output_dir=(
            Path(output_dir).expanduser().resolve()
            if output_dir is not None
            else None
        ),
    )


class _MLBHRResearchPredictionEngine:
    """Research-only engine adapter; it never performs settlement or grading."""

    sport = "mlb"
    modes = frozenset({"research"})

    def __init__(
        self,
        *,
        model_bundle_dir: str | Path,
        odds_path: str | Path,
        prediction_timestamp: datetime,
        repository_root: str | Path,
        run_nonce: str | None,
    ) -> None:
        self.model_bundle_dir = Path(model_bundle_dir)
        self.odds_path = Path(odds_path)
        self.prediction_timestamp = prediction_timestamp
        self.repository_root = Path(repository_root)
        self.run_nonce = run_nonce
        self.runtime = self

    def execute(self, request: PredictionRequest) -> EnginePrediction:
        prediction_run = _generate_daily_research_predictions_internal(
            model_bundle_dir=self.model_bundle_dir,
            odds_path=self.odds_path,
            output_dir=None,
            target_date=request.prediction_date,
            prediction_timestamp=self.prediction_timestamp,
            repository_root=self.repository_root,
            dry_run=True,
            run_nonce=self.run_nonce,
            prediction_run_id=request.run_id,
        )
        feature_manifest = prediction_run.manifest.get(
            "feature_manifest", {}
        )
        source_hashes = (
            feature_manifest.get("source_hashes", {})
            if isinstance(feature_manifest, Mapping)
            else {}
        )
        return EnginePrediction(
            outputs={
                "prediction_run": prediction_run,
                "predictions": prediction_run.predictions,
                "exclusions": prediction_run.exclusions,
                "summary": {
                    "status": prediction_run.manifest.get("status"),
                    "prediction_count": len(prediction_run.predictions),
                    "excluded_row_count": len(prediction_run.exclusions),
                    "eligible_row_count": prediction_run.manifest.get(
                        "eligible_row_count", 0
                    ),
                    "exclusion_reasons": dict(
                        prediction_run.exclusion_reasons
                    ),
                    **dict(prediction_run.input_diagnostics),
                    "research_label": RESEARCH_ONLY_LABEL,
                },
            },
            provider_provenance={
                "data_access": "local_only",
                "odds_path": str(self.odds_path),
                "source_hashes": dict(source_hashes)
                if isinstance(source_hashes, Mapping)
                else {},
            },
            model_version=str(
                prediction_run.manifest.get("model_version", "")
            ),
            status=str(prediction_run.manifest.get("status", "")) or None,
        )


def _load_existing_prediction_run(
    output_dir: Path,
    *,
    target_date: str,
    resolved_model_dir: Path | None,
    resolved_odds_csv: Path | None,
    repository_root: str | Path,
) -> PredictionRunResult:
    required_paths = {
        "predictions": output_dir / "predictions.csv",
        "excluded_rows": output_dir / "excluded_rows.csv",
        "manifest": output_dir / "manifest.json",
    }
    missing = [
        path.name for path in required_paths.values() if not path.is_file()
    ]
    if missing:
        raise MLBHRResearchBaselineError(
            "prediction output directory already exists but is incomplete: "
            + ", ".join(sorted(missing))
        )
    verification = verify_prediction_artifacts(predictions_root=output_dir)
    if not verification.passed:
        raise MLBHRResearchBaselineError(
            "existing immutable prediction output failed verification: "
            + "; ".join(verification.errors)
        )
    _, predictions = _read_csv(
        required_paths["predictions"],
        required_columns=PREDICTION_COLUMNS,
        label="predictions",
    )
    _, exclusions = _read_csv(
        required_paths["excluded_rows"],
        required_columns=EXCLUSION_COLUMNS,
        label="prediction exclusions",
    )
    try:
        manifest = json.loads(
            required_paths["manifest"].read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise MLBHRResearchBaselineError(
            f"could not read existing prediction manifest: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise MLBHRResearchBaselineError(
            "existing prediction manifest must be a JSON object"
        )
    manifest_date = _clean(manifest.get("target_date"))
    if manifest_date and manifest_date != target_date:
        raise MLBHRResearchBaselineError(
            "existing prediction manifest target_date does not match "
            f"{target_date}"
        )
    if resolved_model_dir is None:
        manifest_model_path = _clean(manifest.get("resolved_model_dir"))
        resolved_model_dir = (
            Path(manifest_model_path).expanduser().resolve()
            if manifest_model_path
            else (
                DEFAULT_MODEL_ROOT
                / _clean(manifest.get("model_id"))
            ).resolve()
        )
    if resolved_odds_csv is None:
        provenance = _manifest_mapping(manifest.get("data_provenance"))
        manifest_odds_path = (
            _clean(manifest.get("resolved_odds_csv"))
            or _clean(provenance.get("odds_path"))
        )
        resolved_odds_csv = (
            Path(manifest_odds_path).expanduser().resolve()
            if manifest_odds_path
            else (
                Path(repository_root)
                / "data"
                / "theoddsapi"
                / "live_hr_snapshots"
                / "live_hr_props_master.csv"
            ).resolve()
        )
    feature_manifest = _manifest_mapping(manifest.get("feature_manifest"))
    input_diagnostics = _manifest_dict(manifest, "input_diagnostics")
    if not input_diagnostics:
        input_diagnostics = _manifest_dict(
            feature_manifest, "input_diagnostics"
        )
    if not input_diagnostics:
        source_hashes = _manifest_mapping(
            feature_manifest.get("source_hashes")
        )
        expected_odds_sha = _clean(
            source_hashes.get("odds_csv_sha256")
        )
        if (
            expected_odds_sha
            and resolved_odds_csv.is_file()
            and expected_odds_sha == _file_sha256(resolved_odds_csv)
        ):
            rebuilt = build_live_hr_research_features(
                odds_path=resolved_odds_csv,
                results_path=None,
                target_date=target_date,
                prediction_timestamp=manifest.get("prediction_timestamp"),
                mode="prediction",
                generated_at=manifest.get("prediction_timestamp"),
                repository_root=repository_root,
            )
            input_diagnostics = _manifest_dict(
                rebuilt.manifest, "input_diagnostics"
            )
    exclusion_reasons = _manifest_dict(manifest, "exclusion_reasons")
    if not exclusion_reasons:
        exclusion_reasons = _manifest_dict(
            feature_manifest, "exclusion_counts"
        )
    artifact_paths = {
        label: str(path) for label, path in required_paths.items()
    }
    optional_paths = {
        "exclusion_summary": output_dir / "exclusion_summary.json",
        "application_manifest": output_dir / "application_manifest.json",
    }
    artifact_paths.update(
        {
            label: str(path)
            for label, path in optional_paths.items()
            if path.is_file()
        }
    )
    application_manifest = optional_paths["application_manifest"]
    return PredictionRunResult(
        prediction_run_id=_clean(manifest.get("prediction_run_id")),
        predictions=predictions,
        exclusions=exclusions,
        manifest=manifest,
        output_dir=output_dir,
        application_status="PROTECTED_NO_OP",
        lifecycle_status="PROTECTED_NO_OP",
        application_manifest_path=(
            application_manifest if application_manifest.is_file() else None
        ),
        exclusion_reasons={
            str(key): int(value)
            for key, value in exclusion_reasons.items()
        },
        input_diagnostics={
            str(key): int(value)
            for key, value in input_diagnostics.items()
        },
        artifact_paths=artifact_paths,
        resolved_model_dir=resolved_model_dir,
        resolved_odds_csv=resolved_odds_csv,
        resolved_output_dir=output_dir,
    )


def generate_daily_research_predictions(
    *,
    model_bundle_dir: str | Path | None = None,
    odds_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    target_date: str | None = None,
    prediction_timestamp: datetime | str | None = None,
    repository_root: str | Path = DEFAULT_REPOSITORY_ROOT,
    dry_run: bool = False,
    run_nonce: str | None = None,
) -> PredictionRunResult:
    """Compatibility wrapper over the canonical research application service."""

    timestamp = _coerce_datetime_utc(
        prediction_timestamp,
        "prediction_timestamp",
    )
    prediction_date = (
        _target_operating_date_text(target_date)
        or courtvision_operating_date(timestamp).isoformat()
    )
    resolved_output = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else None
    )
    if resolved_output is not None and not dry_run and resolved_output.exists():
        return _load_existing_prediction_run(
            resolved_output,
            target_date=prediction_date,
            resolved_model_dir=(
                Path(model_bundle_dir).expanduser().resolve()
                if model_bundle_dir is not None
                else None
            ),
            resolved_odds_csv=(
                Path(odds_path).expanduser().resolve()
                if odds_path is not None
                else None
            ),
            repository_root=repository_root,
        )
    resolved_model_dir = resolve_mlb_model_bundle(model_bundle_dir)
    resolved_odds_csv = resolve_mlb_odds_csv(
        odds_path,
        repository_root=repository_root,
    )
    _, preflight_odds_rows = _read_csv(
        resolved_odds_csv,
        required_columns=ODDS_REQUIRED_COLUMNS,
        label="odds",
    )
    for preflight_row in preflight_odds_rows:
        _validate_odds_row(preflight_row)

    engine = _MLBHRResearchPredictionEngine(
        model_bundle_dir=resolved_model_dir,
        odds_path=resolved_odds_csv,
        prediction_timestamp=timestamp,
        repository_root=repository_root,
        run_nonce=run_nonce,
    )
    published_manifest: dict[str, object] = {}

    def publish(
        request: PredictionRequest,
        run_id: str,
        engine_prediction: EnginePrediction,
    ) -> Mapping[str, Path]:
        if resolved_output is None or request.dry_run:
            return {}
        prediction_run = engine_prediction.outputs["prediction_run"]
        if not isinstance(prediction_run, PredictionRunResult):
            raise MLBHRResearchBaselineError(
                "MLB research engine returned an invalid result"
            )
        predictions_path = publish_csv_rows(
            resolved_output / "predictions.csv",
            PREDICTION_COLUMNS,
            prediction_run.predictions,
            prediction_date=request.prediction_date,
            caller="mlb_hr_research_baseline:prediction_application",
            artifact_label="mlb_predictions",
            create_once=True,
        )
        exclusions_path = publish_csv_rows(
            resolved_output / "excluded_rows.csv",
            EXCLUSION_COLUMNS,
            prediction_run.exclusions,
            prediction_date=request.prediction_date,
            caller="mlb_hr_research_baseline:prediction_application",
            artifact_label="mlb_prediction_exclusions",
            create_once=True,
        )
        exclusion_summary_path = publish_json(
            resolved_output / "exclusion_summary.json",
            {
                "prediction_date": request.prediction_date,
                "status": prediction_run.manifest.get("status"),
                "excluded_row_count": len(prediction_run.exclusions),
                "exclusion_reasons": dict(
                    prediction_run.exclusion_reasons
                ),
                "input_diagnostics": dict(
                    prediction_run.input_diagnostics
                ),
            },
            prediction_date=request.prediction_date,
            caller="mlb_hr_research_baseline:prediction_application",
            artifact_label="mlb_prediction_exclusion_summary",
            create_once=True,
            trailing_newline=True,
            sort_keys=True,
        )
        staged_metadata = current_publication_metadata()
        manifest = dict(prediction_run.manifest)
        predictions_metadata = staged_metadata.get("mlb_predictions")
        if predictions_metadata is not None:
            manifest["predictions_csv_sha256"] = (
                predictions_metadata.sha256
            )
        manifest.update(
            {
                "sport": "mlb",
                "mode": "research",
                "run_id": run_id,
                "resolved_model_dir": str(resolved_model_dir),
                "resolved_odds_csv": str(resolved_odds_csv),
                "resolved_output_dir": str(resolved_output),
                "data_provenance": dict(
                    engine_prediction.provider_provenance
                ),
                "artifact_hashes": {
                    label: item.sha256
                    for label, item in staged_metadata.items()
                },
            }
        )
        published_manifest.update(manifest)
        manifest_path = publish_json(
            resolved_output / "manifest.json",
            manifest,
            prediction_date=request.prediction_date,
            caller="mlb_hr_research_baseline:prediction_application",
            artifact_label="mlb_prediction_manifest",
            create_once=True,
            trailing_newline=True,
            sort_keys=True,
        )
        return {
            "predictions": predictions_path,
            "excluded_rows": exclusions_path,
            "exclusion_summary": exclusion_summary_path,
            "manifest": manifest_path,
        }

    publisher = (
        NoArtifactPublisher()
        if resolved_output is None or dry_run
        else CallbackPredictionPublisher(
            publish,
            primary_artifact_label="predictions",
        )
    )
    metadata: dict[str, Any] = {
        "entrypoint": "mlb_hr_research_baseline",
        "command": "mlb_hr_research_baseline predict",
        "run_id_prefix": "mlb-hr-research-pred",
        "write_application_manifest": bool(
            resolved_output is not None and not dry_run
        ),
        "lock_enabled": not dry_run,
    }
    canonical_run_id = (
        "mlb-hr-research-pred-"
        + timestamp.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + _stable_id(
            str(resolved_model_dir),
            _file_sha256(resolved_odds_csv),
            prediction_date,
            run_nonce or "",
            length=8,
        )
    )
    if resolved_output is not None:
        metadata["lock_path"] = str(
            resolved_output.parent
            / f".prediction_mlb_research_{prediction_date}.lock"
        )
        metadata["application_manifest_path"] = str(
            resolved_output / "application_manifest.json"
        )
    service = PredictionApplicationService(
        registry=PredictionEngineRegistry([engine]),
        publisher=publisher,
        lifecycle=(
            DisabledPredictionLifecycle()
            if dry_run or resolved_output is None
            else ShadowPredictionLifecycle(
                repository_root=repository_root,
            )
        ),
    )
    application_result = service.run(
        PredictionRequest(
            sport="mlb",
            prediction_date=prediction_date,
            mode="research",
            run_id=canonical_run_id,
            out_dir=(
                str(resolved_output.parent)
                if resolved_output is not None
                else str(DEFAULT_ARTIFACT_ROOT)
            ),
            dry_run=dry_run,
            metadata=metadata,
        )
    )
    prediction_run = application_result.outputs.get("prediction_run")
    if not isinstance(prediction_run, PredictionRunResult):
        raise MLBHRResearchBaselineError(
            "canonical application returned an invalid MLB result"
        )
    return PredictionRunResult(
        prediction_run_id=application_result.run_id,
        predictions=prediction_run.predictions,
        exclusions=prediction_run.exclusions,
        manifest=(
            published_manifest
            if published_manifest
            else prediction_run.manifest
        ),
        output_dir=(
            resolved_output
            if resolved_output is not None and not dry_run
            else None
        ),
        application_status=application_result.status,
        lifecycle_status=application_result.lifecycle_status,
        application_manifest_path=(
            Path(application_result.manifest_path)
            if application_result.manifest_path
            else None
        ),
        exclusion_reasons=prediction_run.exclusion_reasons,
        input_diagnostics=prediction_run.input_diagnostics,
        artifact_paths=dict(application_result.artifact_paths),
        resolved_model_dir=resolved_model_dir,
        resolved_odds_csv=resolved_odds_csv,
        resolved_output_dir=resolved_output,
    )


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path.with_suffix(path.suffix + ".lock")
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError as exc:
            raise MLBHRResearchBaselineError(
                f"ledger lock already exists: {self.path}"
            ) from exc
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _read_ledger(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    if not path.exists():
        return LEDGER_COLUMNS, []
    columns, rows = _read_csv(path, required_columns=LEDGER_COLUMNS, label="ledger")
    if tuple(columns) != LEDGER_COLUMNS:
        raise MLBHRResearchBaselineError("ledger schema does not match expected columns")
    return columns, [dict(row) for row in rows]


def _append_ledger_rows(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    try:
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(LEDGER_COLUMNS))
            if not file_exists:
                writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in LEDGER_COLUMNS})
    except OSError as exc:
        raise MLBHRResearchBaselineError(f"could not append ledger: {exc}") from exc


def append_predictions_to_ledger(
    *,
    predictions: Sequence[Mapping[str, str]] | None = None,
    predictions_csv: str | Path | None = None,
    ledger_path: str | Path,
) -> LedgerAppendResult:
    """Append immutable prediction records to the prospective research ledger."""

    if predictions is None:
        if predictions_csv is None:
            raise MLBHRResearchBaselineError("predictions or predictions_csv is required")
        _, loaded = _read_csv(
            predictions_csv,
            required_columns=PREDICTION_COLUMNS,
            label="predictions",
        )
        predictions = loaded
    ledger = Path(ledger_path).expanduser().resolve()
    prediction_artifact_sha = _file_sha256(predictions_csv) if predictions_csv else _canonical_sha256({"predictions": list(predictions)})
    with _FileLock(ledger):
        _, existing = _read_ledger(ledger)
        existing_predictions = {
            row["prediction_id"]
            for row in existing
            if row.get("record_type") == "prediction"
        }
        new_rows: list[dict[str, str]] = []
        for prediction in predictions:
            prediction_id = _clean(prediction.get("prediction_id"))
            if not prediction_id:
                raise MLBHRResearchBaselineError("prediction row missing prediction_id")
            if prediction_id in existing_predictions:
                raise MLBHRResearchBaselineError(
                    f"prediction already exists in ledger: {prediction_id}"
                )
            record_id = _stable_id(LEDGER_SCHEMA_VERSION, "prediction", prediction_id)
            new_rows.append(
                {
                    "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                    "ledger_record_id": record_id,
                    "record_type": "prediction",
                    "prediction_id": prediction_id,
                    "prediction_run_id": _clean(prediction.get("prediction_run_id")),
                    "model_id": _clean(prediction.get("model_id")),
                    "game_date": _clean(prediction.get("game_date")),
                    "event_id": _clean(prediction.get("event_id")),
                    "commence_time": _clean(prediction.get("commence_time")),
                    "player_id": _clean(prediction.get("player_id")),
                    "player_name": _clean(prediction.get("player_name")),
                    "normalized_player_name": _clean(prediction.get("normalized_player_name")),
                    "sportsbook": _clean(prediction.get("sportsbook")),
                    "original_odds": _clean(prediction.get("american_odds")),
                    "original_decimal_odds": _clean(prediction.get("decimal_odds")),
                    "original_implied_probability": _clean(prediction.get("implied_probability")),
                    "model_probability": _clean(prediction.get("model_probability")),
                    "prediction_timestamp": _clean(prediction.get("prediction_timestamp")),
                    "prediction_artifact_sha256": prediction_artifact_sha,
                    "source_manifest_reference": _clean(prediction.get("source_manifest_reference")),
                    "repository_commit_sha": _clean(prediction.get("repository_commit_sha")),
                    "closing_odds": "",
                    "closing_implied_probability": "",
                    "closing_line_movement": "",
                    "final_result": "",
                    "grade": "",
                    "unit_profit_loss": "",
                    "settlement_timestamp": "",
                    "settlement_source": "",
                    "settlement_status": "unsettled",
                    "manual_review_status": "",
                    "integrity_status": "prediction_recorded",
                    "research_label": RESEARCH_ONLY_LABEL,
                }
            )
        _append_ledger_rows(ledger, new_rows)
    return LedgerAppendResult(ledger_path=ledger, appended_rows=len(new_rows))


def _prediction_artifact_dir(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if (root / "predictions.csv").is_file() and (root / "manifest.json").is_file():
        return root
    nested = root / "predictions"
    if (nested / "predictions.csv").is_file() and (nested / "manifest.json").is_file():
        return nested
    matches = sorted(root.rglob("predictions.csv")) if root.exists() else []
    candidate_dirs = [
        match.parent for match in matches if (match.parent / "manifest.json").is_file()
    ]
    if len(candidate_dirs) == 1:
        return candidate_dirs[0]
    if not root.exists():
        raise MLBHRResearchBaselineError(f"predictions root does not exist: {root}")
    raise MLBHRResearchBaselineError(
        f"could not identify a single prediction artifact directory under {root}"
    )


def _load_prediction_artifact(
    path: str | Path,
) -> tuple[Path, tuple[dict[str, str], ...], dict[str, object], str]:
    artifact_dir = _prediction_artifact_dir(path)
    predictions_path = artifact_dir / "predictions.csv"
    manifest_path = artifact_dir / "manifest.json"
    _, predictions = _read_csv(
        predictions_path,
        required_columns=PREDICTION_COLUMNS,
        label="predictions",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MLBHRResearchBaselineError(
            f"could not read prediction manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise MLBHRResearchBaselineError("prediction manifest must be a JSON object")
    return artifact_dir, predictions, manifest, _file_sha256(predictions_path)


def _duplicate_values(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def verify_prediction_artifacts(
    *,
    predictions_root: str | Path,
    ledger_path: str | Path | None = None,
) -> VerificationResult:
    """Verify immutable daily prediction evidence and optional ledger linkage."""

    artifact_dir, predictions, manifest, predictions_sha = _load_prediction_artifact(
        predictions_root
    )
    errors: list[str] = []
    warnings: list[str] = []
    manifest_schema = _clean(manifest.get("prediction_schema_version"))
    if manifest_schema != PREDICTION_SCHEMA_VERSION:
        errors.append(
            "manifest prediction_schema_version mismatch: "
            f"{manifest_schema or '<blank>'}"
        )
    manifest_hash = _clean(manifest.get("predictions_csv_sha256"))
    if not manifest_hash:
        errors.append("manifest missing predictions_csv_sha256")
    elif manifest_hash != predictions_sha:
        errors.append("predictions.csv sha256 does not match manifest")
    manifest_run_id = _clean(manifest.get("prediction_run_id"))
    manifest_model_id = _clean(manifest.get("model_id"))
    feature_manifest = manifest.get("feature_manifest")
    if isinstance(feature_manifest, Mapping):
        feature_schema = _clean(feature_manifest.get("feature_schema_version"))
        if feature_schema != FEATURE_SCHEMA_VERSION:
            errors.append(
                "feature_manifest feature_schema_version mismatch: "
                f"{feature_schema or '<blank>'}"
            )
    else:
        warnings.append("manifest missing embedded feature_manifest")

    prediction_ids = [_clean(row.get("prediction_id")) for row in predictions]
    for duplicate_id in _duplicate_values(prediction_ids):
        errors.append(f"duplicate prediction_id in artifact: {duplicate_id}")
    duplicate_market_keys = _duplicate_values(
        "|".join(
            [
                _clean(row.get("event_id")),
                _clean(row.get("normalized_player_name")),
                _clean(row.get("sportsbook")),
                _clean(row.get("market_key")),
                _clean(row.get("point")),
            ]
        )
        for row in predictions
    )
    for duplicate_key in duplicate_market_keys:
        errors.append(f"duplicate event/player/book/market prediction: {duplicate_key}")

    for row_number, row in enumerate(predictions, start=2):
        prefix = f"predictions.csv row {row_number}"
        if _clean(row.get("prediction_schema_version")) != PREDICTION_SCHEMA_VERSION:
            errors.append(f"{prefix} has unexpected prediction_schema_version")
        if _clean(row.get("research_label")) != RESEARCH_ONLY_LABEL:
            errors.append(f"{prefix} missing research-only label")
        if manifest_run_id and _clean(row.get("prediction_run_id")) != manifest_run_id:
            errors.append(f"{prefix} prediction_run_id does not match manifest")
        if manifest_model_id and _clean(row.get("model_id")) != manifest_model_id:
            errors.append(f"{prefix} model_id does not match manifest")
        if _clean(row.get("feature_schema_version")) != FEATURE_SCHEMA_VERSION:
            errors.append(f"{prefix} feature_schema_version mismatch")
        if not _clean(row.get("source_manifest_reference")):
            warnings.append(f"{prefix} missing source_manifest_reference")
        try:
            prediction_time = _parse_datetime(
                row.get("prediction_timestamp"), "prediction_timestamp"
            )
            commence_time = _parse_datetime(row.get("commence_time"), "commence_time")
            snapshot_time = _parse_datetime(row.get("snapshot_time"), "snapshot_time")
        except MLBHRResearchBaselineError as exc:
            errors.append(f"{prefix} has invalid datetime: {exc}")
            continue
        if prediction_time >= commence_time:
            errors.append(f"{prefix} prediction_timestamp is not before commence_time")
        if snapshot_time > prediction_time:
            errors.append(f"{prefix} snapshot_time is after prediction_timestamp")
        if snapshot_time >= commence_time:
            errors.append(f"{prefix} snapshot_time is not before commence_time")

    ledger_summary: dict[str, object] = {}
    if ledger_path is not None:
        _, ledger_rows = _read_ledger(Path(ledger_path).expanduser().resolve())
        ledger_predictions = [
            row for row in ledger_rows if row.get("record_type") == "prediction"
        ]
        ledger_settlements = [
            row for row in ledger_rows if row.get("record_type") == "settlement"
        ]
        artifact_ids = set(prediction_ids)
        run_prediction_rows = [
            row
            for row in ledger_predictions
            if _clean(row.get("prediction_run_id")) == manifest_run_id
        ]
        run_ids = {_clean(row.get("prediction_id")) for row in run_prediction_rows}
        missing_from_artifact = sorted(run_ids - artifact_ids)
        missing_from_ledger = sorted(artifact_ids - run_ids)
        for prediction_id in missing_from_artifact:
            errors.append(f"ledger prediction row lacks artifact row: {prediction_id}")
        for prediction_id in missing_from_ledger:
            warnings.append(f"artifact prediction not yet appended to ledger: {prediction_id}")
        for duplicate_id in _duplicate_values(
            _clean(row.get("prediction_id")) for row in run_prediction_rows
        ):
            errors.append(f"duplicate prediction ledger row for run: {duplicate_id}")
        prediction_ledger_by_id = {
            _clean(row.get("prediction_id")): row for row in run_prediction_rows
        }
        for row in run_prediction_rows:
            if _clean(row.get("prediction_artifact_sha256")) != predictions_sha:
                errors.append(
                    "ledger prediction_artifact_sha256 mismatch for "
                    + _clean(row.get("prediction_id"))
                )
        immutable_fields = (
            "prediction_run_id",
            "model_id",
            "game_date",
            "event_id",
            "commence_time",
            "player_id",
            "player_name",
            "normalized_player_name",
            "sportsbook",
            "original_odds",
            "original_decimal_odds",
            "original_implied_probability",
            "model_probability",
            "prediction_timestamp",
            "prediction_artifact_sha256",
            "source_manifest_reference",
            "repository_commit_sha",
        )
        seen_settlement_ids: set[str] = set()
        for settlement in ledger_settlements:
            prediction_id = _clean(settlement.get("prediction_id"))
            if prediction_id not in artifact_ids:
                continue
            if prediction_id in seen_settlement_ids:
                errors.append(f"duplicate settlement row for prediction: {prediction_id}")
            seen_settlement_ids.add(prediction_id)
            original = prediction_ledger_by_id.get(prediction_id)
            if original is None:
                errors.append(f"settlement without prediction ledger row: {prediction_id}")
                continue
            for field_name in immutable_fields:
                if _clean(original.get(field_name)) != _clean(settlement.get(field_name)):
                    errors.append(
                        "settlement mutated immutable prediction field "
                        f"{field_name} for {prediction_id}"
                    )
        ledger_summary = {
            "ledger_prediction_rows_for_run": len(run_prediction_rows),
            "ledger_settlement_rows_for_artifact": sum(
                _clean(row.get("prediction_id")) in artifact_ids
                for row in ledger_settlements
            ),
            "artifact_predictions_missing_from_ledger": len(missing_from_ledger),
            "ledger_predictions_missing_from_artifact": len(missing_from_artifact),
        }

    summary = {
        "prediction_schema_version": PREDICTION_SCHEMA_VERSION,
        "prediction_artifact_dir": str(artifact_dir),
        "prediction_run_id": manifest_run_id,
        "model_id": manifest_model_id,
        "row_count": len(predictions),
        "predictions_csv_sha256": predictions_sha,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "ledger": ledger_summary,
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
    }
    return VerificationResult(
        passed=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        summary=summary,
    )


def settle_prediction_ledger(
    *,
    ledger_path: str | Path,
    results_path: str | Path = DEFAULT_RESULTS_CSV,
    settlement_timestamp: datetime | str | None = None,
) -> SettlementResult:
    """Append settlement records without mutating original prediction rows."""

    ledger = Path(ledger_path).expanduser().resolve()
    timestamp = _coerce_datetime_utc(settlement_timestamp, "settlement_timestamp")
    result_index, duplicate_results, result_path_text, _ = _load_result_index(results_path)
    with _FileLock(ledger):
        _, ledger_rows = _read_ledger(ledger)
        predictions = [row for row in ledger_rows if row.get("record_type") == "prediction"]
        settled_rows_by_id: dict[str, dict[str, str]] = {}
        for row in ledger_rows:
            if row.get("record_type") != "settlement":
                continue
            prediction_id = _clean(row.get("prediction_id"))
            if not prediction_id:
                raise MLBHRResearchBaselineError("settlement row missing prediction_id")
            if prediction_id in settled_rows_by_id:
                raise MLBHRResearchBaselineError(
                    f"duplicate settlement rows already exist for {prediction_id}"
                )
            settled_rows_by_id[prediction_id] = row
        new_rows: list[dict[str, str]] = []
        pending = 0
        skipped = 0
        conflicts = 0
        for prediction in predictions:
            prediction_id = prediction["prediction_id"]
            integrity_status = "prediction_before_game_start"
            try:
                prediction_time = _parse_datetime(
                    prediction["prediction_timestamp"], "prediction_timestamp"
                )
                commence_time = _parse_datetime(prediction["commence_time"], "commence_time")
                if prediction_time >= commence_time:
                    integrity_status = "failed_prediction_not_before_game_start"
            except MLBHRResearchBaselineError:
                integrity_status = "failed_invalid_prediction_time"
            key = (
                prediction["event_id"],
                normalize_mlb_player_name(prediction["player_name"]),
            )
            result = None if key in duplicate_results else result_index.get(key)
            if result is None and integrity_status.startswith("prediction_before"):
                if prediction_id in settled_rows_by_id:
                    skipped += 1
                    continue
                pending += 1
                continue
            settlement_status = ""
            final_result = ""
            grade = ""
            unit_profit = ""
            manual_review = ""
            if integrity_status.startswith("failed"):
                settlement_status = "integrity_failed"
            else:
                game_status = _clean(result.get("game_status")).casefold() if result else ""
                if game_status == "final":
                    actual = int(_clean(result.get("actual_home_runs")))
                    final_result = "1" if actual >= 1 else "0"
                    grade = "win" if actual >= 1 else "loss"
                    unit_profit = (
                        _format_float(win_profit_1u(int(prediction["original_odds"])))
                        if actual >= 1
                        else "-1"
                    )
                    settlement_status = "settled"
                elif game_status == "void":
                    settlement_status = "void"
                    manual_review = "not_required"
                elif game_status == "void_candidate":
                    settlement_status = "void_candidate"
                    manual_review = "required"
                elif game_status == "manual_review_required":
                    settlement_status = "manual_review_required"
                    manual_review = "required"
                else:
                    if prediction_id in settled_rows_by_id:
                        skipped += 1
                        continue
                    pending += 1
                    continue
            record_id = _stable_id(LEDGER_SCHEMA_VERSION, "settlement", prediction_id)
            settlement_row = {column: prediction.get(column, "") for column in LEDGER_COLUMNS}
            settlement_row.update(
                {
                    "ledger_record_id": record_id,
                    "record_type": "settlement",
                    "closing_odds": "",
                    "closing_implied_probability": "",
                    "closing_line_movement": "",
                    "final_result": final_result,
                    "grade": grade,
                    "unit_profit_loss": unit_profit,
                    "settlement_timestamp": _iso_z(timestamp),
                    "settlement_source": result_path_text,
                    "settlement_status": settlement_status,
                    "manual_review_status": manual_review,
                    "integrity_status": integrity_status,
                    "research_label": RESEARCH_ONLY_LABEL,
                }
            )
            existing_settlement = settled_rows_by_id.get(prediction_id)
            if existing_settlement is not None:
                comparison_fields = (
                    "final_result",
                    "grade",
                    "unit_profit_loss",
                    "settlement_status",
                    "manual_review_status",
                    "integrity_status",
                )
                mismatches = [
                    field_name
                    for field_name in comparison_fields
                    if _clean(existing_settlement.get(field_name))
                    != _clean(settlement_row.get(field_name))
                ]
                if mismatches:
                    conflicts += 1
                    raise MLBHRResearchBaselineError(
                        "conflicting settlement for "
                        + prediction_id
                        + ": "
                        + ", ".join(mismatches)
                    )
                skipped += 1
                continue
            new_rows.append(settlement_row)
        _append_ledger_rows(ledger, new_rows)
    return SettlementResult(
        ledger_path=ledger,
        appended_settlements=len(new_rows),
        pending_predictions=pending,
        skipped_existing_settlements=skipped,
        conflicting_settlements=conflicts,
    )


def _load_predictions_input(
    *,
    predictions: Sequence[Mapping[str, str]] | None,
    predictions_csv: str | Path | None,
) -> tuple[tuple[dict[str, str], ...], str]:
    if predictions is not None:
        return tuple(dict(row) for row in predictions), ""
    if predictions_csv is None:
        raise MLBHRResearchBaselineError("predictions or predictions_csv is required")
    _, loaded = _read_csv(
        predictions_csv,
        required_columns=PREDICTION_COLUMNS,
        label="predictions",
    )
    return loaded, str(Path(predictions_csv).expanduser().resolve())


def _closing_line_candidates(
    prediction: Mapping[str, str],
    odds_rows: Sequence[Mapping[str, str]],
) -> tuple[list[tuple[datetime, Mapping[str, str], int, float, float]], bool]:
    event_id = _clean(prediction.get("event_id"))
    normalized = _clean(prediction.get("normalized_player_name"))
    market = _clean(prediction.get("market_key"))
    point = _clean(prediction.get("point"))
    commence_time = _parse_datetime(prediction.get("commence_time"), "commence_time")
    candidates: list[tuple[datetime, Mapping[str, str], int, float, float]] = []
    has_post_start = False
    for row in odds_rows:
        if _clean(row.get("event_id")) != event_id:
            continue
        if normalize_mlb_player_name(row.get("player")) != normalized:
            continue
        if market and _clean(row.get("market")) != market:
            continue
        if point and _clean(row.get("point")) != point:
            continue
        snapshot_time, row_commence, american, decimal_odds, implied = _validate_odds_row(
            row
        )
        if row_commence != commence_time:
            continue
        if snapshot_time >= commence_time:
            has_post_start = True
            continue
        candidates.append((snapshot_time, row, american, decimal_odds, implied))
    return candidates, has_post_start


def _closing_line_record(
    *,
    prediction: Mapping[str, str],
    status: str,
    method: str,
    source: str,
    snapshot_time: str,
    closing_sportsbook: str,
    closing_sportsbook_name: str,
    closing_american: str,
    closing_decimal: str,
    closing_implied: str,
    consensus_bookmaker_count: int,
    consensus_implied: str,
    captured_at: str,
    integrity_status: str,
) -> dict[str, str]:
    original_american = _clean(prediction.get("american_odds"))
    original_implied = _clean(prediction.get("implied_probability"))
    line_movement = ""
    probability_movement = ""
    if closing_american and original_american:
        line_movement = _format_float(float(closing_american) - float(original_american))
    if closing_implied and original_implied:
        probability_movement = _format_float(float(closing_implied) - float(original_implied))
    record = {
        "closing_line_schema_version": CLOSING_LINE_SCHEMA_VERSION,
        "prediction_id": _clean(prediction.get("prediction_id")),
        "prediction_run_id": _clean(prediction.get("prediction_run_id")),
        "event_id": _clean(prediction.get("event_id")),
        "commence_time": _clean(prediction.get("commence_time")),
        "sportsbook": _clean(prediction.get("sportsbook")),
        "sportsbook_name": _clean(prediction.get("sportsbook_name")),
        "normalized_player_name": _clean(prediction.get("normalized_player_name")),
        "closing_status": status,
        "closing_method": method,
        "closing_source": source,
        "closing_snapshot_time": snapshot_time,
        "closing_sportsbook": closing_sportsbook,
        "closing_sportsbook_name": closing_sportsbook_name,
        "closing_american_odds": closing_american,
        "closing_decimal_odds": closing_decimal,
        "closing_implied_probability": closing_implied,
        "consensus_bookmaker_count": str(consensus_bookmaker_count),
        "consensus_implied_probability": consensus_implied,
        "original_american_odds": original_american,
        "original_implied_probability": original_implied,
        "closing_line_movement": line_movement,
        "closing_probability_movement": probability_movement,
        "captured_at": captured_at,
        "official_evidence_allowed": "false",
        "integrity_status": integrity_status,
        "research_label": RESEARCH_ONLY_LABEL,
    }
    record["closing_record_id"] = _stable_id(
        CLOSING_LINE_SCHEMA_VERSION,
        record["prediction_id"],
        status,
        method,
        snapshot_time,
        closing_sportsbook,
        closing_american,
    )
    return {column: record.get(column, "") for column in CLOSING_LINE_COLUMNS}


def capture_closing_line_snapshots(
    *,
    odds_path: str | Path = DEFAULT_ODDS_CSV,
    predictions: Sequence[Mapping[str, str]] | None = None,
    predictions_csv: str | Path | None = None,
    output_csv: str | Path | None = None,
    captured_at: datetime | str | None = None,
) -> ClosingLineCaptureResult:
    """Capture latest valid pre-start closing evidence for frozen predictions."""

    prediction_rows, prediction_source = _load_predictions_input(
        predictions=predictions,
        predictions_csv=predictions_csv,
    )
    odds_source = Path(odds_path).expanduser().resolve()
    _, odds_rows = _read_csv(odds_source, required_columns=ODDS_REQUIRED_COLUMNS, label="odds")
    timestamp = _coerce_datetime_utc(captured_at, "captured_at")
    captured_text = _iso_z(timestamp)

    records: list[dict[str, str]] = []
    for prediction in prediction_rows:
        candidates, has_post_start = _closing_line_candidates(prediction, odds_rows)
        same_book = [
            item
            for item in candidates
            if _clean(item[1].get("bookmaker_key")) == _clean(prediction.get("sportsbook"))
        ]
        chosen: tuple[datetime, Mapping[str, str], int, float, float] | None = None
        method = ""
        status = ""
        source = str(odds_source)
        consensus_count = 0
        consensus_implied = ""
        integrity = "no_post_start_evidence_used"
        if same_book:
            chosen = sorted(
                same_book,
                key=lambda item: (item[0], _clean(item[1].get("bookmaker_key"))),
            )[-1]
            method = "same_book_latest_prestart"
            status = "captured_same_book"
            latest_snapshot = chosen[0]
            snapshot_rows = [item for item in candidates if item[0] == latest_snapshot]
            consensus_count = len(
                {
                    (
                        _clean(item[1].get("bookmaker_key")),
                        _clean(item[1].get("bookmaker")),
                    )
                    for item in snapshot_rows
                }
            )
            consensus_implied = _format_float(_mean([item[4] for item in snapshot_rows]))
        elif candidates:
            latest_snapshot = max(item[0] for item in candidates)
            snapshot_rows = [item for item in candidates if item[0] == latest_snapshot]
            chosen = sorted(
                snapshot_rows,
                key=lambda item: (-item[3], _clean(item[1].get("bookmaker_key"))),
            )[0]
            method = "consensus_latest_prestart"
            status = "captured_consensus"
            consensus_count = len(
                {
                    (
                        _clean(item[1].get("bookmaker_key")),
                        _clean(item[1].get("bookmaker")),
                    )
                    for item in snapshot_rows
                }
            )
            consensus_implied = _format_float(_mean([item[4] for item in snapshot_rows]))
        else:
            method = "missing_prestart_snapshot" if has_post_start else "missing"
            status = "missing_prestart" if has_post_start else "missing"
            integrity = "no_valid_prestart_snapshot"

        if chosen is None:
            records.append(
                _closing_line_record(
                    prediction=prediction,
                    status=status,
                    method=method,
                    source=source,
                    snapshot_time="",
                    closing_sportsbook="",
                    closing_sportsbook_name="",
                    closing_american="",
                    closing_decimal="",
                    closing_implied="",
                    consensus_bookmaker_count=0,
                    consensus_implied="",
                    captured_at=captured_text,
                    integrity_status=integrity,
                )
            )
            continue
        snapshot_time, row, american, decimal_odds, implied = chosen
        records.append(
            _closing_line_record(
                prediction=prediction,
                status=status,
                method=method,
                source=source,
                snapshot_time=_iso_z(snapshot_time),
                closing_sportsbook=_clean(row.get("bookmaker_key")),
                closing_sportsbook_name=_clean(row.get("bookmaker")),
                closing_american=str(american),
                closing_decimal=_format_float(decimal_odds),
                closing_implied=_format_float(implied),
                consensus_bookmaker_count=consensus_count,
                consensus_implied=consensus_implied,
                captured_at=captured_text,
                integrity_status=integrity,
            )
        )

    output_path = Path(output_csv).expanduser().resolve() if output_csv else None
    skipped_existing = 0
    appended = 0
    if output_path is not None:
        existing_by_prediction: dict[str, dict[str, str]] = {}
        if output_path.exists():
            columns, existing = _read_csv(
                output_path,
                required_columns=CLOSING_LINE_COLUMNS,
                label="closing lines",
            )
            if tuple(columns) != CLOSING_LINE_COLUMNS:
                raise MLBHRResearchBaselineError(
                    "closing line schema does not match expected columns"
                )
            for row in existing:
                prediction_id = _clean(row.get("prediction_id"))
                if prediction_id in existing_by_prediction:
                    raise MLBHRResearchBaselineError(
                        f"duplicate closing-line evidence for {prediction_id}"
                    )
                existing_by_prediction[prediction_id] = row
        new_rows: list[dict[str, str]] = []
        for record in records:
            prediction_id = record["prediction_id"]
            existing = existing_by_prediction.get(prediction_id)
            if existing is None:
                new_rows.append(record)
                continue
            comparable_fields = tuple(
                field for field in CLOSING_LINE_COLUMNS if field != "captured_at"
            )
            if any(_clean(existing.get(field)) != _clean(record.get(field)) for field in comparable_fields):
                raise MLBHRResearchBaselineError(
                    f"conflicting closing-line evidence for {prediction_id}"
                )
            skipped_existing += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = output_path.exists()
        try:
            with output_path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(CLOSING_LINE_COLUMNS))
                if not file_exists:
                    writer.writeheader()
                for row in new_rows:
                    writer.writerow(row)
        except OSError as exc:
            raise MLBHRResearchBaselineError(
                f"could not append closing-line evidence {output_path}: {exc}"
            ) from exc
        appended = len(new_rows)
    status_counts: dict[str, int] = {}
    for record in records:
        status_counts[record["closing_status"]] = (
            status_counts.get(record["closing_status"], 0) + 1
        )
    report = {
        "closing_line_schema_version": CLOSING_LINE_SCHEMA_VERSION,
        "captured_at": captured_text,
        "prediction_source": prediction_source,
        "odds_source": str(odds_source),
        "prediction_count": len(prediction_rows),
        "record_count": len(records),
        "appended_rows": appended,
        "skipped_existing_rows": skipped_existing,
        "status_counts": dict(sorted(status_counts.items())),
        "official_evidence_allowed": False,
        "post_start_evidence_used": False,
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
    }
    return ClosingLineCaptureResult(
        rows=tuple(records),
        output_path=output_path,
        report=report,
    )


def _completed_daily_run(
    *,
    date_root: Path,
    model_dir: Path,
) -> tuple[Path, dict[str, object]] | None:
    if not date_root.exists():
        return None
    for summary_path in sorted(date_root.rglob("run_summary.json"), reverse=True):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("dry_run") is True:
            continue
        if _clean(payload.get("status")) != "completed":
            continue
        if _manifest_int(payload, "prediction_count") <= 0:
            continue
        if Path(_clean(payload.get("model_dir"))).expanduser().resolve() != model_dir:
            continue
        return summary_path.parent, payload
    return None


def _unique_daily_run_dir(date_root: Path, run_id: str) -> tuple[str, Path]:
    candidate_id = run_id
    candidate = date_root / candidate_id
    index = 2
    while candidate.exists():
        candidate_id = f"{run_id}-force-{index}"
        candidate = date_root / candidate_id
        index += 1
    return candidate_id, candidate


def _daily_run_condition(
    prediction_result: PredictionRunResult,
) -> str:
    feature_manifest = _manifest_mapping(
        prediction_result.manifest.get("feature_manifest")
    )
    feature_rows = _manifest_int(feature_manifest, "row_count")
    exclusion_counts = _manifest_dict(feature_manifest, "exclusion_counts")
    if feature_rows == 0:
        return "no_games_found"
    if (
        not prediction_result.predictions
        and exclusion_counts
        and set(exclusion_counts) == {SPECIAL_EVENT_EXCLUSION_REASON}
    ):
        return "special_event_quarantined"
    if not prediction_result.predictions:
        return "no_eligible_players"
    if any(row.get("exclusion_reason") == "game_already_started" for row in prediction_result.exclusions):
        return "some_games_started"
    if prediction_result.exclusions:
        return "completed_with_exclusions"
    return "completed_predictions"


def _daily_run_summary_markdown(summary: Mapping[str, object]) -> str:
    lines = [
        "# MLB HR Manual Research Run",
        "",
        RESEARCH_ONLY_LABEL,
        "",
        f"- Run ID: {summary.get('run_id')}",
        f"- Date: {summary.get('target_date')}",
        f"- Status: {summary.get('status')}",
        f"- Condition: {summary.get('condition')}",
        f"- Predictions: {summary.get('prediction_count')}",
        f"- Exclusions: {summary.get('excluded_row_count')}",
        f"- Ledger appended rows: {summary.get('ledger_appended_rows')}",
        f"- Identity resolved: {summary.get('identity_resolved_count')}",
        f"- Identity unresolved: {summary.get('identity_unresolved_count')}",
        f"- Identity quarantined: {summary.get('identity_quarantined_count')}",
        "",
        "This run is manual, research-only, and not eligible for official picks.",
        "",
    ]
    return "\n".join(lines)


def run_daily_research(
    *,
    target_date: str,
    model_dir: str | Path,
    output_root: str | Path,
    ledger_csv: str | Path,
    odds_csv: str | Path = DEFAULT_ODDS_CSV,
    identity_source_csv: str | Path | None = None,
    identity_cache_csv: str | Path | None = None,
    prediction_timestamp: datetime | str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> DailyResearchRunResult:
    """Run one manual prospective research day with explicit paths."""

    try:
        date.fromisoformat(target_date)
    except ValueError as exc:
        raise MLBHRResearchBaselineError(
            f"target_date must be YYYY-MM-DD: {target_date!r}"
        ) from exc
    model_path = Path(model_dir).expanduser().resolve()
    ledger_path = Path(ledger_csv).expanduser().resolve()
    odds_path = Path(odds_csv).expanduser().resolve()
    odds_sha256 = _file_sha256(odds_path)
    root = Path(output_root).expanduser().resolve()
    date_root = root / target_date
    timestamp = _coerce_datetime_utc(prediction_timestamp, "prediction_timestamp")
    timestamp_text = _iso_z(timestamp)

    if not dry_run and not force:
        completed = _completed_daily_run(date_root=date_root, model_dir=model_path)
        if completed is not None:
            existing_dir, existing_summary = completed
            return DailyResearchRunResult(
                run_id=_clean(existing_summary.get("run_id")),
                status="existing_completed_run",
                output_dir=existing_dir,
                summary=existing_summary,
            )

    run_id_seed = _stable_id(
        DAILY_RUN_SCHEMA_VERSION,
        target_date,
        str(model_path),
        odds_sha256,
        timestamp_text,
        length=8,
    )
    requested_run_id = (
        "mlb-hr-daily-"
        + target_date.replace("-", "")
        + "-"
        + timestamp.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + run_id_seed
    )
    run_id, run_dir = _unique_daily_run_dir(date_root, requested_run_id)

    if dry_run:
        prediction_result = generate_daily_research_predictions(
            model_bundle_dir=model_path,
            odds_path=odds_path,
            target_date=target_date,
            prediction_timestamp=timestamp,
            dry_run=True,
            run_nonce=run_id,
        )
        identity_result = resolve_player_identities(
            feature_rows=prediction_result.predictions,
            identity_source_csv=identity_source_csv,
            identity_cache_csv=identity_cache_csv,
            mapping_version=DEFAULT_IDENTITY_MAPPING_VERSION,
            resolved_at=timestamp,
            write_cache=False,
        )
        condition = _daily_run_condition(prediction_result)
        prediction_summary_fields = _daily_prediction_summary_fields(
            prediction_result
        )
        summary = {
            "daily_run_schema_version": DAILY_RUN_SCHEMA_VERSION,
            "run_id": run_id,
            "status": "dry_run",
            "condition": condition,
            "dry_run": True,
            "force": force,
            "target_date": target_date,
            "target_date_semantics": "courtvision_operating_date",
            "operating_timezone": COURTVISION_OPERATING_TIMEZONE_NAME,
            "model_dir": str(model_path),
            "odds_csv": str(odds_path),
            "source_odds_sha256": odds_sha256,
            "ledger_csv": str(ledger_path),
            "prediction_timestamp": timestamp_text,
            "prediction_run_id": prediction_result.prediction_run_id,
            "prediction_count": len(prediction_result.predictions),
            "excluded_row_count": len(prediction_result.exclusions),
            **prediction_summary_fields,
            "ledger_appended_rows": 0,
            "identity_resolved_count": identity_result.report.get("resolved_count", 0),
            "identity_unresolved_count": identity_result.report.get("unresolved_count", 0),
            "identity_quarantined_count": identity_result.report.get("quarantined_count", 0),
            "idempotency_scope": {
                "target_date": target_date,
                "model_dir": str(model_path),
                "source_odds_sha256": odds_sha256,
                "prediction_status": "dry_run",
                "zero_prediction_runs_block_future_runs": False,
            },
            "research_label": RESEARCH_ONLY_LABEL,
            "approval_status": APPROVAL_STATUS,
        }
        return DailyResearchRunResult(
            run_id=run_id,
            status="dry_run",
            output_dir=None,
            summary=summary,
        )

    predictions_dir = run_dir / "predictions"
    prediction_result = generate_daily_research_predictions(
        model_bundle_dir=model_path,
        odds_path=odds_path,
        output_dir=predictions_dir,
        target_date=target_date,
        prediction_timestamp=timestamp,
        dry_run=False,
        run_nonce=run_id,
    )
    identity_dir = run_dir / "identity"
    identity_result = resolve_player_identities(
        feature_rows=prediction_result.predictions,
        identity_source_csv=identity_source_csv,
        identity_cache_csv=identity_cache_csv,
        mapping_version=DEFAULT_IDENTITY_MAPPING_VERSION,
        resolved_at=timestamp,
        write_cache=identity_cache_csv is not None,
    )
    identity_dir.mkdir(parents=True, exist_ok=False)
    _write_csv_create_once(
        identity_dir / "identity_resolution.csv",
        IDENTITY_CACHE_COLUMNS,
        identity_result.records,
    )
    _write_json_create_once(identity_dir / "identity_report.json", identity_result.report)
    _write_text_create_once(
        identity_dir / "identity_report.md",
        _identity_report_markdown(identity_result.report),
    )

    artifact_verification = verify_prediction_artifacts(predictions_root=predictions_dir)
    if not artifact_verification.passed:
        raise MLBHRResearchBaselineError(
            "prediction artifact verification failed: "
            + "; ".join(artifact_verification.errors)
        )
    ledger_appended = 0
    if prediction_result.predictions:
        append_result = append_predictions_to_ledger(
            predictions_csv=predictions_dir / "predictions.csv",
            ledger_path=ledger_path,
        )
        ledger_appended = append_result.appended_rows
    ledger_verification = verify_prediction_artifacts(
        predictions_root=predictions_dir,
        ledger_path=ledger_path,
    )
    if not ledger_verification.passed:
        raise MLBHRResearchBaselineError(
            "post-ledger prediction verification failed: "
            + "; ".join(ledger_verification.errors)
        )

    condition = _daily_run_condition(prediction_result)
    status = "completed" if prediction_result.predictions else "completed_no_predictions"
    prediction_summary_fields = _daily_prediction_summary_fields(prediction_result)
    summary = {
        "daily_run_schema_version": DAILY_RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "condition": condition,
        "dry_run": False,
        "force": force,
        "target_date": target_date,
        "target_date_semantics": "courtvision_operating_date",
        "operating_timezone": COURTVISION_OPERATING_TIMEZONE_NAME,
        "model_dir": str(model_path),
        "odds_csv": str(odds_path),
        "source_odds_sha256": odds_sha256,
        "ledger_csv": str(ledger_path),
        "prediction_timestamp": timestamp_text,
        "prediction_run_id": prediction_result.prediction_run_id,
        "prediction_count": len(prediction_result.predictions),
        "excluded_row_count": len(prediction_result.exclusions),
        **prediction_summary_fields,
        "prediction_artifact_dir": str(predictions_dir),
        "ledger_appended_rows": ledger_appended,
        "identity_artifact_dir": str(identity_dir),
        "identity_resolved_count": identity_result.report.get("resolved_count", 0),
        "identity_unresolved_count": identity_result.report.get("unresolved_count", 0),
        "identity_quarantined_count": identity_result.report.get("quarantined_count", 0),
        "artifact_verification": artifact_verification.summary,
        "ledger_verification": ledger_verification.summary,
        "idempotency_scope": {
            "target_date": target_date,
            "model_dir": str(model_path),
            "source_odds_sha256": odds_sha256,
            "prediction_status": status,
            "zero_prediction_runs_block_future_runs": False,
            "nonzero_prediction_runs_block_future_runs": bool(
                prediction_result.predictions
            ),
        },
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
    }
    _write_json_create_once(run_dir / "run_summary.json", summary)
    _write_text_create_once(run_dir / "run_summary.md", _daily_run_summary_markdown(summary))
    return DailyResearchRunResult(
        run_id=run_id,
        status=status,
        output_dir=run_dir,
        summary=summary,
    )


def _load_feature_rows_optional(path: str | Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    _, rows = _read_csv(
        path,
        required_columns=(
            "feature_schema_version",
            "game_date",
            "event_id",
            "normalized_player_name",
            "eligibility_status",
            *MODEL_REQUIRED_INPUT_COLUMNS,
        ),
        label="feature rows",
    )
    return _normalize_feature_rows(rows)


def _load_ledger_rows_optional(path: str | Path | None) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    _, rows = _read_ledger(Path(path).expanduser().resolve())
    return tuple(rows)


def build_validation_gate_report(
    *,
    feature_rows_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    metrics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Report explicit research-to-candidate promotion gates."""

    feature_rows = _load_feature_rows_optional(feature_rows_path)
    ledger_rows = _load_ledger_rows_optional(ledger_path)
    prediction_rows = [row for row in ledger_rows if row.get("record_type") == "prediction"]
    settlement_rows = [row for row in ledger_rows if row.get("record_type") == "settlement"]
    completed_settlements = [
        row for row in settlement_rows if row.get("settlement_status") == "settled"
    ]
    calibration_error = None
    if metrics:
        validation = metrics.get("validation")
        if isinstance(validation, Mapping):
            model = validation.get("model")
            if isinstance(model, Mapping):
                calibration_error = model.get("calibration_error")
    missing_feature_cells = 0
    total_feature_cells = 0
    if feature_rows:
        for row in feature_rows:
            for feature in MODEL_REQUIRED_INPUT_COLUMNS:
                total_feature_cells += 1
                missing_feature_cells += int(not _clean(row.get(feature)))
    missing_rate = (
        missing_feature_cells / total_feature_cells if total_feature_cells else None
    )
    identity_rows = prediction_rows or feature_rows
    identity_match_rate = (
        sum(bool(_clean(row.get("normalized_player_name"))) for row in identity_rows)
        / len(identity_rows)
        if identity_rows
        else None
    )
    leakage_findings = sum(
        row.get("leakage_check_status") not in {"", "passed"} for row in feature_rows
    )
    gates = {
        "prediction_dates": {
            "observed": len({row.get("game_date") for row in prediction_rows if row.get("game_date")}),
            "required": 30,
        },
        "completed_games": {
            "observed": len({row.get("event_id") for row in completed_settlements if row.get("event_id")}),
            "required": 100,
        },
        "eligible_player_game_predictions": {
            "observed": len(prediction_rows),
            "required": 1000,
        },
        "unique_players": {
            "observed": len({row.get("normalized_player_name") for row in prediction_rows if row.get("normalized_player_name")}),
            "required": 100,
        },
        "positive_home_run_outcomes": {
            "observed": sum(row.get("final_result") == "1" for row in completed_settlements),
            "required": 50,
        },
        "missing_data_rate": {
            "observed": missing_rate,
            "maximum": 0.20,
        },
        "identity_match_rate": {
            "observed": identity_match_rate,
            "minimum": 0.95,
        },
        "calibration_error": {
            "observed": calibration_error,
            "maximum": 0.075,
        },
        "closing_line_data_rate": {
            "observed": (
                sum(bool(row.get("closing_odds")) for row in completed_settlements)
                / len(completed_settlements)
                if completed_settlements
                else None
            ),
            "minimum": 0.80,
        },
        "unresolved_leakage_findings": {
            "observed": leakage_findings,
            "required": 0,
        },
        "prediction_artifact_mutations": {
            "observed": 0,
            "required": 0,
        },
    }
    evaluated_gates: dict[str, dict[str, object]] = {}
    for name, gate in gates.items():
        observed = gate["observed"]
        passed = False
        if observed is None:
            passed = False
        elif "required" in gate:
            if name in {"unresolved_leakage_findings", "prediction_artifact_mutations"}:
                passed = observed == gate["required"]
            else:
                passed = observed >= gate["required"]
        elif "maximum" in gate:
            passed = observed <= gate["maximum"]
        elif "minimum" in gate:
            passed = observed >= gate["minimum"]
        evaluated_gates[name] = {**gate, "passed": passed}
    all_volume_gates_passed = all(gate["passed"] for gate in evaluated_gates.values())
    return {
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "gates": evaluated_gates,
        "candidate_review_ready": bool(all_volume_gates_passed),
        "official_betting_picks_ready": False,
        "official_pick_note": (
            "Promotion to official picks is never automatic. It requires human "
            "review of calibration, market baseline, closing-line value, and "
            "stability by date/player group/odds/sportsbook/park/model version."
        ),
    }


def _settled_metric_rows(
    settlement_rows: Sequence[Mapping[str, str]],
) -> tuple[tuple[dict[str, str], ...], list[float]]:
    rows: list[dict[str, str]] = []
    probabilities: list[float] = []
    for row in settlement_rows:
        if row.get("settlement_status") != "settled":
            continue
        if row.get("final_result") not in {"0", "1"}:
            continue
        probability = _float_or_none(row.get("model_probability"))
        if probability is None:
            continue
        metric_row = dict(row)
        metric_row["hit_hr"] = row["final_result"]
        rows.append(metric_row)
        probabilities.append(probability)
    return tuple(rows), probabilities


def _market_probability_list(rows: Sequence[Mapping[str, str]]) -> list[float]:
    probabilities: list[float] = []
    for row in rows:
        probability = _float_or_none(row.get("original_implied_probability"))
        probabilities.append(probability if probability is not None else 0.0)
    return probabilities


def _probability_bucket(value: object) -> str:
    probability = _float_or_none(value)
    if probability is None:
        return "missing"
    if probability < 0.05:
        return "0.00-0.05"
    if probability < 0.10:
        return "0.05-0.10"
    if probability < 0.15:
        return "0.10-0.15"
    if probability < 0.20:
        return "0.15-0.20"
    if probability < 0.30:
        return "0.20-0.30"
    return "0.30+"


def _odds_bucket(value: object) -> str:
    odds = _float_or_none(value)
    if odds is None:
        return "missing"
    if odds < 200:
        return "<+200"
    if odds < 400:
        return "+200-399"
    if odds < 600:
        return "+400-599"
    if odds < 900:
        return "+600-899"
    return "+900+"


def _clv_bucket(closing_row: Mapping[str, str] | None) -> str:
    if closing_row is None or not closing_row.get("closing_probability_movement"):
        return "missing"
    movement = float(closing_row["closing_probability_movement"])
    if movement > 0.005:
        return "closed_higher_implied"
    if movement < -0.005:
        return "closed_lower_implied"
    return "flat"


def _performance_by(
    rows: Sequence[Mapping[str, str]],
    *,
    key_name: str,
    key_fn,
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        grouped.setdefault(str(key_fn(row)), []).append(row)
    output: dict[str, dict[str, object]] = {}
    for key, group in sorted(grouped.items()):
        wins = sum(row.get("final_result") == "1" for row in group)
        pnl_values = [
            float(row.get("unit_profit_loss"))
            for row in group
            if _clean(row.get("unit_profit_loss"))
        ]
        output[key] = {
            key_name: key,
            "settled_count": len(group),
            "wins": wins,
            "losses": sum(row.get("final_result") == "0" for row in group),
            "hit_rate": wins / len(group) if group else None,
            "unit_profit_loss": _format_float(sum(pnl_values)),
        }
    return output


def _max_drawdown(rows: Sequence[Mapping[str, str]]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for row in sorted(
        rows,
        key=lambda item: (
            _clean(item.get("game_date")),
            _clean(item.get("settlement_timestamp")),
            _clean(item.get("prediction_id")),
        ),
    ):
        pnl = _float_or_none(row.get("unit_profit_loss")) or 0.0
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return abs(drawdown)


def _load_closing_rows_optional(
    path: str | Path | None,
) -> tuple[dict[str, str], ...]:
    if path is None:
        return ()
    columns, rows = _read_csv(
        path,
        required_columns=CLOSING_LINE_COLUMNS,
        label="closing lines",
    )
    if tuple(columns) != CLOSING_LINE_COLUMNS:
        raise MLBHRResearchBaselineError(
            "closing line schema does not match expected columns"
        )
    return rows


def build_prospective_trial_report(
    *,
    ledger_path: str | Path,
    closing_lines_csv: str | Path | None = None,
    identity_cache_csv: str | Path | None = None,
    generated_at: datetime | str | None = None,
) -> dict[str, object]:
    """Build the prospective MLB HR research evidence report."""

    timestamp = _coerce_datetime_utc(generated_at, "generated_at")
    _, ledger_rows = _read_ledger(Path(ledger_path).expanduser().resolve())
    prediction_rows = [row for row in ledger_rows if row.get("record_type") == "prediction"]
    settlement_rows = [row for row in ledger_rows if row.get("record_type") == "settlement"]
    settlement_by_id = {
        row["prediction_id"]: row for row in settlement_rows if row.get("prediction_id")
    }
    settled_rows = [
        row for row in settlement_rows if row.get("settlement_status") == "settled"
    ]
    void_rows = [
        row for row in settlement_rows if row.get("settlement_status") == "void"
    ]
    manual_rows = [
        row
        for row in settlement_rows
        if row.get("manual_review_status") == "required"
        or row.get("settlement_status") in {"void_candidate", "manual_review_required"}
    ]
    pending_rows = [
        row
        for row in prediction_rows
        if row.get("prediction_id") not in settlement_by_id
    ]
    metric_rows, model_probabilities = _settled_metric_rows(settlement_rows)
    model_metrics = _evaluate_probability_series(
        metric_rows,
        model_probabilities,
        series_name="research_model",
    )
    market_metrics = _evaluate_probability_series(
        metric_rows,
        _market_probability_list(metric_rows),
        series_name="raw_market_implied_probability_vig_included",
    )
    closing_rows = _load_closing_rows_optional(closing_lines_csv)
    closing_by_id = {
        row["prediction_id"]: row for row in closing_rows if row.get("prediction_id")
    }
    closing_counts: dict[str, int] = {}
    for row in closing_rows:
        status = _clean(row.get("closing_status")) or "missing"
        closing_counts[status] = closing_counts.get(status, 0) + 1
    identity_rows = _read_identity_cache(identity_cache_csv)
    identity_counts: dict[str, int] = {}
    for row in identity_rows:
        status = _clean(row.get("identity_status")) or "unknown"
        identity_counts[status] = identity_counts.get(status, 0) + 1
    pnl_values = [
        float(row.get("unit_profit_loss"))
        for row in settled_rows
        if _clean(row.get("unit_profit_loss"))
    ]
    by_clv_rows = [
        {
            **row,
            "clv_bucket": _clv_bucket(closing_by_id.get(row.get("prediction_id", ""))),
        }
        for row in metric_rows
    ]
    return {
        "prospective_report_schema_version": PROSPECTIVE_REPORT_SCHEMA_VERSION,
        "generated_at": _iso_z(timestamp),
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "official_betting_picks_ready": False,
        "counts": {
            "predictions": len(prediction_rows),
            "settlements": len(settlement_rows),
            "settled": len(settled_rows),
            "pending": len(pending_rows),
            "void": len(void_rows),
            "manual_review": len(manual_rows),
            "prediction_dates": len(
                {row.get("game_date") for row in prediction_rows if row.get("game_date")}
            ),
            "unique_players": len(
                {
                    row.get("normalized_player_name")
                    for row in prediction_rows
                    if row.get("normalized_player_name")
                }
            ),
            "unique_games": len(
                {row.get("event_id") for row in prediction_rows if row.get("event_id")}
            ),
        },
        "identity": {
            "cache_rows": len(identity_rows),
            "status_counts": dict(sorted(identity_counts.items())),
            "cache_path": str(Path(identity_cache_csv).expanduser().resolve())
            if identity_cache_csv
            else "",
        },
        "closing_lines": {
            "rows": len(closing_rows),
            "status_counts": dict(sorted(closing_counts.items())),
            "coverage_rate": (
                sum(row.get("closing_status", "").startswith("captured") for row in closing_rows)
                / len(prediction_rows)
                if prediction_rows
                else None
            ),
            "post_start_evidence_used": False,
        },
        "metrics": {
            "model": model_metrics,
            "market_vig_included": market_metrics,
            "calibration": model_metrics.get("calibration_buckets", []),
        },
        "performance": {
            "by_date": _performance_by(metric_rows, key_name="game_date", key_fn=lambda row: row.get("game_date", "")),
            "by_sportsbook": _performance_by(metric_rows, key_name="sportsbook", key_fn=lambda row: row.get("sportsbook", "")),
            "by_model_probability_bucket": _performance_by(metric_rows, key_name="model_probability_bucket", key_fn=lambda row: _probability_bucket(row.get("model_probability"))),
            "by_odds_bucket": _performance_by(metric_rows, key_name="odds_bucket", key_fn=lambda row: _odds_bucket(row.get("original_odds"))),
            "by_park": {
                "unavailable": {
                    "settled_count": len(metric_rows),
                    "note": "park is not present in the current live HR research ledger",
                }
            },
            "by_clv_bucket": _performance_by(by_clv_rows, key_name="clv_bucket", key_fn=lambda row: row.get("clv_bucket", "missing")),
        },
        "research_pnl": {
            "unit_profit_loss": _format_float(sum(pnl_values)),
            "max_drawdown_units": _format_float(_max_drawdown(settled_rows)),
            "staking_assumption": "flat 1u research accounting only; no Kelly or bankroll sizing",
        },
        "gates": build_validation_gate_report(ledger_path=ledger_path),
        "caveats": [
            "Research-only evidence cannot create official picks or betting approval.",
            "P&L is diagnostic flat-unit research accounting, not bankroll guidance.",
            "Market baseline uses raw implied probabilities and remains vig-included.",
            "Park, lineup, pitcher, weather, and Statcast context are not in this live baseline ledger.",
            "Promotion requires human review even if future volume gates pass.",
        ],
    }


def build_advanced_feature_readiness_matrix() -> dict[str, object]:
    """Summarize readiness of requested advanced MLB HR features from local code/docs."""

    rows = [
        {
            "feature": "deterministic player identity / MLBAM ID",
            "existing_module_or_doc": "courtvision/sports/mlb/data/crosswalk_validation.py; docs/COURTVISION_MLB_HR_SOURCE_ACQUISITION_AND_CROSSWALK.md",
            "source_contract": "reviewed MLBAM/Retrosheet/player-source crosswalk; names are supporting evidence only",
            "coverage": "historical staging contract exists; live HR archive lacks IDs",
            "current_day_ready": False,
            "timestamp_as_of": "requires reviewed mapping_version and resolved_at/reviewed_at",
            "leakage_control": "identity is pregame/static, but conflicts must quarantine",
            "identity_dependency": "blocking dependency for most advanced joins",
            "missingness_risk": "high in current live archive",
            "licensing_status": "operator-supplied local authoritative export required",
            "priority": 1,
        },
        {
            "feature": "lineup, batting order, and expected plate appearances",
            "existing_module_or_doc": "courtvision/sports/mlb/research_context.py; docs/COURTVISION_PHASE2C_MLB_CONTEXT_ENRICHED_PIPELINE_2026_06_19.md",
            "source_contract": "sample/offline context schema has lineup_status and batting_order concepts",
            "coverage": "sample contract exists; no live verified lineup feed in this baseline",
            "current_day_ready": False,
            "timestamp_as_of": "must be as-of before prediction and before first pitch",
            "leakage_control": "confirmed lineup after lock is allowed only if captured before prediction",
            "identity_dependency": "requires MLBAM/player-game identity",
            "missingness_risk": "high without a licensed current-day lineup source",
            "licensing_status": "not supplied",
            "priority": 2,
        },
        {
            "feature": "starting pitcher HR, contact, strikeout, and pitch-type indicators",
            "existing_module_or_doc": "courtvision/sports/mlb/data/historical_feature_pack.py; courtvision/sports/mlb/pitch_matchup.py; courtvision/sports/mlb/training/hr_dataset_schema.py",
            "source_contract": "historical pack has pitcher rolling fields and pitcher_pitch_mix_json",
            "coverage": "historical pack builder exists; current live odds archive does not supply probable pitchers",
            "current_day_ready": False,
            "timestamp_as_of": "probable pitcher status must be pregame and timestamped",
            "leakage_control": "rolling stats must use games strictly before target date",
            "identity_dependency": "requires pitcher MLBAM ID and game identity",
            "missingness_risk": "medium-high until probable pitcher source is verified",
            "licensing_status": "operator/local source required",
            "priority": 3,
        },
        {
            "feature": "batter season/rolling HR, barrel, hard-hit, fly-ball, and exit velocity",
            "existing_module_or_doc": "courtvision/sports/mlb/data/historical_feature_pack.py; courtvision/sports/mlb/data/statcast_ingestion.py; courtvision/sports/mlb/hr_features.py",
            "source_contract": "Statcast local export plus historical rolling builder",
            "coverage": "historical/offline support exists; not present in live HR baseline",
            "current_day_ready": False,
            "timestamp_as_of": "rolling windows must exclude target-date outcomes",
            "leakage_control": "historical_feature_pack uses strict source dates before target date",
            "identity_dependency": "requires batter MLBAM ID",
            "missingness_risk": "medium once Statcast pack is complete",
            "licensing_status": "operator-approved Statcast/Baseball Savant export required",
            "priority": 4,
        },
        {
            "feature": "park, weather, roof, wind, and venue context",
            "existing_module_or_doc": "courtvision/sports/mlb/data/weather_ingestion.py; courtvision/sports/mlb/data/ballpark_factors.py; docs/MLB_STADIUM_MAP.md",
            "source_contract": "local weather observations and versioned ballpark factor table",
            "coverage": "ingestion modules exist; current live baseline has only home/away teams",
            "current_day_ready": False,
            "timestamp_as_of": "weather must be observation/forecast captured before prediction with roof status reviewed",
            "leakage_control": "no post-start official evidence for prospective trial",
            "identity_dependency": "requires exact game/venue identity",
            "missingness_risk": "medium-high for roof/retractable states",
            "licensing_status": "weather/park source review required",
            "priority": 5,
        },
        {
            "feature": "batter/pitcher handedness and platoon",
            "existing_module_or_doc": "courtvision/sports/mlb/research_context.py; courtvision/sports/mlb/training/hr_dataset_schema.py",
            "source_contract": "context schema allows batter_hand, pitcher_hand, platoon_side",
            "coverage": "schema support exists; no verified current-day feed in baseline",
            "current_day_ready": False,
            "timestamp_as_of": "static or pregame as-of source required",
            "leakage_control": "safe if sourced independently before prediction",
            "identity_dependency": "requires batter and pitcher identity",
            "missingness_risk": "medium",
            "licensing_status": "not supplied",
            "priority": 6,
        },
        {
            "feature": "team implied runs",
            "existing_module_or_doc": "no MLB HR live team-total module found in current baseline scope",
            "source_contract": "would require pregame market source and event crosswalk",
            "coverage": "not available in current live HR archive",
            "current_day_ready": False,
            "timestamp_as_of": "must be captured before prediction",
            "leakage_control": "pregame-only snapshots and source hashes required",
            "identity_dependency": "requires event/team identity",
            "missingness_risk": "high",
            "licensing_status": "licensed odds/provider source likely required",
            "priority": 7,
        },
        {
            "feature": "bullpen and rest context",
            "existing_module_or_doc": "courtvision/sports/mlb/research_context.py has broad team context; no dedicated live bullpen feed found",
            "source_contract": "would require pregame historical workload/source contract",
            "coverage": "not implemented for current baseline",
            "current_day_ready": False,
            "timestamp_as_of": "must use games completed before target date",
            "leakage_control": "must exclude current-game usage",
            "identity_dependency": "requires team/game identity",
            "missingness_risk": "high",
            "licensing_status": "not supplied",
            "priority": 8,
        },
    ]
    return {
        "research_label": RESEARCH_ONLY_LABEL,
        "approval_status": APPROVAL_STATUS,
        "matrix_version": "mlb-hr-advanced-feature-readiness-v1",
        "top_five_priorities": [row["feature"] for row in sorted(rows, key=lambda item: int(item["priority"]))[:5]],
        "rows": rows,
    }


def _print_json(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    from courtvision.sports.mlb.training import hr_prospective_market_movement
    from courtvision.sports.mlb.training import hr_prospective_trial

    parser = argparse.ArgumentParser(
        description="CourtVision MLB HR research-only baseline commands."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit-data")
    audit.add_argument("--odds-csv", type=Path, default=DEFAULT_ODDS_CSV)
    audit.add_argument("--results-csv", type=Path, default=DEFAULT_RESULTS_CSV)

    build = sub.add_parser("build-features")
    build.add_argument("--odds-csv", type=Path, default=DEFAULT_ODDS_CSV)
    build.add_argument("--results-csv", type=Path, default=DEFAULT_RESULTS_CSV)
    build.add_argument("--date", dest="target_date")
    build.add_argument("--output-dir", type=Path, required=True)

    train = sub.add_parser("train")
    train.add_argument("--features-csv", type=Path, required=True)
    train.add_argument("--output-root", type=Path, required=True)
    train.add_argument("--model-version", default="research-v1")

    predict = sub.add_parser("predict")
    predict.add_argument("--model-dir", type=Path, required=True)
    predict.add_argument("--odds-csv", type=Path, default=DEFAULT_ODDS_CSV)
    predict.add_argument("--output-dir", type=Path, required=True)
    predict.add_argument("--date", dest="target_date")
    predict.add_argument("--prediction-timestamp")
    predict.add_argument("--dry-run", action="store_true")

    append = sub.add_parser("append-ledger")
    append.add_argument("--predictions-csv", type=Path, required=True)
    append.add_argument("--ledger-csv", type=Path, required=True)

    settle = sub.add_parser("settle-ledger")
    settle.add_argument("--ledger-csv", type=Path, required=True)
    settle.add_argument("--results-csv", type=Path, default=DEFAULT_RESULTS_CSV)
    settle.add_argument("--settlement-timestamp")

    gates = sub.add_parser("report-gates")
    gates.add_argument("--features-csv", type=Path)
    gates.add_argument("--ledger-csv", type=Path)
    gates.add_argument("--metrics-json", type=Path)

    identity = sub.add_parser("resolve-identities")
    identity.add_argument("--features-csv", type=Path)
    identity.add_argument("--predictions-csv", type=Path)
    identity.add_argument("--identity-source-csv", type=Path)
    identity.add_argument("--identity-cache-csv", type=Path)
    identity.add_argument("--mapping-version", default=DEFAULT_IDENTITY_MAPPING_VERSION)
    identity.add_argument("--resolved-at")
    identity.add_argument("--output-dir", type=Path)
    identity.add_argument("--write-cache", action="store_true")

    daily = sub.add_parser("run-daily-research")
    daily.add_argument("--date", dest="target_date", required=True)
    daily.add_argument("--model-dir", type=Path, required=True)
    daily.add_argument("--output-root", type=Path, required=True)
    daily.add_argument("--ledger-csv", type=Path, required=True)
    daily.add_argument("--odds-csv", type=Path, default=DEFAULT_ODDS_CSV)
    daily.add_argument("--identity-source-csv", type=Path)
    daily.add_argument("--identity-cache-csv", type=Path)
    daily.add_argument("--prediction-timestamp")
    daily.add_argument("--dry-run", action="store_true")
    daily.add_argument("--force", action="store_true")

    verify = sub.add_parser("verify-predictions")
    verify.add_argument("--predictions-root", type=Path, required=True)
    verify.add_argument("--ledger-csv", type=Path)

    closing = sub.add_parser("capture-closing-lines")
    closing.add_argument("--predictions-csv", type=Path, required=True)
    closing.add_argument("--odds-csv", type=Path, default=DEFAULT_ODDS_CSV)
    closing.add_argument("--output-csv", type=Path, required=True)
    closing.add_argument("--captured-at")

    trial = sub.add_parser("report-trial")
    trial.add_argument("--ledger-csv", type=Path, required=True)
    trial.add_argument("--closing-lines-csv", type=Path)
    trial.add_argument("--identity-cache-csv", type=Path)
    trial.add_argument("--generated-at")

    sub.add_parser("feature-readiness")
    hr_prospective_trial.configure_prospective_cli(sub)
    hr_prospective_market_movement.configure_market_movement_cli(sub)

    args = parser.parse_args(argv)
    try:
        if args.command in hr_prospective_market_movement.REPORT_COMMANDS:
            print(
                json.dumps(
                    hr_prospective_market_movement.execute_market_movement_cli(
                        args
                    ),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command in hr_prospective_trial.PROSPECTIVE_COMMANDS:
            print(
                json.dumps(
                    hr_prospective_trial.execute_prospective_cli(args),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        elif args.command == "audit-data":
            result = build_live_hr_research_features(
                odds_path=args.odds_csv,
                results_path=args.results_csv,
                mode="training",
            )
            _print_json(result.manifest)
        elif args.command == "build-features":
            result = build_live_hr_research_features(
                odds_path=args.odds_csv,
                results_path=args.results_csv,
                target_date=args.target_date,
                mode="training",
            )
            paths = write_feature_artifacts(result, args.output_dir)
            _print_json({"paths": paths, "manifest": result.manifest})
        elif args.command == "train":
            result = train_research_logistic_baseline(
                feature_rows_path=args.features_csv,
                output_root=args.output_root,
                model_version=args.model_version,
            )
            _print_json({"model_id": result.model_id, "bundle_dir": str(result.bundle_dir)})
        elif args.command == "predict":
            result = generate_daily_research_predictions(
                model_bundle_dir=args.model_dir,
                odds_path=args.odds_csv,
                output_dir=args.output_dir,
                target_date=args.target_date,
                prediction_timestamp=args.prediction_timestamp,
                dry_run=args.dry_run,
            )
            _print_json(result.manifest)
        elif args.command == "append-ledger":
            result = append_predictions_to_ledger(
                predictions_csv=args.predictions_csv,
                ledger_path=args.ledger_csv,
            )
            _print_json(
                {
                    "ledger_path": str(result.ledger_path),
                    "appended_rows": result.appended_rows,
                }
            )
        elif args.command == "settle-ledger":
            result = settle_prediction_ledger(
                ledger_path=args.ledger_csv,
                results_path=args.results_csv,
                settlement_timestamp=args.settlement_timestamp,
            )
            _print_json(
                {
                    "ledger_path": str(result.ledger_path),
                    "appended_settlements": result.appended_settlements,
                    "pending_predictions": result.pending_predictions,
                    "skipped_existing_settlements": result.skipped_existing_settlements,
                    "conflicting_settlements": result.conflicting_settlements,
                }
            )
        elif args.command == "report-gates":
            metrics = None
            if args.metrics_json:
                metrics = json.loads(args.metrics_json.read_text(encoding="utf-8"))
            _print_json(
                build_validation_gate_report(
                    feature_rows_path=args.features_csv,
                    ledger_path=args.ledger_csv,
                    metrics=metrics,
                )
            )
        elif args.command == "resolve-identities":
            rows: tuple[dict[str, str], ...]
            if args.features_csv:
                _, rows = _read_csv(
                    args.features_csv,
                    required_columns=("player_name", "normalized_player_name"),
                    label="feature rows",
                )
                rows = _normalize_feature_rows(rows)
            elif args.predictions_csv:
                _, rows = _read_csv(
                    args.predictions_csv,
                    required_columns=PREDICTION_COLUMNS,
                    label="predictions",
                )
            else:
                raise MLBHRResearchBaselineError(
                    "--features-csv or --predictions-csv is required"
                )
            result = resolve_player_identities(
                feature_rows=rows,
                identity_source_csv=args.identity_source_csv,
                identity_cache_csv=args.identity_cache_csv,
                mapping_version=args.mapping_version,
                resolved_at=args.resolved_at,
                write_cache=args.write_cache,
            )
            if args.output_dir:
                output_dir = args.output_dir.expanduser().resolve()
                if output_dir.exists():
                    raise MLBHRResearchBaselineError(
                        f"identity output directory already exists: {output_dir}"
                    )
                output_dir.mkdir(parents=True, exist_ok=False)
                _write_csv_create_once(
                    output_dir / "identity_resolution.csv",
                    IDENTITY_CACHE_COLUMNS,
                    result.records,
                )
                _write_json_create_once(output_dir / "identity_report.json", result.report)
                _write_text_create_once(
                    output_dir / "identity_report.md",
                    _identity_report_markdown(result.report),
                )
            _print_json(result.report)
        elif args.command == "run-daily-research":
            result = run_daily_research(
                target_date=args.target_date,
                model_dir=args.model_dir,
                output_root=args.output_root,
                ledger_csv=args.ledger_csv,
                odds_csv=args.odds_csv,
                identity_source_csv=args.identity_source_csv,
                identity_cache_csv=args.identity_cache_csv,
                prediction_timestamp=args.prediction_timestamp,
                dry_run=args.dry_run,
                force=args.force,
            )
            _print_json(
                {
                    "run_id": result.run_id,
                    "status": result.status,
                    "output_dir": str(result.output_dir) if result.output_dir else "",
                    "summary": result.summary,
                }
            )
        elif args.command == "verify-predictions":
            result = verify_prediction_artifacts(
                predictions_root=args.predictions_root,
                ledger_path=args.ledger_csv,
            )
            _print_json(
                {
                    "passed": result.passed,
                    "errors": result.errors,
                    "warnings": result.warnings,
                    "summary": result.summary,
                }
            )
            if not result.passed:
                return 1
        elif args.command == "capture-closing-lines":
            result = capture_closing_line_snapshots(
                odds_path=args.odds_csv,
                predictions_csv=args.predictions_csv,
                output_csv=args.output_csv,
                captured_at=args.captured_at,
            )
            _print_json(result.report)
        elif args.command == "report-trial":
            _print_json(
                build_prospective_trial_report(
                    ledger_path=args.ledger_csv,
                    closing_lines_csv=args.closing_lines_csv,
                    identity_cache_csv=args.identity_cache_csv,
                    generated_at=args.generated_at,
                )
            )
        elif args.command == "feature-readiness":
            _print_json(build_advanced_feature_readiness_matrix())
    except MLBHRResearchBaselineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "APPROVAL_STATUS",
    "CLOSING_LINE_COLUMNS",
    "CLOSING_LINE_SCHEMA_VERSION",
    "COMPATIBLE_FEATURE_SCHEMA_VERSIONS",
    "COURTVISION_OPERATING_TIMEZONE",
    "COURTVISION_OPERATING_TIMEZONE_NAME",
    "DAILY_RUN_SCHEMA_VERSION",
    "DEFAULT_IDENTITY_MAPPING_VERSION",
    "FEATURE_COLUMNS",
    "FEATURE_SCHEMA_VERSION",
    "FEATURE_SCHEMA_VERSION_V1",
    "IDENTITY_CACHE_COLUMNS",
    "IDENTITY_CACHE_SCHEMA_VERSION",
    "LEDGER_COLUMNS",
    "LEDGER_SCHEMA_VERSION",
    "MLBHRResearchBaselineError",
    "MODEL_BUNDLE_SCHEMA_VERSION",
    "MODEL_REQUIRED_INPUT_COLUMNS",
    "PREDICTION_COLUMNS",
    "PREDICTION_SCHEMA_VERSION",
    "PROSPECTIVE_REPORT_SCHEMA_VERSION",
    "RESEARCH_ONLY_LABEL",
    "SPECIAL_EVENT_EXCLUSION_REASON",
    "append_predictions_to_ledger",
    "append_identity_cache_records",
    "american_to_decimal",
    "american_to_implied_probability",
    "build_advanced_feature_readiness_matrix",
    "build_live_hr_research_features",
    "build_prospective_trial_report",
    "build_validation_gate_report",
    "capture_closing_line_snapshots",
    "chronological_split_rows",
    "courtvision_operating_date",
    "generate_daily_research_predictions",
    "load_model_bundle",
    "main",
    "predict_model_probability",
    "resolve_mlb_model_bundle",
    "resolve_mlb_odds_csv",
    "resolve_mlb_output_dir",
    "resolve_player_identities",
    "run_daily_research",
    "settle_prediction_ledger",
    "train_research_logistic_baseline",
    "verify_prediction_artifacts",
    "win_profit_1u",
    "write_feature_artifacts",
]
