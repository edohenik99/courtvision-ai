"""Offline NBA player-points pregame assembly contract.

This module combines already-normalized, offline evidence records into
research-only NBA player-points rows. It performs no provider I/O, reads no
credentials, writes no files, creates no ledgers, and does not invoke the
production prediction engine, scoring, selection, Kelly, grading, or dashboard
paths.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Final

from courtvision.sports.nba.player_minutes_research import (
    NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION,
)
from courtvision.sports.nba.player_points_crosswalk import (
    NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_MARKET,
    NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    NBA_PLAYER_POINTS_RESEARCH_ROW_FIELDS,
    NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION,
    NBAPlayerPointsResearchSchemaError,
    decimal_odds_from_american,
    implied_probability_from_american,
    normalize_player_name,
    toronto_operating_date,
)


NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION: Final = "nba-player-points-assembly-v1"
NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION: Final = "nba-player-points-projection-v1"
NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION: Final = "nba-player-points-market-v1"
NBA_PLAYER_POINTS_PROBABILITY_SCHEMA_VERSION: Final = "nba-player-points-probability-v1"
NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION: Final = (
    "nba-player-points-source-manifest-preview-v1"
)

NBA_PLAYER_POINTS_ASSEMBLY_STATUSES: Final = (
    "eligible_projection_research",
    "eligible_probability_research",
    "excluded",
    "quarantined",
    "conflicting",
)
NBA_PLAYER_POINTS_PROBABILITY_STATUSES: Final = (
    "unavailable",
    "valid",
    "incomplete",
    "malformed",
)
NBA_PLAYER_POINTS_ASSEMBLY_UTC_TIMESTAMP_FIELDS: Final = (
    "commence_time_utc",
    "market_timestamp_utc",
    "feature_timestamp_utc",
    "feature_cutoff_timestamp_utc",
    "projection_timestamp_utc",
    "projection_cutoff_timestamp_utc",
    "prediction_timestamp_utc",
    "probability_timestamp_utc",
)
NBA_PLAYER_POINTS_ASSEMBLY_ROW_FIELDS: Final = (
    *NBA_PLAYER_POINTS_RESEARCH_ROW_FIELDS,
    "assembly_schema_version",
    "assembly_status",
    "assembly_exclusion_reason",
    "projection_research_eligible",
    "probability_research_eligible",
    "probability_status",
    "probability_model_id",
    "probability_source_id",
    "probability_schema_version",
    "probability_source_hash",
    "probability_timestamp_utc",
    "probability_based_edge",
    "market_status",
    "minutes_status",
    "projection_status",
    "source_manifest_hash",
    "assembled_record_hash",
    "directional_diagnostic_label",
    "projection_line_difference",
    "projected_points_above_line",
    "projected_points_below_line",
    "diagnostics",
)

NBA_PLAYER_POINTS_PROJECTION_RESEARCH_MINUTES_STATUSES: Final = ("projected",)
NBA_PLAYER_POINTS_DIAGNOSTIC_ONLY_MINUTES_STATUSES: Final = ("lineup_unconfirmed",)
NBA_PLAYER_POINTS_PROBABILITY_SUM_TOLERANCE: Final = 1e-6

_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_ZERO_HASH: Final = "0" * 64
_NONE: Final = object()
_LEAKAGE_FIELDS: Final = (
    "actual_points",
    "final_points",
    "target_game_actual_points",
    "target_game_final_points",
    "actual_minutes",
    "target_game_actual_minutes",
    "final_stats",
    "box_score",
)
_STATUS_SORT_ORDER: Final = {
    "eligible_probability_research": 0,
    "eligible_projection_research": 1,
    "excluded": 2,
    "quarantined": 3,
    "conflicting": 4,
}


class NBAPlayerPointsAssemblyContractError(NBAPlayerPointsResearchSchemaError):
    """Raised when the offline assembly contract fails closed."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsProjectionEvidence:
    """Strict offline pregame player-points projection evidence."""

    projected_points: float
    projection_method: str
    projection_timestamp_utc: datetime
    projection_cutoff_timestamp_utc: datetime
    projection_source: str
    projection_source_id: str
    projection_source_hash: str
    projection_schema_version: str
    raw_evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _reject_target_game_leakage(self.raw_evidence, "projection_evidence")
        object.__setattr__(
            self,
            "projected_points",
            _require_nonnegative_number(self.projected_points, "projected_points"),
        )
        object.__setattr__(
            self,
            "projection_method",
            _require_text(self.projection_method, "projection_method"),
        )
        object.__setattr__(
            self,
            "projection_timestamp_utc",
            _coerce_utc_datetime(
                self.projection_timestamp_utc,
                "projection_timestamp_utc",
            ),
        )
        object.__setattr__(
            self,
            "projection_cutoff_timestamp_utc",
            _coerce_utc_datetime(
                self.projection_cutoff_timestamp_utc,
                "projection_cutoff_timestamp_utc",
            ),
        )
        if self.projection_timestamp_utc > self.projection_cutoff_timestamp_utc:
            raise NBAPlayerPointsAssemblyContractError(
                "projection_timestamp_utc must be at or before projection cutoff"
            )
        object.__setattr__(
            self,
            "projection_source",
            _require_text(self.projection_source, "projection_source"),
        )
        object.__setattr__(
            self,
            "projection_source_id",
            _require_identifier(self.projection_source_id, "projection_source_id"),
        )
        object.__setattr__(
            self,
            "projection_source_hash",
            _require_sha256(self.projection_source_hash, "projection_source_hash"),
        )
        schema_version = _require_text(
            self.projection_schema_version,
            "projection_schema_version",
        )
        if schema_version != NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION:
            raise NBAPlayerPointsAssemblyContractError(
                f"unsupported projection_schema_version: {schema_version!r}"
            )
        object.__setattr__(self, "projection_schema_version", schema_version)
        object.__setattr__(
            self,
            "raw_evidence",
            MappingProxyType(_json_clone_mapping(self.raw_evidence)),
        )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
        *,
        commence_time_utc: datetime | None = None,
    ) -> "NBAPlayerPointsProjectionEvidence":
        """Build strict projection evidence from an offline normalized mapping."""

        evidence = cls(
            projected_points=_required_mapping_value(payload, "projected_points"),
            projection_method=_required_mapping_value(payload, "projection_method"),
            projection_timestamp_utc=_coerce_utc_datetime(
                _required_mapping_value(payload, "projection_timestamp_utc"),
                "projection_timestamp_utc",
            ),
            projection_cutoff_timestamp_utc=_coerce_utc_datetime(
                _required_mapping_value(payload, "projection_cutoff_timestamp_utc"),
                "projection_cutoff_timestamp_utc",
            ),
            projection_source=_required_mapping_value(payload, "projection_source"),
            projection_source_id=_required_mapping_value(payload, "projection_source_id"),
            projection_source_hash=_required_mapping_value(payload, "projection_source_hash"),
            projection_schema_version=_required_mapping_value(
                payload,
                "projection_schema_version",
            ),
            raw_evidence=payload,
        )
        if commence_time_utc is not None and evidence.projection_timestamp_utc >= commence_time_utc:
            raise NBAPlayerPointsAssemblyContractError(
                "projection_timestamp_utc must be before tipoff"
            )
        return evidence

    def to_dict(self) -> dict[str, object]:
        return {
            "projected_points": self.projected_points,
            "projection_method": self.projection_method,
            "projection_timestamp_utc": _format_utc(self.projection_timestamp_utc),
            "projection_cutoff_timestamp_utc": _format_utc(
                self.projection_cutoff_timestamp_utc
            ),
            "projection_source": self.projection_source,
            "projection_source_id": self.projection_source_id,
            "projection_source_hash": self.projection_source_hash,
            "projection_schema_version": self.projection_schema_version,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsProbabilityValidation:
    """Nullable probability evidence validation result."""

    probability_status: str
    model_over_probability: float | None = None
    model_under_probability: float | None = None
    probability_model_id: str | None = None
    probability_source_id: str | None = None
    probability_schema_version: str | None = None
    probability_source_hash: str | None = None
    probability_timestamp_utc: datetime | None = None
    claims_probability_eligibility: bool = False
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.probability_status not in NBA_PLAYER_POINTS_PROBABILITY_STATUSES:
            raise NBAPlayerPointsAssemblyContractError(
                f"unsupported probability_status: {self.probability_status!r}"
            )
        object.__setattr__(
            self,
            "diagnostics",
            tuple(_require_text(item, "probability diagnostics") for item in self.diagnostics),
        )

    @property
    def probability_research_eligible(self) -> bool:
        return self.probability_status == "valid"


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsAssembledRow:
    """Research-only assembled row using the player-points row contract as base."""

    schema_version: str
    prediction_id: str
    prediction_run_id: str
    model_id: str
    provider_event_id: str
    canonical_event_id: str | None
    operating_date: date | None
    operating_timezone: str
    commence_time_utc: datetime | None
    team: str | None
    opponent: str | None
    player_id: str | None
    player_name: str
    normalized_player_name: str
    identity_status: str
    identity_source: str
    identity_conflict_reason: str
    sportsbook: str | None
    market: str | None
    line: float | None
    american_odds: int | None
    decimal_odds: float | None
    implied_probability: float | None
    market_timestamp_utc: datetime | None
    projected_points: float | None
    projected_minutes: float | None
    recent_minutes: float | None
    season_minutes: float | None
    points_per_minute: float | None
    lineup_status: str | None
    injury_status: str | None
    feature_timestamp_utc: datetime | None
    feature_source: str | None
    model_over_probability: float | None
    model_under_probability: float | None
    selected_side: str | None
    model_edge: float | None
    eligibility_status: str
    exclusion_reason: str
    prediction_timestamp_utc: datetime
    feature_schema_version: str | None
    repository_commit_sha: str
    source_manifest_id: str
    source_hashes: Mapping[str, str]
    assembly_schema_version: str
    assembly_status: str
    assembly_exclusion_reason: str
    projection_research_eligible: bool
    probability_research_eligible: bool
    probability_status: str
    market_status: str
    minutes_status: str
    projection_status: str
    source_manifest_hash: str
    probability_model_id: str | None = None
    probability_source_id: str | None = None
    probability_schema_version: str | None = None
    probability_source_hash: str | None = None
    probability_timestamp_utc: datetime | None = None
    probability_based_edge: float | None = None
    directional_diagnostic_label: str = "non_probabilistic_projection_line_difference"
    projection_line_difference: float | None = None
    projected_points_above_line: bool | None = None
    projected_points_below_line: bool | None = None
    diagnostics: tuple[str, ...] = ()
    artifact_hash: str = ""
    assembled_record_hash: str = ""
    research_only_label: str = NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION:
            raise NBAPlayerPointsAssemblyContractError(
                f"unsupported schema_version: {self.schema_version!r}"
            )
        if self.assembly_schema_version != NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION:
            raise NBAPlayerPointsAssemblyContractError(
                f"unsupported assembly_schema_version: {self.assembly_schema_version!r}"
            )
        if self.assembly_status not in NBA_PLAYER_POINTS_ASSEMBLY_STATUSES:
            raise NBAPlayerPointsAssemblyContractError(
                f"unsupported assembly_status: {self.assembly_status!r}"
            )
        if self.probability_status not in NBA_PLAYER_POINTS_PROBABILITY_STATUSES:
            raise NBAPlayerPointsAssemblyContractError(
                f"unsupported probability_status: {self.probability_status!r}"
            )
        object.__setattr__(self, "prediction_id", _require_identifier(self.prediction_id, "prediction_id"))
        object.__setattr__(
            self,
            "prediction_run_id",
            _require_identifier(self.prediction_run_id, "prediction_run_id"),
        )
        object.__setattr__(self, "model_id", _require_identifier(self.model_id, "model_id"))
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
        if self.player_id is not None:
            object.__setattr__(self, "player_id", _require_identifier(self.player_id, "player_id"))
        if self.commence_time_utc is not None:
            object.__setattr__(
                self,
                "commence_time_utc",
                _coerce_utc_datetime(self.commence_time_utc, "commence_time_utc"),
            )
        object.__setattr__(self, "operating_timezone", NBA_PLAYER_POINTS_OPERATING_TIMEZONE)
        object.__setattr__(self, "player_name", _require_text(self.player_name, "player_name"))
        object.__setattr__(self, "normalized_player_name", normalize_player_name(self.player_name))
        for field_name in (
            "identity_status",
            "identity_source",
            "identity_conflict_reason",
            "eligibility_status",
            "exclusion_reason",
            "source_manifest_id",
            "market_status",
            "minutes_status",
            "projection_status",
            "directional_diagnostic_label",
        ):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), field_name))
        if self.market is not None:
            market = _normalize_market(self.market)
            object.__setattr__(self, "market", market)
        if self.sportsbook is not None:
            object.__setattr__(self, "sportsbook", _require_text(self.sportsbook, "sportsbook"))
        for field_name in (
            "line",
            "decimal_odds",
            "implied_probability",
            "projected_points",
            "projected_minutes",
            "recent_minutes",
            "season_minutes",
            "points_per_minute",
            "model_edge",
            "probability_based_edge",
            "projection_line_difference",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _require_finite_number(value, field_name))
        if self.line is not None and self.line < 0:
            raise NBAPlayerPointsAssemblyContractError("line must be non-negative")
        if self.american_odds is not None:
            object.__setattr__(
                self,
                "american_odds",
                _require_american_odds(self.american_odds, "american_odds"),
            )
        for field_name in ("model_over_probability", "model_under_probability"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _require_probability(value, field_name))
        for field_name in (
            "market_timestamp_utc",
            "feature_timestamp_utc",
            "prediction_timestamp_utc",
            "probability_timestamp_utc",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _coerce_utc_datetime(value, field_name),
                )
        commit_sha = _require_text(self.repository_commit_sha, "repository_commit_sha").casefold()
        if _COMMIT_SHA_RE.fullmatch(commit_sha) is None:
            raise NBAPlayerPointsAssemblyContractError(
                "repository_commit_sha must be a 7-40 character lowercase git SHA"
            )
        object.__setattr__(self, "repository_commit_sha", commit_sha)
        object.__setattr__(self, "source_hashes", _validate_source_hashes(self.source_hashes))
        object.__setattr__(
            self,
            "source_manifest_hash",
            _require_sha256(self.source_manifest_hash, "source_manifest_hash"),
        )
        if self.probability_source_hash is not None:
            object.__setattr__(
                self,
                "probability_source_hash",
                _require_sha256(self.probability_source_hash, "probability_source_hash"),
            )
        if self.research_only_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
            raise NBAPlayerPointsAssemblyContractError("research_only_label is unsupported")
        if self.research_only is not True:
            raise NBAPlayerPointsAssemblyContractError("research_only must be true")
        object.__setattr__(
            self,
            "diagnostics",
            tuple(_require_text(item, "diagnostics") for item in self.diagnostics),
        )
        digest = _canonical_payload_sha256(self._to_payload(False))
        object.__setattr__(self, "assembled_record_hash", digest)
        object.__setattr__(self, "artifact_hash", digest)

    @property
    def prediction_identity(self) -> tuple[str, str, str]:
        return (self.prediction_id, self.prediction_run_id, self.model_id)

    def _to_payload(self, include_hashes: bool) -> dict[str, object]:
        payload = {
            "schema_version": self.schema_version,
            "prediction_id": self.prediction_id,
            "prediction_run_id": self.prediction_run_id,
            "model_id": self.model_id,
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "operating_date": self.operating_date.isoformat() if self.operating_date else None,
            "operating_timezone": self.operating_timezone,
            "commence_time_utc": _format_optional_utc(self.commence_time_utc),
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
            "market_timestamp_utc": _format_optional_utc(self.market_timestamp_utc),
            "projected_points": self.projected_points,
            "projected_minutes": self.projected_minutes,
            "recent_minutes": self.recent_minutes,
            "season_minutes": self.season_minutes,
            "points_per_minute": self.points_per_minute,
            "lineup_status": self.lineup_status,
            "injury_status": self.injury_status,
            "feature_timestamp_utc": _format_optional_utc(self.feature_timestamp_utc),
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
            "assembly_schema_version": self.assembly_schema_version,
            "assembly_status": self.assembly_status,
            "assembly_exclusion_reason": self.assembly_exclusion_reason,
            "projection_research_eligible": self.projection_research_eligible,
            "probability_research_eligible": self.probability_research_eligible,
            "probability_status": self.probability_status,
            "probability_model_id": self.probability_model_id,
            "probability_source_id": self.probability_source_id,
            "probability_schema_version": self.probability_schema_version,
            "probability_source_hash": self.probability_source_hash,
            "probability_timestamp_utc": _format_optional_utc(self.probability_timestamp_utc),
            "probability_based_edge": self.probability_based_edge,
            "market_status": self.market_status,
            "minutes_status": self.minutes_status,
            "projection_status": self.projection_status,
            "source_manifest_hash": self.source_manifest_hash,
            "directional_diagnostic_label": self.directional_diagnostic_label,
            "projection_line_difference": self.projection_line_difference,
            "projected_points_above_line": self.projected_points_above_line,
            "projected_points_below_line": self.projected_points_below_line,
            "diagnostics": list(self.diagnostics),
        }
        if include_hashes:
            payload["artifact_hash"] = self.artifact_hash
            payload["assembled_record_hash"] = self.assembled_record_hash
        return payload

    def to_dict(self) -> dict[str, object]:
        return self._to_payload(True)


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSourceManifestPreview:
    """In-memory source-manifest preview for one offline assembly batch."""

    manifest_schema_version: str
    prediction_run_id: str
    operating_date: date | None
    created_at_utc: datetime
    repository_commit_sha: str
    source_records: tuple[Mapping[str, object], ...]
    provider_capabilities: tuple[Mapping[str, object], ...] = ()
    manifest_hash: str = ""

    def __post_init__(self) -> None:
        if self.manifest_schema_version != NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION:
            raise NBAPlayerPointsAssemblyContractError(
                f"unsupported manifest_schema_version: {self.manifest_schema_version!r}"
            )
        object.__setattr__(
            self,
            "prediction_run_id",
            _require_identifier(self.prediction_run_id, "prediction_run_id"),
        )
        object.__setattr__(
            self,
            "created_at_utc",
            _coerce_utc_datetime(self.created_at_utc, "created_at_utc"),
        )
        commit_sha = _require_text(self.repository_commit_sha, "repository_commit_sha").casefold()
        if _COMMIT_SHA_RE.fullmatch(commit_sha) is None:
            raise NBAPlayerPointsAssemblyContractError(
                "repository_commit_sha must be a 7-40 character lowercase git SHA"
            )
        object.__setattr__(self, "repository_commit_sha", commit_sha)
        normalized_records = tuple(
            MappingProxyType(_json_clone_mapping(record))
            for record in sorted(
                self.source_records,
                key=lambda item: _canonical_json_text(item),
            )
        )
        object.__setattr__(self, "source_records", normalized_records)
        capabilities = tuple(
            MappingProxyType(_json_clone_mapping(capability))
            for capability in sorted(
                self.provider_capabilities,
                key=lambda item: _canonical_json_text(item),
            )
        )
        object.__setattr__(self, "provider_capabilities", capabilities)
        object.__setattr__(self, "manifest_hash", _canonical_payload_sha256(self._to_payload(False)))

    def _to_payload(self, include_hash: bool) -> dict[str, object]:
        payload = {
            "manifest_schema_version": self.manifest_schema_version,
            "prediction_run_id": self.prediction_run_id,
            "operating_date": self.operating_date.isoformat() if self.operating_date else None,
            "created_at_utc": _format_utc(self.created_at_utc),
            "repository_commit_sha": self.repository_commit_sha,
            "source_records": [_json_ready(record) for record in self.source_records],
            "provider_capabilities": [
                _json_ready(capability) for capability in self.provider_capabilities
            ],
        }
        if include_hash:
            payload["manifest_hash"] = self.manifest_hash
        return payload

    def to_dict(self) -> dict[str, object]:
        return self._to_payload(True)


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsDuplicateDiagnostic:
    """Duplicate prediction ID handling details for a pure batch assembly."""

    prediction_id: str
    duplicate_status: str
    input_indexes: tuple[int, ...]
    record_hashes: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "prediction_id": self.prediction_id,
            "duplicate_status": self.duplicate_status,
            "input_indexes": list(self.input_indexes),
            "record_hashes": list(self.record_hashes),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsAssemblyBatchResult:
    """Pure offline batch assembly result with all rows preserved by status."""

    rows: tuple[NBAPlayerPointsAssembledRow, ...]
    duplicate_diagnostics: tuple[NBAPlayerPointsDuplicateDiagnostic, ...]
    source_manifest_preview: NBAPlayerPointsSourceManifestPreview
    batch_summary_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        rows = tuple(_canonical_sort_rows(self.rows))
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "duplicate_diagnostics", tuple(self.duplicate_diagnostics))
        for row in rows:
            if not isinstance(row, NBAPlayerPointsAssembledRow):
                raise TypeError("rows must contain NBAPlayerPointsAssembledRow values")
        if not isinstance(self.source_manifest_preview, NBAPlayerPointsSourceManifestPreview):
            raise TypeError("source_manifest_preview must be NBAPlayerPointsSourceManifestPreview")
        object.__setattr__(
            self,
            "batch_summary_counts",
            MappingProxyType({str(key): int(value) for key, value in self.batch_summary_counts.items()}),
        )

    @property
    def eligible_projection_rows(self) -> tuple[NBAPlayerPointsAssembledRow, ...]:
        return tuple(row for row in self.rows if row.projection_research_eligible)

    @property
    def eligible_probability_rows(self) -> tuple[NBAPlayerPointsAssembledRow, ...]:
        return tuple(row for row in self.rows if row.probability_research_eligible)

    @property
    def excluded_rows(self) -> tuple[NBAPlayerPointsAssembledRow, ...]:
        return tuple(row for row in self.rows if row.assembly_status == "excluded")

    @property
    def quarantined_rows(self) -> tuple[NBAPlayerPointsAssembledRow, ...]:
        return tuple(row for row in self.rows if row.assembly_status == "quarantined")

    @property
    def conflicting_rows(self) -> tuple[NBAPlayerPointsAssembledRow, ...]:
        return tuple(row for row in self.rows if row.assembly_status == "conflicting")

    def to_dict(self) -> dict[str, object]:
        return {
            "eligible_projection_rows": [
                row.to_dict() for row in self.eligible_projection_rows
            ],
            "eligible_probability_rows": [
                row.to_dict() for row in self.eligible_probability_rows
            ],
            "excluded_rows": [row.to_dict() for row in self.excluded_rows],
            "quarantined_rows": [row.to_dict() for row in self.quarantined_rows],
            "conflicting_rows": [row.to_dict() for row in self.conflicting_rows],
            "duplicate_diagnostics": [
                diagnostic.to_dict() for diagnostic in self.duplicate_diagnostics
            ],
            "source_manifest_preview": self.source_manifest_preview.to_dict(),
            "batch_summary_counts": dict(self.batch_summary_counts),
        }


