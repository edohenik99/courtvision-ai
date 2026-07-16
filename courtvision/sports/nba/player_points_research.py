"""Research-only NBA player-points evidence contract.

This module defines a sport-specific schema boundary and offline fixture
mappers. It performs no provider I/O, writes no files, and has no production
runtime, scoring, Kelly, grading, or operator-board side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
import unicodedata
from types import MappingProxyType
from typing import Any, Final, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION: Final = "nba-player-points-research-v1"
NBA_PLAYER_POINTS_MARKET: Final = "player_points"
NBA_PLAYER_POINTS_OPERATING_TIMEZONE: Final = "America/Toronto"
NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL: Final = "research_only_not_for_betting"

_UTC: Final = timezone.utc
_TORONTO_FALLBACK: Final = object()
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")

UTC_TIMESTAMP_FIELDS: Final = (
    "commence_time_utc",
    "market_timestamp_utc",
    "feature_timestamp_utc",
    "prediction_timestamp_utc",
)

NBA_PLAYER_POINTS_RESEARCH_ROW_FIELDS: Final = (
    "schema_version",
    "prediction_id",
    "prediction_run_id",
    "model_id",
    "provider_event_id",
    "canonical_event_id",
    "operating_date",
    "operating_timezone",
    "commence_time_utc",
    "team",
    "opponent",
    "player_id",
    "player_name",
    "normalized_player_name",
    "identity_status",
    "identity_source",
    "identity_conflict_reason",
    "sportsbook",
    "market",
    "line",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "market_timestamp_utc",
    "projected_points",
    "projected_minutes",
    "recent_minutes",
    "season_minutes",
    "points_per_minute",
    "lineup_status",
    "injury_status",
    "feature_timestamp_utc",
    "feature_source",
    "model_over_probability",
    "model_under_probability",
    "selected_side",
    "model_edge",
    "eligibility_status",
    "exclusion_reason",
    "prediction_timestamp_utc",
    "feature_schema_version",
    "repository_commit_sha",
    "source_manifest_id",
    "source_hashes",
    "artifact_hash",
    "research_only_label",
    "research_only",
)


class NBAPlayerPointsResearchSchemaError(ValueError):
    """Raised when the research-only NBA player-points contract fails closed."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsProviderCapability:
    """Explicit field support for one offline provider fixture."""

    provider_name: str
    provider_role: str
    source_type: str
    mode: str
    supports_live_calls: bool
    available_fields: tuple[str, ...] = ()
    unsupported_field_reasons: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_name",
            _require_text(self.provider_name, "provider_name").casefold(),
        )
        object.__setattr__(
            self,
            "provider_role",
            _require_text(self.provider_role, "provider_role"),
        )
        object.__setattr__(
            self,
            "source_type",
            _require_text(self.source_type, "source_type"),
        )
        object.__setattr__(self, "mode", _require_text(self.mode, "mode"))
        object.__setattr__(
            self,
            "available_fields",
            _tuple_of_text(self.available_fields, "available_fields"),
        )
        reasons = {
            _require_text(field_name, "unsupported field name"): _require_text(
                reason,
                f"unsupported_field_reasons.{field_name}",
            )
            for field_name, reason in dict(self.unsupported_field_reasons).items()
        }
        object.__setattr__(self, "unsupported_field_reasons", MappingProxyType(reasons))

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


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsMarketEvidence:
    """One player-points market row from an offline odds fixture."""

    provider_name: str
    provider_event_id: str
    canonical_event_id: str | None
    operating_date: date
    operating_timezone: str
    commence_time_utc: datetime
    team: str
    opponent: str
    player_name: str
    normalized_player_name: str
    sportsbook: str
    market: str
    side: str
    line: float
    american_odds: int
    decimal_odds: float
    implied_probability: float
    market_timestamp_utc: datetime
    unsupported_field_reasons: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _require_text(self.provider_name, "provider_name"))
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
        object.__setattr__(self, "operating_timezone", NBA_PLAYER_POINTS_OPERATING_TIMEZONE)
        object.__setattr__(
            self,
            "commence_time_utc",
            _require_utc_datetime(self.commence_time_utc, "commence_time_utc"),
        )
        expected_date = toronto_operating_date(self.commence_time_utc)
        if self.operating_date != expected_date:
            raise NBAPlayerPointsResearchSchemaError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        object.__setattr__(self, "team", _normalize_team(self.team, "team"))
        object.__setattr__(self, "opponent", _normalize_team(self.opponent, "opponent"))
        player_name = _require_text(self.player_name, "player_name")
        object.__setattr__(self, "player_name", player_name)
        expected_normalized = normalize_player_name(player_name)
        object.__setattr__(self, "normalized_player_name", expected_normalized)
        object.__setattr__(self, "sportsbook", _require_text(self.sportsbook, "sportsbook"))
        object.__setattr__(self, "market", _normalize_market(self.market))
        object.__setattr__(self, "side", _normalize_side(self.side, "side"))
        object.__setattr__(self, "line", _require_nonnegative_number(self.line, "line"))
        american = _require_american_odds(self.american_odds, "american_odds")
        object.__setattr__(self, "american_odds", american)
        object.__setattr__(self, "decimal_odds", decimal_odds_from_american(american))
        object.__setattr__(
            self,
            "implied_probability",
            implied_probability_from_american(american),
        )
        object.__setattr__(
            self,
            "market_timestamp_utc",
            _require_utc_datetime(self.market_timestamp_utc, "market_timestamp_utc"),
        )
        object.__setattr__(
            self,
            "unsupported_field_reasons",
            _normalized_unsupported_reasons(
                self.unsupported_field_reasons,
                THE_ODDS_API_NBA_CAPABILITY,
            ),
        )

    @property
    def unsupported_fields(self) -> tuple[str, ...]:
        return tuple(self.unsupported_field_reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "operating_date": self.operating_date.isoformat(),
            "operating_timezone": self.operating_timezone,
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "team": self.team,
            "opponent": self.opponent,
            "player_name": self.player_name,
            "normalized_player_name": self.normalized_player_name,
            "sportsbook": self.sportsbook,
            "market": self.market,
            "side": self.side,
            "line": self.line,
            "american_odds": self.american_odds,
            "decimal_odds": self.decimal_odds,
            "implied_probability": self.implied_probability,
            "market_timestamp_utc": _format_utc(self.market_timestamp_utc),
            "unsupported_fields": dict(self.unsupported_field_reasons),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsFinalStatEvidence:
    """One final-stat row from an offline result fixture."""

    provider_name: str
    provider_event_id: str
    canonical_event_id: str
    operating_date: date
    operating_timezone: str
    commence_time_utc: datetime
    team: str
    opponent: str
    player_id: str
    player_name: str
    normalized_player_name: str
    final_points: float
    actual_minutes: float
    stats_timestamp_utc: datetime
    unsupported_field_reasons: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _require_text(self.provider_name, "provider_name"))
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
        object.__setattr__(self, "operating_timezone", NBA_PLAYER_POINTS_OPERATING_TIMEZONE)
        object.__setattr__(
            self,
            "commence_time_utc",
            _require_utc_datetime(self.commence_time_utc, "commence_time_utc"),
        )
        expected_date = toronto_operating_date(self.commence_time_utc)
        if self.operating_date != expected_date:
            raise NBAPlayerPointsResearchSchemaError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        object.__setattr__(self, "team", _normalize_team(self.team, "team"))
        object.__setattr__(self, "opponent", _normalize_team(self.opponent, "opponent"))
        object.__setattr__(
            self,
            "player_id",
            _require_identifier(self.player_id, "player_id"),
        )
        player_name = _require_text(self.player_name, "player_name")
        object.__setattr__(self, "player_name", player_name)
        object.__setattr__(self, "normalized_player_name", normalize_player_name(player_name))
        object.__setattr__(
            self,
            "final_points",
            _require_nonnegative_number(self.final_points, "final_points"),
        )
        object.__setattr__(
            self,
            "actual_minutes",
            _require_nonnegative_number(self.actual_minutes, "actual_minutes"),
        )
        object.__setattr__(
            self,
            "stats_timestamp_utc",
            _require_utc_datetime(self.stats_timestamp_utc, "stats_timestamp_utc"),
        )
        object.__setattr__(
            self,
            "unsupported_field_reasons",
            _normalized_unsupported_reasons(
                self.unsupported_field_reasons,
                NBA_FINAL_STATS_FIXTURE_CAPABILITY,
            ),
        )

    @property
    def unsupported_fields(self) -> tuple[str, ...]:
        return tuple(self.unsupported_field_reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "operating_date": self.operating_date.isoformat(),
            "operating_timezone": self.operating_timezone,
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "team": self.team,
            "opponent": self.opponent,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "normalized_player_name": self.normalized_player_name,
            "final_points": self.final_points,
            "actual_minutes": self.actual_minutes,
            "stats_timestamp_utc": _format_utc(self.stats_timestamp_utc),
            "unsupported_fields": dict(self.unsupported_field_reasons),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsProviderMappingResult:
    """Pure mapper result with provider support metadata attached."""

    provider: NBAPlayerPointsProviderCapability
    rows: tuple[NBAPlayerPointsMarketEvidence | NBAPlayerPointsFinalStatEvidence, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "warnings", _tuple_of_text(self.warnings, "warnings"))


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsPredictionFeatures:
    """Pregame feature values required by the research row contract."""

    projected_points: float
    projected_minutes: float
    recent_minutes: float
    season_minutes: float
    points_per_minute: float
    lineup_status: str
    injury_status: str
    feature_timestamp_utc: datetime
    feature_source: str

    def __post_init__(self) -> None:
        for field_name in (
            "projected_points",
            "projected_minutes",
            "recent_minutes",
            "season_minutes",
            "points_per_minute",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_nonnegative_number(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "lineup_status", _require_text(self.lineup_status, "lineup_status"))
        object.__setattr__(self, "injury_status", _require_text(self.injury_status, "injury_status"))
        object.__setattr__(
            self,
            "feature_timestamp_utc",
            _require_utc_datetime(self.feature_timestamp_utc, "feature_timestamp_utc"),
        )
        object.__setattr__(self, "feature_source", _require_text(self.feature_source, "feature_source"))


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsResearchRow:
    """Complete validated prediction evidence row for offline research only."""

    schema_version: str
    prediction_id: str
    prediction_run_id: str
    model_id: str
    provider_event_id: str
    canonical_event_id: str
    operating_date: date
    operating_timezone: str
    commence_time_utc: datetime
    team: str
    opponent: str
    player_id: str
    player_name: str
    normalized_player_name: str
    identity_status: str
    identity_source: str
    identity_conflict_reason: str
    sportsbook: str
    market: str
    line: float
    american_odds: int
    decimal_odds: float
    implied_probability: float
    market_timestamp_utc: datetime
    projected_points: float
    projected_minutes: float
    recent_minutes: float
    season_minutes: float
    points_per_minute: float
    lineup_status: str
    injury_status: str
    feature_timestamp_utc: datetime
    feature_source: str
    model_over_probability: float
    model_under_probability: float
    selected_side: str
    model_edge: float
    eligibility_status: str
    exclusion_reason: str
    prediction_timestamp_utc: datetime
    feature_schema_version: str
    repository_commit_sha: str
    source_manifest_id: str
    source_hashes: Mapping[str, str]
    artifact_hash: str = ""
    research_only_label: str = NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL
    research_only: bool = True

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version)
        for field_name in ("prediction_id", "prediction_run_id", "model_id"):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )
        for field_name in ("provider_event_id", "canonical_event_id", "player_id"):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "operating_timezone", NBA_PLAYER_POINTS_OPERATING_TIMEZONE)
        object.__setattr__(
            self,
            "commence_time_utc",
            _require_utc_datetime(self.commence_time_utc, "commence_time_utc"),
        )
        if self.operating_date != toronto_operating_date(self.commence_time_utc):
            raise NBAPlayerPointsResearchSchemaError(
                "operating_date must preserve America/Toronto semantics"
            )
        object.__setattr__(self, "team", _normalize_team(self.team, "team"))
        object.__setattr__(self, "opponent", _normalize_team(self.opponent, "opponent"))
        player_name = _require_text(self.player_name, "player_name")
        object.__setattr__(self, "player_name", player_name)
        object.__setattr__(self, "normalized_player_name", normalize_player_name(player_name))
        if self.identity_status != "resolved":
            raise NBAPlayerPointsResearchSchemaError(
                "complete prediction rows require identity_status='resolved'"
            )
        object.__setattr__(self, "identity_source", _require_text(self.identity_source, "identity_source"))
        if not _clean_text(self.identity_conflict_reason):
            raise NBAPlayerPointsResearchSchemaError("identity_conflict_reason is required")
        object.__setattr__(self, "sportsbook", _require_text(self.sportsbook, "sportsbook"))
        object.__setattr__(self, "market", _normalize_market(self.market))
        object.__setattr__(self, "line", _require_nonnegative_number(self.line, "line"))
        american = _require_american_odds(self.american_odds, "american_odds")
        object.__setattr__(self, "american_odds", american)
        object.__setattr__(self, "decimal_odds", decimal_odds_from_american(american))
        object.__setattr__(self, "implied_probability", implied_probability_from_american(american))
        for field_name in (
            "market_timestamp_utc",
            "feature_timestamp_utc",
            "prediction_timestamp_utc",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_utc_datetime(getattr(self, field_name), field_name),
            )
        for field_name in (
            "projected_points",
            "projected_minutes",
            "recent_minutes",
            "season_minutes",
            "points_per_minute",
            "model_edge",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_finite_number(getattr(self, field_name), field_name),
            )
        if self.projected_minutes < 0:
            raise NBAPlayerPointsResearchSchemaError("projected_minutes must be non-negative")
        if self.projected_minutes == 0:
            raise NBAPlayerPointsResearchSchemaError("projected_minutes is required")
        for field_name in ("model_over_probability", "model_under_probability"):
            object.__setattr__(
                self,
                field_name,
                _require_probability(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "selected_side", _normalize_side(self.selected_side, "selected_side"))
        for field_name in (
            "lineup_status",
            "injury_status",
            "feature_source",
            "feature_schema_version",
            "source_manifest_id",
            "eligibility_status",
            "exclusion_reason",
        ):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), field_name))
        commit_sha = _require_text(self.repository_commit_sha, "repository_commit_sha").casefold()
        if _COMMIT_SHA_RE.fullmatch(commit_sha) is None:
            raise NBAPlayerPointsResearchSchemaError(
                "repository_commit_sha must be a 7-40 character lowercase git SHA"
            )
        object.__setattr__(self, "repository_commit_sha", commit_sha)
        object.__setattr__(self, "source_hashes", _validate_source_hashes(self.source_hashes))
        if self.research_only_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
            raise NBAPlayerPointsResearchSchemaError("research_only_label is unsupported")
        if self.research_only is not True:
            raise NBAPlayerPointsResearchSchemaError("research_only must be true")
        object.__setattr__(self, "artifact_hash", _canonical_payload_sha256(self._to_payload(False)))

    @property
    def prediction_identity(self) -> tuple[str, str, str]:
        return (self.prediction_id, self.prediction_run_id, self.model_id)

    def _to_payload(self, include_artifact_hash: bool) -> dict[str, object]:
        payload = {
            "schema_version": self.schema_version,
            "prediction_id": self.prediction_id,
            "prediction_run_id": self.prediction_run_id,
            "model_id": self.model_id,
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "operating_date": self.operating_date.isoformat(),
            "operating_timezone": self.operating_timezone,
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "team": self.team,
            "opponent": self.opponent,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "normalized_player_name": self.normalized_player_name,
            "identity_status": self.identity_status,
            "identity_source": self.identity_source,
            "identity_conflict_reason": self.identity_conflict_reason,
            "sportsbook": self.sportsbook,
            "market": self.market,
            "line": self.line,
            "american_odds": self.american_odds,
            "decimal_odds": self.decimal_odds,
            "implied_probability": self.implied_probability,
            "market_timestamp_utc": _format_utc(self.market_timestamp_utc),
            "projected_points": self.projected_points,
            "projected_minutes": self.projected_minutes,
            "recent_minutes": self.recent_minutes,
            "season_minutes": self.season_minutes,
            "points_per_minute": self.points_per_minute,
            "lineup_status": self.lineup_status,
            "injury_status": self.injury_status,
            "feature_timestamp_utc": _format_utc(self.feature_timestamp_utc),
            "feature_source": self.feature_source,
            "model_over_probability": self.model_over_probability,
            "model_under_probability": self.model_under_probability,
            "selected_side": self.selected_side,
            "model_edge": self.model_edge,
            "eligibility_status": self.eligibility_status,
            "exclusion_reason": self.exclusion_reason,
            "prediction_timestamp_utc": _format_utc(self.prediction_timestamp_utc),
            "feature_schema_version": self.feature_schema_version,
            "repository_commit_sha": self.repository_commit_sha,
            "source_manifest_id": self.source_manifest_id,
            "source_hashes": dict(self.source_hashes),
            "research_only_label": self.research_only_label,
            "research_only": self.research_only,
        }
        if include_artifact_hash:
            payload["artifact_hash"] = self.artifact_hash
        return payload

    def to_dict(self) -> dict[str, object]:
        return self._to_payload(True)


