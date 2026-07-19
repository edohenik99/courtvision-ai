"""Offline NBA projected-minutes feature contract.

This module is research-only. It builds deterministic pregame minutes features
from offline fixtures without provider I/O, credential reads, production output
writes, daily runners, ledgers, scoring changes, selection changes, Kelly
changes, grading changes, or dashboard side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Final, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION: Final = "nba-player-minutes-feature-v1"
NBA_PLAYER_MINUTES_OPERATING_TIMEZONE: Final = "America/Toronto"
NBA_PLAYER_MINUTES_RESEARCH_LABEL: Final = "research_only_not_for_betting"

NBA_PLAYER_MINUTES_PROJECTION_STATUSES: Final = (
    "projected",
    "insufficient_data",
    "inactive",
    "did_not_dress",
    "lineup_unconfirmed",
    "identity_unresolved",
    "event_unresolved",
    "conflicting",
    "quarantined",
)

NBA_PLAYER_MINUTES_CONFIDENCE_LEVELS: Final = (
    "high",
    "medium",
    "low",
    "unavailable",
)

NBA_PLAYER_MINUTES_FEATURE_FIELDS: Final = (
    "canonical_event_id",
    "provider_event_id",
    "player_id",
    "canonical_player_name",
    "team",
    "opponent",
    "operating_date",
    "commence_time_utc",
    "event_identity_status",
    "player_identity_status",
    "season_minutes",
    "season_minutes_sample_size",
    "recent_minutes",
    "recent_minutes_sample_size",
    "recent_minutes_stddev",
    "last_game_minutes",
    "starter_status",
    "lineup_status",
    "injury_status",
    "availability_status",
    "role_status",
    "days_rest",
    "games_last_7_days",
    "games_last_14_days",
    "teammate_absence_context",
    "projected_minutes",
    "projected_minutes_low",
    "projected_minutes_high",
    "minutes_confidence",
    "minutes_projection_status",
    "minutes_projection_method",
    "minutes_exclusion_reason",
    "applied_adjustments",
    "unadjusted_minutes_basis",
    "feature_timestamp_utc",
    "feature_cutoff_timestamp_utc",
    "source_manifest_id",
    "source_hashes",
    "feature_schema_version",
    "repository_commit_sha",
    "research_label",
)

NBA_PLAYER_MINUTES_UTC_TIMESTAMP_FIELDS: Final = (
    "commence_time_utc",
    "feature_timestamp_utc",
    "feature_cutoff_timestamp_utc",
)

NBA_PLAYER_MINUTES_HARD_MINUTES_MIN: Final = 0.0
NBA_PLAYER_MINUTES_HARD_MINUTES_MAX: Final = 48.0
NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MIN: Final = 8.0
NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MAX: Final = 42.0
NBA_PLAYER_MINUTES_MIN_RECENT_SAMPLE: Final = 3
NBA_PLAYER_MINUTES_MAX_POSITIVE_ADJUSTMENT: Final = 6.0
NBA_PLAYER_MINUTES_MAX_NEGATIVE_ADJUSTMENT: Final = -24.0

_UTC: Final = timezone.utc
_TORONTO_FALLBACK: Final = object()
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_IDENTITY_STATUSES: Final = ("resolved", "unresolved", "conflicting", "quarantined")
_ZERO_HASH: Final = "0" * 64


class NBAPlayerMinutesFeatureSchemaError(ValueError):
    """Raised when the offline minutes feature contract fails closed."""


def _bootstrap_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    return text


def _bootstrap_normalize_key(value: object, field_name: str) -> str:
    text = _bootstrap_text(value, field_name).casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not normalized:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    return normalized


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesProviderCapability:
    """Explicit field support for one offline projected-minutes fixture source."""

    provider_name: str
    provider_role: str
    source_type: str
    mode: str
    supports_live_calls: bool
    available_fields: tuple[str, ...] = ()
    unsupported_field_reasons: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _bootstrap_normalize_key(self.provider_name, "provider_name"))
        object.__setattr__(self, "provider_role", _bootstrap_text(self.provider_role, "provider_role"))
        object.__setattr__(self, "source_type", _bootstrap_text(self.source_type, "source_type"))
        object.__setattr__(self, "mode", _bootstrap_text(self.mode, "mode"))
        object.__setattr__(
            self,
            "available_fields",
            tuple(_bootstrap_text(value, "available_fields") for value in self.available_fields),
        )
        reasons = {
            _bootstrap_text(field_name, "unsupported field name"): _bootstrap_text(
                reason,
                f"unsupported_field_reasons.{field_name}",
            )
            for field_name, reason in dict(self.unsupported_field_reasons).items()
        }
        object.__setattr__(self, "unsupported_field_reasons", MappingProxyType(reasons))
        if self.supports_live_calls is not False:
            raise NBAPlayerMinutesFeatureSchemaError("offline fixture capabilities must not support live calls")

    @property
    def unsupported_fields(self) -> tuple[str, ...]:
        return tuple(self.unsupported_field_reasons)

    def to_matrix_row(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_role": self.provider_role,
            "source_type": self.source_type,
            "mode": self.mode,
            "supports_live_calls": self.supports_live_calls,
            "available_fields": list(self.available_fields),
            "unsupported_fields": dict(self.unsupported_field_reasons),
        }


PLAYER_BASELINE_CAPABILITY: Final = NBAPlayerMinutesProviderCapability(
    provider_name="nba_player_baseline_fixture",
    provider_role="player_minutes_baseline",
    source_type="offline_fixture",
    mode="offline",
    supports_live_calls=False,
    available_fields=(
        "provider_event_id",
        "canonical_event_id",
        "player_id",
        "canonical_player_name",
        "team",
        "opponent",
        "commence_time_utc",
        "event_identity_status",
        "player_identity_status",
        "min_avg",
        "min_recent",
        "season_minutes_sample_size",
        "recent_minutes_sample_size",
        "recent_minutes_stddev",
        "last_game_minutes",
        "source_timestamp_utc",
    ),
    unsupported_field_reasons={
        "projected_minutes": "feature builder calculates projected_minutes; source aliases are rejected",
        "actual_minutes": "target-game actual minutes are leakage",
        "final_points": "target-game final statistics are leakage",
    },
)

LINEUP_STATUS_CAPABILITY: Final = NBAPlayerMinutesProviderCapability(
    provider_name="nba_lineup_status_fixture",
    provider_role="pregame_lineup_status",
    source_type="offline_fixture",
    mode="offline",
    supports_live_calls=False,
    available_fields=("starter_status", "lineup_status", "source_timestamp_utc"),
    unsupported_field_reasons={
        "actual_minutes": "post-tip participation is not permitted before cutoff",
        "final_points": "target-event final statistics are not permitted before cutoff",
    },
)

INJURY_AVAILABILITY_CAPABILITY: Final = NBAPlayerMinutesProviderCapability(
    provider_name="nba_injury_availability_fixture",
    provider_role="pregame_injury_availability",
    source_type="offline_fixture",
    mode="offline",
    supports_live_calls=False,
    available_fields=("injury_status", "availability_status", "source_timestamp_utc"),
    unsupported_field_reasons={
        "actual_minutes": "target-game actual minutes are leakage",
        "final_points": "target-event final statistics are leakage",
    },
)

SCHEDULE_REST_CAPABILITY: Final = NBAPlayerMinutesProviderCapability(
    provider_name="nba_schedule_rest_fixture",
    provider_role="pregame_schedule_rest",
    source_type="offline_fixture",
    mode="offline",
    supports_live_calls=False,
    available_fields=("days_rest", "games_last_7_days", "games_last_14_days", "source_timestamp_utc"),
    unsupported_field_reasons={
        "actual_minutes": "schedule evidence must not include target-game participation",
        "final_points": "schedule evidence must not include target-game results",
    },
)

ROLE_CONTEXT_CAPABILITY: Final = NBAPlayerMinutesProviderCapability(
    provider_name="nba_role_context_fixture",
    provider_role="reviewed_role_context",
    source_type="offline_fixture",
    mode="offline",
    supports_live_calls=False,
    available_fields=(
        "role_status",
        "teammate_absence_context",
        "minutes_restriction",
        "source_timestamp_utc",
    ),
    unsupported_field_reasons={
        "actual_minutes": "role context must be reviewed pregame evidence, not target-game results",
        "final_points": "target-event final statistics are leakage",
    },
)

NBA_PLAYER_MINUTES_PROVIDER_CAPABILITIES: Final = (
    PLAYER_BASELINE_CAPABILITY,
    LINEUP_STATUS_CAPABILITY,
    INJURY_AVAILABILITY_CAPABILITY,
    SCHEDULE_REST_CAPABILITY,
    ROLE_CONTEXT_CAPABILITY,
)


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesBaselineEvidence:
    """Offline baseline row with player/event identity and historical minutes inputs."""

    provider_name: str
    provider_event_id: str
    canonical_event_id: str | None
    player_id: str | None
    canonical_player_name: str
    team: str
    opponent: str
    operating_date: date
    commence_time_utc: datetime
    event_identity_status: str
    player_identity_status: str
    season_minutes: float | None
    season_minutes_sample_size: int | None
    recent_minutes: float | None
    recent_minutes_sample_size: int | None
    recent_minutes_stddev: float | None
    last_game_minutes: float | None
    source_timestamp_utc: datetime
    source_reference: str
    source_hash: str
    raw_source_row: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_key(self.provider_name, "provider_name"))
        object.__setattr__(self, "provider_event_id", _require_identifier(self.provider_event_id, "provider_event_id"))
        event_status = _normalize_status(
            self.event_identity_status,
            "event_identity_status",
            _IDENTITY_STATUSES,
        )
        player_status = _normalize_status(
            self.player_identity_status,
            "player_identity_status",
            _IDENTITY_STATUSES,
        )
        object.__setattr__(self, "event_identity_status", event_status)
        object.__setattr__(self, "player_identity_status", player_status)
        if event_status == "resolved":
            object.__setattr__(
                self,
                "canonical_event_id",
                _require_identifier(self.canonical_event_id, "canonical_event_id"),
            )
        elif self.canonical_event_id is not None:
            object.__setattr__(
                self,
                "canonical_event_id",
                _optional_identifier(self.canonical_event_id, "canonical_event_id"),
            )
        if player_status == "resolved":
            object.__setattr__(self, "player_id", _require_identifier(self.player_id, "player_id"))
        elif self.player_id is not None:
            object.__setattr__(self, "player_id", _optional_identifier(self.player_id, "player_id"))
        object.__setattr__(self, "canonical_player_name", _require_text(self.canonical_player_name, "canonical_player_name"))
        object.__setattr__(self, "team", _normalize_team(self.team, "team"))
        object.__setattr__(self, "opponent", _normalize_team(self.opponent, "opponent"))
        object.__setattr__(
            self,
            "commence_time_utc",
            _require_utc_datetime(self.commence_time_utc, "commence_time_utc"),
        )
        if self.operating_date != toronto_operating_date(self.commence_time_utc):
            raise NBAPlayerMinutesFeatureSchemaError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        for field_name in ("season_minutes", "recent_minutes", "recent_minutes_stddev", "last_game_minutes"):
            object.__setattr__(
                self,
                field_name,
                _optional_nonnegative_number(getattr(self, field_name), field_name),
            )
        for field_name in ("season_minutes_sample_size", "recent_minutes_sample_size"):
            object.__setattr__(
                self,
                field_name,
                _optional_nonnegative_int(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _require_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(self, "source_reference", _require_text(self.source_reference, "source_reference"))
        object.__setattr__(self, "source_hash", _require_sha256(self.source_hash, "source_hash"))
        object.__setattr__(self, "raw_source_row", MappingProxyType(_json_clone_mapping(self.raw_source_row)))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "player_id": self.player_id,
            "canonical_player_name": self.canonical_player_name,
            "team": self.team,
            "opponent": self.opponent,
            "operating_date": self.operating_date.isoformat(),
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "event_identity_status": self.event_identity_status,
            "player_identity_status": self.player_identity_status,
            "season_minutes": self.season_minutes,
            "season_minutes_sample_size": self.season_minutes_sample_size,
            "recent_minutes": self.recent_minutes,
            "recent_minutes_sample_size": self.recent_minutes_sample_size,
            "recent_minutes_stddev": self.recent_minutes_stddev,
            "last_game_minutes": self.last_game_minutes,
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "source_reference": self.source_reference,
            "source_hash": self.source_hash,
            "raw_source_row": _json_clone(self.raw_source_row),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesLineupEvidence:
    """Offline pregame lineup evidence."""

    provider_name: str
    starter_status: str
    lineup_status: str
    source_timestamp_utc: datetime
    source_reference: str
    source_hash: str
    raw_source_row: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_key(self.provider_name, "provider_name"))
        object.__setattr__(self, "starter_status", _normalize_key(self.starter_status, "starter_status"))
        object.__setattr__(self, "lineup_status", _normalize_key(self.lineup_status, "lineup_status"))
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _require_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(self, "source_reference", _require_text(self.source_reference, "source_reference"))
        object.__setattr__(self, "source_hash", _require_sha256(self.source_hash, "source_hash"))
        object.__setattr__(self, "raw_source_row", MappingProxyType(_json_clone_mapping(self.raw_source_row)))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "starter_status": self.starter_status,
            "lineup_status": self.lineup_status,
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "source_reference": self.source_reference,
            "source_hash": self.source_hash,
            "raw_source_row": _json_clone(self.raw_source_row),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesAvailabilityEvidence:
    """Offline pregame injury and availability evidence."""

    provider_name: str
    injury_status: str
    availability_status: str
    source_timestamp_utc: datetime
    source_reference: str
    source_hash: str
    raw_source_row: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_key(self.provider_name, "provider_name"))
        object.__setattr__(self, "injury_status", _normalize_key(self.injury_status, "injury_status"))
        object.__setattr__(self, "availability_status", _normalize_key(self.availability_status, "availability_status"))
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _require_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(self, "source_reference", _require_text(self.source_reference, "source_reference"))
        object.__setattr__(self, "source_hash", _require_sha256(self.source_hash, "source_hash"))
        object.__setattr__(self, "raw_source_row", MappingProxyType(_json_clone_mapping(self.raw_source_row)))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "injury_status": self.injury_status,
            "availability_status": self.availability_status,
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "source_reference": self.source_reference,
            "source_hash": self.source_hash,
            "raw_source_row": _json_clone(self.raw_source_row),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesScheduleEvidence:
    """Offline pregame schedule and rest evidence."""

    provider_name: str
    days_rest: int | None
    games_last_7_days: int | None
    games_last_14_days: int | None
    source_timestamp_utc: datetime
    source_reference: str
    source_hash: str
    raw_source_row: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_key(self.provider_name, "provider_name"))
        for field_name in ("days_rest", "games_last_7_days", "games_last_14_days"):
            object.__setattr__(
                self,
                field_name,
                _optional_nonnegative_int(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _require_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(self, "source_reference", _require_text(self.source_reference, "source_reference"))
        object.__setattr__(self, "source_hash", _require_sha256(self.source_hash, "source_hash"))
        object.__setattr__(self, "raw_source_row", MappingProxyType(_json_clone_mapping(self.raw_source_row)))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "days_rest": self.days_rest,
            "games_last_7_days": self.games_last_7_days,
            "games_last_14_days": self.games_last_14_days,
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "source_reference": self.source_reference,
            "source_hash": self.source_hash,
            "raw_source_row": _json_clone(self.raw_source_row),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesRoleContextEvidence:
    """Offline reviewed role-change and teammate-absence evidence."""

    provider_name: str
    role_status: str
    teammate_absence_context: Mapping[str, object]
    minutes_restriction: Mapping[str, object] | None
    source_timestamp_utc: datetime
    source_reference: str
    source_hash: str
    raw_source_row: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_key(self.provider_name, "provider_name"))
        object.__setattr__(self, "role_status", _normalize_key(self.role_status, "role_status"))
        object.__setattr__(
            self,
            "teammate_absence_context",
            MappingProxyType(_json_clone_mapping(self.teammate_absence_context)),
        )
        if self.minutes_restriction is not None:
            object.__setattr__(
                self,
                "minutes_restriction",
                MappingProxyType(_json_clone_mapping(self.minutes_restriction)),
            )
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _require_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(self, "source_reference", _require_text(self.source_reference, "source_reference"))
        object.__setattr__(self, "source_hash", _require_sha256(self.source_hash, "source_hash"))
        object.__setattr__(self, "raw_source_row", MappingProxyType(_json_clone_mapping(self.raw_source_row)))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "role_status": self.role_status,
            "teammate_absence_context": _json_clone(self.teammate_absence_context),
            "minutes_restriction": _json_clone(self.minutes_restriction),
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "source_reference": self.source_reference,
            "source_hash": self.source_hash,
            "raw_source_row": _json_clone(self.raw_source_row),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesAdjustment:
    """One explicit bounded minutes adjustment from verified pregame evidence."""

    adjustment_name: str
    input_evidence: str
    numeric_value: float
    maximum_allowed_magnitude: float
    source_timestamp_utc: datetime
    source_reference: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "adjustment_name", _normalize_key(self.adjustment_name, "adjustment_name"))
        object.__setattr__(self, "input_evidence", _require_text(self.input_evidence, "input_evidence"))
        numeric_value = _require_finite_number(self.numeric_value, "numeric_value")
        maximum = _require_nonnegative_number(self.maximum_allowed_magnitude, "maximum_allowed_magnitude")
        if abs(numeric_value) > maximum:
            raise NBAPlayerMinutesFeatureSchemaError(
                f"{self.adjustment_name} exceeds maximum allowed magnitude"
            )
        object.__setattr__(self, "numeric_value", round(numeric_value, 4))
        object.__setattr__(self, "maximum_allowed_magnitude", round(maximum, 4))
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _require_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(self, "source_reference", _require_text(self.source_reference, "source_reference"))
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))

    def to_dict(self) -> dict[str, object]:
        return {
            "adjustment_name": self.adjustment_name,
            "input_evidence": self.input_evidence,
            "numeric_value": self.numeric_value,
            "maximum_allowed_magnitude": self.maximum_allowed_magnitude,
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "source_reference": self.source_reference,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerMinutesFeatureRow:
    """Complete projected-minutes feature row for offline player-points research."""

    canonical_event_id: str | None
    provider_event_id: str
    player_id: str | None
    canonical_player_name: str
    team: str
    opponent: str
    operating_date: date
    commence_time_utc: datetime
    event_identity_status: str
    player_identity_status: str
    season_minutes: float | None
    season_minutes_sample_size: int | None
    recent_minutes: float | None
    recent_minutes_sample_size: int | None
    recent_minutes_stddev: float | None
    last_game_minutes: float | None
    starter_status: str
    lineup_status: str
    injury_status: str
    availability_status: str
    role_status: str
    days_rest: int | None
    games_last_7_days: int | None
    games_last_14_days: int | None
    teammate_absence_context: Mapping[str, object]
    projected_minutes: float | None
    projected_minutes_low: float | None
    projected_minutes_high: float | None
    minutes_confidence: str
    minutes_projection_status: str
    minutes_projection_method: str
    minutes_exclusion_reason: str
    applied_adjustments: tuple[NBAPlayerMinutesAdjustment, ...]
    unadjusted_minutes_basis: Mapping[str, object]
    feature_timestamp_utc: datetime
    feature_cutoff_timestamp_utc: datetime
    source_manifest_id: str
    source_hashes: Mapping[str, str]
    feature_schema_version: str
    repository_commit_sha: str
    research_label: str = NBA_PLAYER_MINUTES_RESEARCH_LABEL

    def __post_init__(self) -> None:
        validate_schema_version(self.feature_schema_version)
        object.__setattr__(self, "provider_event_id", _require_identifier(self.provider_event_id, "provider_event_id"))
        event_status = _normalize_status(
            self.event_identity_status,
            "event_identity_status",
            _IDENTITY_STATUSES,
        )
        player_status = _normalize_status(
            self.player_identity_status,
            "player_identity_status",
            _IDENTITY_STATUSES,
        )
        object.__setattr__(self, "event_identity_status", event_status)
        object.__setattr__(self, "player_identity_status", player_status)
        if event_status == "resolved":
            object.__setattr__(
                self,
                "canonical_event_id",
                _require_identifier(self.canonical_event_id, "canonical_event_id"),
            )
        elif self.canonical_event_id is not None:
            object.__setattr__(
                self,
                "canonical_event_id",
                _optional_identifier(self.canonical_event_id, "canonical_event_id"),
            )
        if player_status == "resolved":
            object.__setattr__(self, "player_id", _require_identifier(self.player_id, "player_id"))
        elif self.player_id is not None:
            object.__setattr__(self, "player_id", _optional_identifier(self.player_id, "player_id"))
        object.__setattr__(self, "canonical_player_name", _require_text(self.canonical_player_name, "canonical_player_name"))
        object.__setattr__(self, "team", _normalize_team(self.team, "team"))
        object.__setattr__(self, "opponent", _normalize_team(self.opponent, "opponent"))
        object.__setattr__(
            self,
            "commence_time_utc",
            _require_utc_datetime(self.commence_time_utc, "commence_time_utc"),
        )
        if self.operating_date != toronto_operating_date(self.commence_time_utc):
            raise NBAPlayerMinutesFeatureSchemaError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        for field_name in ("season_minutes", "recent_minutes", "recent_minutes_stddev", "last_game_minutes"):
            object.__setattr__(
                self,
                field_name,
                _optional_nonnegative_number(getattr(self, field_name), field_name),
            )
        for field_name in (
            "season_minutes_sample_size",
            "recent_minutes_sample_size",
            "days_rest",
            "games_last_7_days",
            "games_last_14_days",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_nonnegative_int(getattr(self, field_name), field_name),
            )
        for field_name in (
            "starter_status",
            "lineup_status",
            "injury_status",
            "availability_status",
            "role_status",
            "minutes_projection_method",
        ):
            object.__setattr__(self, field_name, _normalize_key(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "minutes_exclusion_reason",
            _require_text(self.minutes_exclusion_reason, "minutes_exclusion_reason"),
        )
        object.__setattr__(self, "source_manifest_id", _require_text(self.source_manifest_id, "source_manifest_id"))
        object.__setattr__(
            self,
            "minutes_projection_status",
            _normalize_status(
                self.minutes_projection_status,
                "minutes_projection_status",
                NBA_PLAYER_MINUTES_PROJECTION_STATUSES,
            ),
        )
        object.__setattr__(
            self,
            "minutes_confidence",
            _normalize_status(
                self.minutes_confidence,
                "minutes_confidence",
                NBA_PLAYER_MINUTES_CONFIDENCE_LEVELS,
            ),
        )
        object.__setattr__(
            self,
            "teammate_absence_context",
            MappingProxyType(_json_clone_mapping(self.teammate_absence_context)),
        )
        object.__setattr__(self, "applied_adjustments", tuple(self.applied_adjustments))
        for adjustment in self.applied_adjustments:
            if not isinstance(adjustment, NBAPlayerMinutesAdjustment):
                raise TypeError("applied_adjustments must contain NBAPlayerMinutesAdjustment values")
        object.__setattr__(
            self,
            "unadjusted_minutes_basis",
            MappingProxyType(_json_clone_mapping(self.unadjusted_minutes_basis)),
        )
        for field_name in ("feature_timestamp_utc", "feature_cutoff_timestamp_utc"):
            object.__setattr__(
                self,
                field_name,
                _require_utc_datetime(getattr(self, field_name), field_name),
            )
        if not (self.feature_timestamp_utc <= self.feature_cutoff_timestamp_utc < self.commence_time_utc):
            raise NBAPlayerMinutesFeatureSchemaError(
                "feature_timestamp_utc <= feature_cutoff_timestamp_utc < commence_time_utc is required"
            )
        object.__setattr__(self, "source_hashes", _validate_source_hashes(self.source_hashes))
        commit_sha = _require_text(self.repository_commit_sha, "repository_commit_sha").casefold()
        if _COMMIT_SHA_RE.fullmatch(commit_sha) is None:
            raise NBAPlayerMinutesFeatureSchemaError(
                "repository_commit_sha must be a 7-40 character lowercase git SHA"
            )
        object.__setattr__(self, "repository_commit_sha", commit_sha)
        if self.research_label != NBA_PLAYER_MINUTES_RESEARCH_LABEL:
            raise NBAPlayerMinutesFeatureSchemaError("research_label is unsupported")
        _validate_projected_minutes_contract(
            projected_minutes=self.projected_minutes,
            projected_minutes_low=self.projected_minutes_low,
            projected_minutes_high=self.projected_minutes_high,
            status=self.minutes_projection_status,
        )
        if self.minutes_projection_status != "projected" and self.minutes_confidence == "high":
            raise NBAPlayerMinutesFeatureSchemaError("diagnostic or excluded rows cannot have high confidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_event_id": self.canonical_event_id,
            "provider_event_id": self.provider_event_id,
            "player_id": self.player_id,
            "canonical_player_name": self.canonical_player_name,
            "team": self.team,
            "opponent": self.opponent,
            "operating_date": self.operating_date.isoformat(),
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "event_identity_status": self.event_identity_status,
            "player_identity_status": self.player_identity_status,
            "season_minutes": self.season_minutes,
            "season_minutes_sample_size": self.season_minutes_sample_size,
            "recent_minutes": self.recent_minutes,
            "recent_minutes_sample_size": self.recent_minutes_sample_size,
            "recent_minutes_stddev": self.recent_minutes_stddev,
            "last_game_minutes": self.last_game_minutes,
            "starter_status": self.starter_status,
            "lineup_status": self.lineup_status,
            "injury_status": self.injury_status,
            "availability_status": self.availability_status,
            "role_status": self.role_status,
            "days_rest": self.days_rest,
            "games_last_7_days": self.games_last_7_days,
            "games_last_14_days": self.games_last_14_days,
            "teammate_absence_context": _json_clone(self.teammate_absence_context),
            "projected_minutes": self.projected_minutes,
            "projected_minutes_low": self.projected_minutes_low,
            "projected_minutes_high": self.projected_minutes_high,
            "minutes_confidence": self.minutes_confidence,
            "minutes_projection_status": self.minutes_projection_status,
            "minutes_projection_method": self.minutes_projection_method,
            "minutes_exclusion_reason": self.minutes_exclusion_reason,
            "applied_adjustments": [adjustment.to_dict() for adjustment in self.applied_adjustments],
            "unadjusted_minutes_basis": _json_clone(self.unadjusted_minutes_basis),
            "feature_timestamp_utc": _format_utc(self.feature_timestamp_utc),
            "feature_cutoff_timestamp_utc": _format_utc(self.feature_cutoff_timestamp_utc),
            "source_manifest_id": self.source_manifest_id,
            "source_hashes": dict(self.source_hashes),
            "feature_schema_version": self.feature_schema_version,
            "repository_commit_sha": self.repository_commit_sha,
            "research_label": self.research_label,
        }


def schema_definition() -> dict[str, object]:
    """Return the versioned projected-minutes feature schema contract."""

    return {
        "schema_version": NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION,
        "operating_timezone": NBA_PLAYER_MINUTES_OPERATING_TIMEZONE,
        "required_fields": list(NBA_PLAYER_MINUTES_FEATURE_FIELDS),
        "projection_statuses": list(NBA_PLAYER_MINUTES_PROJECTION_STATUSES),
        "confidence_levels": list(NBA_PLAYER_MINUTES_CONFIDENCE_LEVELS),
        "utc_timestamp_fields": list(NBA_PLAYER_MINUTES_UTC_TIMESTAMP_FIELDS),
        "hard_minutes_bounds": {
            "minimum": NBA_PLAYER_MINUTES_HARD_MINUTES_MIN,
            "maximum": NBA_PLAYER_MINUTES_HARD_MINUTES_MAX,
        },
        "research_eligibility_bounds": {
            "minimum": NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MIN,
            "maximum": NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MAX,
        },
        "adjustment_total_bounds": {
            "minimum": NBA_PLAYER_MINUTES_MAX_NEGATIVE_ADJUSTMENT,
            "maximum": NBA_PLAYER_MINUTES_MAX_POSITIVE_ADJUSTMENT,
        },
        "research_label": NBA_PLAYER_MINUTES_RESEARCH_LABEL,
    }


def provider_capability_matrix() -> dict[str, dict[str, object]]:
    """Return explicit field support for all offline minutes fixture adapters."""

    return {
        capability.provider_name: capability.to_matrix_row()
        for capability in NBA_PLAYER_MINUTES_PROVIDER_CAPABILITIES
    }


def validate_schema_version(schema_version: object) -> str:
    value = _require_text(schema_version, "feature_schema_version")
    if value != NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION:
        raise NBAPlayerMinutesFeatureSchemaError(f"unsupported feature_schema_version: {value!r}")
    return value


def toronto_operating_date(commence_time_utc: datetime) -> date:
    """Return the America/Toronto operating date from a UTC commence time."""

    utc_value = _require_utc_datetime(commence_time_utc, "commence_time_utc")
    return _convert_utc_to_toronto(utc_value).date()


def map_player_baseline_fixture(
    payload: Mapping[str, object],
    *,
    feature_cutoff_timestamp_utc: datetime | str,
) -> NBAPlayerMinutesBaselineEvidence:
    """Map one existing-style baseline row into offline minutes evidence."""

    _reject_leakage_fields(payload)
    cutoff = _parse_utc_timestamp(feature_cutoff_timestamp_utc, "feature_cutoff_timestamp_utc")
    source_timestamp = _parse_utc_timestamp(
        _required_mapping_value(payload, "source_timestamp_utc"),
        "source_timestamp_utc",
    )
    _require_not_after_cutoff(source_timestamp, cutoff, "baseline.source_timestamp_utc")
    commence_time = _parse_utc_timestamp(
        _required_mapping_value(payload, "commence_time_utc"),
        "commence_time_utc",
    )
    season_minutes = _first_present(payload, "season_minutes", "min_avg")
    recent_minutes = _first_present(payload, "recent_minutes", "min_recent")
    return NBAPlayerMinutesBaselineEvidence(
        provider_name=PLAYER_BASELINE_CAPABILITY.provider_name,
        provider_event_id=_required_mapping_value(payload, "provider_event_id"),
        canonical_event_id=payload.get("canonical_event_id"),
        player_id=payload.get("player_id"),
        canonical_player_name=_required_mapping_value(payload, "canonical_player_name"),
        team=_required_mapping_value(payload, "team"),
        opponent=_required_mapping_value(payload, "opponent"),
        operating_date=toronto_operating_date(commence_time),
        commence_time_utc=commence_time,
        event_identity_status=payload.get("event_identity_status", "resolved"),
        player_identity_status=payload.get("player_identity_status", "resolved"),
        season_minutes=season_minutes,
        season_minutes_sample_size=payload.get("season_minutes_sample_size"),
        recent_minutes=recent_minutes,
        recent_minutes_sample_size=payload.get("recent_minutes_sample_size"),
        recent_minutes_stddev=payload.get("recent_minutes_stddev"),
        last_game_minutes=payload.get("last_game_minutes"),
        source_timestamp_utc=source_timestamp,
        source_reference=payload.get("source_reference", "offline:baseline"),
        source_hash=_canonical_payload_sha256(payload),
        raw_source_row=payload,
    )


def map_pregame_lineup_status_fixture(
    payload: Mapping[str, object],
    *,
    feature_cutoff_timestamp_utc: datetime | str,
) -> NBAPlayerMinutesLineupEvidence:
    """Map one offline pregame lineup/status row."""

    _reject_leakage_fields(payload)
    cutoff = _parse_utc_timestamp(feature_cutoff_timestamp_utc, "feature_cutoff_timestamp_utc")
    source_timestamp = _parse_utc_timestamp(
        _required_mapping_value(payload, "source_timestamp_utc"),
        "source_timestamp_utc",
    )
    _require_not_after_cutoff(source_timestamp, cutoff, "lineup.source_timestamp_utc")
    return NBAPlayerMinutesLineupEvidence(
        provider_name=LINEUP_STATUS_CAPABILITY.provider_name,
        starter_status=payload.get("starter_status", "unknown"),
        lineup_status=payload.get("lineup_status", "unconfirmed"),
        source_timestamp_utc=source_timestamp,
        source_reference=payload.get("source_reference", "offline:lineup"),
        source_hash=_canonical_payload_sha256(payload),
        raw_source_row=payload,
    )


def map_pregame_injury_availability_fixture(
    payload: Mapping[str, object],
    *,
    feature_cutoff_timestamp_utc: datetime | str,
) -> NBAPlayerMinutesAvailabilityEvidence:
    """Map one offline pregame injury/availability row."""

    _reject_leakage_fields(payload)
    cutoff = _parse_utc_timestamp(feature_cutoff_timestamp_utc, "feature_cutoff_timestamp_utc")
    source_timestamp = _parse_utc_timestamp(
        _required_mapping_value(payload, "source_timestamp_utc"),
        "source_timestamp_utc",
    )
    _require_not_after_cutoff(source_timestamp, cutoff, "availability.source_timestamp_utc")
    return NBAPlayerMinutesAvailabilityEvidence(
        provider_name=INJURY_AVAILABILITY_CAPABILITY.provider_name,
        injury_status=payload.get("injury_status", "unknown"),
        availability_status=payload.get("availability_status", "unknown"),
        source_timestamp_utc=source_timestamp,
        source_reference=payload.get("source_reference", "offline:availability"),
        source_hash=_canonical_payload_sha256(payload),
        raw_source_row=payload,
    )


def map_schedule_rest_fixture(
    payload: Mapping[str, object],
    *,
    feature_cutoff_timestamp_utc: datetime | str,
) -> NBAPlayerMinutesScheduleEvidence:
    """Map one offline pregame schedule/rest row."""

    _reject_leakage_fields(payload)
    cutoff = _parse_utc_timestamp(feature_cutoff_timestamp_utc, "feature_cutoff_timestamp_utc")
    source_timestamp = _parse_utc_timestamp(
        _required_mapping_value(payload, "source_timestamp_utc"),
        "source_timestamp_utc",
    )
    _require_not_after_cutoff(source_timestamp, cutoff, "schedule.source_timestamp_utc")
    return NBAPlayerMinutesScheduleEvidence(
        provider_name=SCHEDULE_REST_CAPABILITY.provider_name,
        days_rest=payload.get("days_rest"),
        games_last_7_days=payload.get("games_last_7_days"),
        games_last_14_days=payload.get("games_last_14_days"),
        source_timestamp_utc=source_timestamp,
        source_reference=payload.get("source_reference", "offline:schedule"),
        source_hash=_canonical_payload_sha256(payload),
        raw_source_row=payload,
    )


def map_role_context_fixture(
    payload: Mapping[str, object],
    *,
    feature_cutoff_timestamp_utc: datetime | str,
) -> NBAPlayerMinutesRoleContextEvidence:
    """Map one offline reviewed role-change and teammate-absence row."""

    _reject_leakage_fields(payload)
    cutoff = _parse_utc_timestamp(feature_cutoff_timestamp_utc, "feature_cutoff_timestamp_utc")
    source_timestamp = _parse_utc_timestamp(
        _required_mapping_value(payload, "source_timestamp_utc"),
        "source_timestamp_utc",
    )
    _require_not_after_cutoff(source_timestamp, cutoff, "role_context.source_timestamp_utc")
    teammate_context = payload.get("teammate_absence_context", {})
    if not isinstance(teammate_context, Mapping):
        raise NBAPlayerMinutesFeatureSchemaError("teammate_absence_context must be an object")
    minutes_restriction = payload.get("minutes_restriction")
    if minutes_restriction is not None and not isinstance(minutes_restriction, Mapping):
        raise NBAPlayerMinutesFeatureSchemaError("minutes_restriction must be an object")
    return NBAPlayerMinutesRoleContextEvidence(
        provider_name=ROLE_CONTEXT_CAPABILITY.provider_name,
        role_status=payload.get("role_status", "stable"),
        teammate_absence_context=teammate_context,
        minutes_restriction=minutes_restriction,
        source_timestamp_utc=source_timestamp,
        source_reference=payload.get("source_reference", "offline:role_context"),
        source_hash=_canonical_payload_sha256(payload),
        raw_source_row=payload,
    )


def map_minutes_feature_case_fixture(payload: Mapping[str, object]) -> NBAPlayerMinutesFeatureRow:
    """Build one complete projected-minutes feature row from a composite fixture."""

    _reject_composite_leakage(payload)
    cutoff = _parse_utc_timestamp(
        _required_mapping_value(payload, "feature_cutoff_timestamp_utc"),
        "feature_cutoff_timestamp_utc",
    )
    baseline = map_player_baseline_fixture(
        _required_mapping_object(payload, "baseline"),
        feature_cutoff_timestamp_utc=cutoff,
    )
    lineup = map_pregame_lineup_status_fixture(
        _required_mapping_object(payload, "lineup"),
        feature_cutoff_timestamp_utc=cutoff,
    )
    availability = map_pregame_injury_availability_fixture(
        _required_mapping_object(payload, "injury_availability"),
        feature_cutoff_timestamp_utc=cutoff,
    )
    schedule = map_schedule_rest_fixture(
        _required_mapping_object(payload, "schedule"),
        feature_cutoff_timestamp_utc=cutoff,
    )
    role_context = map_role_context_fixture(
        _required_mapping_object(payload, "role_context"),
        feature_cutoff_timestamp_utc=cutoff,
    )
    return build_projected_minutes_feature(
        baseline=baseline,
        lineup=lineup,
        availability=availability,
        schedule=schedule,
        role_context=role_context,
        feature_timestamp_utc=_required_mapping_value(payload, "feature_timestamp_utc"),
        feature_cutoff_timestamp_utc=cutoff,
        source_manifest_id=_required_mapping_value(payload, "source_manifest_id"),
        repository_commit_sha=_required_mapping_value(payload, "repository_commit_sha"),
        research_label=payload.get("research_label", NBA_PLAYER_MINUTES_RESEARCH_LABEL),
    )


def map_minutes_feature_cases_fixture(
    payloads: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
) -> tuple[NBAPlayerMinutesFeatureRow, ...]:
    """Map many offline fixture cases without writes or provider calls."""

    return tuple(map_minutes_feature_case_fixture(payload) for payload in payloads)


def build_projected_minutes_feature(
    *,
    baseline: NBAPlayerMinutesBaselineEvidence,
    lineup: NBAPlayerMinutesLineupEvidence,
    availability: NBAPlayerMinutesAvailabilityEvidence,
    schedule: NBAPlayerMinutesScheduleEvidence,
    role_context: NBAPlayerMinutesRoleContextEvidence,
    feature_timestamp_utc: datetime | str,
    feature_cutoff_timestamp_utc: datetime | str,
    source_manifest_id: str,
    repository_commit_sha: str,
    research_label: str = NBA_PLAYER_MINUTES_RESEARCH_LABEL,
    feature_schema_version: str = NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION,
) -> NBAPlayerMinutesFeatureRow:
    """Calculate a deterministic, explainable pregame projected-minutes row."""

    if not isinstance(baseline, NBAPlayerMinutesBaselineEvidence):
        raise TypeError("baseline must be NBAPlayerMinutesBaselineEvidence")
    if not isinstance(lineup, NBAPlayerMinutesLineupEvidence):
        raise TypeError("lineup must be NBAPlayerMinutesLineupEvidence")
    if not isinstance(availability, NBAPlayerMinutesAvailabilityEvidence):
        raise TypeError("availability must be NBAPlayerMinutesAvailabilityEvidence")
    if not isinstance(schedule, NBAPlayerMinutesScheduleEvidence):
        raise TypeError("schedule must be NBAPlayerMinutesScheduleEvidence")
    if not isinstance(role_context, NBAPlayerMinutesRoleContextEvidence):
        raise TypeError("role_context must be NBAPlayerMinutesRoleContextEvidence")

    feature_timestamp = _parse_utc_timestamp(feature_timestamp_utc, "feature_timestamp_utc")
    cutoff = _parse_utc_timestamp(feature_cutoff_timestamp_utc, "feature_cutoff_timestamp_utc")
    if not (feature_timestamp <= cutoff < baseline.commence_time_utc):
        raise NBAPlayerMinutesFeatureSchemaError(
            "feature_timestamp_utc <= feature_cutoff_timestamp_utc < commence_time_utc is required"
        )
    for source_name, source_timestamp in {
        "baseline": baseline.source_timestamp_utc,
        "lineup": lineup.source_timestamp_utc,
        "availability": availability.source_timestamp_utc,
        "schedule": schedule.source_timestamp_utc,
        "role_context": role_context.source_timestamp_utc,
    }.items():
        _require_not_after_cutoff(source_timestamp, cutoff, f"{source_name}.source_timestamp_utc")

    status, exclusion_reason = _pre_projection_status(baseline, lineup, availability, role_context)
    basis = _calculate_unadjusted_basis(baseline)
    projected_minutes: float | None = None
    projected_minutes_low: float | None = None
    projected_minutes_high: float | None = None
    adjustments: tuple[NBAPlayerMinutesAdjustment, ...] = ()

    if status in {"projected", "lineup_unconfirmed"} and not basis["can_project"]:
        status = "insufficient_data"
        exclusion_reason = str(basis["insufficient_reason"])

    if status in {"projected", "lineup_unconfirmed"}:
        basis_minutes = _require_finite_number(basis["basis_minutes"], "basis_minutes")
        adjustment_values = _collect_adjustments(
            basis_minutes=basis_minutes,
            lineup=lineup,
            availability=availability,
            schedule=schedule,
            role_context=role_context,
        )
        adjustments = tuple(adjustment_values)
        raw_adjustment_total = sum(adjustment.numeric_value for adjustment in adjustments)
        clamped_adjustment_total = _clamp(
            raw_adjustment_total,
            NBA_PLAYER_MINUTES_MAX_NEGATIVE_ADJUSTMENT,
            NBA_PLAYER_MINUTES_MAX_POSITIVE_ADJUSTMENT,
        )
        projected_minutes = _round_minutes(
            _clamp(
                basis_minutes + clamped_adjustment_total,
                NBA_PLAYER_MINUTES_HARD_MINUTES_MIN,
                NBA_PLAYER_MINUTES_HARD_MINUTES_MAX,
            )
        )
        restriction_cap = _minutes_restriction_cap(role_context)
        if restriction_cap is not None:
            projected_minutes = min(projected_minutes, _round_minutes(restriction_cap))
        width = _uncertainty_width(
            basis=basis,
            lineup=lineup,
            availability=availability,
            role_context=role_context,
        )
        projected_minutes_low = _round_minutes(
            _clamp(
                projected_minutes - width,
                NBA_PLAYER_MINUTES_HARD_MINUTES_MIN,
                NBA_PLAYER_MINUTES_HARD_MINUTES_MAX,
            )
        )
        high_limit = NBA_PLAYER_MINUTES_HARD_MINUTES_MAX
        if restriction_cap is not None:
            high_limit = min(high_limit, restriction_cap)
        projected_minutes_high = _round_minutes(
            _clamp(projected_minutes + width, projected_minutes, high_limit)
        )
        basis = {
            **basis,
            "total_adjustment_raw": round(raw_adjustment_total, 4),
            "total_adjustment_clamped": round(clamped_adjustment_total, 4),
            "global_adjustment_clamp": {
                "minimum": NBA_PLAYER_MINUTES_MAX_NEGATIVE_ADJUSTMENT,
                "maximum": NBA_PLAYER_MINUTES_MAX_POSITIVE_ADJUSTMENT,
            },
            "hard_minutes_bounds": {
                "minimum": NBA_PLAYER_MINUTES_HARD_MINUTES_MIN,
                "maximum": NBA_PLAYER_MINUTES_HARD_MINUTES_MAX,
            },
            "research_eligibility_bounds": {
                "minimum": NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MIN,
                "maximum": NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MAX,
            },
            "uncertainty_width": round(width, 4),
            "minutes_restriction_cap": restriction_cap,
        }
    else:
        basis = {
            **basis,
            "total_adjustment_raw": 0.0,
            "total_adjustment_clamped": 0.0,
            "global_adjustment_clamp": {
                "minimum": NBA_PLAYER_MINUTES_MAX_NEGATIVE_ADJUSTMENT,
                "maximum": NBA_PLAYER_MINUTES_MAX_POSITIVE_ADJUSTMENT,
            },
            "hard_minutes_bounds": {
                "minimum": NBA_PLAYER_MINUTES_HARD_MINUTES_MIN,
                "maximum": NBA_PLAYER_MINUTES_HARD_MINUTES_MAX,
            },
            "research_eligibility_bounds": {
                "minimum": NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MIN,
                "maximum": NBA_PLAYER_MINUTES_RESEARCH_ELIGIBILITY_MAX,
            },
            "uncertainty_width": None,
            "minutes_restriction_cap": None,
        }

    confidence = _confidence_level(
        status=status,
        basis=basis,
        lineup=lineup,
        availability=availability,
        role_context=role_context,
    )
    method = str(basis["calculation_method"]) if status in {"projected", "lineup_unconfirmed"} else "no_projection"

    return NBAPlayerMinutesFeatureRow(
        canonical_event_id=baseline.canonical_event_id,
        provider_event_id=baseline.provider_event_id,
        player_id=baseline.player_id,
        canonical_player_name=baseline.canonical_player_name,
        team=baseline.team,
        opponent=baseline.opponent,
        operating_date=baseline.operating_date,
        commence_time_utc=baseline.commence_time_utc,
        event_identity_status=baseline.event_identity_status,
        player_identity_status=baseline.player_identity_status,
        season_minutes=baseline.season_minutes,
        season_minutes_sample_size=baseline.season_minutes_sample_size,
        recent_minutes=baseline.recent_minutes,
        recent_minutes_sample_size=baseline.recent_minutes_sample_size,
        recent_minutes_stddev=baseline.recent_minutes_stddev,
        last_game_minutes=baseline.last_game_minutes,
        starter_status=lineup.starter_status,
        lineup_status=lineup.lineup_status,
        injury_status=availability.injury_status,
        availability_status=availability.availability_status,
        role_status=role_context.role_status,
        days_rest=schedule.days_rest,
        games_last_7_days=schedule.games_last_7_days,
        games_last_14_days=schedule.games_last_14_days,
        teammate_absence_context=role_context.teammate_absence_context,
        projected_minutes=projected_minutes,
        projected_minutes_low=projected_minutes_low,
        projected_minutes_high=projected_minutes_high,
        minutes_confidence=confidence,
        minutes_projection_status=status,
        minutes_projection_method=method,
        minutes_exclusion_reason=exclusion_reason,
        applied_adjustments=adjustments,
        unadjusted_minutes_basis=basis,
        feature_timestamp_utc=feature_timestamp,
        feature_cutoff_timestamp_utc=cutoff,
        source_manifest_id=source_manifest_id,
        source_hashes={
            "baseline": baseline.source_hash,
            "lineup": lineup.source_hash,
            "injury_availability": availability.source_hash,
            "schedule": schedule.source_hash,
            "role_context": role_context.source_hash,
        },
        feature_schema_version=feature_schema_version,
        repository_commit_sha=repository_commit_sha,
        research_label=research_label,
    )


def validate_feature_rows(
    rows: tuple[NBAPlayerMinutesFeatureRow, ...] | list[NBAPlayerMinutesFeatureRow],
) -> tuple[NBAPlayerMinutesFeatureRow, ...]:
    """Validate row collection identity without writes or ledgers."""

    normalized = tuple(rows)
    seen: set[tuple[str | None, str | None, str]] = set()
    for index, row in enumerate(normalized):
        if not isinstance(row, NBAPlayerMinutesFeatureRow):
            raise TypeError("rows must contain NBAPlayerMinutesFeatureRow values")
        identity = (row.canonical_event_id, row.player_id, row.source_manifest_id)
        if identity in seen:
            raise NBAPlayerMinutesFeatureSchemaError(
                f"duplicate projected-minutes feature identity at rows[{index}]"
            )
        seen.add(identity)
    return normalized


def _calculate_unadjusted_basis(baseline: NBAPlayerMinutesBaselineEvidence) -> dict[str, object]:
    season_available = (
        baseline.season_minutes is not None
        and baseline.season_minutes_sample_size is not None
        and baseline.season_minutes_sample_size > 0
    )
    recent_available = (
        baseline.recent_minutes is not None
        and baseline.recent_minutes_sample_size is not None
        and baseline.recent_minutes_sample_size > 0
    )
    recent_stddev = baseline.recent_minutes_stddev
    basis: float | None = None
    season_weight = 0.0
    recent_weight = 0.0
    prior_weight = 0.0
    calculation_method = "insufficient_data"
    recent_weight_rule = "none"
    insufficient_reason = "none"

    if season_available and recent_available:
        recent_weight = _recent_minutes_weight(
            baseline.recent_minutes_sample_size or 0,
            recent_stddev,
        )
        season_weight = 1.0 - recent_weight
        basis = (baseline.season_minutes or 0.0) * season_weight + (baseline.recent_minutes or 0.0) * recent_weight
        calculation_method = "weighted_season_recent"
        recent_weight_rule = (
            "recent weight increases with sample size and decreases when recent volatility is high"
        )
    elif season_available:
        season_weight = 0.95
        prior_weight = 0.05
        basis = (baseline.season_minutes or 0.0) * season_weight + 24.0 * prior_weight
        calculation_method = "season_with_research_prior"
        recent_weight_rule = "recent unavailable; five percent neutral role prior prevents direct aliasing"
    elif recent_available:
        if (baseline.recent_minutes_sample_size or 0) < NBA_PLAYER_MINUTES_MIN_RECENT_SAMPLE:
            insufficient_reason = "recent minutes sample is below minimum and season minutes are unavailable"
        else:
            recent_weight = 0.90
            prior_weight = 0.10
            basis = (baseline.recent_minutes or 0.0) * recent_weight + 24.0 * prior_weight
            calculation_method = "recent_with_research_prior"
            recent_weight_rule = "season unavailable; ten percent neutral role prior prevents direct aliasing"
    else:
        insufficient_reason = "season and recent minutes are unavailable"

    can_project = basis is not None
    return {
        "can_project": can_project,
        "insufficient_reason": insufficient_reason,
        "calculation_method": calculation_method,
        "basis_minutes": round(float(basis), 4) if basis is not None else None,
        "season_minutes": baseline.season_minutes,
        "season_minutes_sample_size": baseline.season_minutes_sample_size,
        "recent_minutes": baseline.recent_minutes,
        "recent_minutes_sample_size": baseline.recent_minutes_sample_size,
        "recent_minutes_stddev": recent_stddev,
        "last_game_minutes": baseline.last_game_minutes,
        "last_game_minutes_context": _last_game_context(baseline.last_game_minutes, basis),
        "season_weight": round(season_weight, 4),
        "recent_weight": round(recent_weight, 4),
        "neutral_role_prior_minutes": 24.0 if prior_weight else None,
        "neutral_role_prior_weight": round(prior_weight, 4),
        "recent_weight_rule": recent_weight_rule,
        "minimum_recent_sample": NBA_PLAYER_MINUTES_MIN_RECENT_SAMPLE,
        "volatility_penalty": _volatility_penalty(recent_stddev),
    }


def _recent_minutes_weight(sample_size: int, stddev: float | None) -> float:
    if sample_size >= 10:
        weight = 0.50
    elif sample_size >= 8:
        weight = 0.45
    elif sample_size >= 5:
        weight = 0.40
    elif sample_size >= NBA_PLAYER_MINUTES_MIN_RECENT_SAMPLE:
        weight = 0.30
    else:
        weight = 0.20
    if stddev is not None:
        if stddev >= 8.0:
            weight -= 0.10
        elif stddev >= 6.0:
            weight -= 0.05
    return round(_clamp(weight, 0.20, 0.50), 4)


def _pre_projection_status(
    baseline: NBAPlayerMinutesBaselineEvidence,
    lineup: NBAPlayerMinutesLineupEvidence,
    availability: NBAPlayerMinutesAvailabilityEvidence,
    role_context: NBAPlayerMinutesRoleContextEvidence,
) -> tuple[str, str]:
    if baseline.event_identity_status == "quarantined" or baseline.player_identity_status == "quarantined":
        return "quarantined", "identity evidence is quarantined"
    if baseline.event_identity_status == "conflicting" or baseline.player_identity_status == "conflicting":
        return "conflicting", "identity evidence is conflicting"
    if baseline.event_identity_status != "resolved":
        return "event_unresolved", "event identity is unresolved"
    if baseline.player_identity_status != "resolved":
        return "identity_unresolved", "player identity is unresolved"
    if (
        lineup.lineup_status == "conflicting"
        or availability.availability_status == "conflicting"
        or availability.injury_status == "conflicting"
        or role_context.role_status == "conflicting"
        or (availability.availability_status in {"inactive", "did_not_dress"} and lineup.starter_status == "confirmed_starter")
    ):
        return "conflicting", "pregame status evidence is conflicting"
    if availability.availability_status == "did_not_dress" or availability.injury_status == "did_not_dress":
        return "did_not_dress", "player did not dress"
    if availability.availability_status == "inactive" or availability.injury_status == "inactive":
        return "inactive", "player is inactive"
    if lineup.lineup_status == "unconfirmed":
        return "lineup_unconfirmed", "lineup is unconfirmed; projection is diagnostic"
    return "projected", "none"


def _collect_adjustments(
    *,
    basis_minutes: float,
    lineup: NBAPlayerMinutesLineupEvidence,
    availability: NBAPlayerMinutesAvailabilityEvidence,
    schedule: NBAPlayerMinutesScheduleEvidence,
    role_context: NBAPlayerMinutesRoleContextEvidence,
) -> list[NBAPlayerMinutesAdjustment]:
    adjustments: list[NBAPlayerMinutesAdjustment] = []
    if lineup.starter_status == "confirmed_starter":
        adjustments.append(
            _adjustment(
                "confirmed_starter",
                "starter_status=confirmed_starter",
                1.25,
                3.0,
                lineup.source_timestamp_utc,
                lineup.source_reference,
                "confirmed starter evidence supports a modest minutes increase",
            )
        )
    if lineup.starter_status == "confirmed_bench":
        adjustments.append(
            _adjustment(
                "confirmed_bench_role",
                "starter_status=confirmed_bench",
                -1.50,
                3.0,
                lineup.source_timestamp_utc,
                lineup.source_reference,
                "confirmed bench role supports a modest minutes decrease",
            )
        )
    if role_context.role_status == "role_increase_verified":
        adjustments.append(
            _adjustment(
                "verified_role_increase",
                "role_status=role_increase_verified",
                3.00,
                4.0,
                role_context.source_timestamp_utc,
                role_context.source_reference,
                "reviewed pregame role evidence documents a larger role",
            )
        )
    if role_context.role_status == "role_decrease_verified":
        adjustments.append(
            _adjustment(
                "verified_role_decrease",
                "role_status=role_decrease_verified",
                -3.00,
                4.0,
                role_context.source_timestamp_utc,
                role_context.source_reference,
                "reviewed pregame role evidence documents a smaller role",
            )
        )
    if role_context.role_status == "returning_from_injury" or availability.injury_status == "returning_from_injury":
        adjustments.append(
            _adjustment(
                "return_from_injury",
                f"role_status={role_context.role_status}; injury_status={availability.injury_status}",
                -2.00,
                3.0,
                role_context.source_timestamp_utc,
                role_context.source_reference,
                "return-from-injury evidence reduces baseline minutes",
            )
        )
    if availability.availability_status == "questionable" or availability.injury_status == "questionable":
        adjustments.append(
            _adjustment(
                "questionable_status",
                f"availability_status={availability.availability_status}; injury_status={availability.injury_status}",
                -2.50,
                4.0,
                availability.source_timestamp_utc,
                availability.source_reference,
                "questionable pregame status reduces minutes and confidence",
            )
        )
    teammate_context = role_context.teammate_absence_context
    if bool(teammate_context.get("verified")):
        impact = _optional_number(teammate_context.get("role_impact_minutes"), "teammate_absence_context.role_impact_minutes")
        if impact is None:
            impact = 2.0
        impact = _clamp(impact, -3.0, 3.0)
        adjustments.append(
            _adjustment(
                "confirmed_teammate_absence",
                json.dumps(_json_clone(teammate_context), sort_keys=True),
                impact,
                3.0,
                role_context.source_timestamp_utc,
                role_context.source_reference,
                "verified teammate absence has documented role impact",
            )
        )
    if schedule.days_rest == 0:
        adjustments.append(
            _adjustment(
                "back_to_back_game",
                "days_rest=0",
                -1.25,
                2.0,
                schedule.source_timestamp_utc,
                schedule.source_reference,
                "back-to-back schedule modestly reduces projected minutes",
            )
        )
    if (schedule.games_last_7_days is not None and schedule.games_last_7_days >= 5) or (
        schedule.games_last_14_days is not None and schedule.games_last_14_days >= 9
    ):
        adjustments.append(
            _adjustment(
                "condensed_schedule",
                f"games_last_7_days={schedule.games_last_7_days}; games_last_14_days={schedule.games_last_14_days}",
                -1.00,
                2.0,
                schedule.source_timestamp_utc,
                schedule.source_reference,
                "condensed schedule modestly reduces projected minutes",
            )
        )
    restriction_cap = _minutes_restriction_cap(role_context)
    if restriction_cap is not None:
        current_total = sum(adjustment.numeric_value for adjustment in adjustments)
        restriction_value = min(0.0, restriction_cap - (basis_minutes + current_total))
        adjustments.append(
            _adjustment(
                "confirmed_minutes_restriction",
                json.dumps(_json_clone(role_context.minutes_restriction), sort_keys=True),
                _clamp(restriction_value, -24.0, 0.0),
                24.0,
                role_context.source_timestamp_utc,
                role_context.source_reference,
                "confirmed minutes restriction caps the diagnostic projection",
            )
        )
    return adjustments


def _adjustment(
    adjustment_name: str,
    input_evidence: str,
    numeric_value: float,
    maximum_allowed_magnitude: float,
    source_timestamp_utc: datetime,
    source_reference: str,
    reason: str,
) -> NBAPlayerMinutesAdjustment:
    clamped_value = _clamp(numeric_value, -maximum_allowed_magnitude, maximum_allowed_magnitude)
    return NBAPlayerMinutesAdjustment(
        adjustment_name=adjustment_name,
        input_evidence=input_evidence,
        numeric_value=clamped_value,
        maximum_allowed_magnitude=maximum_allowed_magnitude,
        source_timestamp_utc=source_timestamp_utc,
        source_reference=source_reference,
        reason=reason,
    )


def _confidence_level(
    *,
    status: str,
    basis: Mapping[str, object],
    lineup: NBAPlayerMinutesLineupEvidence,
    availability: NBAPlayerMinutesAvailabilityEvidence,
    role_context: NBAPlayerMinutesRoleContextEvidence,
) -> str:
    if status not in {"projected", "lineup_unconfirmed"}:
        return "unavailable"
    score = 0
    season_sample = basis.get("season_minutes_sample_size")
    recent_sample = basis.get("recent_minutes_sample_size")
    stddev = basis.get("recent_minutes_stddev")
    if isinstance(season_sample, int):
        score += 2 if season_sample >= 20 else 1 if season_sample >= 5 else 0
    if isinstance(recent_sample, int):
        score += 2 if recent_sample >= 5 else 1 if recent_sample >= NBA_PLAYER_MINUTES_MIN_RECENT_SAMPLE else 0
    if isinstance(stddev, (int, float)):
        if stddev <= 4.0:
            score += 1
        elif stddev >= 8.0:
            score -= 2
        elif stddev >= 6.0:
            score -= 1
    if lineup.lineup_status == "confirmed":
        score += 1
    if lineup.starter_status in {"confirmed_starter", "confirmed_bench"}:
        score += 1
    if availability.availability_status == "questionable" or availability.injury_status == "questionable":
        score -= 2
    if lineup.lineup_status == "unconfirmed":
        score -= 2
    if role_context.role_status in {"role_increase_verified", "role_decrease_verified", "returning_from_injury"}:
        score -= 1
    if score >= 6 and status == "projected":
        level = "high"
    elif score >= 4:
        level = "medium" if status == "projected" else "low"
    else:
        level = "low"
    if availability.availability_status == "questionable" or availability.injury_status == "questionable":
        return "low"
    if status == "lineup_unconfirmed":
        return "low"
    if role_context.role_status in {"role_increase_verified", "role_decrease_verified", "returning_from_injury"} and level == "high":
        return "medium"
    return level


def _uncertainty_width(
    *,
    basis: Mapping[str, object],
    lineup: NBAPlayerMinutesLineupEvidence,
    availability: NBAPlayerMinutesAvailabilityEvidence,
    role_context: NBAPlayerMinutesRoleContextEvidence,
) -> float:
    width = 2.5
    recent_sample = basis.get("recent_minutes_sample_size")
    season_sample = basis.get("season_minutes_sample_size")
    stddev = basis.get("recent_minutes_stddev")
    if isinstance(stddev, (int, float)):
        width += min(float(stddev) * 0.30, 4.0)
        if stddev >= 8.0:
            width += 1.5
    if not isinstance(recent_sample, int) or recent_sample < 5:
        width += 1.0
    if not isinstance(season_sample, int) or season_sample < 10:
        width += 1.0
    if lineup.lineup_status == "unconfirmed":
        width += 2.0
    if availability.availability_status == "questionable" or availability.injury_status == "questionable":
        width += 2.0
    if role_context.role_status in {"role_increase_verified", "role_decrease_verified", "returning_from_injury"}:
        width += 1.5
    if _minutes_restriction_cap(role_context) is not None:
        width += 1.0
    return round(width, 4)


def _minutes_restriction_cap(role_context: NBAPlayerMinutesRoleContextEvidence) -> float | None:
    restriction = role_context.minutes_restriction
    if not restriction:
        return None
    if not bool(restriction.get("confirmed")):
        return None
    cap = _optional_nonnegative_number(restriction.get("minutes_cap"), "minutes_restriction.minutes_cap")
    if cap is None:
        raise NBAPlayerMinutesFeatureSchemaError("confirmed minutes_restriction requires minutes_cap")
    return _clamp(cap, NBA_PLAYER_MINUTES_HARD_MINUTES_MIN, NBA_PLAYER_MINUTES_HARD_MINUTES_MAX)


def _last_game_context(last_game_minutes: float | None, basis: float | None) -> dict[str, object]:
    if last_game_minutes is None or basis is None:
        return {"available": last_game_minutes is not None, "deviation_from_basis": None}
    return {
        "available": True,
        "deviation_from_basis": round(last_game_minutes - basis, 4),
        "used_as_weighted_input": False,
    }


def _volatility_penalty(stddev: float | None) -> str:
    if stddev is None:
        return "missing"
    if stddev >= 8.0:
        return "high"
    if stddev >= 6.0:
        return "medium"
    return "none"


def _reject_composite_leakage(payload: Mapping[str, object]) -> None:
    for field_name in ("target_game_actual_minutes", "target_game_final_stats", "final_stats", "box_score"):
        if field_name in payload:
            if field_name == "target_game_actual_minutes":
                raise NBAPlayerMinutesFeatureSchemaError("target-game actual minutes are not permitted")
            raise NBAPlayerMinutesFeatureSchemaError("target-event final statistics are not permitted")


def _reject_leakage_fields(payload: Mapping[str, object]) -> None:
    if "projected_minutes" in payload:
        raise NBAPlayerMinutesFeatureSchemaError(
            "projected_minutes is calculated by the feature builder and cannot be supplied by a source row"
        )
    for field_name in ("actual_minutes", "target_game_actual_minutes"):
        if field_name in payload:
            raise NBAPlayerMinutesFeatureSchemaError("target-game actual minutes are not permitted")
    for field_name in ("final_points", "target_game_final_points", "final_stats", "box_score"):
        if field_name in payload:
            raise NBAPlayerMinutesFeatureSchemaError("target-event final statistics are not permitted")


def _validate_projected_minutes_contract(
    *,
    projected_minutes: float | None,
    projected_minutes_low: float | None,
    projected_minutes_high: float | None,
    status: str,
) -> None:
    if projected_minutes is None or projected_minutes_low is None or projected_minutes_high is None:
        if status in {"projected", "lineup_unconfirmed"}:
            raise NBAPlayerMinutesFeatureSchemaError("projected statuses require finite minutes and bounds")
        if any(value is not None for value in (projected_minutes, projected_minutes_low, projected_minutes_high)):
            raise NBAPlayerMinutesFeatureSchemaError("excluded rows must not contain partial minutes bounds")
        return
    minutes = _require_finite_number(projected_minutes, "projected_minutes")
    low = _require_finite_number(projected_minutes_low, "projected_minutes_low")
    high = _require_finite_number(projected_minutes_high, "projected_minutes_high")
    if minutes < NBA_PLAYER_MINUTES_HARD_MINUTES_MIN:
        raise NBAPlayerMinutesFeatureSchemaError("projected_minutes must be non-negative")
    for field_name, value in (
        ("projected_minutes", minutes),
        ("projected_minutes_low", low),
        ("projected_minutes_high", high),
    ):
        if value < NBA_PLAYER_MINUTES_HARD_MINUTES_MIN:
            raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be non-negative")
        if value > NBA_PLAYER_MINUTES_HARD_MINUTES_MAX:
            raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} exceeds hard maximum")
    if low > minutes:
        raise NBAPlayerMinutesFeatureSchemaError("projected_minutes_low cannot exceed projected_minutes")
    if minutes > high:
        raise NBAPlayerMinutesFeatureSchemaError("projected_minutes cannot exceed projected_minutes_high")


def _parse_utc_timestamp(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _require_utc_datetime(value, field_name)
    if not isinstance(value, str) or not value.strip():
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be an ISO-8601 UTC timestamp")
    text = value.strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise NBAPlayerMinutesFeatureSchemaError(
            f"{field_name} must be an ISO-8601 UTC timestamp"
        ) from exc
    return _require_utc_datetime(parsed, field_name)


def _require_utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be UTC")
    return value.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _require_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _resolve_toronto_timezone() -> ZoneInfo | object:
    try:
        return ZoneInfo(NBA_PLAYER_MINUTES_OPERATING_TIMEZONE)
    except ZoneInfoNotFoundError:
        return _TORONTO_FALLBACK


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first_day = date(year, month, 1)
    offset_days = (weekday - first_day.weekday()) % 7
    return first_day + timedelta(days=offset_days + (occurrence - 1) * 7)


def _toronto_dst_bounds_utc(year: int) -> tuple[datetime, datetime]:
    dst_start_day = _nth_weekday(year, 3, 6, 2)
    dst_end_day = _nth_weekday(year, 11, 6, 1)
    return (
        datetime.combine(dst_start_day, time(7, 0), tzinfo=_UTC),
        datetime.combine(dst_end_day, time(6, 0), tzinfo=_UTC),
    )


def _convert_utc_to_toronto(value: datetime) -> datetime:
    utc_value = _require_utc_datetime(value, "commence_time_utc")
    timezone_info = _resolve_toronto_timezone()
    if timezone_info is not _TORONTO_FALLBACK:
        return utc_value.astimezone(timezone_info)
    dst_start, dst_end = _toronto_dst_bounds_utc(utc_value.year)
    offset_hours = -4 if dst_start <= utc_value < dst_end else -5
    label = "EDT" if offset_hours == -4 else "EST"
    return utc_value.astimezone(timezone(timedelta(hours=offset_hours), label))


def _require_not_after_cutoff(timestamp: datetime, cutoff: datetime, field_name: str) -> None:
    if timestamp > cutoff:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be at or before feature cutoff")


def _required_mapping_value(payload: Mapping[str, object], field_name: str) -> object:
    if field_name not in payload:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    return payload[field_name]


def _required_mapping_object(payload: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    value = _required_mapping_value(payload, field_name)
    if not isinstance(value, Mapping):
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be an object")
    return value


def _first_present(payload: Mapping[str, object], *field_names: str) -> object:
    for field_name in field_names:
        if field_name in payload:
            value = payload[field_name]
            if value is not None and value != "":
                return value
    return None


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    return text


def _optional_identifier(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _require_identifier(text, field_name)


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    return text


def _normalize_key(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold().strip()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not normalized:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} is required")
    return normalized


def _normalize_status(value: object, field_name: str, allowed: tuple[str, ...]) -> str:
    normalized = _normalize_key(value, field_name)
    if normalized not in allowed:
        raise NBAPlayerMinutesFeatureSchemaError(f"unsupported {field_name}: {normalized!r}")
    return normalized


def _normalize_team(value: object, field_name: str) -> str:
    return _require_text(value, field_name).upper()


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be finite")
    return numeric


def _optional_number(value: object, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    return _require_finite_number(value, field_name)


def _require_nonnegative_number(value: object, field_name: str) -> float:
    numeric = _require_finite_number(value, field_name)
    if numeric < 0:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be non-negative")
    return numeric


def _optional_nonnegative_number(value: object, field_name: str) -> float | None:
    numeric = _optional_number(value, field_name)
    if numeric is None:
        return None
    if numeric < 0:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be non-negative")
    return numeric


def _optional_nonnegative_int(value: object, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be an integer")
    if value < 0:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be non-negative")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerMinutesFeatureSchemaError(f"{field_name} must be a SHA-256 hex digest")
    return text


def _validate_source_hashes(source_hashes: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(source_hashes, Mapping):
        raise NBAPlayerMinutesFeatureSchemaError("source_hashes must be an object")
    normalized: dict[str, str] = {}
    for key, value in source_hashes.items():
        source_key = _normalize_key(key, "source_hashes key")
        normalized[source_key] = _require_sha256(value, f"source_hashes.{source_key}")
    if not normalized:
        raise NBAPlayerMinutesFeatureSchemaError("source_hashes must not be empty")
    return MappingProxyType(normalized)


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_stable_json_bytes(payload)).hexdigest()


def _stable_json_bytes(payload: object) -> bytes:
    return json.dumps(
        _json_clone(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _json_clone(value: object) -> object:
    if isinstance(value, MappingProxyType):
        value = dict(value)
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True))


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NBAPlayerMinutesFeatureSchemaError("raw source row must be an object")
    cloned = _json_clone(value)
    if not isinstance(cloned, dict):
        raise NBAPlayerMinutesFeatureSchemaError("raw source row must be an object")
    return cloned


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_minutes(value: float) -> float:
    return round(float(value), 2)
