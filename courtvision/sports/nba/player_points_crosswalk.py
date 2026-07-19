"""Offline NBA player-points identity crosswalk contracts.

This module is research-only. It links offline sportsbook player-points rows to
canonical NBA event and player identities without provider I/O, file writes,
runner entrypoints, prediction ledgers, settlement ledgers, scoring changes, or
bankroll-facing side effects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re
from types import MappingProxyType
from typing import Any, Final

from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
    NBAPlayerPointsFinalStatEvidence,
    NBAPlayerPointsMarketEvidence,
    NBAPlayerPointsResearchSchemaError,
    normalize_player_name,
    toronto_operating_date,
)


NBA_PLAYER_POINTS_CROSSWALK_SCHEMA_VERSION: Final = "nba-player-points-crosswalk-v1"
NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION: Final = (
    "nba-player-points-crosswalk-mapping-v1"
)
NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE: Final = timedelta(minutes=15)
NBA_PLAYER_POINTS_MAX_EVENT_TIME_TOLERANCE: Final = timedelta(minutes=30)

NBA_IDENTITY_STATUSES: Final = (
    "resolved",
    "unresolved",
    "ambiguous",
    "conflicting",
    "quarantined",
)
NBA_CROSSWALK_EXCLUSION_REASONS: Final = (
    "none",
    "event_unresolved",
    "event_ambiguous",
    "event_conflict",
    "player_unresolved",
    "player_ambiguous",
    "player_conflict",
    "team_mismatch",
    "commence_time_mismatch",
    "missing_required_identity_field",
)

_UTC: Final = timezone.utc
_NONE: Final = object()
_TEAM_ALIAS_KEY_RE: Final = re.compile(r"[^a-z0-9]+")
_APPROVED_REVIEW_STATUS: Final = "approved"


@dataclass(frozen=True, slots=True)
class NBATeamNormalizationResult:
    """Explicit team alias resolution without substring guessing."""

    original_team: str
    canonical_team: str | None
    team_identity_status: str
    team_identity_method: str
    team_conflict_reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_team", _require_text(self.original_team, "team"))
        if self.canonical_team is not None:
            object.__setattr__(
                self,
                "canonical_team",
                _require_canonical_team(self.canonical_team, "canonical_team"),
            )
        _require_status(self.team_identity_status, "team_identity_status")
        object.__setattr__(
            self,
            "team_identity_method",
            _require_text(self.team_identity_method, "team_identity_method"),
        )
        object.__setattr__(
            self,
            "team_conflict_reason",
            _require_text(self.team_conflict_reason, "team_conflict_reason"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "original_team": self.original_team,
            "canonical_team": self.canonical_team,
            "team_identity_status": self.team_identity_status,
            "team_identity_method": self.team_identity_method,
            "team_conflict_reason": self.team_conflict_reason,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsEventIdentity:
    """Event identity result for one offline sportsbook row."""

    provider_event_id: str
    canonical_event_id: str | None
    provider_name: str
    operating_date: date
    commence_time_utc: datetime
    home_team: str
    away_team: str
    canonical_home_team: str | None
    canonical_away_team: str | None
    event_status: str
    event_identity_status: str
    event_identity_method: str
    event_identity_confidence: float
    event_conflict_reason: str
    source_timestamp_utc: datetime
    mapping_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_event_id",
            _require_identifier(self.provider_event_id, "provider_event_id"),
        )
        if self.canonical_event_id is not None:
            object.__setattr__(
                self,
                "canonical_event_id",
                _require_identifier(self.canonical_event_id, "canonical_event_id"),
            )
        object.__setattr__(self, "provider_name", _normalize_provider_name(self.provider_name))
        object.__setattr__(
            self,
            "commence_time_utc",
            _coerce_utc_datetime(self.commence_time_utc, "commence_time_utc"),
        )
        if self.operating_date != toronto_operating_date(self.commence_time_utc):
            raise NBAPlayerPointsResearchSchemaError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        object.__setattr__(self, "home_team", _require_text(self.home_team, "home_team"))
        object.__setattr__(self, "away_team", _require_text(self.away_team, "away_team"))
        if self.canonical_home_team is not None:
            object.__setattr__(
                self,
                "canonical_home_team",
                _require_canonical_team(self.canonical_home_team, "canonical_home_team"),
            )
        if self.canonical_away_team is not None:
            object.__setattr__(
                self,
                "canonical_away_team",
                _require_canonical_team(self.canonical_away_team, "canonical_away_team"),
            )
        _require_status(self.event_status, "event_status")
        _require_status(self.event_identity_status, "event_identity_status")
        if self.event_status != self.event_identity_status:
            raise NBAPlayerPointsResearchSchemaError(
                "event_status and event_identity_status must match"
            )
        object.__setattr__(
            self,
            "event_identity_method",
            _require_text(self.event_identity_method, "event_identity_method"),
        )
        object.__setattr__(
            self,
            "event_identity_confidence",
            _require_probability(
                self.event_identity_confidence,
                "event_identity_confidence",
            ),
        )
        object.__setattr__(
            self,
            "event_conflict_reason",
            _require_text(self.event_conflict_reason, "event_conflict_reason"),
        )
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _coerce_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(
            self,
            "mapping_version",
            _require_text(self.mapping_version, "mapping_version"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "provider_name": self.provider_name,
            "operating_date": self.operating_date.isoformat(),
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "canonical_home_team": self.canonical_home_team,
            "canonical_away_team": self.canonical_away_team,
            "event_status": self.event_status,
            "event_identity_status": self.event_identity_status,
            "event_identity_method": self.event_identity_method,
            "event_identity_confidence": self.event_identity_confidence,
            "event_conflict_reason": self.event_conflict_reason,
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "mapping_version": self.mapping_version,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsPlayerIdentity:
    """Player identity result for one offline sportsbook row."""

    provider_player_name: str
    normalized_player_name: str
    player_id: str | None
    canonical_player_name: str | None
    team: str
    canonical_team: str | None
    player_identity_status: str
    player_identity_method: str
    player_identity_confidence: float
    player_conflict_reason: str
    identity_source: str
    mapping_version: str
    provider_player_id: str | None = None

    def __post_init__(self) -> None:
        provider_name = _require_text(self.provider_player_name, "provider_player_name")
        object.__setattr__(self, "provider_player_name", provider_name)
        object.__setattr__(
            self,
            "normalized_player_name",
            normalize_player_name(self.provider_player_name),
        )
        if self.player_id is not None:
            object.__setattr__(self, "player_id", _require_identifier(self.player_id, "player_id"))
        if self.canonical_player_name is not None:
            object.__setattr__(
                self,
                "canonical_player_name",
                _require_text(self.canonical_player_name, "canonical_player_name"),
            )
        object.__setattr__(self, "team", _require_text(self.team, "team"))
        if self.canonical_team is not None:
            object.__setattr__(
                self,
                "canonical_team",
                _require_canonical_team(self.canonical_team, "canonical_team"),
            )
        _require_status(self.player_identity_status, "player_identity_status")
        object.__setattr__(
            self,
            "player_identity_method",
            _require_text(self.player_identity_method, "player_identity_method"),
        )
        object.__setattr__(
            self,
            "player_identity_confidence",
            _require_probability(
                self.player_identity_confidence,
                "player_identity_confidence",
            ),
        )
        object.__setattr__(
            self,
            "player_conflict_reason",
            _require_text(self.player_conflict_reason, "player_conflict_reason"),
        )
        object.__setattr__(
            self,
            "identity_source",
            _require_text(self.identity_source, "identity_source"),
        )
        object.__setattr__(
            self,
            "mapping_version",
            _require_text(self.mapping_version, "mapping_version"),
        )
        if self.provider_player_id is not None:
            object.__setattr__(
                self,
                "provider_player_id",
                _require_identifier(self.provider_player_id, "provider_player_id"),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_player_name": self.provider_player_name,
            "normalized_player_name": self.normalized_player_name,
            "provider_player_id": self.provider_player_id,
            "player_id": self.player_id,
            "canonical_player_name": self.canonical_player_name,
            "team": self.team,
            "canonical_team": self.canonical_team,
            "player_identity_status": self.player_identity_status,
            "player_identity_method": self.player_identity_method,
            "player_identity_confidence": self.player_identity_confidence,
            "player_conflict_reason": self.player_conflict_reason,
            "identity_source": self.identity_source,
            "mapping_version": self.mapping_version,
        }


@dataclass(frozen=True, slots=True)
class NBAReviewedEventMapping:
    """One reviewed provider-event to canonical-event mapping."""

    provider_name: str
    provider_event_id: str
    canonical_event_id: str
    canonical_home_team: str
    canonical_away_team: str
    mapping_source: str
    reviewed_at: datetime
    review_status: str
    mapping_version: str
    reviewer: str = ""
    review_reference: str = ""
    allow_reversed_teams: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_provider_name(self.provider_name))
        object.__setattr__(
            self,
            "provider_event_id",
            _require_identifier(self.provider_event_id, "provider_event_id"),
        )
        object.__setattr__(
            self,
            "canonical_event_id",
            _require_identifier(self.canonical_event_id, "canonical_event_id"),
        )
        object.__setattr__(
            self,
            "canonical_home_team",
            _require_canonical_team(self.canonical_home_team, "canonical_home_team"),
        )
        object.__setattr__(
            self,
            "canonical_away_team",
            _require_canonical_team(self.canonical_away_team, "canonical_away_team"),
        )
        object.__setattr__(self, "mapping_source", _require_text(self.mapping_source, "mapping_source"))
        object.__setattr__(
            self,
            "reviewed_at",
            _coerce_utc_datetime(self.reviewed_at, "reviewed_at"),
        )
        object.__setattr__(self, "review_status", _require_text(self.review_status, "review_status"))
        object.__setattr__(self, "mapping_version", _require_text(self.mapping_version, "mapping_version"))
        object.__setattr__(self, "reviewer", _clean_text(self.reviewer))
        object.__setattr__(self, "review_reference", _clean_text(self.review_reference))
        if not isinstance(self.allow_reversed_teams, bool):
            raise NBAPlayerPointsResearchSchemaError("allow_reversed_teams must be boolean")

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider_name, self.provider_event_id)

    @property
    def canonical_key(self) -> tuple[str, str]:
        return (self.provider_name, self.canonical_event_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "canonical_home_team": self.canonical_home_team,
            "canonical_away_team": self.canonical_away_team,
            "mapping_source": self.mapping_source,
            "reviewed_at": _format_utc(self.reviewed_at),
            "review_status": self.review_status,
            "mapping_version": self.mapping_version,
            "reviewer": self.reviewer,
            "review_reference": self.review_reference,
            "allow_reversed_teams": self.allow_reversed_teams,
        }


@dataclass(frozen=True, slots=True)
class NBAReviewedPlayerMapping:
    """One reviewed provider-player to canonical-player mapping."""

    provider_name: str
    player_id: str
    canonical_player_name: str
    canonical_team: str
    mapping_source: str
    reviewed_at: datetime
    review_status: str
    mapping_version: str
    provider_player_id: str | None = None
    provider_player_name: str | None = None
    mapping_type: str = "identity"
    reviewer: str = ""
    review_reference: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_provider_name(self.provider_name))
        object.__setattr__(self, "player_id", _require_identifier(self.player_id, "player_id"))
        object.__setattr__(
            self,
            "canonical_player_name",
            _require_text(self.canonical_player_name, "canonical_player_name"),
        )
        object.__setattr__(
            self,
            "canonical_team",
            _require_canonical_team(self.canonical_team, "canonical_team"),
        )
        object.__setattr__(self, "mapping_source", _require_text(self.mapping_source, "mapping_source"))
        object.__setattr__(
            self,
            "reviewed_at",
            _coerce_utc_datetime(self.reviewed_at, "reviewed_at"),
        )
        object.__setattr__(self, "review_status", _require_text(self.review_status, "review_status"))
        object.__setattr__(self, "mapping_version", _require_text(self.mapping_version, "mapping_version"))
        if self.provider_player_id is not None:
            object.__setattr__(
                self,
                "provider_player_id",
                _require_identifier(self.provider_player_id, "provider_player_id"),
            )
        if self.provider_player_name is not None:
            object.__setattr__(
                self,
                "provider_player_name",
                _require_text(self.provider_player_name, "provider_player_name"),
            )
        if self.provider_player_id is None and self.provider_player_name is None:
            raise NBAPlayerPointsResearchSchemaError(
                "player mapping requires provider_player_id or provider_player_name"
            )
        mapping_type = _require_text(self.mapping_type, "mapping_type").casefold()
        if mapping_type not in {"identity", "alias"}:
            raise NBAPlayerPointsResearchSchemaError("mapping_type must be identity or alias")
        object.__setattr__(self, "mapping_type", mapping_type)
        object.__setattr__(self, "reviewer", _clean_text(self.reviewer))
        object.__setattr__(self, "review_reference", _clean_text(self.review_reference))

    @property
    def normalized_provider_player_name(self) -> str | None:
        if self.provider_player_name is None:
            return None
        return normalize_player_name(self.provider_player_name)

    @property
    def key(self) -> tuple[str, str, str, str]:
        if self.provider_player_id is not None:
            return (self.provider_name, "provider_player_id", self.provider_player_id, "")
        normalized_name = self.normalized_provider_player_name
        if normalized_name is None:
            raise NBAPlayerPointsResearchSchemaError("provider_player_name is required")
        return (self.provider_name, self.mapping_type, normalized_name, self.canonical_team)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_player_id": self.provider_player_id,
            "provider_player_name": self.provider_player_name,
            "normalized_provider_player_name": self.normalized_provider_player_name,
            "player_id": self.player_id,
            "canonical_player_name": self.canonical_player_name,
            "canonical_team": self.canonical_team,
            "mapping_type": self.mapping_type,
            "mapping_source": self.mapping_source,
            "reviewed_at": _format_utc(self.reviewed_at),
            "review_status": self.review_status,
            "mapping_version": self.mapping_version,
            "reviewer": self.reviewer,
            "review_reference": self.review_reference,
        }


@dataclass(frozen=True, slots=True)
class NBAReviewedIdentityMappingArtifact:
    """Versioned reviewed mapping artifact loaded from offline JSON fixtures."""

    schema_version: str
    mapping_version: str
    event_mappings: tuple[NBAReviewedEventMapping, ...] = ()
    player_mappings: tuple[NBAReviewedPlayerMapping, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION:
            raise NBAPlayerPointsResearchSchemaError(
                f"unsupported mapping schema_version: {self.schema_version!r}"
            )
        object.__setattr__(self, "mapping_version", _require_text(self.mapping_version, "mapping_version"))
        object.__setattr__(self, "event_mappings", tuple(self.event_mappings))
        object.__setattr__(self, "player_mappings", tuple(self.player_mappings))
        _validate_reviewed_event_mappings(self.event_mappings)
        _validate_reviewed_player_mappings(self.player_mappings)

    def approved_event_mapping(
        self,
        provider_name: str,
        provider_event_id: str,
    ) -> NBAReviewedEventMapping | None:
        provider = _normalize_provider_name(provider_name)
        event_id = _require_identifier(provider_event_id, "provider_event_id")
        for mapping in self.event_mappings:
            if (
                mapping.provider_name == provider
                and mapping.provider_event_id == event_id
                and mapping.review_status.casefold() == _APPROVED_REVIEW_STATUS
            ):
                return mapping
        return None

    def approved_player_id_mapping(
        self,
        provider_name: str,
        provider_player_id: str | None,
    ) -> NBAReviewedPlayerMapping | None:
        if provider_player_id is None:
            return None
        provider = _normalize_provider_name(provider_name)
        player_id = _require_identifier(provider_player_id, "provider_player_id")
        for mapping in self.player_mappings:
            if (
                mapping.provider_name == provider
                and mapping.provider_player_id == player_id
                and mapping.mapping_type == "identity"
                and mapping.review_status.casefold() == _APPROVED_REVIEW_STATUS
            ):
                return mapping
        return None

    def approved_player_name_mapping(
        self,
        provider_name: str,
        provider_player_name: str,
        canonical_team: str | None,
        *,
        mapping_type: str,
    ) -> NBAReviewedPlayerMapping | None:
        provider = _normalize_provider_name(provider_name)
        normalized_name = normalize_player_name(provider_player_name)
        normalized_type = _require_text(mapping_type, "mapping_type").casefold()
        for mapping in self.player_mappings:
            if (
                mapping.provider_name == provider
                and mapping.normalized_provider_player_name == normalized_name
                and mapping.mapping_type == normalized_type
                and mapping.review_status.casefold() == _APPROVED_REVIEW_STATUS
                and (canonical_team is None or mapping.canonical_team == canonical_team)
            ):
                return mapping
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "mapping_version": self.mapping_version,
            "event_mappings": [mapping.to_dict() for mapping in self.event_mappings],
            "player_mappings": [mapping.to_dict() for mapping in self.player_mappings],
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsCrosswalkJoinRow:
    """Offline crosswalk output that preserves eligible and excluded rows."""

    original_odds_row: Mapping[str, object]
    event_identity: NBAPlayerPointsEventIdentity
    player_identity: NBAPlayerPointsPlayerIdentity
    canonical_event_id: str | None
    canonical_player_id: str | None
    eligibility_status: str
    exclusion_reason: str
    resolution_provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_odds_row", MappingProxyType(dict(self.original_odds_row)))
        if not isinstance(self.event_identity, NBAPlayerPointsEventIdentity):
            raise TypeError("event_identity must be NBAPlayerPointsEventIdentity")
        if not isinstance(self.player_identity, NBAPlayerPointsPlayerIdentity):
            raise TypeError("player_identity must be NBAPlayerPointsPlayerIdentity")
        if self.canonical_event_id is not None:
            object.__setattr__(
                self,
                "canonical_event_id",
                _require_identifier(self.canonical_event_id, "canonical_event_id"),
            )
        if self.canonical_player_id is not None:
            object.__setattr__(
                self,
                "canonical_player_id",
                _require_identifier(self.canonical_player_id, "canonical_player_id"),
            )
        eligibility = _require_text(self.eligibility_status, "eligibility_status")
        if eligibility not in {"eligible", "excluded"}:
            raise NBAPlayerPointsResearchSchemaError(
                "eligibility_status must be eligible or excluded"
            )
        object.__setattr__(self, "eligibility_status", eligibility)
        reason = _require_text(self.exclusion_reason, "exclusion_reason")
        if reason not in NBA_CROSSWALK_EXCLUSION_REASONS:
            raise NBAPlayerPointsResearchSchemaError(f"unsupported exclusion_reason: {reason!r}")
        object.__setattr__(self, "exclusion_reason", reason)
        provenance = {
            _require_text(key, "resolution_provenance key"): value
            for key, value in dict(self.resolution_provenance).items()
        }
        object.__setattr__(self, "resolution_provenance", MappingProxyType(provenance))

    def to_dict(self) -> dict[str, object]:
        return {
            "original_odds_row": dict(self.original_odds_row),
            "event_identity": self.event_identity.to_dict(),
            "player_identity": self.player_identity.to_dict(),
            "canonical_event_id": self.canonical_event_id,
            "canonical_player_id": self.canonical_player_id,
            "eligibility_status": self.eligibility_status,
            "exclusion_reason": self.exclusion_reason,
            "resolution_provenance": dict(self.resolution_provenance),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsCrosswalkJoinResult:
    """Pure offline join result."""

    rows: tuple[NBAPlayerPointsCrosswalkJoinRow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        for row in self.rows:
            if not isinstance(row, NBAPlayerPointsCrosswalkJoinRow):
                raise TypeError("rows must contain NBAPlayerPointsCrosswalkJoinRow values")

    @property
    def eligible_rows(self) -> tuple[NBAPlayerPointsCrosswalkJoinRow, ...]:
        return tuple(row for row in self.rows if row.eligibility_status == "eligible")

    @property
    def excluded_rows(self) -> tuple[NBAPlayerPointsCrosswalkJoinRow, ...]:
        return tuple(row for row in self.rows if row.eligibility_status == "excluded")

    def to_dicts(self) -> tuple[dict[str, object], ...]:
        return tuple(row.to_dict() for row in self.rows)


def event_identity_schema() -> dict[str, object]:
    """Return the event identity contract."""

    return {
        "schema_version": NBA_PLAYER_POINTS_CROSSWALK_SCHEMA_VERSION,
        "fields": [
            "provider_event_id",
            "canonical_event_id",
            "provider_name",
            "operating_date",
            "commence_time_utc",
            "home_team",
            "away_team",
            "canonical_home_team",
            "canonical_away_team",
            "event_status",
            "event_identity_status",
            "event_identity_method",
            "event_identity_confidence",
            "event_conflict_reason",
            "source_timestamp_utc",
            "mapping_version",
        ],
        "statuses": list(NBA_IDENTITY_STATUSES),
        "default_event_time_tolerance_seconds": int(
            NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE.total_seconds()
        ),
        "max_event_time_tolerance_seconds": int(
            NBA_PLAYER_POINTS_MAX_EVENT_TIME_TOLERANCE.total_seconds()
        ),
    }


def player_identity_schema() -> dict[str, object]:
    """Return the player identity contract."""

    return {
        "schema_version": NBA_PLAYER_POINTS_CROSSWALK_SCHEMA_VERSION,
        "fields": [
            "provider_player_name",
            "normalized_player_name",
            "player_id",
            "canonical_player_name",
            "team",
            "canonical_team",
            "player_identity_status",
            "player_identity_method",
            "player_identity_confidence",
            "player_conflict_reason",
            "identity_source",
            "mapping_version",
        ],
        "statuses": list(NBA_IDENTITY_STATUSES),
        "matching_priority": [
            "provider_player_id_exact",
            "approved_identity_mapping",
            "normalized_full_name_plus_team",
            "approved_alias_mapping",
            "unresolved_or_ambiguous",
        ],
    }


def mapping_artifact_schema() -> dict[str, object]:
    """Return the offline reviewed mapping artifact format."""

    return {
        "schema_version": NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION,
        "top_level_fields": [
            "schema_version",
            "mapping_version",
            "event_mappings",
            "player_mappings",
        ],
        "event_mapping_key": ["provider_name", "provider_event_id"],
        "player_mapping_keys": [
            ["provider_name", "provider_player_id"],
            ["provider_name", "mapping_type", "normalized_provider_player_name", "canonical_team"],
        ],
        "required_review_fields": [
            "mapping_source",
            "reviewed_at",
            "review_status",
            "reviewer_or_review_reference",
        ],
    }


def validate_nba_team_normalization_table() -> Mapping[tuple[str, str], str]:
    """Return the explicit team alias table after conflict validation."""

    return _NBA_TEAM_ALIAS_TABLE


def normalize_nba_team(
    value: object,
    *,
    provider_name: str | None = None,
) -> NBATeamNormalizationResult:
    """Resolve a team alias by explicit table lookup only."""

    original = _require_text(value, "team")
    alias_key = _team_alias_key(original)
    provider = _normalize_provider_name(provider_name) if provider_name else "generic"
    provider_key = (provider, alias_key)
    generic_key = ("generic", alias_key)
    if provider_key in _NBA_TEAM_ALIAS_TABLE:
        return NBATeamNormalizationResult(
            original_team=original,
            canonical_team=_NBA_TEAM_ALIAS_TABLE[provider_key],
            team_identity_status="resolved",
            team_identity_method="provider_team_alias",
            team_conflict_reason="none",
        )
    if generic_key in _NBA_TEAM_ALIAS_TABLE:
        return NBATeamNormalizationResult(
            original_team=original,
            canonical_team=_NBA_TEAM_ALIAS_TABLE[generic_key],
            team_identity_status="resolved",
            team_identity_method="canonical_team_alias",
            team_conflict_reason="none",
        )
    return NBATeamNormalizationResult(
        original_team=original,
        canonical_team=None,
        team_identity_status="unresolved",
        team_identity_method="unresolved_team_alias",
        team_conflict_reason="unknown_team_alias",
    )


def load_reviewed_identity_mapping_artifact(
    payload: Mapping[str, object] | NBAReviewedIdentityMappingArtifact | None,
) -> NBAReviewedIdentityMappingArtifact:
    """Load and validate an offline reviewed mapping artifact."""

    if payload is None:
        return NBAReviewedIdentityMappingArtifact(
            schema_version=NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION,
            mapping_version="unmapped",
        )
    if isinstance(payload, NBAReviewedIdentityMappingArtifact):
        return payload
    if not isinstance(payload, Mapping):
        raise NBAPlayerPointsResearchSchemaError("mapping artifact must be an object")
    schema_version = _require_text(_required_mapping_value(payload, "schema_version"), "schema_version")
    mapping_version = _require_text(_required_mapping_value(payload, "mapping_version"), "mapping_version")
    event_mappings_payload = payload.get("event_mappings", ())
    player_mappings_payload = payload.get("player_mappings", ())
    if not isinstance(event_mappings_payload, Sequence) or isinstance(event_mappings_payload, (str, bytes)):
        raise NBAPlayerPointsResearchSchemaError("event_mappings must be a list")
    if not isinstance(player_mappings_payload, Sequence) or isinstance(player_mappings_payload, (str, bytes)):
        raise NBAPlayerPointsResearchSchemaError("player_mappings must be a list")

    event_mappings = tuple(
        _build_event_mapping(entry, mapping_version)
        for entry in event_mappings_payload
    )
    player_mappings = tuple(
        _build_player_mapping(entry, mapping_version)
        for entry in player_mappings_payload
    )
    return NBAReviewedIdentityMappingArtifact(
        schema_version=schema_version,
        mapping_version=mapping_version,
        event_mappings=event_mappings,
        player_mappings=player_mappings,
    )


def resolve_nba_event_identity(
    odds_row: Mapping[str, object] | NBAPlayerPointsMarketEvidence,
    canonical_schedule_rows: Sequence[Mapping[str, object] | object],
    *,
    reviewed_mapping: Mapping[str, object] | NBAReviewedIdentityMappingArtifact | None = None,
    event_time_tolerance: timedelta = NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE,
) -> NBAPlayerPointsEventIdentity:
    """Resolve one odds event to a canonical event without guessing."""

    tolerance = _validate_event_time_tolerance(event_time_tolerance)
    artifact = load_reviewed_identity_mapping_artifact(reviewed_mapping)
    odds = _extract_odds_event(odds_row)
    schedule = tuple(_extract_canonical_event(row) for row in canonical_schedule_rows)

    home_team = normalize_nba_team(odds["home_team"], provider_name=odds["provider_name"])
    away_team = normalize_nba_team(odds["away_team"], provider_name=odds["provider_name"])
    if home_team.canonical_team is None or away_team.canonical_team is None:
        return _event_identity(
            odds,
            status="unresolved",
            method="team_alias_unresolved",
            confidence=0.0,
            reason="unknown_team_alias",
            mapping_version=artifact.mapping_version,
            canonical_event=None,
            canonical_home_team=home_team.canonical_team,
            canonical_away_team=away_team.canonical_team,
        )

    reviewed = artifact.approved_event_mapping(
        odds["provider_name"],
        odds["provider_event_id"],
    )
    if reviewed is not None:
        return _resolve_reviewed_event_mapping(
            odds,
            schedule,
            reviewed,
            tolerance,
            mapping_version=artifact.mapping_version,
        )

    exact_id_matches = tuple(
        event for event in schedule if event["canonical_event_id"] == odds["provider_event_id"]
    )
    if exact_id_matches:
        return _resolve_event_candidates(
            odds,
            exact_id_matches,
            tolerance,
            method="canonical_event_id_exact",
            mapping_version=artifact.mapping_version,
            home_team=home_team.canonical_team,
            away_team=away_team.canonical_team,
        )

    same_team_date = tuple(
        event
        for event in schedule
        if event["operating_date"] == odds["operating_date"]
        and event["canonical_home_team"] == home_team.canonical_team
        and event["canonical_away_team"] == away_team.canonical_team
    )
    within_tolerance = tuple(
        event
        for event in same_team_date
        if _within_tolerance(odds["commence_time_utc"], event["commence_time_utc"], tolerance)
    )
    if within_tolerance:
        return _resolve_event_candidates(
            odds,
            within_tolerance,
            tolerance,
            method="team_date_time",
            mapping_version=artifact.mapping_version,
            home_team=home_team.canonical_team,
            away_team=away_team.canonical_team,
        )
    if same_team_date:
        return _event_identity(
            odds,
            status="conflicting",
            method="team_date_time",
            confidence=0.0,
            reason="commence_time_mismatch",
            mapping_version=artifact.mapping_version,
            canonical_event=None,
            canonical_home_team=home_team.canonical_team,
            canonical_away_team=away_team.canonical_team,
        )

    reversed_team_date = tuple(
        event
        for event in schedule
        if event["operating_date"] == odds["operating_date"]
        and event["canonical_home_team"] == away_team.canonical_team
        and event["canonical_away_team"] == home_team.canonical_team
        and _within_tolerance(odds["commence_time_utc"], event["commence_time_utc"], tolerance)
    )
    if reversed_team_date:
        return _event_identity(
            odds,
            status="quarantined",
            method="reversed_team_quarantine",
            confidence=0.0,
            reason="reversed_teams",
            mapping_version=artifact.mapping_version,
            canonical_event=None,
            canonical_home_team=home_team.canonical_team,
            canonical_away_team=away_team.canonical_team,
        )

    return _event_identity(
        odds,
        status="unresolved",
        method="no_event_match",
        confidence=0.0,
        reason="no_matching_event",
        mapping_version=artifact.mapping_version,
        canonical_event=None,
        canonical_home_team=home_team.canonical_team,
        canonical_away_team=away_team.canonical_team,
    )


def resolve_nba_player_identity(
    odds_row: Mapping[str, object] | NBAPlayerPointsMarketEvidence,
    canonical_player_rows: Sequence[Mapping[str, object] | NBAPlayerPointsFinalStatEvidence],
    *,
    reviewed_mapping: Mapping[str, object] | NBAReviewedIdentityMappingArtifact | None = None,
    canonical_event_id: str | None = None,
) -> NBAPlayerPointsPlayerIdentity:
    """Resolve one odds player to a canonical player without fuzzy matching."""

    artifact = load_reviewed_identity_mapping_artifact(reviewed_mapping)
    odds = _extract_odds_player(odds_row)
    team_result = normalize_nba_team(odds["team"], provider_name=odds["provider_name"])
    if odds.get("missing_required_identity_field") is True:
        return _player_identity(
            odds,
            status="unresolved",
            method="missing_required_identity_field",
            confidence=0.0,
            reason="missing_required_identity_field",
            identity_source="odds_row",
            mapping_version=artifact.mapping_version,
            player=None,
            canonical_team=team_result.canonical_team,
        )
    canonical_players = tuple(
        _extract_canonical_player(row, canonical_event_id=canonical_event_id)
        for row in canonical_player_rows
    )
    if canonical_event_id is not None:
        canonical_players = tuple(
            player
            for player in canonical_players
            if player.get("canonical_event_id") in {None, canonical_event_id}
        )
    if team_result.canonical_team is None:
        return _player_identity(
            odds,
            status="unresolved",
            method="team_alias_unresolved",
            confidence=0.0,
            reason="unknown_team_alias",
            identity_source="team_normalization",
            mapping_version=artifact.mapping_version,
            player=None,
            canonical_team=None,
        )

    provider_player_id = odds.get("provider_player_id")
    if provider_player_id:
        direct_matches = tuple(
            player for player in canonical_players if player["player_id"] == provider_player_id
        )
        if direct_matches:
            return _resolve_player_candidates(
                odds,
                direct_matches,
                team_result.canonical_team,
                method="provider_player_id_exact",
                identity_source="canonical_player_rows",
                mapping_version=artifact.mapping_version,
            )

    reviewed_id = artifact.approved_player_id_mapping(
        odds["provider_name"],
        provider_player_id if isinstance(provider_player_id, str) else None,
    )
    if reviewed_id is not None:
        return _resolve_reviewed_player_mapping(
            odds,
            canonical_players,
            reviewed_id,
            team_result.canonical_team,
            method="reviewed_player_id_mapping",
            mapping_version=artifact.mapping_version,
        )

    reviewed_identity = artifact.approved_player_name_mapping(
        odds["provider_name"],
        odds["provider_player_name"],
        team_result.canonical_team,
        mapping_type="identity",
    )
    if reviewed_identity is not None:
        return _resolve_reviewed_player_mapping(
            odds,
            canonical_players,
            reviewed_identity,
            team_result.canonical_team,
            method="reviewed_player_mapping",
            mapping_version=artifact.mapping_version,
        )

    normalized_name = normalize_player_name(odds["provider_player_name"])
    same_name = tuple(
        player for player in canonical_players if player["normalized_player_name"] == normalized_name
    )
    compatible_team = tuple(
        player for player in same_name if player["canonical_team"] == team_result.canonical_team
    )
    if compatible_team:
        return _resolve_player_candidates(
            odds,
            compatible_team,
            team_result.canonical_team,
            method="normalized_full_name_plus_team",
            identity_source="canonical_player_rows",
            mapping_version=artifact.mapping_version,
        )
    if same_name:
        return _player_identity(
            odds,
            status="conflicting",
            method="normalized_full_name_plus_team",
            confidence=0.0,
            reason="team_mismatch",
            identity_source="canonical_player_rows",
            mapping_version=artifact.mapping_version,
            player=None,
            canonical_team=team_result.canonical_team,
        )

    reviewed_alias = artifact.approved_player_name_mapping(
        odds["provider_name"],
        odds["provider_player_name"],
        team_result.canonical_team,
        mapping_type="alias",
    )
    if reviewed_alias is not None:
        return _resolve_reviewed_player_mapping(
            odds,
            canonical_players,
            reviewed_alias,
            team_result.canonical_team,
            method="reviewed_alias_mapping",
            mapping_version=artifact.mapping_version,
        )

    return _player_identity(
        odds,
        status="unresolved",
        method="no_player_match",
        confidence=0.0,
        reason="no_matching_player",
        identity_source="canonical_player_rows",
        mapping_version=artifact.mapping_version,
        player=None,
        canonical_team=team_result.canonical_team,
    )


def join_nba_player_points_crosswalk(
    odds_rows: Sequence[Mapping[str, object] | NBAPlayerPointsMarketEvidence],
    canonical_schedule_rows: Sequence[Mapping[str, object] | object],
    canonical_player_rows: Sequence[Mapping[str, object] | NBAPlayerPointsFinalStatEvidence],
    *,
    reviewed_event_mapping: Mapping[str, object] | NBAReviewedIdentityMappingArtifact | None = None,
    reviewed_player_mapping: Mapping[str, object] | NBAReviewedIdentityMappingArtifact | None = None,
    event_time_tolerance: timedelta = NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE,
) -> NBAPlayerPointsCrosswalkJoinResult:
    """Join odds rows to event and player identities, preserving exclusions."""

    event_artifact = load_reviewed_identity_mapping_artifact(reviewed_event_mapping)
    player_artifact = load_reviewed_identity_mapping_artifact(reviewed_player_mapping)
    rows: list[NBAPlayerPointsCrosswalkJoinRow] = []
    for odds_row in odds_rows:
        original_row = _row_to_dict(odds_row)
        event_identity = resolve_nba_event_identity(
            original_row,
            canonical_schedule_rows,
            reviewed_mapping=event_artifact,
            event_time_tolerance=event_time_tolerance,
        )
        player_identity = resolve_nba_player_identity(
            original_row,
            canonical_player_rows,
            reviewed_mapping=player_artifact,
            canonical_event_id=event_identity.canonical_event_id,
        )
        eligibility_status, exclusion_reason = _eligibility(event_identity, player_identity)
        rows.append(
            NBAPlayerPointsCrosswalkJoinRow(
                original_odds_row=original_row,
                event_identity=event_identity,
                player_identity=player_identity,
                canonical_event_id=(
                    event_identity.canonical_event_id
                    if eligibility_status == "eligible"
                    else None
                ),
                canonical_player_id=(
                    player_identity.player_id
                    if eligibility_status == "eligible"
                    else None
                ),
                eligibility_status=eligibility_status,
                exclusion_reason=exclusion_reason,
                resolution_provenance={
                    "event_identity_method": event_identity.event_identity_method,
                    "player_identity_method": player_identity.player_identity_method,
                    "event_mapping_version": event_artifact.mapping_version,
                    "player_mapping_version": player_artifact.mapping_version,
                    "event_time_tolerance_seconds": int(event_time_tolerance.total_seconds()),
                },
            )
        )
    return NBAPlayerPointsCrosswalkJoinResult(rows=tuple(rows))


def _resolve_reviewed_event_mapping(
    odds: Mapping[str, Any],
    schedule: tuple[Mapping[str, Any], ...],
    reviewed: NBAReviewedEventMapping,
    tolerance: timedelta,
    *,
    mapping_version: str,
) -> NBAPlayerPointsEventIdentity:
    canonical_matches = tuple(
        event for event in schedule if event["canonical_event_id"] == reviewed.canonical_event_id
    )
    if not canonical_matches:
        return _event_identity(
            odds,
            status="conflicting",
            method="reviewed_event_mapping",
            confidence=0.0,
            reason="reviewed_mapping_target_missing",
            mapping_version=mapping_version,
            canonical_event=None,
            canonical_home_team=reviewed.canonical_home_team,
            canonical_away_team=reviewed.canonical_away_team,
        )
    if len(canonical_matches) > 1:
        return _event_identity(
            odds,
            status="ambiguous",
            method="reviewed_event_mapping",
            confidence=0.0,
            reason="duplicate_canonical_event_id",
            mapping_version=mapping_version,
            canonical_event=None,
            canonical_home_team=reviewed.canonical_home_team,
            canonical_away_team=reviewed.canonical_away_team,
        )
    event = canonical_matches[0]
    odds_home = normalize_nba_team(odds["home_team"], provider_name=odds["provider_name"]).canonical_team
    odds_away = normalize_nba_team(odds["away_team"], provider_name=odds["provider_name"]).canonical_team
    direct_team_match = (
        event["canonical_home_team"] == odds_home
        and event["canonical_away_team"] == odds_away
    )
    reversed_team_match = (
        event["canonical_home_team"] == odds_away
        and event["canonical_away_team"] == odds_home
    )
    if not direct_team_match and not (reviewed.allow_reversed_teams and reversed_team_match):
        return _event_identity(
            odds,
            status="quarantined" if reversed_team_match else "conflicting",
            method="reviewed_event_mapping",
            confidence=0.0,
            reason="reversed_teams" if reversed_team_match else "team_mismatch",
            mapping_version=mapping_version,
            canonical_event=None,
            canonical_home_team=odds_home,
            canonical_away_team=odds_away,
        )
    if not _within_tolerance(odds["commence_time_utc"], event["commence_time_utc"], tolerance):
        return _event_identity(
            odds,
            status="conflicting",
            method="reviewed_event_mapping",
            confidence=0.0,
            reason="commence_time_mismatch",
            mapping_version=mapping_version,
            canonical_event=None,
            canonical_home_team=event["canonical_home_team"],
            canonical_away_team=event["canonical_away_team"],
        )
    return _event_identity(
        odds,
        status="resolved",
        method=(
            "reviewed_event_mapping_reversed"
            if reversed_team_match
            else "reviewed_event_mapping"
        ),
        confidence=1.0,
        reason="none",
        mapping_version=mapping_version,
        canonical_event=event,
        canonical_home_team=event["canonical_home_team"],
        canonical_away_team=event["canonical_away_team"],
    )


def _resolve_event_candidates(
    odds: Mapping[str, Any],
    candidates: tuple[Mapping[str, Any], ...],
    tolerance: timedelta,
    *,
    method: str,
    mapping_version: str,
    home_team: str,
    away_team: str,
) -> NBAPlayerPointsEventIdentity:
    valid_candidates = tuple(
        event
        for event in candidates
        if event["canonical_home_team"] == home_team
        and event["canonical_away_team"] == away_team
        and event["operating_date"] == odds["operating_date"]
        and _within_tolerance(odds["commence_time_utc"], event["commence_time_utc"], tolerance)
    )
    if len(valid_candidates) == 1:
        event = valid_candidates[0]
        return _event_identity(
            odds,
            status="resolved",
            method=method,
            confidence=1.0,
            reason="none",
            mapping_version=mapping_version,
            canonical_event=event,
            canonical_home_team=event["canonical_home_team"],
            canonical_away_team=event["canonical_away_team"],
        )
    if len(valid_candidates) > 1:
        return _event_identity(
            odds,
            status="ambiguous",
            method=method,
            confidence=0.0,
            reason="multiple_candidate_events",
            mapping_version=mapping_version,
            canonical_event=None,
            canonical_home_team=home_team,
            canonical_away_team=away_team,
        )
    reversed_matches = tuple(
        event
        for event in candidates
        if event["canonical_home_team"] == away_team
        and event["canonical_away_team"] == home_team
        and _within_tolerance(odds["commence_time_utc"], event["commence_time_utc"], tolerance)
    )
    if reversed_matches:
        return _event_identity(
            odds,
            status="quarantined",
            method=method,
            confidence=0.0,
            reason="reversed_teams",
            mapping_version=mapping_version,
            canonical_event=None,
            canonical_home_team=home_team,
            canonical_away_team=away_team,
        )
    return _event_identity(
        odds,
        status="conflicting",
        method=method,
        confidence=0.0,
        reason="commence_time_mismatch",
        mapping_version=mapping_version,
        canonical_event=None,
        canonical_home_team=home_team,
        canonical_away_team=away_team,
    )


def _resolve_player_candidates(
    odds: Mapping[str, Any],
    candidates: tuple[Mapping[str, Any], ...],
    canonical_team: str,
    *,
    method: str,
    identity_source: str,
    mapping_version: str,
) -> NBAPlayerPointsPlayerIdentity:
    compatible = tuple(player for player in candidates if player["canonical_team"] == canonical_team)
    if not compatible:
        return _player_identity(
            odds,
            status="conflicting",
            method=method,
            confidence=0.0,
            reason="team_mismatch",
            identity_source=identity_source,
            mapping_version=mapping_version,
            player=None,
            canonical_team=canonical_team,
        )
    unique_player_ids = {player["player_id"] for player in compatible}
    if len(unique_player_ids) > 1:
        return _player_identity(
            odds,
            status="ambiguous",
            method=method,
            confidence=0.0,
            reason="multiple_candidate_players",
            identity_source=identity_source,
            mapping_version=mapping_version,
            player=None,
            canonical_team=canonical_team,
        )
    return _player_identity(
        odds,
        status="resolved",
        method=method,
        confidence=1.0,
        reason="none",
        identity_source=identity_source,
        mapping_version=mapping_version,
        player=compatible[0],
        canonical_team=canonical_team,
    )


def _resolve_reviewed_player_mapping(
    odds: Mapping[str, Any],
    canonical_players: tuple[Mapping[str, Any], ...],
    reviewed: NBAReviewedPlayerMapping,
    canonical_team: str,
    *,
    method: str,
    mapping_version: str,
) -> NBAPlayerPointsPlayerIdentity:
    if reviewed.canonical_team != canonical_team:
        return _player_identity(
            odds,
            status="conflicting",
            method=method,
            confidence=0.0,
            reason="team_mismatch",
            identity_source=reviewed.mapping_source,
            mapping_version=mapping_version,
            player=None,
            canonical_team=canonical_team,
        )
    candidates = tuple(
        player for player in canonical_players if player["player_id"] == reviewed.player_id
    )
    if candidates:
        return _resolve_player_candidates(
            odds,
            candidates,
            canonical_team,
            method=method,
            identity_source=reviewed.mapping_source,
            mapping_version=mapping_version,
        )
    reviewed_player = {
        "player_id": reviewed.player_id,
        "canonical_player_name": reviewed.canonical_player_name,
        "normalized_player_name": normalize_player_name(reviewed.canonical_player_name),
        "canonical_team": reviewed.canonical_team,
        "canonical_event_id": None,
    }
    return _player_identity(
        odds,
        status="resolved",
        method=method,
        confidence=1.0,
        reason="none",
        identity_source=reviewed.mapping_source,
        mapping_version=mapping_version,
        player=reviewed_player,
        canonical_team=canonical_team,
    )


def _event_identity(
    odds: Mapping[str, Any],
    *,
    status: str,
    method: str,
    confidence: float,
    reason: str,
    mapping_version: str,
    canonical_event: Mapping[str, Any] | None,
    canonical_home_team: str | None,
    canonical_away_team: str | None,
) -> NBAPlayerPointsEventIdentity:
    return NBAPlayerPointsEventIdentity(
        provider_event_id=odds["provider_event_id"],
        canonical_event_id=(
            canonical_event["canonical_event_id"] if canonical_event is not None else None
        ),
        provider_name=odds["provider_name"],
        operating_date=odds["operating_date"],
        commence_time_utc=odds["commence_time_utc"],
        home_team=odds["home_team"],
        away_team=odds["away_team"],
        canonical_home_team=canonical_home_team,
        canonical_away_team=canonical_away_team,
        event_status=status,
        event_identity_status=status,
        event_identity_method=method,
        event_identity_confidence=confidence,
        event_conflict_reason=reason,
        source_timestamp_utc=odds["source_timestamp_utc"],
        mapping_version=mapping_version,
    )


def _player_identity(
    odds: Mapping[str, Any],
    *,
    status: str,
    method: str,
    confidence: float,
    reason: str,
    identity_source: str,
    mapping_version: str,
    player: Mapping[str, Any] | None,
    canonical_team: str | None,
) -> NBAPlayerPointsPlayerIdentity:
    return NBAPlayerPointsPlayerIdentity(
        provider_player_name=odds["provider_player_name"],
        normalized_player_name=normalize_player_name(odds["provider_player_name"]),
        provider_player_id=odds.get("provider_player_id"),
        player_id=player["player_id"] if player is not None else None,
        canonical_player_name=(
            player["canonical_player_name"] if player is not None else None
        ),
        team=odds["team"],
        canonical_team=canonical_team,
        player_identity_status=status,
        player_identity_method=method,
        player_identity_confidence=confidence,
        player_conflict_reason=reason,
        identity_source=identity_source,
        mapping_version=mapping_version,
    )


def _eligibility(
    event_identity: NBAPlayerPointsEventIdentity,
    player_identity: NBAPlayerPointsPlayerIdentity,
) -> tuple[str, str]:
    if event_identity.event_identity_status != "resolved":
        if event_identity.event_conflict_reason == "commence_time_mismatch":
            return ("excluded", "commence_time_mismatch")
        if event_identity.event_identity_status == "ambiguous":
            return ("excluded", "event_ambiguous")
        if event_identity.event_identity_status in {"conflicting", "quarantined"}:
            return ("excluded", "event_conflict")
        return ("excluded", "event_unresolved")
    if player_identity.player_identity_status != "resolved":
        if player_identity.player_conflict_reason == "team_mismatch":
            return ("excluded", "team_mismatch")
        if player_identity.player_conflict_reason == "missing_required_identity_field":
            return ("excluded", "missing_required_identity_field")
        if player_identity.player_identity_status == "ambiguous":
            return ("excluded", "player_ambiguous")
        if player_identity.player_identity_status in {"conflicting", "quarantined"}:
            return ("excluded", "player_conflict")
        return ("excluded", "player_unresolved")
    return ("eligible", "none")


def _build_event_mapping(entry: object, mapping_version: str) -> NBAReviewedEventMapping:
    if not isinstance(entry, Mapping):
        raise NBAPlayerPointsResearchSchemaError("event mapping entries must be objects")
    return NBAReviewedEventMapping(
        provider_name=_required_mapping_value(entry, "provider_name"),
        provider_event_id=_required_mapping_value(entry, "provider_event_id"),
        canonical_event_id=_required_mapping_value(entry, "canonical_event_id"),
        canonical_home_team=_required_mapping_value(entry, "canonical_home_team"),
        canonical_away_team=_required_mapping_value(entry, "canonical_away_team"),
        mapping_source=_required_mapping_value(entry, "mapping_source"),
        reviewed_at=_coerce_utc_datetime(_required_mapping_value(entry, "reviewed_at"), "reviewed_at"),
        review_status=_required_mapping_value(entry, "review_status"),
        mapping_version=str(entry.get("mapping_version") or mapping_version),
        reviewer=str(entry.get("reviewer") or ""),
        review_reference=str(entry.get("review_reference") or ""),
        allow_reversed_teams=bool(entry.get("allow_reversed_teams", False)),
    )


def _build_player_mapping(entry: object, mapping_version: str) -> NBAReviewedPlayerMapping:
    if not isinstance(entry, Mapping):
        raise NBAPlayerPointsResearchSchemaError("player mapping entries must be objects")
    return NBAReviewedPlayerMapping(
        provider_name=_required_mapping_value(entry, "provider_name"),
        provider_player_id=_optional_text(entry.get("provider_player_id")),
        provider_player_name=_optional_text(entry.get("provider_player_name")),
        player_id=_required_mapping_value(entry, "player_id"),
        canonical_player_name=_required_mapping_value(entry, "canonical_player_name"),
        canonical_team=_required_mapping_value(entry, "canonical_team"),
        mapping_type=str(entry.get("mapping_type") or "identity"),
        mapping_source=_required_mapping_value(entry, "mapping_source"),
        reviewed_at=_coerce_utc_datetime(_required_mapping_value(entry, "reviewed_at"), "reviewed_at"),
        review_status=_required_mapping_value(entry, "review_status"),
        mapping_version=str(entry.get("mapping_version") or mapping_version),
        reviewer=str(entry.get("reviewer") or ""),
        review_reference=str(entry.get("review_reference") or ""),
    )


def _validate_reviewed_event_mappings(mappings: tuple[NBAReviewedEventMapping, ...]) -> None:
    by_key: dict[tuple[str, str], NBAReviewedEventMapping] = {}
    by_canonical: dict[tuple[str, str], NBAReviewedEventMapping] = {}
    for mapping in mappings:
        existing = by_key.get(mapping.key)
        if existing is not None:
            if existing.canonical_event_id != mapping.canonical_event_id:
                raise NBAPlayerPointsResearchSchemaError(
                    "one provider event identity maps to multiple canonical event identities"
                )
            raise NBAPlayerPointsResearchSchemaError("duplicate event mapping key")
        by_key[mapping.key] = mapping
        canonical_existing = by_canonical.get(mapping.canonical_key)
        if canonical_existing is not None and (
            canonical_existing.canonical_home_team != mapping.canonical_home_team
            or canonical_existing.canonical_away_team != mapping.canonical_away_team
        ):
            raise NBAPlayerPointsResearchSchemaError(
                "incompatible provider events map to one canonical event identity"
            )
        by_canonical[mapping.canonical_key] = mapping


def _validate_reviewed_player_mappings(mappings: tuple[NBAReviewedPlayerMapping, ...]) -> None:
    by_key: dict[tuple[str, str, str, str], NBAReviewedPlayerMapping] = {}
    by_player_id: dict[str, NBAReviewedPlayerMapping] = {}
    for mapping in mappings:
        existing = by_key.get(mapping.key)
        if existing is not None:
            if existing.player_id != mapping.player_id:
                raise NBAPlayerPointsResearchSchemaError(
                    "one provider player identity maps to multiple canonical player identities"
                )
            raise NBAPlayerPointsResearchSchemaError("duplicate player mapping key")
        by_key[mapping.key] = mapping
        canonical_existing = by_player_id.get(mapping.player_id)
        if canonical_existing is not None and (
            canonical_existing.canonical_team != mapping.canonical_team
            or normalize_player_name(canonical_existing.canonical_player_name)
            != normalize_player_name(mapping.canonical_player_name)
        ):
            raise NBAPlayerPointsResearchSchemaError(
                "incompatible provider players map to one canonical player identity"
            )
        by_player_id[mapping.player_id] = mapping


def _extract_odds_event(
    row: Mapping[str, object] | NBAPlayerPointsMarketEvidence,
) -> Mapping[str, Any]:
    payload = _row_to_dict(row)
    provider_name = _normalize_provider_name(
        _first_value(payload.get("provider_name"), "the_odds_api_nba")
    )
    provider_event_id = _require_identifier(
        _first_value(payload.get("provider_event_id"), payload.get("id")),
        "provider_event_id",
    )
    commence_time = _coerce_utc_datetime(
        _first_value(payload.get("commence_time_utc"), payload.get("commence_time")),
        "commence_time_utc",
    )
    operating_date = _coerce_operating_date(payload.get("operating_date"), commence_time)
    home_team = _require_text(
        _first_value(payload.get("home_team"), payload.get("team")),
        "home_team",
    )
    away_team = _require_text(
        _first_value(payload.get("away_team"), payload.get("opponent")),
        "away_team",
    )
    source_timestamp = _coerce_utc_datetime(
        _first_value(
            payload.get("source_timestamp_utc"),
            payload.get("market_timestamp_utc"),
            payload.get("last_update"),
            commence_time,
        ),
        "source_timestamp_utc",
    )
    return MappingProxyType(
        {
            "provider_name": provider_name,
            "provider_event_id": provider_event_id,
            "operating_date": operating_date,
            "commence_time_utc": commence_time,
            "home_team": home_team,
            "away_team": away_team,
            "source_timestamp_utc": source_timestamp,
        }
    )


def _extract_odds_player(
    row: Mapping[str, object] | NBAPlayerPointsMarketEvidence,
) -> Mapping[str, Any]:
    payload = _row_to_dict(row)
    provider_name = _normalize_provider_name(
        _first_value(payload.get("provider_name"), "the_odds_api_nba")
    )
    provider_player_name = _first_value(
        payload.get("provider_player_name"),
        payload.get("player_name"),
        payload.get("description"),
    )
    if not _clean_text(provider_player_name):
        return MappingProxyType(
            {
                "provider_name": provider_name,
                "provider_player_name": "missing-player-name",
                "provider_player_id": _optional_text(payload.get("provider_player_id")),
                "team": _require_text(_first_value(payload.get("team"), payload.get("home_team")), "team"),
                "missing_required_identity_field": True,
            }
        )
    team = _require_text(_first_value(payload.get("team"), payload.get("home_team")), "team")
    return MappingProxyType(
        {
            "provider_name": provider_name,
            "provider_player_name": _require_text(provider_player_name, "provider_player_name"),
            "provider_player_id": _optional_text(
                _first_value(payload.get("provider_player_id"), payload.get("player_id"))
            ),
            "team": team,
            "missing_required_identity_field": False,
        }
    )


def _extract_canonical_event(row: Mapping[str, object] | object) -> Mapping[str, Any]:
    payload = _row_to_dict(row)
    canonical_event_id = _require_identifier(payload.get("canonical_event_id"), "canonical_event_id")
    commence_time = _coerce_utc_datetime(
        _first_value(payload.get("commence_time_utc"), payload.get("commence_time")),
        "commence_time_utc",
    )
    operating_date = _coerce_operating_date(payload.get("operating_date"), commence_time)
    home_team = normalize_nba_team(
        _first_value(payload.get("canonical_home_team"), payload.get("home_team"), payload.get("team")),
        provider_name="canonical_schedule",
    )
    away_team = normalize_nba_team(
        _first_value(payload.get("canonical_away_team"), payload.get("away_team"), payload.get("opponent")),
        provider_name="canonical_schedule",
    )
    if home_team.canonical_team is None or away_team.canonical_team is None:
        raise NBAPlayerPointsResearchSchemaError("canonical schedule contains unknown team alias")
    return MappingProxyType(
        {
            "canonical_event_id": canonical_event_id,
            "operating_date": operating_date,
            "commence_time_utc": commence_time,
            "canonical_home_team": home_team.canonical_team,
            "canonical_away_team": away_team.canonical_team,
        }
    )


def _extract_canonical_player(
    row: Mapping[str, object] | NBAPlayerPointsFinalStatEvidence,
    *,
    canonical_event_id: str | None,
) -> Mapping[str, Any]:
    payload = _row_to_dict(row)
    player_id = _require_identifier(payload.get("player_id"), "player_id")
    player_name = _require_text(
        _first_value(payload.get("canonical_player_name"), payload.get("player_name")),
        "canonical_player_name",
    )
    team = normalize_nba_team(
        _first_value(payload.get("canonical_team"), payload.get("team")),
        provider_name="canonical_player_rows",
    )
    if team.canonical_team is None:
        raise NBAPlayerPointsResearchSchemaError("canonical player row contains unknown team alias")
    row_event_id = _optional_text(payload.get("canonical_event_id"))
    return MappingProxyType(
        {
            "player_id": player_id,
            "canonical_player_name": player_name,
            "normalized_player_name": normalize_player_name(player_name),
            "canonical_team": team.canonical_team,
            "canonical_event_id": row_event_id or canonical_event_id,
        }
    )


def _row_to_dict(row: Mapping[str, object] | object) -> dict[str, object]:
    if isinstance(row, NBAPlayerPointsMarketEvidence | NBAPlayerPointsFinalStatEvidence):
        return dict(row.to_dict())
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "to_dict"):
        result = row.to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    raise TypeError("row must be a mapping or research evidence row")


def _validate_event_time_tolerance(value: timedelta) -> timedelta:
    if not isinstance(value, timedelta):
        raise NBAPlayerPointsResearchSchemaError("event_time_tolerance must be a timedelta")
    if value < timedelta(0):
        raise NBAPlayerPointsResearchSchemaError("event_time_tolerance cannot be negative")
    if value > NBA_PLAYER_POINTS_MAX_EVENT_TIME_TOLERANCE:
        raise NBAPlayerPointsResearchSchemaError(
            "event_time_tolerance exceeds the configured maximum"
        )
    return value


def _within_tolerance(left: datetime, right: datetime, tolerance: timedelta) -> bool:
    return abs(_coerce_utc_datetime(left, "left") - _coerce_utc_datetime(right, "right")) <= tolerance


def _coerce_operating_date(value: object, commence_time: datetime) -> date:
    expected = toronto_operating_date(commence_time)
    if value in (None, ""):
        return expected
    if isinstance(value, date) and not isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise NBAPlayerPointsResearchSchemaError("operating_date must be ISO date") from exc
    else:
        raise NBAPlayerPointsResearchSchemaError("operating_date must be a date")
    if parsed != expected:
        raise NBAPlayerPointsResearchSchemaError(
            "operating_date must equal the America/Toronto date for commence_time_utc"
        )
    return parsed


def _coerce_utc_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise NBAPlayerPointsResearchSchemaError(
                f"{field_name} must be an ISO-8601 UTC timestamp"
            ) from exc
    else:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be an ISO-8601 UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be UTC")
    return parsed.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _coerce_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _require_probability(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be numeric")
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be within [0, 1]")
    return parsed


def _require_status(value: object, field_name: str) -> str:
    status = _require_text(value, field_name)
    if status not in NBA_IDENTITY_STATUSES:
        raise NBAPlayerPointsResearchSchemaError(f"unsupported {field_name}: {status!r}")
    return status


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} is required")
    return text


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    text = _clean_text(value)
    return text or None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _required_mapping_value(payload: Mapping[str, object], field_name: str) -> object:
    if field_name not in payload:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} is required")
    return payload[field_name]


def _first_value(*values: object) -> object:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _normalize_provider_name(value: object) -> str:
    provider = _require_text(value, "provider_name").casefold()
    return _TEAM_ALIAS_KEY_RE.sub("_", provider).strip("_")


def _team_alias_key(value: object) -> str:
    text = _require_text(value, "team").casefold()
    return _TEAM_ALIAS_KEY_RE.sub(" ", text).strip()


def _require_canonical_team(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).upper()
    if text not in _NBA_CANONICAL_TEAMS:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} is not a canonical NBA team")
    return text


def _build_team_alias_table(rows: tuple[tuple[str, str, str], ...]) -> Mapping[tuple[str, str], str]:
    table: dict[tuple[str, str], str] = {}
    for provider_name, alias, canonical_team in rows:
        provider = _normalize_provider_name(provider_name)
        alias_key = _team_alias_key(alias)
        canonical = _require_canonical_team(canonical_team, "canonical_team")
        key = (provider, alias_key)
        existing = table.get(key)
        if existing is not None and existing != canonical:
            raise NBAPlayerPointsResearchSchemaError("conflicting NBA team alias mapping")
        table[key] = canonical
    return MappingProxyType(table)


_NBA_CANONICAL_TEAMS: Final = MappingProxyType(
    {
        "ATL": "Atlanta Hawks",
        "BOS": "Boston Celtics",
        "BKN": "Brooklyn Nets",
        "CHA": "Charlotte Hornets",
        "CHI": "Chicago Bulls",
        "CLE": "Cleveland Cavaliers",
        "DAL": "Dallas Mavericks",
        "DEN": "Denver Nuggets",
        "DET": "Detroit Pistons",
        "GSW": "Golden State Warriors",
        "HOU": "Houston Rockets",
        "IND": "Indiana Pacers",
        "LAC": "LA Clippers",
        "LAL": "Los Angeles Lakers",
        "MEM": "Memphis Grizzlies",
        "MIA": "Miami Heat",
        "MIL": "Milwaukee Bucks",
        "MIN": "Minnesota Timberwolves",
        "NOP": "New Orleans Pelicans",
        "NYK": "New York Knicks",
        "OKC": "Oklahoma City Thunder",
        "ORL": "Orlando Magic",
        "PHI": "Philadelphia 76ers",
        "PHX": "Phoenix Suns",
        "POR": "Portland Trail Blazers",
        "SAC": "Sacramento Kings",
        "SAS": "San Antonio Spurs",
        "TOR": "Toronto Raptors",
        "UTA": "Utah Jazz",
        "WAS": "Washington Wizards",
    }
)


_NBA_TEAM_ALIAS_ROWS: Final = (
    ("generic", "ATL", "ATL"),
    ("generic", "Atlanta Hawks", "ATL"),
    ("generic", "BOS", "BOS"),
    ("generic", "Boston Celtics", "BOS"),
    ("generic", "BKN", "BKN"),
    ("generic", "BK", "BKN"),
    ("generic", "Brooklyn Nets", "BKN"),
    ("generic", "CHA", "CHA"),
    ("generic", "CHO", "CHA"),
    ("generic", "Charlotte Hornets", "CHA"),
    ("generic", "CHI", "CHI"),
    ("generic", "Chicago Bulls", "CHI"),
    ("generic", "CLE", "CLE"),
    ("generic", "Cleveland Cavaliers", "CLE"),
    ("generic", "DAL", "DAL"),
    ("generic", "Dallas Mavericks", "DAL"),
    ("generic", "DEN", "DEN"),
    ("generic", "Denver Nuggets", "DEN"),
    ("generic", "DET", "DET"),
    ("generic", "Detroit Pistons", "DET"),
    ("generic", "GSW", "GSW"),
    ("generic", "GS", "GSW"),
    ("generic", "Golden State Warriors", "GSW"),
    ("generic", "HOU", "HOU"),
    ("generic", "Houston Rockets", "HOU"),
    ("generic", "IND", "IND"),
    ("generic", "Indiana Pacers", "IND"),
    ("generic", "LAC", "LAC"),
    ("generic", "LA Clippers", "LAC"),
    ("generic", "Los Angeles Clippers", "LAC"),
    ("generic", "LAL", "LAL"),
    ("generic", "LA Lakers", "LAL"),
    ("generic", "Los Angeles Lakers", "LAL"),
    ("generic", "MEM", "MEM"),
    ("generic", "Memphis Grizzlies", "MEM"),
    ("generic", "MIA", "MIA"),
    ("generic", "Miami Heat", "MIA"),
    ("generic", "MIL", "MIL"),
    ("generic", "Milwaukee Bucks", "MIL"),
    ("generic", "MIN", "MIN"),
    ("generic", "Minnesota Timberwolves", "MIN"),
    ("generic", "NOP", "NOP"),
    ("generic", "NO", "NOP"),
    ("generic", "New Orleans Pelicans", "NOP"),
    ("generic", "NYK", "NYK"),
    ("generic", "NY", "NYK"),
    ("generic", "New York Knicks", "NYK"),
    ("generic", "OKC", "OKC"),
    ("generic", "Oklahoma City Thunder", "OKC"),
    ("generic", "ORL", "ORL"),
    ("generic", "Orlando Magic", "ORL"),
    ("generic", "PHI", "PHI"),
    ("generic", "Philadelphia 76ers", "PHI"),
    ("generic", "Philadelphia Sixers", "PHI"),
    ("generic", "PHX", "PHX"),
    ("generic", "PHO", "PHX"),
    ("generic", "Phoenix Suns", "PHX"),
    ("generic", "POR", "POR"),
    ("generic", "Portland Trail Blazers", "POR"),
    ("generic", "SAC", "SAC"),
    ("generic", "Sacramento Kings", "SAC"),
    ("generic", "SAS", "SAS"),
    ("generic", "SA", "SAS"),
    ("generic", "San Antonio Spurs", "SAS"),
    ("generic", "TOR", "TOR"),
    ("generic", "Toronto Raptors", "TOR"),
    ("generic", "UTA", "UTA"),
    ("generic", "UTAH", "UTA"),
    ("generic", "Utah Jazz", "UTA"),
    ("generic", "WAS", "WAS"),
    ("generic", "WSH", "WAS"),
    ("generic", "Washington Wizards", "WAS"),
    ("the_odds_api_nba", "Oklahoma City Thunder", "OKC"),
    ("the_odds_api_nba", "Indiana Pacers", "IND"),
    ("the_odds_api_nba", "Boston Celtics", "BOS"),
    ("the_odds_api_nba", "Los Angeles Lakers", "LAL"),
)

_NBA_TEAM_ALIAS_TABLE: Final = _build_team_alias_table(_NBA_TEAM_ALIAS_ROWS)


__all__ = [
    "NBA_CROSSWALK_EXCLUSION_REASONS",
    "NBA_IDENTITY_STATUSES",
    "NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_CROSSWALK_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE",
    "NBA_PLAYER_POINTS_MAX_EVENT_TIME_TOLERANCE",
    "NBAPlayerPointsCrosswalkJoinResult",
    "NBAPlayerPointsCrosswalkJoinRow",
    "NBAPlayerPointsEventIdentity",
    "NBAPlayerPointsPlayerIdentity",
    "NBAReviewedEventMapping",
    "NBAReviewedIdentityMappingArtifact",
    "NBAReviewedPlayerMapping",
    "NBATeamNormalizationResult",
    "event_identity_schema",
    "join_nba_player_points_crosswalk",
    "load_reviewed_identity_mapping_artifact",
    "mapping_artifact_schema",
    "normalize_nba_team",
    "player_identity_schema",
    "resolve_nba_event_identity",
    "resolve_nba_player_identity",
    "validate_nba_team_normalization_table",
]