def projection_evidence_schema() -> dict[str, object]:
    """Return the strict offline projection-evidence contract."""

    return {
        "projection_schema_version": NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION,
        "required_fields": [
            "projected_points",
            "projection_method",
            "projection_timestamp_utc",
            "projection_cutoff_timestamp_utc",
            "projection_source",
            "projection_source_id",
            "projection_source_hash",
            "projection_schema_version",
        ],
        "constraints": [
            "projected_points finite and non-negative",
            "projection_timestamp_utc UTC-aware and before tipoff",
            "projection_timestamp_utc at or before projection_cutoff_timestamp_utc",
            "projection source, method, source ID, schema version, and hash explicit",
            "target-game actual points and actual minutes rejected",
            "probabilities accepted only from explicit validated probability evidence",
        ],
    }


def assembly_schema_definition() -> dict[str, object]:
    """Return the offline assembly preview schema without invoking runtime code."""

    return {
        "assembly_schema_version": NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION,
        "base_schema_version": NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION,
        "market": NBA_PLAYER_POINTS_MARKET,
        "operating_timezone": NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
        "required_fields": list(NBA_PLAYER_POINTS_ASSEMBLY_ROW_FIELDS),
        "assembly_statuses": list(NBA_PLAYER_POINTS_ASSEMBLY_STATUSES),
        "probability_statuses": list(NBA_PLAYER_POINTS_PROBABILITY_STATUSES),
        "probability_sum_tolerance": NBA_PLAYER_POINTS_PROBABILITY_SUM_TOLERANCE,
        "utc_timestamp_fields": list(NBA_PLAYER_POINTS_ASSEMBLY_UTC_TIMESTAMP_FIELDS),
        "prediction_id_canonical_inputs": [
            "prediction_run_id",
            "canonical_event_id",
            "player_id",
            "sportsbook",
            "market",
            "line",
            "american_odds",
            "prediction_timestamp_utc",
            "model_id",
        ],
        "hash_algorithm": "SHA-256",
        "output_ordering": [
            "assembly_status rank",
            "prediction_id",
            "assembled_record_hash",
        ],
        "directional_diagnostic_label": "non_probabilistic_projection_line_difference",
        "research_only_label": NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    }