def schema_definition() -> dict[str, object]:
    """Return the versioned schema contract without invoking any runtime path."""

    return {
        "schema_version": NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION,
        "market": NBA_PLAYER_POINTS_MARKET,
        "operating_timezone": NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
        "utc_timestamp_fields": list(UTC_TIMESTAMP_FIELDS),
        "required_fields": list(NBA_PLAYER_POINTS_RESEARCH_ROW_FIELDS),
        "research_only_label": NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    }


def provider_capability_matrix() -> dict[str, dict[str, object]]:
    """Return explicit provider field support for offline fixture mappers."""

    return {
        capability.provider_name: capability.to_matrix_row()
        for capability in NBA_PLAYER_POINTS_PROVIDER_CAPABILITIES
    }


def validate_schema_version(schema_version: object) -> str:
    value = _require_text(schema_version, "schema_version")
    if value != NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION:
        raise NBAPlayerPointsResearchSchemaError(
            f"unsupported schema_version: {value!r}"
        )
    return value


def normalize_player_name(value: object) -> str:
    text = _require_text(value, "player_name")
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    normalized = re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        raise NBAPlayerPointsResearchSchemaError("normalized_player_name is required")
    return normalized


def toronto_operating_date(commence_time_utc: datetime) -> date:
    utc_value = _require_utc_datetime(commence_time_utc, "commence_time_utc")
    return _convert_utc_to_toronto(utc_value).date()


