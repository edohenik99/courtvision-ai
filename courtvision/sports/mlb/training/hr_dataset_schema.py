"""Canonical, leakage-aware MLB home-run batter-game dataset contracts.

Phase 4A is schema-only. The immutable structures in this module do not read,
join, label, write, or train data. Odds remain nullable market context, outcome
labels are explicitly separated from pregame feature fields, and every row and
dataset is default-deny for production, wagering, and Kelly use.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime
import hashlib
import json
import math
from typing import Final, Iterable, Mapping, Sequence


MLB_HR_DATASET_SCHEMA_VERSION: Final = "1.0"
MLB_SPORT: Final = "MLB"
MLB_LEAGUE: Final = "MLB"
MLB_HR_MARKET_TYPE: Final = "home_run"
NOT_APPROVED: Final = "not_approved"

SUPPORTED_LINEUP_STATUSES: Final = frozenset(
    {"confirmed", "projected", "unknown", "not_starting"}
)
SUPPORTED_PITCHER_STATUSES: Final = frozenset(
    {"confirmed", "probable", "projected", "unknown"}
)
SUPPORTED_DATASET_MODES: Final = frozenset({"historical", "research"})
SUPPORTED_LEAKAGE_CHECK_STATUSES: Final = frozenset(
    {"not_checked", "passed", "failed"}
)

IDENTITY_FIELD_NAMES: Final = (
    "sport",
    "league",
    "schema_version",
    "dataset_version",
    "row_id",
    "game_id",
    "game_date",
    "event_start_time",
    "season",
    "game_number",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "home_team",
    "away_team",
    "is_home_team",
    "venue_name",
    "batting_order",
    "lineup_status",
    "probable_pitcher_id",
    "probable_pitcher_name",
    "probable_pitcher_team",
    "probable_pitcher_status",
)

HISTORICAL_ROLLING_FEATURE_FIELD_NAMES: Final = (
    "hitter_pa_window",
    "hitter_recent_hr_rate",
    "hitter_recent_barrel_rate",
    "hitter_recent_hard_hit_rate",
    "hitter_recent_fly_ball_rate",
    "hitter_recent_pull_rate",
    "hitter_avg_exit_velocity",
    "hitter_max_exit_velocity",
    "hitter_season_hr_rate_to_date",
    "hitter_season_barrel_rate_to_date",
    "hitter_season_hard_hit_rate_to_date",
    "pitcher_batters_faced_window",
    "pitcher_hr_allowed_rate_to_date",
    "pitcher_barrel_allowed_rate_to_date",
    "pitcher_hard_hit_allowed_rate_to_date",
    "pitcher_fly_ball_allowed_rate_to_date",
    "pitcher_pitch_mix_json",
)

PREGAME_CONTEXT_FEATURE_FIELD_NAMES: Final = (
    "batter_hand",
    "pitcher_hand",
    "platoon_side",
    "primary_pitch_matchup_score",
    "weather_temperature",
    "weather_wind_speed",
    "weather_wind_direction",
    "weather_wind_out_to_field",
    "weather_humidity",
    "roof_status",
    "park_factor_hr",
    "park_factor_lhb",
    "park_factor_rhb",
    "altitude",
)

PREGAME_FEATURE_FIELD_NAMES: Final = (
    *HISTORICAL_ROLLING_FEATURE_FIELD_NAMES,
    *PREGAME_CONTEXT_FEATURE_FIELD_NAMES,
)

ODDS_CONTEXT_FIELD_NAMES: Final = (
    "sportsbook",
    "odds_provider",
    "hr_market_available",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "odds_collected_at",
    "odds_as_of",
    "odds_is_fresh_for_pregame",
)

OUTCOME_LABEL_FIELD_NAMES: Final = (
    "hit_hr_today",
    "home_run_count",
    "plate_appearances",
    "game_completed",
    "label_source",
    "label_available",
    "label_as_of",
)

PROVENANCE_FIELD_NAMES: Final = (
    "feature_as_of",
    "source_manifest_ids",
    "statcast_manifest_id",
    "retrosheet_manifest_id",
    "weather_manifest_id",
    "ballpark_manifest_id",
    "odds_manifest_id",
    "lineup_source_type",
    "pitcher_source_type",
    "weather_source_type",
    "ballpark_source_type",
    "data_quality",
    "warnings",
    "missing_required_fields",
    "leakage_check_status",
    "eligible_for_training",
    "eligible_for_backtest",
    "eligible_for_betting",
    "kelly_eligible",
    "approval_status",
)

FORBIDDEN_DECISION_FIELD_NAMES: Final = frozenset(
    {
        "ev",
        "expected_value",
        "fair_probability",
        "stake",
        "stake_size",
        "unit",
        "units",
        "unit_size",
        "bankroll",
        "kelly_fraction",
        "recommendation",
        "selection_tier",
    }
)


class MLBHRDatasetSchemaError(ValueError):
    """Raised when a Phase 4A row or dataset contract fails validation."""


def _tuple_of_text(values: Sequence[str] | str, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    elif isinstance(values, bytes):
        raise TypeError(f"{field_name} must be a sequence of strings")
    normalized = tuple(values)
    if any(not isinstance(value, str) for value in normalized):
        raise TypeError(f"{field_name} must contain only strings")
    return normalized


@dataclass(frozen=True, slots=True)
class MLBHRBatterGameRow:
    """One batter, one game, pregame context, provenance, and later label."""

    sport: str = MLB_SPORT
    league: str = MLB_LEAGUE
    schema_version: str = MLB_HR_DATASET_SCHEMA_VERSION
    dataset_version: str | None = None
    row_id: str = ""
    game_id: str = ""
    game_date: date | str | None = None
    event_start_time: datetime | str | None = None
    season: int | None = None
    game_number: int | None = None
    player_id: str | int | None = None
    player_name: str = ""
    team: str | None = None
    opponent: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    is_home_team: bool | None = None
    venue_name: str | None = None
    batting_order: int | None = None
    lineup_status: str = "unknown"
    probable_pitcher_id: str | int | None = None
    probable_pitcher_name: str | None = None
    probable_pitcher_team: str | None = None
    probable_pitcher_status: str = "unknown"

    hitter_pa_window: int | None = None
    hitter_recent_hr_rate: float | None = None
    hitter_recent_barrel_rate: float | None = None
    hitter_recent_hard_hit_rate: float | None = None
    hitter_recent_fly_ball_rate: float | None = None
    hitter_recent_pull_rate: float | None = None
    hitter_avg_exit_velocity: float | None = None
    hitter_max_exit_velocity: float | None = None
    hitter_season_hr_rate_to_date: float | None = None
    hitter_season_barrel_rate_to_date: float | None = None
    hitter_season_hard_hit_rate_to_date: float | None = None

    pitcher_batters_faced_window: int | None = None
    pitcher_hr_allowed_rate_to_date: float | None = None
    pitcher_barrel_allowed_rate_to_date: float | None = None
    pitcher_hard_hit_allowed_rate_to_date: float | None = None
    pitcher_fly_ball_allowed_rate_to_date: float | None = None
    pitcher_pitch_mix_json: str | Mapping[str, float] | None = None

    batter_hand: str | None = None
    pitcher_hand: str | None = None
    platoon_side: str | None = None
    primary_pitch_matchup_score: float | None = None
    weather_temperature: float | None = None
    weather_wind_speed: float | None = None
    weather_wind_direction: str | None = None
    weather_wind_out_to_field: str | None = None
    weather_humidity: float | None = None
    roof_status: str | None = None
    park_factor_hr: float | None = None
    park_factor_lhb: float | None = None
    park_factor_rhb: float | None = None
    altitude: float | None = None

    sportsbook: str | None = None
    odds_provider: str | None = None
    hr_market_available: bool | None = None
    american_odds: int | None = None
    decimal_odds: float | None = None
    implied_probability: float | None = None
    odds_collected_at: datetime | str | None = None
    odds_as_of: datetime | str | None = None
    odds_is_fresh_for_pregame: bool | None = None

    hit_hr_today: bool | None = None
    home_run_count: int | None = None
    plate_appearances: int | None = None
    game_completed: bool | None = None
    label_source: str | None = None
    label_available: bool = False
    label_as_of: datetime | str | None = None

    feature_as_of: datetime | str | None = None
    source_manifest_ids: tuple[str, ...] = field(default_factory=tuple)
    statcast_manifest_id: str | None = None
    retrosheet_manifest_id: str | None = None
    weather_manifest_id: str | None = None
    ballpark_manifest_id: str | None = None
    odds_manifest_id: str | None = None
    lineup_source_type: str | None = None
    pitcher_source_type: str | None = None
    weather_source_type: str | None = None
    ballpark_source_type: str | None = None
    data_quality: str = "incomplete"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    missing_required_fields: tuple[str, ...] = field(default_factory=tuple)
    leakage_check_status: str = "not_checked"
    eligible_for_training: bool = False
    eligible_for_backtest: bool = False
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    approval_status: str = NOT_APPROVED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_manifest_ids",
            _tuple_of_text(self.source_manifest_ids, "source_manifest_ids"),
        )
        object.__setattr__(self, "warnings", _tuple_of_text(self.warnings, "warnings"))
        object.__setattr__(
            self,
            "missing_required_fields",
            _tuple_of_text(self.missing_required_fields, "missing_required_fields"),
        )


@dataclass(frozen=True, slots=True)
class MLBHRDatasetMetadata:
    """Immutable metadata for a research or historical row collection."""

    dataset_id: str
    generated_at: datetime | str
    date_range_start: date | str
    date_range_end: date | str
    row_count: int
    generated_by: str
    sport: str = MLB_SPORT
    league: str = MLB_LEAGUE
    market_type: str = MLB_HR_MARKET_TYPE
    schema_version: str = MLB_HR_DATASET_SCHEMA_VERSION
    source_manifest_ids: tuple[str, ...] = field(default_factory=tuple)
    mode: str = "historical"
    approval_status: str = NOT_APPROVED
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_manifest_ids",
            _tuple_of_text(self.source_manifest_ids, "source_manifest_ids"),
        )
        object.__setattr__(self, "warnings", _tuple_of_text(self.warnings, "warnings"))


@dataclass(frozen=True, slots=True)
class MLBHRDatasetManifest:
    """Schema-level dataset manifest; it does not materialize dataset rows."""

    metadata: MLBHRDatasetMetadata
    row_ids: tuple[str, ...] = field(default_factory=tuple)
    row_field_names: tuple[str, ...] = field(default_factory=lambda: ROW_FIELD_NAMES)
    feature_field_names: tuple[str, ...] = field(
        default_factory=lambda: PREGAME_FEATURE_FIELD_NAMES
    )
    label_field_names: tuple[str, ...] = field(
        default_factory=lambda: OUTCOME_LABEL_FIELD_NAMES
    )
    approval_status: str = NOT_APPROVED
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for field_name in (
            "row_ids",
            "row_field_names",
            "feature_field_names",
            "label_field_names",
            "warnings",
        ):
            object.__setattr__(
                self,
                field_name,
                _tuple_of_text(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class MLBHRDatasetValidationResult:
    """Deterministic row or metadata validation diagnostics."""

    is_valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def raise_for_errors(self) -> None:
        if not self.is_valid:
            raise MLBHRDatasetSchemaError("; ".join(self.errors))


ROW_FIELD_NAMES: Final = tuple(item.name for item in fields(MLBHRBatterGameRow))
ROW_FIELD_NAME_SET: Final = frozenset(ROW_FIELD_NAMES)


def _missing_text(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def _missing_id(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return not value.strip()
    return not isinstance(value, int)


def _parse_date(value: object, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"{field_name} must use ISO date format YYYY-MM-DD"
            ) from exc
    raise ValueError(f"{field_name} must be a date or ISO date string")


def _parse_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO datetime") from exc
    raise ValueError(f"{field_name} must be a datetime or ISO datetime string")


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _validate_optional_datetime(
    value: object, field_name: str, errors: list[str]
) -> datetime | None:
    if value is None:
        return None
    try:
        return _parse_datetime(value, field_name)
    except ValueError as exc:
        errors.append(str(exc))
        return None


def _feature_payload(row: MLBHRBatterGameRow) -> dict[str, object]:
    return {
        name: getattr(row, name)
        for name in (*PREGAME_FEATURE_FIELD_NAMES, *ODDS_CONTEXT_FIELD_NAMES)
    }


def _contains_label_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in OUTCOME_LABEL_FIELD_NAMES or _contains_label_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_label_key(item) for item in value)
    return False


def assert_no_label_leakage(row: MLBHRBatterGameRow) -> None:
    """Reject label names embedded inside any pregame feature payload."""

    if not isinstance(row, MLBHRBatterGameRow):
        raise MLBHRDatasetSchemaError("row has invalid type")
    if set(PREGAME_FEATURE_FIELD_NAMES) & set(OUTCOME_LABEL_FIELD_NAMES):
        raise MLBHRDatasetSchemaError("feature and label field declarations overlap")

    payload = _feature_payload(row)
    pitch_mix = payload.get("pitcher_pitch_mix_json")
    if isinstance(pitch_mix, str):
        try:
            pitch_mix = json.loads(pitch_mix)
        except json.JSONDecodeError as exc:
            raise MLBHRDatasetSchemaError(
                "pitcher_pitch_mix_json must contain a JSON object"
            ) from exc
        if not isinstance(pitch_mix, Mapping):
            raise MLBHRDatasetSchemaError(
                "pitcher_pitch_mix_json must contain a JSON object"
            )
    if _contains_label_key(pitch_mix):
        raise MLBHRDatasetSchemaError(
            "outcome label fields cannot appear in a feature namespace"
        )


def assert_feature_as_of_before_game(row: MLBHRBatterGameRow) -> None:
    """Fail closed when a known feature timestamp is not strictly pregame."""

    if row.feature_as_of is None or row.event_start_time is None:
        return
    try:
        feature_as_of = _parse_datetime(row.feature_as_of, "feature_as_of")
        event_start = _parse_datetime(row.event_start_time, "event_start_time")
        is_before = feature_as_of < event_start
    except (TypeError, ValueError) as exc:
        raise MLBHRDatasetSchemaError(
            "feature_as_of and event_start_time must be comparable ISO datetimes"
        ) from exc
    if not is_before:
        raise MLBHRDatasetSchemaError(
            "feature_as_of must be before event_start_time"
        )


def validate_batter_game_row(
    row: MLBHRBatterGameRow,
) -> MLBHRDatasetValidationResult:
    """Validate one canonical row without computing features or labels."""

    if not isinstance(row, MLBHRBatterGameRow):
        return MLBHRDatasetValidationResult(False, ("row has invalid type",))

    errors: list[str] = []
    validation_warnings: list[str] = []

    if row.sport != MLB_SPORT:
        errors.append("sport must be 'MLB'")
    if row.league != MLB_LEAGUE:
        errors.append("league must be 'MLB'")
    for field_name in ("schema_version", "row_id", "game_id", "player_name"):
        if _missing_text(getattr(row, field_name)):
            errors.append(f"{field_name} is required")
    if _missing_id(row.player_id):
        errors.append("player_id is required")
    if row.game_date is None:
        errors.append("game_date is required")
    else:
        try:
            _parse_date(row.game_date, "game_date")
        except ValueError as exc:
            errors.append(str(exc))

    if row.lineup_status not in SUPPORTED_LINEUP_STATUSES:
        errors.append(f"unsupported lineup_status: {row.lineup_status!r}")
    if row.probable_pitcher_status not in SUPPORTED_PITCHER_STATUSES:
        errors.append(
            f"unsupported probable_pitcher_status: {row.probable_pitcher_status!r}"
        )
    if row.leakage_check_status not in SUPPORTED_LEAKAGE_CHECK_STATUSES:
        errors.append(
            f"unsupported leakage_check_status: {row.leakage_check_status!r}"
        )

    event_start = _validate_optional_datetime(
        row.event_start_time, "event_start_time", errors
    )
    feature_as_of = _validate_optional_datetime(
        row.feature_as_of, "feature_as_of", errors
    )
    for field_name in ("odds_collected_at", "odds_as_of", "label_as_of"):
        _validate_optional_datetime(getattr(row, field_name), field_name, errors)
    if event_start is not None and feature_as_of is not None:
        try:
            if feature_as_of >= event_start:
                errors.append("feature_as_of must be before event_start_time")
        except TypeError:
            errors.append(
                "feature_as_of and event_start_time must use compatible timezone formats"
            )

    for field_name in (
        "season",
        "game_number",
        "batting_order",
        "hitter_pa_window",
        "pitcher_batters_faced_window",
        "home_run_count",
        "plate_appearances",
    ):
        value = getattr(row, field_name)
        minimum = 1 if field_name in {"season", "game_number", "batting_order"} else 0
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < minimum
        ):
            errors.append(f"{field_name} must be an integer >= {minimum} or null")

    numeric_optional_fields = (
        set(PREGAME_FEATURE_FIELD_NAMES)
        - {"pitcher_pitch_mix_json", "weather_wind_direction", "weather_wind_out_to_field", "roof_status", "batter_hand", "pitcher_hand", "platoon_side"}
    )
    for field_name in sorted(numeric_optional_fields):
        value = getattr(row, field_name)
        if value is not None and not _is_finite_number(value):
            errors.append(f"{field_name} must be a finite number or null")

    for field_name in ("decimal_odds", "implied_probability"):
        value = getattr(row, field_name)
        if value is not None and not _is_finite_number(value):
            errors.append(f"{field_name} must be a finite number or null")
    if row.american_odds is not None and (
        isinstance(row.american_odds, bool) or not isinstance(row.american_odds, int)
    ):
        errors.append("american_odds must be an integer or null")

    if row.approval_status != NOT_APPROVED:
        errors.append("approval_status must be 'not_approved'")
    if row.eligible_for_betting is not False:
        errors.append("eligible_for_betting must be false")
    if row.kelly_eligible is not False:
        errors.append("kelly_eligible must be false")
    if ROW_FIELD_NAME_SET & FORBIDDEN_DECISION_FIELD_NAMES:
        errors.append("row schema contains a forbidden decision field")

    try:
        assert_no_label_leakage(row)
    except MLBHRDatasetSchemaError as exc:
        errors.append(str(exc))

    missing_features = tuple(
        name for name in PREGAME_FEATURE_FIELD_NAMES if getattr(row, name) is None
    )
    if missing_features:
        validation_warnings.append(
            "optional pregame features missing: " + ", ".join(missing_features)
        )
    if row.feature_as_of is None:
        validation_warnings.append("feature_as_of is missing")
    if not row.source_manifest_ids:
        validation_warnings.append("source_manifest_ids is empty")
    if row.missing_required_fields:
        errors.append(
            "missing_required_fields must be empty for a valid row: "
            + ", ".join(row.missing_required_fields)
        )
    if _missing_text(row.data_quality):
        errors.append("data_quality is required")

    return MLBHRDatasetValidationResult(
        not errors,
        tuple(errors),
        tuple((*row.warnings, *validation_warnings)),
    )


def validate_dataset_metadata(
    metadata: MLBHRDatasetMetadata,
) -> MLBHRDatasetValidationResult:
    """Validate immutable dataset-level provenance and default-deny fields."""

    if not isinstance(metadata, MLBHRDatasetMetadata):
        return MLBHRDatasetValidationResult(False, ("metadata has invalid type",))

    errors: list[str] = []
    if _missing_text(metadata.dataset_id):
        errors.append("dataset_id is required")
    if metadata.sport != MLB_SPORT:
        errors.append("sport must be 'MLB'")
    if metadata.league != MLB_LEAGUE:
        errors.append("league must be 'MLB'")
    if metadata.market_type != MLB_HR_MARKET_TYPE:
        errors.append("market_type must be 'home_run'")
    if _missing_text(metadata.schema_version):
        errors.append("schema_version is required")
    if _missing_text(metadata.generated_by):
        errors.append("generated_by is required")
    if metadata.mode not in SUPPORTED_DATASET_MODES:
        errors.append(f"unsupported dataset mode: {metadata.mode!r}")

    generated_at = _validate_optional_datetime(
        metadata.generated_at, "generated_at", errors
    )
    if generated_at is None and metadata.generated_at is None:
        errors.append("generated_at is required")
    start: date | None = None
    end: date | None = None
    for field_name in ("date_range_start", "date_range_end"):
        try:
            parsed = _parse_date(getattr(metadata, field_name), field_name)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if field_name == "date_range_start":
                start = parsed
            else:
                end = parsed
    if start is not None and end is not None and start > end:
        errors.append("date_range_start must not be after date_range_end")
    if (
        isinstance(metadata.row_count, bool)
        or not isinstance(metadata.row_count, int)
        or metadata.row_count < 0
    ):
        errors.append("row_count must be a non-negative integer")

    if metadata.approval_status != NOT_APPROVED:
        errors.append("approval_status must be 'not_approved'")
    if metadata.eligible_for_betting is not False:
        errors.append("eligible_for_betting must be false")
    if metadata.kelly_eligible is not False:
        errors.append("kelly_eligible must be false")

    warnings = metadata.warnings
    if not metadata.source_manifest_ids:
        warnings = (*warnings, "source_manifest_ids is empty")
    return MLBHRDatasetValidationResult(not errors, tuple(errors), tuple(warnings))


def validate_dataset_manifest(
    manifest: MLBHRDatasetManifest,
) -> MLBHRDatasetValidationResult:
    """Validate manifest consistency without reading or materializing rows."""

    if not isinstance(manifest, MLBHRDatasetManifest):
        return MLBHRDatasetValidationResult(False, ("manifest has invalid type",))
    metadata_result = validate_dataset_metadata(manifest.metadata)
    errors = list(metadata_result.errors)
    if manifest.approval_status != NOT_APPROVED:
        errors.append("manifest approval_status must be 'not_approved'")
    if manifest.eligible_for_betting is not False:
        errors.append("manifest eligible_for_betting must be false")
    if manifest.kelly_eligible is not False:
        errors.append("manifest kelly_eligible must be false")
    if manifest.row_field_names != ROW_FIELD_NAMES:
        errors.append("manifest row_field_names must match the canonical schema")
    if manifest.feature_field_names != PREGAME_FEATURE_FIELD_NAMES:
        errors.append("manifest feature_field_names must match the canonical schema")
    if manifest.label_field_names != OUTCOME_LABEL_FIELD_NAMES:
        errors.append("manifest label_field_names must match the canonical schema")
    if len(manifest.row_ids) != manifest.metadata.row_count:
        errors.append("manifest row_ids count must match metadata.row_count")
    if len(set(manifest.row_ids)) != len(manifest.row_ids):
        errors.append("manifest row_ids must be unique")
    return MLBHRDatasetValidationResult(
        not errors,
        tuple(errors),
        tuple((*metadata_result.warnings, *manifest.warnings)),
    )


def dataset_row_id(
    game_id: str | int,
    player_id: str | int,
    market_type: str = MLB_HR_MARKET_TYPE,
) -> str:
    """Return a deterministic opaque identifier for one batter-game market."""

    if _missing_id(game_id):
        raise MLBHRDatasetSchemaError("game_id is required")
    if _missing_id(player_id):
        raise MLBHRDatasetSchemaError("player_id is required")
    if _missing_text(market_type):
        raise MLBHRDatasetSchemaError("market_type is required")
    canonical = f"MLB|{str(game_id).strip()}|{str(player_id).strip()}|{market_type.strip()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialized_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _serialized_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_serialized_value(item) for item in value]
    return value


def row_to_dict(row: MLBHRBatterGameRow) -> dict[str, object]:
    """Return the stable flat row schema after fail-closed validation."""

    validate_batter_game_row(row).raise_for_errors()
    return {name: _serialized_value(getattr(row, name)) for name in ROW_FIELD_NAMES}


def row_feature_dict(row: MLBHRBatterGameRow) -> dict[str, object]:
    """Serialize only declared pregame features and nullable odds context."""

    validate_batter_game_row(row).raise_for_errors()
    return {
        name: _serialized_value(getattr(row, name))
        for name in (*PREGAME_FEATURE_FIELD_NAMES, *ODDS_CONTEXT_FIELD_NAMES)
    }


def row_label_dict(row: MLBHRBatterGameRow) -> dict[str, object]:
    """Serialize only historical outcome labels, never pregame features."""

    validate_batter_game_row(row).raise_for_errors()
    return {
        name: _serialized_value(getattr(row, name))
        for name in OUTCOME_LABEL_FIELD_NAMES
    }


def row_to_json(row: MLBHRBatterGameRow, *, indent: int | None = None) -> str:
    """Return deterministic JSON for a validated canonical row."""

    return json.dumps(
        row_to_dict(row), ensure_ascii=False, indent=indent, sort_keys=True
    )


def _csv_value(value: object) -> object:
    serialized = _serialized_value(value)
    if isinstance(serialized, (dict, list)):
        return json.dumps(serialized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return serialized


def rows_to_csv_dicts(
    rows: Iterable[MLBHRBatterGameRow],
) -> list[dict[str, object]]:
    """Return validated flat mappings with deterministic column order."""

    serialized_rows: list[dict[str, object]] = []
    for row in rows:
        validate_batter_game_row(row).raise_for_errors()
        serialized_rows.append(
            {name: _csv_value(getattr(row, name)) for name in ROW_FIELD_NAMES}
        )
    return serialized_rows


def metadata_to_dict(metadata: MLBHRDatasetMetadata) -> dict[str, object]:
    """Return deterministic validated dataset metadata."""

    validate_dataset_metadata(metadata).raise_for_errors()
    return {
        item.name: _serialized_value(getattr(metadata, item.name))
        for item in fields(MLBHRDatasetMetadata)
    }


def metadata_to_json(
    metadata: MLBHRDatasetMetadata, *, indent: int | None = 2
) -> str:
    """Return deterministic JSON for validated dataset metadata."""

    return json.dumps(
        metadata_to_dict(metadata),
        ensure_ascii=False,
        indent=indent,
        sort_keys=True,
    )


def manifest_to_dict(manifest: MLBHRDatasetManifest) -> dict[str, object]:
    """Return deterministic schema-manifest data after validation."""

    validate_dataset_manifest(manifest).raise_for_errors()
    return {
        "metadata": metadata_to_dict(manifest.metadata),
        "row_ids": list(manifest.row_ids),
        "row_field_names": list(manifest.row_field_names),
        "feature_field_names": list(manifest.feature_field_names),
        "label_field_names": list(manifest.label_field_names),
        "approval_status": manifest.approval_status,
        "eligible_for_betting": manifest.eligible_for_betting,
        "kelly_eligible": manifest.kelly_eligible,
        "warnings": list(manifest.warnings),
    }


def manifest_to_json(
    manifest: MLBHRDatasetManifest, *, indent: int | None = 2
) -> str:
    """Return deterministic JSON for a validated schema manifest."""

    return json.dumps(
        manifest_to_dict(manifest),
        ensure_ascii=False,
        indent=indent,
        sort_keys=True,
    )


__all__ = [
    "FORBIDDEN_DECISION_FIELD_NAMES",
    "HISTORICAL_ROLLING_FEATURE_FIELD_NAMES",
    "IDENTITY_FIELD_NAMES",
    "MLB_HR_DATASET_SCHEMA_VERSION",
    "MLB_HR_MARKET_TYPE",
    "MLBHRBatterGameRow",
    "MLBHRDatasetManifest",
    "MLBHRDatasetMetadata",
    "MLBHRDatasetSchemaError",
    "MLBHRDatasetValidationResult",
    "ODDS_CONTEXT_FIELD_NAMES",
    "OUTCOME_LABEL_FIELD_NAMES",
    "PREGAME_CONTEXT_FEATURE_FIELD_NAMES",
    "PREGAME_FEATURE_FIELD_NAMES",
    "PROVENANCE_FIELD_NAMES",
    "ROW_FIELD_NAMES",
    "SUPPORTED_LINEUP_STATUSES",
    "SUPPORTED_PITCHER_STATUSES",
    "assert_feature_as_of_before_game",
    "assert_no_label_leakage",
    "dataset_row_id",
    "manifest_to_dict",
    "manifest_to_json",
    "metadata_to_dict",
    "metadata_to_json",
    "row_feature_dict",
    "row_label_dict",
    "row_to_dict",
    "row_to_json",
    "rows_to_csv_dicts",
    "validate_batter_game_row",
    "validate_dataset_manifest",
    "validate_dataset_metadata",
]