def build_projection_evidence(
    payload: Mapping[str, object],
    *,
    commence_time_utc: datetime | str | None = None,
) -> NBAPlayerPointsProjectionEvidence:
    """Validate strict projection evidence without provider calls or file reads."""

    tipoff = None
    if commence_time_utc is not None:
        tipoff = _coerce_utc_datetime(commence_time_utc, "commence_time_utc")
    return NBAPlayerPointsProjectionEvidence.from_mapping(payload, commence_time_utc=tipoff)


def generate_preview_prediction_id(
    *,
    prediction_run_id: str,
    canonical_event_id: str | None,
    provider_event_id: str,
    player_id: str | None,
    provider_player_name: str,
    sportsbook: str | None,
    market: str | None,
    line: float | None,
    american_odds: int | None,
    prediction_timestamp_utc: datetime | str,
    model_id: str,
) -> str:
    """Generate a deterministic in-memory preview prediction ID."""

    timestamp = _coerce_utc_datetime(prediction_timestamp_utc, "prediction_timestamp_utc")
    payload = {
        "prediction_run_id": _require_identifier(prediction_run_id, "prediction_run_id"),
        "canonical_event_id": canonical_event_id
        or f"unresolved_event:{_require_identifier(provider_event_id, 'provider_event_id')}",
        "player_id": player_id
        or f"unresolved_player:{normalize_player_name(provider_player_name)}",
        "sportsbook": _clean_text(sportsbook),
        "market": _clean_text(market),
        "line": line,
        "american_odds": american_odds,
        "prediction_timestamp_utc": _format_utc(timestamp),
        "model_id": _require_identifier(model_id, "model_id"),
    }
    return f"nba-pp-preview-{_canonical_payload_sha256(payload)[:32]}"


def build_source_manifest_preview(
    records: Sequence[Mapping[str, object]],
    *,
    prediction_run_id: str,
    operating_date: date | str | None,
    created_at_utc: datetime | str,
    repository_commit_sha: str,
) -> NBAPlayerPointsSourceManifestPreview:
    """Create an in-memory source-manifest preview from offline source metadata."""

    parsed_date = _coerce_optional_date(operating_date)
    source_records = _collect_source_records(records)
    capabilities = (
        {
            "provider": "offline_market_evidence",
            "source_type": "normalized_market_fixture",
            "mode": "offline",
            "supports_live_calls": False,
        },
        {
            "provider": "offline_crosswalk_evidence",
            "source_type": "normalized_crosswalk_fixture",
            "mode": "offline",
            "supports_live_calls": False,
        },
        {
            "provider": "offline_projected_minutes_evidence",
            "source_type": "normalized_minutes_fixture",
            "mode": "offline",
            "supports_live_calls": False,
        },
        {
            "provider": "offline_player_points_projection_evidence",
            "source_type": "normalized_projection_fixture",
            "mode": "offline",
            "supports_live_calls": False,
        },
    )
    return NBAPlayerPointsSourceManifestPreview(
        manifest_schema_version=NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION,
        prediction_run_id=prediction_run_id,
        operating_date=parsed_date,
        created_at_utc=_coerce_utc_datetime(created_at_utc, "created_at_utc"),
        repository_commit_sha=repository_commit_sha,
        source_records=tuple(source_records),
        provider_capabilities=capabilities,
    )