def decimal_odds_from_american(american_odds: object) -> float:
    american = _require_american_odds(american_odds, "american_odds")
    if american > 0:
        return round(1.0 + american / 100.0, 6)
    return round(1.0 + 100.0 / abs(american), 6)


def implied_probability_from_american(american_odds: object) -> float:
    american = _require_american_odds(american_odds, "american_odds")
    if american > 0:
        probability = 100.0 / (american + 100.0)
    else:
        probability = abs(american) / (abs(american) + 100.0)
    return round(probability, 6)


def build_prediction_features(payload: Mapping[str, object]) -> NBAPlayerPointsPredictionFeatures:
    """Build feature evidence without aliasing legacy minutes fields."""

    if "projected_minutes" not in payload:
        if "min_avg" in payload:
            raise NBAPlayerPointsResearchSchemaError(
                "projected_minutes is required; min_avg is not accepted as projected_minutes"
            )
        raise NBAPlayerPointsResearchSchemaError("projected_minutes is required")
    return NBAPlayerPointsPredictionFeatures(
        projected_points=_required_mapping_value(payload, "projected_points"),
        projected_minutes=payload["projected_minutes"],
        recent_minutes=_required_mapping_value(payload, "recent_minutes"),
        season_minutes=_required_mapping_value(payload, "season_minutes"),
        points_per_minute=_required_mapping_value(payload, "points_per_minute"),
        lineup_status=_required_mapping_value(payload, "lineup_status"),
        injury_status=_required_mapping_value(payload, "injury_status"),
        feature_timestamp_utc=_parse_utc_timestamp(
            _required_mapping_value(payload, "feature_timestamp_utc"),
            "feature_timestamp_utc",
        ),
        feature_source=_required_mapping_value(payload, "feature_source"),
    )