def assemble_player_points_row(
    *,
    market_evidence: Mapping[str, object],
    crosswalk_evidence: Mapping[str, object],
    minutes_evidence: Mapping[str, object],
    projection_evidence: Mapping[str, object],
    provenance: Mapping[str, object],
    probability_evidence: Mapping[str, object] | None = None,
    source_manifest_hash: str | None = None,
) -> NBAPlayerPointsAssembledRow:
    """Assemble one offline player-points row, preserving excluded diagnostics."""

    manifest = build_source_manifest_preview(
        (
            {
                "market": market_evidence,
                "crosswalk": crosswalk_evidence,
                "minutes": minutes_evidence,
                "projection": projection_evidence,
                "probability": probability_evidence or {},
                "provenance": provenance,
            },
        ),
        prediction_run_id=str(provenance.get("prediction_run_id") or "single-row-preview"),
        operating_date=_first_present(crosswalk_evidence, minutes_evidence, "operating_date"),
        created_at_utc=provenance.get("prediction_timestamp_utc") or _format_utc(datetime.now(tz=_UTC)),
        repository_commit_sha=str(provenance.get("repository_commit_sha") or "0" * 7),
    )
    return _assemble_one(
        market_evidence=market_evidence,
        crosswalk_evidence=crosswalk_evidence,
        minutes_evidence=minutes_evidence,
        projection_evidence=projection_evidence,
        provenance=provenance,
        probability_evidence=probability_evidence,
        source_manifest_hash=source_manifest_hash or manifest.manifest_hash,
    )


def assemble_nba_player_points_batch(
    records: Sequence[Mapping[str, object]],
    *,
    manifest_created_at_utc: datetime | str | None = None,
) -> NBAPlayerPointsAssemblyBatchResult:
    """Assemble a pure offline batch with canonical ordering and diagnostics."""

    normalized_records = tuple(_json_clone_mapping(record) for record in records)
    if not normalized_records:
        raise NBAPlayerPointsAssemblyContractError("records must not be empty")

    first_provenance = _required_mapping_object(normalized_records[0], "provenance")
    prediction_run_id = _require_identifier(
        first_provenance.get("prediction_run_id"),
        "prediction_run_id",
    )
    repository_commit_sha = _require_text(
        first_provenance.get("repository_commit_sha"),
        "repository_commit_sha",
    )
    operating_date = _first_available_operating_date(normalized_records)
    created_at = manifest_created_at_utc or _max_prediction_timestamp(normalized_records)
    manifest = build_source_manifest_preview(
        normalized_records,
        prediction_run_id=prediction_run_id,
        operating_date=operating_date,
        created_at_utc=created_at,
        repository_commit_sha=repository_commit_sha,
    )

    rows_with_indexes: list[tuple[int, NBAPlayerPointsAssembledRow]] = []
    for index, record in enumerate(normalized_records):
        rows_with_indexes.append(
            (
                index,
                _assemble_one(
                    market_evidence=_required_mapping_object(record, "market"),
                    crosswalk_evidence=_required_mapping_object(record, "crosswalk"),
                    minutes_evidence=_required_mapping_object(record, "minutes"),
                    projection_evidence=_required_mapping_object(record, "projection"),
                    provenance=_required_mapping_object(record, "provenance"),
                    probability_evidence=_optional_mapping_object(record, "probability"),
                    source_manifest_hash=manifest.manifest_hash,
                ),
            )
        )

    rows, duplicate_diagnostics = _apply_duplicate_policy(rows_with_indexes)
    sorted_rows = tuple(_canonical_sort_rows(rows))
    summary = _summary_counts(sorted_rows, duplicate_diagnostics)
    return NBAPlayerPointsAssemblyBatchResult(
        rows=sorted_rows,
        duplicate_diagnostics=tuple(duplicate_diagnostics),
        source_manifest_preview=manifest,
        batch_summary_counts=summary,
    )


def validate_assembled_rows(
    rows: Sequence[NBAPlayerPointsAssembledRow],
) -> tuple[NBAPlayerPointsAssembledRow, ...]:
    """Validate assembled rows without writing ledgers."""

    normalized = tuple(rows)
    seen: dict[str, str] = {}
    for index, row in enumerate(normalized):
        if not isinstance(row, NBAPlayerPointsAssembledRow):
            raise TypeError("rows must contain NBAPlayerPointsAssembledRow values")
        existing_hash = seen.get(row.prediction_id)
        if existing_hash is not None and existing_hash != row.assembled_record_hash:
            raise NBAPlayerPointsAssemblyContractError(
                f"conflicting duplicate prediction_id at rows[{index}]"
            )
        seen[row.prediction_id] = row.assembled_record_hash
    return normalized