def map_the_odds_api_player_points_fixture(
    payload: Mapping[str, object],
) -> NBAPlayerPointsProviderMappingResult:
    """Map one The Odds API event-odds fixture into market evidence rows."""

    provider_event_id = _require_identifier(payload.get("id"), "provider_event_id")
    commence_time = _parse_utc_timestamp(payload.get("commence_time"), "commence_time_utc")
    operating_date = toronto_operating_date(commence_time)
    rows: list[NBAPlayerPointsMarketEvidence] = []
    warnings: list[str] = []
    bookmakers = payload.get("bookmakers")
    if not isinstance(bookmakers, list):
        raise NBAPlayerPointsResearchSchemaError("bookmakers must be a list")
    for bookmaker_index, bookmaker in enumerate(bookmakers):
        if not isinstance(bookmaker, Mapping):
            warnings.append(f"bookmakers[{bookmaker_index}] ignored: not an object")
            continue
        sportsbook = _require_text(
            _first_value(bookmaker.get("title"), bookmaker.get("key")),
            f"bookmakers[{bookmaker_index}].sportsbook",
        )
        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            warnings.append(f"bookmakers[{bookmaker_index}].markets ignored: not a list")
            continue
        for market_index, market in enumerate(markets):
            if not isinstance(market, Mapping):
                warnings.append(f"bookmakers[{bookmaker_index}].markets[{market_index}] ignored: not an object")
                continue
            market_key = _normalize_market(market.get("key"))
            if market_key != NBA_PLAYER_POINTS_MARKET:
                warnings.append(f"unsupported market ignored: {market_key}")
                continue
            market_timestamp = _parse_utc_timestamp(
                _first_value(market.get("last_update"), bookmaker.get("last_update")),
                "market_timestamp_utc",
            )
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, list):
                raise NBAPlayerPointsResearchSchemaError("outcomes must be a list")
            for outcome_index, outcome in enumerate(outcomes):
                if not isinstance(outcome, Mapping):
                    warnings.append(
                        f"bookmakers[{bookmaker_index}].markets[{market_index}].outcomes[{outcome_index}] ignored: not an object"
                    )
                    continue
                player_name = _require_text(
                    _first_value(outcome.get("description"), outcome.get("player_name")),
                    "player_name",
                )
                rows.append(
                    NBAPlayerPointsMarketEvidence(
                        provider_name=THE_ODDS_API_NBA_CAPABILITY.provider_name,
                        provider_event_id=provider_event_id,
                        canonical_event_id=None,
                        operating_date=operating_date,
                        operating_timezone=NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
                        commence_time_utc=commence_time,
                        team=_required_mapping_value(outcome, "team"),
                        opponent=_required_mapping_value(outcome, "opponent"),
                        player_name=player_name,
                        normalized_player_name=normalize_player_name(player_name),
                        sportsbook=sportsbook,
                        market=market_key,
                        side=_required_mapping_value(outcome, "name"),
                        line=_required_mapping_value(outcome, "point"),
                        american_odds=_required_mapping_value(outcome, "price"),
                        decimal_odds=0.0,
                        implied_probability=0.0,
                        market_timestamp_utc=market_timestamp,
                        unsupported_field_reasons=THE_ODDS_API_NBA_CAPABILITY.unsupported_field_reasons,
                    )
                )
    return NBAPlayerPointsProviderMappingResult(
        provider=THE_ODDS_API_NBA_CAPABILITY,
        rows=tuple(rows),
        warnings=tuple(warnings),
    )