def _assemble_one(
    *,
    market_evidence: Mapping[str, object],
    crosswalk_evidence: Mapping[str, object],
    minutes_evidence: Mapping[str, object],
    projection_evidence: Mapping[str, object],
    provenance: Mapping[str, object],
    probability_evidence: Mapping[str, object] | None,
    source_manifest_hash: str,
) -> NBAPlayerPointsAssembledRow:
    market = _market_view(market_evidence)
    crosswalk = _crosswalk_view(crosswalk_evidence)
    minutes = _minutes_view(minutes_evidence)
    prediction_timestamp = _coerce_utc_datetime(
        _required_mapping_value(provenance, "prediction_timestamp_utc"),
        "prediction_timestamp_utc",
    )
    repository_commit_sha = _require_text(
        _required_mapping_value(provenance, "repository_commit_sha"),
        "repository_commit_sha",
    )
    prediction_run_id = _require_identifier(
        _required_mapping_value(provenance, "prediction_run_id"),
        "prediction_run_id",
    )
    model_id = _require_identifier(_required_mapping_value(provenance, "model_id"), "model_id")
    source_manifest_id = _require_identifier(
        _required_mapping_value(provenance, "source_manifest_id"),
        "source_manifest_id",
    )
    research_label = str(provenance.get("research_label") or NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL)
    if research_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
        raise NBAPlayerPointsAssemblyContractError("research_label is unsupported")

    conflict_reasons: list[str] = []
    exclusion_reasons: list[str] = []
    quarantine_reasons: list[str] = []
    diagnostics: list[str] = []
    market_conflicts = market.pop("conflict_reasons")
    market_exclusions = market.pop("exclusion_reasons")
    conflict_reasons.extend(market_conflicts)
    exclusion_reasons.extend(market_exclusions)
    conflict_reasons.extend(crosswalk.pop("conflict_reasons"))
    exclusion_reasons.extend(crosswalk.pop("exclusion_reasons"))
    conflict_reasons.extend(minutes.pop("conflict_reasons"))
    exclusion_reasons.extend(minutes.pop("exclusion_reasons"))

    try:
        projection = NBAPlayerPointsProjectionEvidence.from_mapping(
            projection_evidence,
            commence_time_utc=crosswalk.get("commence_time_utc"),
        )
        projection_status = "valid"
        projected_points = projection.projected_points
        projection_method = projection.projection_method
        projection_source = projection.projection_source
        projection_source_hash = projection.projection_source_hash
    except NBAPlayerPointsAssemblyContractError as exc:
        projection_status = "invalid"
        projected_points = _optional_nonnegative_number(
            projection_evidence.get("projected_points"),
            "projected_points",
        )
        projection_method = _optional_text(projection_evidence.get("projection_method"))
        projection_source = _optional_text(projection_evidence.get("projection_source"))
        projection_source_hash = _optional_text(projection_evidence.get("projection_source_hash"))
        if "actual" in str(exc) or "final" in str(exc) or "box_score" in str(exc):
            quarantine_reasons.append(str(exc))
            projection_status = "quarantined"
        else:
            conflict_reasons.append(str(exc))

    if projection_status == "valid":
        projection_timestamp = _coerce_utc_datetime(
            projection_evidence["projection_timestamp_utc"],
            "projection_timestamp_utc",
        )
        projection_cutoff = _coerce_utc_datetime(
            projection_evidence["projection_cutoff_timestamp_utc"],
            "projection_cutoff_timestamp_utc",
        )
        if crosswalk.get("commence_time_utc") is not None and projection_timestamp >= crosswalk.get(
            "commence_time_utc"
        ):
            conflict_reasons.append("projection_timestamp_utc must be before tipoff")
        if crosswalk.get("commence_time_utc") is not None and prediction_timestamp >= crosswalk.get(
            "commence_time_utc"
        ):
            conflict_reasons.append("prediction_timestamp_utc must be before tipoff")
        if projection_timestamp > projection_cutoff:
            conflict_reasons.append("projection_timestamp_utc must be at or before projection cutoff")

    if _contains_leakage(minutes_evidence):
        quarantine_reasons.append("minutes_evidence contains target-game leakage")
    if _contains_leakage(projection_evidence):
        quarantine_reasons.append("projection_evidence contains target-game leakage")

    _validate_cross_source_consistency(crosswalk, minutes, conflict_reasons)
    _validate_market_cutoff(market, minutes, crosswalk, conflict_reasons)
    _validate_minutes_cutoff(minutes, conflict_reasons)

    probability = _validate_probability_evidence(
        probability_evidence,
        cutoff=minutes.get("feature_cutoff_timestamp_utc"),
        tipoff=crosswalk.get("commence_time_utc"),
    )
    diagnostics.extend(probability.diagnostics)
    if probability.claims_probability_eligibility and probability.probability_status != "valid":
        conflict_reasons.append("malformed probability evidence claimed probability eligibility")

    if projection_status != "valid":
        exclusion_reasons.append("valid projection evidence is required")
    if market.get("market") != NBA_PLAYER_POINTS_MARKET:
        exclusion_reasons.append("market must be player_points")
    if crosswalk.get("event_identity_status") != "resolved":
        exclusion_reasons.append("resolved event identity is required")
    if crosswalk.get("player_identity_status") != "resolved":
        exclusion_reasons.append("resolved player identity is required")
    if not crosswalk.get("canonical_event_id"):
        exclusion_reasons.append("canonical_event_id is required")
    if not crosswalk.get("player_id"):
        exclusion_reasons.append("player_id is required")
    if minutes.get("minutes_projection_status") in NBA_PLAYER_POINTS_DIAGNOSTIC_ONLY_MINUTES_STATUSES:
        exclusion_reasons.append("minutes status is diagnostic-only")
    if minutes.get("minutes_projection_status") not in NBA_PLAYER_POINTS_PROJECTION_RESEARCH_MINUTES_STATUSES:
        exclusion_reasons.append("minutes status does not allow projection research")
    if minutes.get("projected_minutes") is None:
        exclusion_reasons.append("projected_minutes is required")

    source_hashes, source_hash_conflicts = _assembled_source_hashes(
        market_evidence,
        crosswalk_evidence,
        minutes_evidence,
        projection_evidence,
        probability_evidence,
    )
    conflict_reasons.extend(source_hash_conflicts)

    projection_line_difference = None
    projected_points_above_line = None
    projected_points_below_line = None
    if projected_points is not None and market.get("line") is not None:
        projection_line_difference = round(projected_points - market["line"], 6)
        projected_points_above_line = projection_line_difference > 0
        projected_points_below_line = projection_line_difference < 0

    if quarantine_reasons:
        assembly_status = "quarantined"
    elif conflict_reasons:
        assembly_status = "conflicting"
    elif exclusion_reasons:
        assembly_status = "excluded"
    elif probability.probability_status == "valid":
        assembly_status = "eligible_probability_research"
    else:
        assembly_status = "eligible_projection_research"

    projection_research_eligible = assembly_status in {
        "eligible_projection_research",
        "eligible_probability_research",
    }
    probability_research_eligible = assembly_status == "eligible_probability_research"
    all_reasons = _unique_reasons(
        [*quarantine_reasons, *conflict_reasons, *exclusion_reasons]
    )
    assembly_exclusion_reason = ";".join(all_reasons) if all_reasons else "none"
    eligibility_status = "research_eligible" if projection_research_eligible else "excluded"
    identity_status = _combined_identity_status(crosswalk, assembly_status)
    market_status = "valid" if not market_exclusions else "excluded"
    if any("market" in reason for reason in conflict_reasons):
        market_status = "conflicting"

    provider_player_name = str(market.get("provider_player_name") or "unknown-player")
    player_name = str(crosswalk.get("canonical_player_name") or provider_player_name)
    prediction_id = generate_preview_prediction_id(
        prediction_run_id=prediction_run_id,
        canonical_event_id=crosswalk.get("canonical_event_id"),
        provider_event_id=market.get("provider_event_id") or "unresolved-event",
        player_id=crosswalk.get("player_id"),
        provider_player_name=provider_player_name,
        sportsbook=market.get("sportsbook"),
        market=market.get("market"),
        line=market.get("line"),
        american_odds=market.get("american_odds"),
        prediction_timestamp_utc=prediction_timestamp,
        model_id=model_id,
    )
    return NBAPlayerPointsAssembledRow(
        schema_version=NBA_PLAYER_POINTS_RESEARCH_SCHEMA_VERSION,
        prediction_id=prediction_id,
        prediction_run_id=prediction_run_id,
        model_id=model_id,
        provider_event_id=str(market.get("provider_event_id") or "unresolved-event"),
        canonical_event_id=crosswalk.get("canonical_event_id"),
        operating_date=crosswalk.get("operating_date"),
        operating_timezone=NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
        commence_time_utc=crosswalk.get("commence_time_utc"),
        team=crosswalk.get("team"),
        opponent=crosswalk.get("opponent"),
        player_id=crosswalk.get("player_id"),
        player_name=player_name,
        normalized_player_name=normalize_player_name(player_name),
        identity_status=identity_status,
        identity_source=str(crosswalk.get("mapping_version") or "offline_crosswalk_evidence"),
        identity_conflict_reason=assembly_exclusion_reason if identity_status != "resolved" else "none",
        sportsbook=market.get("sportsbook"),
        market=market.get("market"),
        line=market.get("line"),
        american_odds=market.get("american_odds"),
        decimal_odds=market.get("decimal_odds"),
        implied_probability=market.get("implied_probability"),
        market_timestamp_utc=market.get("market_timestamp_utc"),
        projected_points=projected_points,
        projected_minutes=minutes.get("projected_minutes"),
        recent_minutes=minutes.get("recent_minutes"),
        season_minutes=minutes.get("season_minutes"),
        points_per_minute=_points_per_minute(projected_points, minutes.get("projected_minutes")),
        lineup_status=minutes.get("lineup_status"),
        injury_status=minutes.get("injury_status"),
        feature_timestamp_utc=minutes.get("feature_timestamp_utc"),
        feature_source=minutes.get("minutes_projection_method"),
        model_over_probability=probability.model_over_probability,
        model_under_probability=probability.model_under_probability,
        selected_side=None,
        model_edge=None,
        eligibility_status=eligibility_status,
        exclusion_reason=assembly_exclusion_reason,
        prediction_timestamp_utc=prediction_timestamp,
        feature_schema_version=minutes.get("feature_schema_version"),
        repository_commit_sha=repository_commit_sha,
        source_manifest_id=source_manifest_id,
        source_hashes=source_hashes,
        assembly_schema_version=NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION,
        assembly_status=assembly_status,
        assembly_exclusion_reason=assembly_exclusion_reason,
        projection_research_eligible=projection_research_eligible,
        probability_research_eligible=probability_research_eligible,
        probability_status=probability.probability_status,
        probability_model_id=probability.probability_model_id,
        probability_source_id=probability.probability_source_id,
        probability_schema_version=probability.probability_schema_version,
        probability_source_hash=probability.probability_source_hash,
        probability_timestamp_utc=probability.probability_timestamp_utc,
        probability_based_edge=None,
        market_status=market_status,
        minutes_status=str(minutes.get("minutes_projection_status") or "invalid"),
        projection_status=projection_status,
        source_manifest_hash=source_manifest_hash,
        projection_line_difference=projection_line_difference,
        projected_points_above_line=projected_points_above_line,
        projected_points_below_line=projected_points_below_line,
        diagnostics=tuple(_unique_reasons([*diagnostics, *all_reasons])),
    )


def _market_view(payload: Mapping[str, object]) -> dict[str, Any]:
    conflicts: list[str] = []
    exclusions: list[str] = []
    provider_event_id = _optional_text(payload.get("provider_event_id"))
    if provider_event_id is None:
        exclusions.append("provider_event_id is required")
        provider_event_id = "unresolved-event"
    sportsbook = _optional_text(payload.get("sportsbook"))
    if sportsbook is None:
        exclusions.append("sportsbook is required")
    market = _optional_text(payload.get("market"))
    if market is not None:
        try:
            market = _normalize_market(market)
        except NBAPlayerPointsAssemblyContractError as exc:
            exclusions.append(str(exc))
    else:
        exclusions.append("market is required")
    provider_player_name = _optional_text(payload.get("provider_player_name"))
    if provider_player_name is None:
        exclusions.append("provider_player_name is required")
        provider_player_name = "unknown-player"
    line = _optional_nonnegative_number(payload.get("line"), "line")
    if line is None:
        exclusions.append("line is required")
    american_odds = _optional_american_odds(payload.get("american_odds"), "american_odds")
    if american_odds is None:
        exclusions.append("american_odds is required")
    decimal_odds = _optional_positive_number(payload.get("decimal_odds"), "decimal_odds")
    if decimal_odds is None and american_odds is not None:
        decimal_odds = decimal_odds_from_american(american_odds)
    if decimal_odds is None:
        exclusions.append("decimal_odds is required")
    implied_probability = _optional_probability(payload.get("implied_probability"), "implied_probability")
    if implied_probability is None and american_odds is not None:
        implied_probability = implied_probability_from_american(american_odds)
    if implied_probability is None:
        exclusions.append("implied_probability is required")
    market_timestamp = _optional_utc_datetime(payload.get("market_timestamp_utc"), "market_timestamp_utc")
    if market_timestamp is None:
        exclusions.append("market_timestamp_utc is required")
    market_source_hash = _optional_text(payload.get("market_source_hash"))
    if market_source_hash is None:
        conflicts.append("market_source_hash is required")
    else:
        try:
            market_source_hash = _require_sha256(market_source_hash, "market_source_hash")
        except NBAPlayerPointsAssemblyContractError as exc:
            conflicts.append(str(exc))
    schema_version = _optional_text(payload.get("market_schema_version"))
    if schema_version != NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION:
        conflicts.append(f"unsupported market_schema_version: {schema_version!r}")
    return {
        "provider_event_id": provider_event_id,
        "sportsbook": sportsbook,
        "market": market,
        "provider_player_name": provider_player_name,
        "line": line,
        "american_odds": american_odds,
        "decimal_odds": decimal_odds,
        "implied_probability": implied_probability,
        "market_timestamp_utc": market_timestamp,
        "market_source_id": _optional_text(payload.get("market_source_id")),
        "market_source_hash": market_source_hash,
        "market_schema_version": schema_version,
        "conflict_reasons": conflicts,
        "exclusion_reasons": exclusions,
    }


def _crosswalk_view(payload: Mapping[str, object]) -> dict[str, Any]:
    conflicts: list[str] = []
    exclusions: list[str] = []
    canonical_event_id = _optional_text(payload.get("canonical_event_id"))
    player_id = _optional_text(payload.get("player_id"))
    canonical_player_name = _optional_text(payload.get("canonical_player_name"))
    if canonical_player_name is None:
        exclusions.append("canonical_player_name is required")
        canonical_player_name = "unknown-player"
    team = _optional_team(payload.get("team"), "team")
    opponent = _optional_team(payload.get("opponent"), "opponent")
    if team is None:
        exclusions.append("team is required")
    if opponent is None:
        exclusions.append("opponent is required")
    commence_time = _optional_utc_datetime(payload.get("commence_time_utc"), "commence_time_utc")
    if commence_time is None:
        exclusions.append("commence_time_utc is required")
    operating_date = _optional_date(payload.get("operating_date"))
    if commence_time is not None:
        expected_operating_date = toronto_operating_date(commence_time)
        if operating_date is None:
            exclusions.append("operating_date is required")
        elif operating_date != expected_operating_date:
            conflicts.append("operating dates disagree")
    event_status = _normalize_identity_status(payload.get("event_identity_status"))
    player_status = _normalize_identity_status(payload.get("player_identity_status"))
    if event_status != "resolved":
        exclusions.append("event identity is unresolved")
    if player_status != "resolved":
        exclusions.append("player identity is unresolved")
    if event_status == "resolved" and canonical_event_id is None:
        exclusions.append("canonical_event_id is required")
    if player_status == "resolved" and player_id is None:
        exclusions.append("player_id is required")
    crosswalk_hashes = payload.get("crosswalk_source_hashes")
    if not isinstance(crosswalk_hashes, Mapping) or not crosswalk_hashes:
        conflicts.append("crosswalk_source_hashes are required")
    else:
        for key, value in crosswalk_hashes.items():
            try:
                _require_sha256(value, f"crosswalk_source_hashes.{key}")
            except NBAPlayerPointsAssemblyContractError as exc:
                conflicts.append(str(exc))
    if _optional_text(payload.get("mapping_version")) is None:
        conflicts.append("mapping_version is required")
    return {
        "canonical_event_id": canonical_event_id,
        "player_id": player_id,
        "canonical_player_name": canonical_player_name,
        "team": team,
        "opponent": opponent,
        "commence_time_utc": commence_time,
        "operating_date": operating_date,
        "event_identity_status": event_status,
        "player_identity_status": player_status,
        "event_identity_method": _optional_text(payload.get("event_identity_method")),
        "player_identity_method": _optional_text(payload.get("player_identity_method")),
        "mapping_version": _optional_text(payload.get("mapping_version")),
        "crosswalk_source_hashes": crosswalk_hashes,
        "conflict_reasons": conflicts,
        "exclusion_reasons": exclusions,
    }


def _minutes_view(payload: Mapping[str, object]) -> dict[str, Any]:
    conflicts: list[str] = []
    exclusions: list[str] = []
    projected_minutes = _optional_nonnegative_number(payload.get("projected_minutes"), "projected_minutes")
    if projected_minutes is None:
        exclusions.append("projected_minutes is required")
    low = _optional_nonnegative_number(payload.get("projected_minutes_low"), "projected_minutes_low")
    high = _optional_nonnegative_number(payload.get("projected_minutes_high"), "projected_minutes_high")
    if projected_minutes is not None and low is not None and projected_minutes < low:
        conflicts.append("projected_minutes_low cannot exceed projected_minutes")
    if projected_minutes is not None and high is not None and projected_minutes > high:
        conflicts.append("projected_minutes cannot exceed projected_minutes_high")
    minutes_status = _normalize_key(
        payload.get("minutes_projection_status") or "missing",
        "minutes_projection_status",
    )
    if minutes_status != "projected":
        exclusions.append(f"minutes_projection_status={minutes_status}")
    feature_timestamp = _optional_utc_datetime(payload.get("feature_timestamp_utc"), "feature_timestamp_utc")
    cutoff = _optional_utc_datetime(
        payload.get("feature_cutoff_timestamp_utc"),
        "feature_cutoff_timestamp_utc",
    )
    if feature_timestamp is None:
        exclusions.append("feature_timestamp_utc is required")
    if cutoff is None:
        conflicts.append("feature_cutoff_timestamp_utc is required")
    if feature_timestamp is not None and cutoff is not None and feature_timestamp > cutoff:
        conflicts.append("feature_timestamp_utc must be at or before feature cutoff")
    schema_version = _optional_text(payload.get("feature_schema_version"))
    if schema_version != NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION:
        conflicts.append(f"unsupported feature_schema_version: {schema_version!r}")
    minutes_hashes = payload.get("minutes_source_hashes")
    if not isinstance(minutes_hashes, Mapping) or not minutes_hashes:
        conflicts.append("minutes_source_hashes are required")
    else:
        for key, value in minutes_hashes.items():
            try:
                _require_sha256(value, f"minutes_source_hashes.{key}")
            except NBAPlayerPointsAssemblyContractError as exc:
                conflicts.append(str(exc))
    return {
        "canonical_event_id": _optional_text(payload.get("canonical_event_id")),
        "player_id": _optional_text(payload.get("player_id")),
        "canonical_player_name": _optional_text(payload.get("canonical_player_name")),
        "team": _optional_team(payload.get("team"), "team"),
        "opponent": _optional_team(payload.get("opponent"), "opponent"),
        "operating_date": _optional_date(payload.get("operating_date")),
        "commence_time_utc": _optional_utc_datetime(payload.get("commence_time_utc"), "commence_time_utc"),
        "projected_minutes": projected_minutes,
        "projected_minutes_low": low,
        "projected_minutes_high": high,
        "minutes_confidence": _optional_text(payload.get("minutes_confidence")),
        "minutes_projection_status": minutes_status,
        "minutes_projection_method": _optional_text(payload.get("minutes_projection_method")),
        "minutes_exclusion_reason": _optional_text(payload.get("minutes_exclusion_reason")),
        "feature_timestamp_utc": feature_timestamp,
        "feature_cutoff_timestamp_utc": cutoff,
        "minutes_source_hashes": minutes_hashes,
        "feature_schema_version": schema_version,
        "lineup_status": _optional_text(payload.get("lineup_status")),
        "injury_status": _optional_text(payload.get("injury_status")),
        "recent_minutes": _optional_nonnegative_number(payload.get("recent_minutes"), "recent_minutes"),
        "season_minutes": _optional_nonnegative_number(payload.get("season_minutes"), "season_minutes"),
        "conflict_reasons": conflicts,
        "exclusion_reasons": exclusions,
    }


def _validate_cross_source_consistency(
    crosswalk: Mapping[str, Any],
    minutes: Mapping[str, Any],
    conflict_reasons: list[str],
) -> None:
    comparisons = (
        ("canonical_event_id", "canonical event IDs disagree"),
        ("player_id", "player IDs disagree"),
        ("team", "teams disagree"),
        ("opponent", "opponents disagree"),
        ("operating_date", "operating dates disagree"),
    )
    for field_name, reason in comparisons:
        left = crosswalk.get(field_name)
        right = minutes.get(field_name)
        if left is not None and right is not None and left != right:
            conflict_reasons.append(reason)
    left_time = crosswalk.get("commence_time_utc")
    right_time = minutes.get("commence_time_utc")
    if left_time is not None and right_time is not None:
        if abs(left_time - right_time) > NBA_PLAYER_POINTS_DEFAULT_EVENT_TIME_TOLERANCE:
            conflict_reasons.append("commence times disagree beyond approved tolerance")


def _validate_market_cutoff(
    market: Mapping[str, Any],
    minutes: Mapping[str, Any],
    crosswalk: Mapping[str, Any],
    conflict_reasons: list[str],
) -> None:
    market_timestamp = market.get("market_timestamp_utc")
    cutoff = minutes.get("feature_cutoff_timestamp_utc")
    tipoff = crosswalk.get("commence_time_utc")
    if market_timestamp is not None and cutoff is not None and market_timestamp > cutoff:
        conflict_reasons.append("market timestamp is after cutoff")
    if market_timestamp is not None and tipoff is not None and market_timestamp >= tipoff:
        conflict_reasons.append("market timestamp must be before tipoff")


def _validate_minutes_cutoff(
    minutes: Mapping[str, Any],
    conflict_reasons: list[str],
) -> None:
    feature_timestamp = minutes.get("feature_timestamp_utc")
    cutoff = minutes.get("feature_cutoff_timestamp_utc")
    if feature_timestamp is not None and cutoff is not None and feature_timestamp > cutoff:
        conflict_reasons.append("minutes timestamp is after cutoff")