def map_final_stats_provider_fixture(
    payload: Mapping[str, object],
) -> NBAPlayerPointsProviderMappingResult:
    """Map one final-stat fixture into identity and outcome evidence rows."""

    provider_name = _require_text(
        payload.get("provider_name", NBA_FINAL_STATS_FIXTURE_CAPABILITY.provider_name),
        "provider_name",
    ).casefold()
    if provider_name != NBA_FINAL_STATS_FIXTURE_CAPABILITY.provider_name:
        raise NBAPlayerPointsResearchSchemaError("unsupported final-stat fixture provider")
    stats_timestamp = _parse_utc_timestamp(
        _required_mapping_value(payload, "stats_timestamp_utc"),
        "stats_timestamp_utc",
    )
    games = payload.get("games")
    if not isinstance(games, list):
        raise NBAPlayerPointsResearchSchemaError("games must be a list")
    rows: list[NBAPlayerPointsFinalStatEvidence] = []
    warnings: list[str] = []
    for game_index, game in enumerate(games):
        if not isinstance(game, Mapping):
            warnings.append(f"games[{game_index}] ignored: not an object")
            continue
        provider_event_id = _require_identifier(game.get("provider_event_id"), "provider_event_id")
        canonical_event_id = _require_identifier(game.get("canonical_event_id"), "canonical_event_id")
        commence_time = _parse_utc_timestamp(game.get("commence_time_utc"), "commence_time_utc")
        operating_date = toronto_operating_date(commence_time)
        players = game.get("players")
        if not isinstance(players, list):
            raise NBAPlayerPointsResearchSchemaError("players must be a list")
        for player_index, player in enumerate(players):
            if not isinstance(player, Mapping):
                warnings.append(f"games[{game_index}].players[{player_index}] ignored: not an object")
                continue
            player_name = _require_text(_required_mapping_value(player, "player_name"), "player_name")
            rows.append(
                NBAPlayerPointsFinalStatEvidence(
                    provider_name=NBA_FINAL_STATS_FIXTURE_CAPABILITY.provider_name,
                    provider_event_id=provider_event_id,
                    canonical_event_id=canonical_event_id,
                    operating_date=operating_date,
                    operating_timezone=NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
                    commence_time_utc=commence_time,
                    team=_required_mapping_value(player, "team"),
                    opponent=_required_mapping_value(player, "opponent"),
                    player_id=_required_mapping_value(player, "player_id"),
                    player_name=player_name,
                    normalized_player_name=normalize_player_name(player_name),
                    final_points=_required_mapping_value(player, "final_points"),
                    actual_minutes=_required_mapping_value(player, "actual_minutes"),
                    stats_timestamp_utc=stats_timestamp,
                    unsupported_field_reasons=NBA_FINAL_STATS_FIXTURE_CAPABILITY.unsupported_field_reasons,
                )
            )
    return NBAPlayerPointsProviderMappingResult(
        provider=NBA_FINAL_STATS_FIXTURE_CAPABILITY,
        rows=tuple(rows),
        warnings=tuple(warnings),
    )