def _validate_probability_evidence(
    payload: Mapping[str, object] | None,
    *,
    cutoff: datetime | None,
    tipoff: datetime | None,
) -> NBAPlayerPointsProbabilityValidation:
    if payload is None or not payload:
        return NBAPlayerPointsProbabilityValidation(probability_status="unavailable")
    claims = bool(payload.get("claims_probability_eligibility", False))
    diagnostics: list[str] = []
    has_probability_values = any(
        key in payload
        for key in ("model_over_probability", "model_under_probability")
    )
    if not has_probability_values:
        return NBAPlayerPointsProbabilityValidation(
            probability_status="unavailable",
            claims_probability_eligibility=claims,
        )
    over = _optional_probability(payload.get("model_over_probability"), "model_over_probability")
    under = _optional_probability(payload.get("model_under_probability"), "model_under_probability")
    if over is None:
        diagnostics.append("model_over_probability is malformed or missing")
    if under is None:
        diagnostics.append("model_under_probability is malformed or missing")
    if over is not None and under is not None:
        if abs((over + under) - 1.0) > NBA_PLAYER_POINTS_PROBABILITY_SUM_TOLERANCE:
            diagnostics.append("model probabilities do not sum to one within tolerance")
    source_id = _optional_text(payload.get("probability_source_id"))
    model_id = _optional_text(payload.get("probability_model_id"))
    schema_version = _optional_text(payload.get("probability_schema_version"))
    source_hash = _optional_text(payload.get("probability_source_hash"))
    timestamp = _optional_utc_datetime(
        payload.get("probability_timestamp_utc"),
        "probability_timestamp_utc",
    )
    missing_metadata = [
        name
        for name, value in (
            ("probability_source_id", source_id),
            ("probability_model_id", model_id),
            ("probability_schema_version", schema_version),
            ("probability_source_hash", source_hash),
            ("probability_timestamp_utc", timestamp),
        )
        if value is None
    ]
    if missing_metadata:
        diagnostics.append(f"probability metadata missing: {','.join(missing_metadata)}")
    if schema_version is not None and schema_version != NBA_PLAYER_POINTS_PROBABILITY_SCHEMA_VERSION:
        diagnostics.append(f"unsupported probability_schema_version: {schema_version!r}")
    if source_hash is not None:
        try:
            source_hash = _require_sha256(source_hash, "probability_source_hash")
        except NBAPlayerPointsAssemblyContractError as exc:
            diagnostics.append(str(exc))
    if timestamp is not None and cutoff is not None and timestamp > cutoff:
        diagnostics.append("probability timestamp is after cutoff")
    if timestamp is not None and tipoff is not None and timestamp >= tipoff:
        diagnostics.append("probability timestamp must be before tipoff")
    if diagnostics:
        status = "incomplete" if any("metadata missing" in item for item in diagnostics) else "malformed"
        return NBAPlayerPointsProbabilityValidation(
            probability_status=status,
            model_over_probability=over,
            model_under_probability=under,
            probability_model_id=model_id,
            probability_source_id=source_id,
            probability_schema_version=schema_version,
            probability_source_hash=source_hash,
            probability_timestamp_utc=timestamp,
            claims_probability_eligibility=claims,
            diagnostics=tuple(diagnostics),
        )
    return NBAPlayerPointsProbabilityValidation(
        probability_status="valid",
        model_over_probability=over,
        model_under_probability=under,
        probability_model_id=model_id,
        probability_source_id=source_id,
        probability_schema_version=schema_version,
        probability_source_hash=source_hash,
        probability_timestamp_utc=timestamp,
        claims_probability_eligibility=claims,
    )


def _assembled_source_hashes(
    market: Mapping[str, object],
    crosswalk: Mapping[str, object],
    minutes: Mapping[str, object],
    projection: Mapping[str, object],
    probability: Mapping[str, object] | None,
) -> tuple[Mapping[str, str], list[str]]:
    conflicts: list[str] = []
    hashes: dict[str, str] = {}
    _add_hash(hashes, conflicts, "market", market.get("market_source_hash"))
    _add_hash(hashes, conflicts, "projection", projection.get("projection_source_hash"))
    for key, value in _mapping_items(crosswalk.get("crosswalk_source_hashes")):
        _add_hash(hashes, conflicts, f"crosswalk:{key}", value)
    for key, value in _mapping_items(minutes.get("minutes_source_hashes")):
        _add_hash(hashes, conflicts, f"minutes:{key}", value)
    if probability:
        probability_hash = probability.get("probability_source_hash")
        if probability_hash:
            _add_hash(hashes, conflicts, "probability", probability_hash)
    return MappingProxyType(hashes), conflicts


def _add_hash(
    hashes: dict[str, str],
    conflicts: list[str],
    key: str,
    value: object,
) -> None:
    try:
        hashes[key] = _require_sha256(value, f"source_hashes.{key}")
    except NBAPlayerPointsAssemblyContractError as exc:
        conflicts.append(str(exc))


def _apply_duplicate_policy(
    rows_with_indexes: Sequence[tuple[int, NBAPlayerPointsAssembledRow]],
) -> tuple[tuple[NBAPlayerPointsAssembledRow, ...], tuple[NBAPlayerPointsDuplicateDiagnostic, ...]]:
    groups: dict[str, list[tuple[int, NBAPlayerPointsAssembledRow]]] = {}
    for index, row in rows_with_indexes:
        groups.setdefault(row.prediction_id, []).append((index, row))
    output_rows: list[NBAPlayerPointsAssembledRow] = []
    diagnostics: list[NBAPlayerPointsDuplicateDiagnostic] = []
    for prediction_id in sorted(groups):
        group = groups[prediction_id]
        if len(group) == 1:
            output_rows.append(group[0][1])
            continue
        hashes = tuple(sorted({row.assembled_record_hash for _, row in group}))
        indexes = tuple(sorted(index for index, _ in group))
        if len(hashes) == 1:
            diagnostics.append(
                NBAPlayerPointsDuplicateDiagnostic(
                    prediction_id=prediction_id,
                    duplicate_status="identical_collapsed",
                    input_indexes=indexes,
                    record_hashes=hashes,
                    reason="identical duplicate prediction IDs collapsed idempotently",
                )
            )
            output_rows.append(sorted((row for _, row in group), key=lambda item: item.assembled_record_hash)[0])
            continue
        diagnostics.append(
            NBAPlayerPointsDuplicateDiagnostic(
                prediction_id=prediction_id,
                duplicate_status="conflicting",
                input_indexes=indexes,
                record_hashes=hashes,
                reason="same prediction ID has conflicting assembled evidence",
            )
        )
        for _, row in group:
            output_rows.append(
                _mark_duplicate_conflict(row, "conflicting duplicate prediction_id")
            )
    return tuple(output_rows), tuple(diagnostics)


def _mark_duplicate_conflict(
    row: NBAPlayerPointsAssembledRow,
    reason: str,
) -> NBAPlayerPointsAssembledRow:
    reasons = _unique_reasons([*row.diagnostics, reason])
    return replace(
        row,
        assembly_status="conflicting",
        assembly_exclusion_reason=";".join(reasons),
        projection_research_eligible=False,
        probability_research_eligible=False,
        eligibility_status="excluded",
        exclusion_reason=";".join(reasons),
        identity_conflict_reason=";".join(reasons),
        diagnostics=tuple(reasons),
        assembled_record_hash="",
        artifact_hash="",
    )


def _collect_source_records(records: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    collected: dict[str, Mapping[str, object]] = {}
    for record in records:
        market = _optional_mapping_object(record, "market") or {}
        crosswalk = _optional_mapping_object(record, "crosswalk") or {}
        minutes = _optional_mapping_object(record, "minutes") or {}
        projection = _optional_mapping_object(record, "projection") or {}
        probability = _optional_mapping_object(record, "probability") or {}
        _collect_source_record(
            collected,
            source_type="market",
            provider=str(market.get("sportsbook") or "unknown_sportsbook"),
            source_id=market.get("market_source_id"),
            source_timestamp=market.get("market_timestamp_utc"),
            source_hash=market.get("market_source_hash"),
            source_schema_version=market.get("market_schema_version"),
        )
        for key, value in _mapping_items(crosswalk.get("crosswalk_source_hashes")):
            _collect_source_record(
                collected,
                source_type="crosswalk",
                provider="offline_crosswalk",
                source_id=key,
                source_timestamp=None,
                source_hash=value,
                source_schema_version=crosswalk.get("mapping_version"),
            )
        for key, value in _mapping_items(minutes.get("minutes_source_hashes")):
            _collect_source_record(
                collected,
                source_type="projected_minutes",
                provider="offline_minutes",
                source_id=key,
                source_timestamp=minutes.get("feature_timestamp_utc"),
                source_hash=value,
                source_schema_version=minutes.get("feature_schema_version"),
            )
        _collect_source_record(
            collected,
            source_type="player_points_projection",
            provider=projection.get("projection_source"),
            source_id=projection.get("projection_source_id"),
            source_timestamp=projection.get("projection_timestamp_utc"),
            source_hash=projection.get("projection_source_hash"),
            source_schema_version=projection.get("projection_schema_version"),
        )
        if probability:
            _collect_source_record(
                collected,
                source_type="validated_probability",
                provider=probability.get("probability_model_id"),
                source_id=probability.get("probability_source_id"),
                source_timestamp=probability.get("probability_timestamp_utc"),
                source_hash=probability.get("probability_source_hash"),
                source_schema_version=probability.get("probability_schema_version"),
            )
    return tuple(collected.values())


def _collect_source_record(
    collected: dict[str, Mapping[str, object]],
    *,
    source_type: str,
    provider: object,
    source_id: object,
    source_timestamp: object,
    source_hash: object,
    source_schema_version: object,
) -> None:
    if source_hash in (None, "") and source_id in (None, ""):
        return
    timestamp = None
    if source_timestamp not in (None, ""):
        try:
            timestamp = _format_utc(_coerce_utc_datetime(source_timestamp, "source_timestamp"))
        except NBAPlayerPointsAssemblyContractError:
            timestamp = str(source_timestamp)
    row = {
        "source_type": _require_text(source_type, "source_type"),
        "provider": _clean_text(provider) or "unknown",
        "source_id": _clean_text(source_id) or "unknown",
        "source_timestamp_utc": timestamp,
        "source_hash": _clean_text(source_hash) or None,
        "source_schema_version": _clean_text(source_schema_version) or None,
        "provider_capability": {
            "mode": "offline",
            "supports_live_calls": False,
        },
    }
    collected[_canonical_json_text(row)] = MappingProxyType(row)


def _summary_counts(
    rows: Sequence[NBAPlayerPointsAssembledRow],
    diagnostics: Sequence[NBAPlayerPointsDuplicateDiagnostic],
) -> Mapping[str, int]:
    counts = {
        "total_rows": len(rows),
        "eligible_projection_rows": sum(1 for row in rows if row.projection_research_eligible),
        "eligible_probability_rows": sum(1 for row in rows if row.probability_research_eligible),
        "excluded_rows": sum(1 for row in rows if row.assembly_status == "excluded"),
        "quarantined_rows": sum(1 for row in rows if row.assembly_status == "quarantined"),
        "conflicting_rows": sum(1 for row in rows if row.assembly_status == "conflicting"),
        "duplicate_diagnostics": len(diagnostics),
    }
    return MappingProxyType(counts)


def _canonical_sort_rows(
    rows: Sequence[NBAPlayerPointsAssembledRow],
) -> tuple[NBAPlayerPointsAssembledRow, ...]:
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                _STATUS_SORT_ORDER[row.assembly_status],
                row.prediction_id,
                row.assembled_record_hash,
            ),
        )
    )


def _first_available_operating_date(records: Sequence[Mapping[str, object]]) -> date | None:
    for record in records:
        for section in ("crosswalk", "minutes"):
            payload = _optional_mapping_object(record, section)
            if payload is None:
                continue
            parsed = _optional_date(payload.get("operating_date"))
            if parsed is not None:
                return parsed
    return None


def _max_prediction_timestamp(records: Sequence[Mapping[str, object]]) -> datetime:
    timestamps = []
    for record in records:
        provenance = _required_mapping_object(record, "provenance")
        timestamps.append(
            _coerce_utc_datetime(
                _required_mapping_value(provenance, "prediction_timestamp_utc"),
                "prediction_timestamp_utc",
            )
        )
    return max(timestamps)


def _points_per_minute(
    projected_points: float | None,
    projected_minutes: float | None,
) -> float | None:
    if projected_points is None or projected_minutes is None or projected_minutes <= 0:
        return None
    return round(projected_points / projected_minutes, 6)


def _combined_identity_status(
    crosswalk: Mapping[str, Any],
    assembly_status: str,
) -> str:
    if assembly_status in {"conflicting", "quarantined"}:
        return assembly_status
    if (
        crosswalk.get("event_identity_status") == "resolved"
        and crosswalk.get("player_identity_status") == "resolved"
    ):
        return "resolved"
    return "unresolved"


def _unique_reasons(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text == "none" or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


def _contains_leakage(payload: object) -> bool:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in _LEAKAGE_FIELDS:
                return True
            if _contains_leakage(value):
                return True
    elif isinstance(payload, (list, tuple)):
        return any(_contains_leakage(item) for item in payload)
    return False


def _reject_target_game_leakage(payload: Mapping[str, object], context: str) -> None:
    if _contains_leakage(payload):
        raise NBAPlayerPointsAssemblyContractError(
            f"{context} contains target-game actual values"
        )


def _mapping_items(value: object) -> tuple[tuple[str, object], ...]:
    if not isinstance(value, Mapping):
        return ()
    return tuple((str(key), item) for key, item in value.items())


def _first_present(
    left: Mapping[str, object],
    right: Mapping[str, object],
    field_name: str,
) -> object:
    if field_name in left and left[field_name] not in (None, ""):
        return left[field_name]
    return right.get(field_name)


def _required_mapping_object(payload: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    value = _required_mapping_value(payload, field_name)
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be an object")
    return value


def _optional_mapping_object(payload: Mapping[str, object], field_name: str) -> Mapping[str, object] | None:
    value = payload.get(field_name)
    if value in (None, ""):
        return None
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be an object")
    return value


def _required_mapping_value(payload: Mapping[str, object], field_name: str) -> object:
    if field_name not in payload:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} is required")
    return payload[field_name]


def _normalize_market(value: object) -> str:
    text = _normalize_key(value, "market")
    if text != NBA_PLAYER_POINTS_MARKET:
        raise NBAPlayerPointsAssemblyContractError(
            f"unsupported market for this contract: {text!r}"
        )
    return text


def _normalize_identity_status(value: object) -> str:
    status = _normalize_key(value or "unresolved", "identity_status")
    if status not in {"resolved", "unresolved", "ambiguous", "conflicting", "quarantined"}:
        raise NBAPlayerPointsAssemblyContractError(f"unsupported identity_status: {status!r}")
    return status


def _normalize_key(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold().strip()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not normalized:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} is required")
    return normalized


def _optional_team(value: object, field_name: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip().upper()


def _coerce_optional_date(value: object) -> date | None:
    return _optional_date(value)


def _optional_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise NBAPlayerPointsAssemblyContractError("operating_date must be ISO date") from exc
    raise NBAPlayerPointsAssemblyContractError("operating_date must be a date")


def _coerce_utc_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise NBAPlayerPointsAssemblyContractError(
                f"{field_name} must be an ISO-8601 UTC timestamp"
            ) from exc
    else:
        raise NBAPlayerPointsAssemblyContractError(
            f"{field_name} must be an ISO-8601 UTC timestamp"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be UTC")
    return parsed.astimezone(_UTC)


def _optional_utc_datetime(value: object, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    return _coerce_utc_datetime(value, field_name)


def _format_utc(value: datetime) -> str:
    return _coerce_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _format_optional_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _format_utc(value)


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} is required")
    return text


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    text = _clean_text(value)
    return text or None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be finite")
    return parsed


def _require_nonnegative_number(value: object, field_name: str) -> float:
    parsed = _require_finite_number(value, field_name)
    if parsed < 0:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be non-negative")
    return parsed


def _optional_nonnegative_number(value: object, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    return _require_nonnegative_number(value, field_name)


def _optional_positive_number(value: object, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    parsed = _require_finite_number(value, field_name)
    if parsed <= 0:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be positive")
    return parsed


def _require_probability(value: object, field_name: str) -> float:
    parsed = _require_finite_number(value, field_name)
    if not 0.0 <= parsed <= 1.0:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be within [0, 1]")
    return parsed


def _optional_probability(value: object, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return _require_probability(value, field_name)
    except NBAPlayerPointsAssemblyContractError:
        return None


def _require_american_odds(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be an integer")
    if value == 0:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} cannot be 0")
    return value


def _optional_american_odds(value: object, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    return _require_american_odds(value, field_name)


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerPointsAssemblyContractError(f"{field_name} must be lowercase SHA-256")
    return text


def _validate_source_hashes(value: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise NBAPlayerPointsAssemblyContractError("source_hashes must be a non-empty mapping")
    normalized: dict[str, str] = {}
    for key, digest in value.items():
        name = _require_text(key, "source_hashes key")
        normalized[name] = _require_sha256(digest, f"source_hashes.{name}")
    return MappingProxyType(normalized)


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_stable_json_bytes(payload)).hexdigest()


def _stable_json_bytes(payload: object) -> bytes:
    return json.dumps(
        _json_ready(payload),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_json_text(payload: object) -> str:
    return _stable_json_bytes(payload).decode("utf-8")


def _json_ready(value: object) -> object:
    if isinstance(value, datetime):
        return _format_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    return value


def _json_clone(value: object) -> object:
    return json.loads(json.dumps(_json_ready(value), sort_keys=True, ensure_ascii=True))


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsAssemblyContractError("value must be an object")
    cloned = _json_clone(value)
    if not isinstance(cloned, dict):
        raise NBAPlayerPointsAssemblyContractError("value must be an object")
    return cloned


__all__ = [
    "NBA_PLAYER_POINTS_ASSEMBLY_ROW_FIELDS",
    "NBA_PLAYER_POINTS_ASSEMBLY_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_ASSEMBLY_STATUSES",
    "NBA_PLAYER_POINTS_ASSEMBLY_UTC_TIMESTAMP_FIELDS",
    "NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_PROBABILITY_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_PROBABILITY_STATUSES",
    "NBA_PLAYER_POINTS_PROBABILITY_SUM_TOLERANCE",
    "NBA_PLAYER_POINTS_PROJECTION_RESEARCH_MINUTES_STATUSES",
    "NBA_PLAYER_POINTS_PROJECTION_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SOURCE_MANIFEST_SCHEMA_VERSION",
    "NBAPlayerPointsAssembledRow",
    "NBAPlayerPointsAssemblyBatchResult",
    "NBAPlayerPointsAssemblyContractError",
    "NBAPlayerPointsDuplicateDiagnostic",
    "NBAPlayerPointsProbabilityValidation",
    "NBAPlayerPointsProjectionEvidence",
    "NBAPlayerPointsSourceManifestPreview",
    "assemble_nba_player_points_batch",
    "assemble_player_points_row",
    "assembly_schema_definition",
    "build_projection_evidence",
    "build_source_manifest_preview",
    "generate_preview_prediction_id",
    "projection_evidence_schema",
    "validate_assembled_rows",
]