def resolve_final_stat_for_market(
    market: NBAPlayerPointsMarketEvidence,
    final_stats: tuple[NBAPlayerPointsFinalStatEvidence, ...],
) -> NBAPlayerPointsFinalStatEvidence:
    """Resolve a market row to exactly one final-stat identity row."""

    candidates = [
        row
        for row in final_stats
        if row.normalized_player_name == market.normalized_player_name
        and row.operating_date == market.operating_date
        and row.team == market.team
        and row.opponent == market.opponent
    ]
    if not candidates:
        raise NBAPlayerPointsResearchSchemaError(
            "player_id is unavailable: no final-stat fixture identity match"
        )
    player_ids = {candidate.player_id for candidate in candidates}
    if len(candidates) > 1 or len(player_ids) > 1:
        raise NBAPlayerPointsResearchSchemaError(
            "ambiguous identity: multiple final-stat rows match market evidence"
        )
    return candidates[0]


def build_research_prediction_row(
    *,
    prediction_id: str,
    prediction_run_id: str,
    model_id: str,
    market: NBAPlayerPointsMarketEvidence,
    final_stats: tuple[NBAPlayerPointsFinalStatEvidence, ...],
    features: NBAPlayerPointsPredictionFeatures | Mapping[str, object],
    outputs: Mapping[str, object],
    provenance: Mapping[str, object],
    schema_version: str = NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION,
) -> NBAPlayerPointsResearchRow:
    """Construct one complete schema row from explicit offline evidence."""

    validate_schema_version(schema_version)
    resolved_stats = resolve_final_stat_for_market(market, tuple(final_stats))
    feature_values = (
        build_prediction_features(features)
        if isinstance(features, Mapping)
        else features
    )
    if not isinstance(feature_values, NBAPlayerPointsPredictionFeatures):
        raise TypeError("features must be NBAPlayerPointsPredictionFeatures or a mapping")

    return NBAPlayerPointsResearchRow(
        schema_version=schema_version,
        prediction_id=prediction_id,
        prediction_run_id=prediction_run_id,
        model_id=model_id,
        provider_event_id=market.provider_event_id,
        canonical_event_id=resolved_stats.canonical_event_id,
        operating_date=market.operating_date,
        operating_timezone=NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
        commence_time_utc=market.commence_time_utc,
        team=market.team,
        opponent=market.opponent,
        player_id=resolved_stats.player_id,
        player_name=market.player_name,
        normalized_player_name=market.normalized_player_name,
        identity_status="resolved",
        identity_source=resolved_stats.provider_name,
        identity_conflict_reason="none",
        sportsbook=market.sportsbook,
        market=market.market,
        line=market.line,
        american_odds=market.american_odds,
        decimal_odds=market.decimal_odds,
        implied_probability=market.implied_probability,
        market_timestamp_utc=market.market_timestamp_utc,
        projected_points=feature_values.projected_points,
        projected_minutes=feature_values.projected_minutes,
        recent_minutes=feature_values.recent_minutes,
        season_minutes=feature_values.season_minutes,
        points_per_minute=feature_values.points_per_minute,
        lineup_status=feature_values.lineup_status,
        injury_status=feature_values.injury_status,
        feature_timestamp_utc=feature_values.feature_timestamp_utc,
        feature_source=feature_values.feature_source,
        model_over_probability=_required_mapping_value(outputs, "model_over_probability"),
        model_under_probability=_required_mapping_value(outputs, "model_under_probability"),
        selected_side=_required_mapping_value(outputs, "selected_side"),
        model_edge=_required_mapping_value(outputs, "model_edge"),
        eligibility_status=_required_mapping_value(outputs, "eligibility_status"),
        exclusion_reason=_required_mapping_value(outputs, "exclusion_reason"),
        prediction_timestamp_utc=_parse_utc_timestamp(
            _required_mapping_value(provenance, "prediction_timestamp_utc"),
            "prediction_timestamp_utc",
        ),
        feature_schema_version=_required_mapping_value(provenance, "feature_schema_version"),
        repository_commit_sha=_required_mapping_value(provenance, "repository_commit_sha"),
        source_manifest_id=_required_mapping_value(provenance, "source_manifest_id"),
        source_hashes=_required_mapping_value(provenance, "source_hashes"),
    )


def validate_prediction_rows(
    rows: tuple[NBAPlayerPointsResearchRow, ...] | list[NBAPlayerPointsResearchRow],
) -> tuple[NBAPlayerPointsResearchRow, ...]:
    """Validate row collection identity without writing ledgers."""

    normalized = tuple(rows)
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(normalized):
        if not isinstance(row, NBAPlayerPointsResearchRow):
            raise TypeError("rows must contain NBAPlayerPointsResearchRow values")
        identity = row.prediction_identity
        if identity in seen:
            raise NBAPlayerPointsResearchSchemaError(
                f"duplicate prediction identity at rows[{index}]"
            )
        seen.add(identity)
    return normalized


def _parse_utc_timestamp(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _require_utc_datetime(value, field_name)
    if not isinstance(value, str) or not value.strip():
        raise NBAPlayerPointsResearchSchemaError(
            f"{field_name} must be an ISO-8601 UTC timestamp"
        )
    text = value.strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise NBAPlayerPointsResearchSchemaError(
            f"{field_name} must be an ISO-8601 UTC timestamp"
        ) from exc
    return _require_utc_datetime(parsed, field_name)


def _require_utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be UTC")
    return value.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _require_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _resolve_toronto_timezone() -> ZoneInfo | object:
    try:
        return ZoneInfo(NBA_PLAYER_POINTS_OPERATING_TIMEZONE)
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


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _tuple_of_text(values: tuple[str, ...] | list[str] | object, field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be a sequence of strings")
    return tuple(_require_text(value, field_name) for value in values)


def _first_value(*values: object) -> object:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _required_mapping_value(payload: Mapping[str, object], field_name: str) -> object:
    if field_name not in payload:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} is required")
    return payload[field_name]


def _normalize_team(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    text = re.sub(r"\s+", " ", text).strip().upper()
    if not text:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} is required")
    return text


def _normalize_market(value: object) -> str:
    text = _require_text(value, "market").casefold().strip()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if normalized != NBA_PLAYER_POINTS_MARKET:
        raise NBAPlayerPointsResearchSchemaError(
            f"unsupported market for this contract: {normalized!r}"
        )
    return normalized


def _normalize_side(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if text not in {"over", "under"}:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be over or under")
    return text


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be finite")
    return parsed


def _require_nonnegative_number(value: object, field_name: str) -> float:
    parsed = _require_finite_number(value, field_name)
    if parsed < 0:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be non-negative")
    return parsed


def _require_probability(value: object, field_name: str) -> float:
    parsed = _require_finite_number(value, field_name)
    if not 0.0 <= parsed <= 1.0:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be within [0, 1]")
    return parsed


def _require_american_odds(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} must be an integer")
    if value == 0:
        raise NBAPlayerPointsResearchSchemaError(f"{field_name} cannot be 0")
    return value


def _normalized_unsupported_reasons(
    reasons: Mapping[str, str],
    capability: NBAPlayerPointsProviderCapability,
) -> Mapping[str, str]:
    normalized = {
        _require_text(field_name, "unsupported field name"): _require_text(
            reason,
            f"unsupported_field_reasons.{field_name}",
        )
        for field_name, reason in dict(reasons).items()
    }
    for field_name, reason in capability.unsupported_field_reasons.items():
        normalized.setdefault(field_name, reason)
    return MappingProxyType(normalized)


def _validate_source_hashes(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise NBAPlayerPointsResearchSchemaError("source_hashes must be a non-empty mapping")
    normalized: dict[str, str] = {}
    for source_name, digest in value.items():
        name = _require_text(source_name, "source_hashes key")
        hash_text = _require_text(digest, f"source_hashes.{name}").casefold()
        if _SHA256_RE.fullmatch(hash_text) is None:
            raise NBAPlayerPointsResearchSchemaError(
                f"source_hashes.{name} must be lowercase SHA-256"
            )
        normalized[name] = hash_text
    return MappingProxyType(normalized)


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


THE_ODDS_API_NBA_CAPABILITY: Final = NBAPlayerPointsProviderCapability(
    provider_name="the_odds_api_nba",
    provider_role="market_odds",
    source_type="fixture",
    mode="offline_test_fixture",
    supports_live_calls=False,
    available_fields=(
        "provider_event_id",
        "operating_date",
        "commence_time_utc",
        "team",
        "opponent",
        "player_name",
        "normalized_player_name",
        "sportsbook",
        "market",
        "line",
        "american_odds",
        "decimal_odds",
        "implied_probability",
        "market_timestamp_utc",
    ),
    unsupported_field_reasons={
        "canonical_event_id": "odds fixture exposes provider event IDs only",
        "player_id": "odds fixture exposes player display names, not player IDs",
        "final_points": "odds fixture does not contain settled player stats",
        "actual_minutes": "odds fixture does not contain settled player stats",
    },
)

NBA_FINAL_STATS_FIXTURE_CAPABILITY: Final = NBAPlayerPointsProviderCapability(
    provider_name="nba_final_stats_fixture",
    provider_role="final_stats",
    source_type="fixture",
    mode="offline_test_fixture",
    supports_live_calls=False,
    available_fields=(
        "provider_event_id",
        "canonical_event_id",
        "operating_date",
        "commence_time_utc",
        "team",
        "opponent",
        "player_id",
        "player_name",
        "normalized_player_name",
        "final_points",
        "actual_minutes",
        "stats_timestamp_utc",
    ),
    unsupported_field_reasons={
        "sportsbook": "final-stat fixture does not contain sportsbook markets",
        "market": "final-stat fixture does not contain sportsbook markets",
        "line": "final-stat fixture does not contain sportsbook lines",
        "american_odds": "final-stat fixture does not contain sportsbook prices",
        "decimal_odds": "final-stat fixture does not contain sportsbook prices",
        "implied_probability": "final-stat fixture does not contain sportsbook prices",
        "market_timestamp_utc": "final-stat fixture does not contain market timestamps",
    },
)

NBA_PLAYER_POINTS_PROVIDER_CAPABILITIES: Final = (
    THE_ODDS_API_NBA_CAPABILITY,
    NBA_FINAL_STATS_FIXTURE_CAPABILITY,
)


__all__ = [
    "NBA_FINAL_STATS_FIXTURE_CAPABILITY",
    "NBA_PLAYER_POINTS_MARKET",
    "NBA_PLAYER_POINTS_OPERATING_TIMEZONE",
    "NBA_PLAYER_POINTS_PROVIDER_CAPABILITIES",
    "NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL",
    "NBA_PLAYER_POINTS_RESEARCH_ROW_FIELDS",
    "NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION",
    "NBAPlayerPointsFinalStatEvidence",
    "NBAPlayerPointsMarketEvidence",
    "NBAPlayerPointsPredictionFeatures",
    "NBAPlayerPointsProviderCapability",
    "NBAPlayerPointsProviderMappingResult",
    "NBAPlayerPointsResearchRow",
    "NBAPlayerPointsResearchSchemaError",
    "THE_ODDS_API_NBA_CAPABILITY",
    "UTC_TIMESTAMP_FIELDS",
    "build_prediction_features",
    "build_research_prediction_row",
    "decimal_odds_from_american",
    "implied_probability_from_american",
    "map_final_stats_provider_fixture",
    "map_the_odds_api_player_points_fixture",
    "normalize_player_name",
    "provider_capability_matrix",
    "resolve_final_stat_for_market",
    "schema_definition",
    "toronto_operating_date",
    "validate_prediction_rows",
    "validate_schema_version",
]
