"""Offline NBA player-points settlement contracts.

This module is research-only. It consumes validated prediction rows, resolved
crosswalk rows, and offline final-stat fixture rows without provider I/O, file
writes, runner entrypoints, ledgers, scoring changes, grading changes, Kelly
changes, or bankroll-facing side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Final, Mapping, Sequence

from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
    NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    NBAPlayerPointsProviderCapability,
    NBAPlayerPointsResearchRow,
    NBAPlayerPointsResearchSchemaError,
    normalize_player_name,
    toronto_operating_date,
)


NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION: Final = "nba-player-points-settlement-v1"

NBA_PLAYER_POINTS_SETTLEMENT_STATUSES: Final = (
    "settled",
    "pending",
    "void",
    "unresolved",
    "ambiguous",
    "conflicting",
    "manual_review_required",
)

NBA_PLAYER_POINTS_PARTICIPATION_STATUSES: Final = (
    "participated",
    "did_not_participate",
    "zero_minutes",
    "unknown",
)

NBA_PLAYER_POINTS_GAME_STATUSES: Final = (
    "final",
    "not_final",
    "scheduled",
    "in_progress",
    "postponed",
    "cancelled",
    "suspended",
    "unknown",
)

NBA_PLAYER_POINTS_MANUAL_REVIEW_STATUSES: Final = (
    "not_required",
    "required",
    "quarantined",
)

NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS: Final = (
    "settlement_id",
    "prediction_id",
    "prediction_run_id",
    "model_id",
    "canonical_event_id",
    "provider_event_id",
    "provider_name",
    "operating_date",
    "commence_time_utc",
    "home_team",
    "away_team",
    "player_id",
    "canonical_player_name",
    "team",
    "opponent",
    "player_identity_status",
    "event_identity_status",
    "game_status",
    "game_final",
    "final_points",
    "actual_minutes",
    "participation_status",
    "settlement_status",
    "exclusion_reason",
    "manual_review_status",
    "settlement_timestamp_utc",
    "settlement_provider",
    "settlement_source_id",
    "settlement_source_timestamp_utc",
    "settlement_source_hash",
    "settlement_schema_version",
    "prediction_artifact_hash",
    "settlement_record_hash",
    "repository_commit_sha",
    "research_label",
)

NBA_PLAYER_POINTS_SETTLEMENT_UTC_TIMESTAMP_FIELDS: Final = (
    "commence_time_utc",
    "settlement_timestamp_utc",
    "settlement_source_timestamp_utc",
)

_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE: Final = re.compile(r"^[0-9a-f]{7,40}$")
_PROVIDER_NAME_RE: Final = re.compile(r"[^a-z0-9]+")
_ZERO_HASH: Final = "0" * 64


class NBAPlayerPointsSettlementSchemaError(NBAPlayerPointsResearchSchemaError):
    """Raised when the settlement contract fails closed."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsFinalStatSettlementEvidence:
    """One offline final-stat row suitable for settlement matching."""

    provider_name: str
    provider_event_id: str | None
    canonical_event_id: str | None
    prediction_id: str | None
    operating_date: date
    commence_time_utc: datetime
    home_team: str
    away_team: str
    player_id: str | None
    canonical_player_name: str | None
    team: str | None
    opponent: str | None
    game_status: str
    game_final: bool
    final_points: float | None
    actual_minutes: float | None
    participation_status: str
    source_timestamp_utc: datetime
    source_row_id: str
    source_hash: str
    unsupported_field_reasons: Mapping[str, str] = field(default_factory=dict)
    raw_evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_name", _normalize_provider_name(self.provider_name))
        if self.provider_event_id is not None:
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
        if self.prediction_id is not None:
            object.__setattr__(
                self,
                "prediction_id",
                _require_identifier(self.prediction_id, "prediction_id"),
            )
        object.__setattr__(
            self,
            "commence_time_utc",
            _coerce_utc_datetime(self.commence_time_utc, "commence_time_utc"),
        )
        if self.operating_date != toronto_operating_date(self.commence_time_utc):
            raise NBAPlayerPointsSettlementSchemaError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        object.__setattr__(self, "home_team", _normalize_team(self.home_team, "home_team"))
        object.__setattr__(self, "away_team", _normalize_team(self.away_team, "away_team"))
        if self.player_id is not None:
            object.__setattr__(self, "player_id", _require_identifier(self.player_id, "player_id"))
        if self.canonical_player_name is not None:
            object.__setattr__(
                self,
                "canonical_player_name",
                _require_text(self.canonical_player_name, "canonical_player_name"),
            )
        if self.team is not None:
            object.__setattr__(self, "team", _normalize_team(self.team, "team"))
        if self.opponent is not None:
            object.__setattr__(self, "opponent", _normalize_team(self.opponent, "opponent"))
        object.__setattr__(self, "game_status", _normalize_game_status(self.game_status))
        if not isinstance(self.game_final, bool):
            raise NBAPlayerPointsSettlementSchemaError("game_final must be boolean")
        if self.game_status == "final" and self.game_final is not True:
            raise NBAPlayerPointsSettlementSchemaError("final game_status requires game_final=true")
        if self.game_status != "final" and self.game_final is True:
            raise NBAPlayerPointsSettlementSchemaError("game_final=true requires game_status='final'")
        if self.final_points is not None:
            object.__setattr__(
                self,
                "final_points",
                _require_nonnegative_number(self.final_points, "final_points"),
            )
        if self.actual_minutes is not None:
            object.__setattr__(
                self,
                "actual_minutes",
                _require_nonnegative_number(self.actual_minutes, "actual_minutes"),
            )
        object.__setattr__(
            self,
            "participation_status",
            _normalize_participation_status(
                self.participation_status,
                actual_minutes=self.actual_minutes,
            ),
        )
        object.__setattr__(
            self,
            "source_timestamp_utc",
            _coerce_utc_datetime(self.source_timestamp_utc, "source_timestamp_utc"),
        )
        object.__setattr__(self, "source_row_id", _require_identifier(self.source_row_id, "source_row_id"))
        object.__setattr__(
            self,
            "source_hash",
            _require_sha256(self.source_hash, "source_hash"),
        )
        object.__setattr__(
            self,
            "unsupported_field_reasons",
            _normalized_reasons(self.unsupported_field_reasons),
        )
        object.__setattr__(self, "raw_evidence", MappingProxyType(dict(self.raw_evidence)))

    @property
    def authoritative_identity(self) -> tuple[str | None, str | None]:
        return (self.canonical_event_id or self.provider_event_id, self.player_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "provider_event_id": self.provider_event_id,
            "canonical_event_id": self.canonical_event_id,
            "prediction_id": self.prediction_id,
            "operating_date": self.operating_date.isoformat(),
            "operating_timezone": NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "player_id": self.player_id,
            "canonical_player_name": self.canonical_player_name,
            "team": self.team,
            "opponent": self.opponent,
            "game_status": self.game_status,
            "game_final": self.game_final,
            "final_points": self.final_points,
            "actual_minutes": self.actual_minutes,
            "participation_status": self.participation_status,
            "source_timestamp_utc": _format_utc(self.source_timestamp_utc),
            "source_row_id": self.source_row_id,
            "source_hash": self.source_hash,
            "unsupported_fields": dict(self.unsupported_field_reasons),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementProviderMappingResult:
    """Pure offline final-stat mapper result."""

    provider: NBAPlayerPointsProviderCapability
    rows: tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        for row in self.rows:
            if not isinstance(row, NBAPlayerPointsFinalStatSettlementEvidence):
                raise TypeError("rows must contain NBAPlayerPointsFinalStatSettlementEvidence values")
        object.__setattr__(self, "warnings", tuple(_require_text(item, "warnings") for item in self.warnings))


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementRow:
    """Complete validated settlement row for offline research only."""

    settlement_id: str
    prediction_id: str
    prediction_run_id: str
    model_id: str
    canonical_event_id: str
    provider_event_id: str
    provider_name: str
    operating_date: date
    commence_time_utc: datetime
    home_team: str
    away_team: str
    player_id: str
    canonical_player_name: str
    team: str
    opponent: str
    player_identity_status: str
    event_identity_status: str
    game_status: str
    game_final: bool
    final_points: float | None
    actual_minutes: float | None
    participation_status: str
    settlement_status: str
    exclusion_reason: str
    manual_review_status: str
    settlement_timestamp_utc: datetime
    settlement_provider: str
    settlement_source_id: str
    settlement_source_timestamp_utc: datetime
    settlement_source_hash: str
    settlement_schema_version: str
    prediction_artifact_hash: str
    repository_commit_sha: str
    research_label: str = NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL
    settlement_record_hash: str = ""

    def __post_init__(self) -> None:
        if self.settlement_schema_version != NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION:
            raise NBAPlayerPointsSettlementSchemaError(
                f"unsupported settlement_schema_version: {self.settlement_schema_version!r}"
            )
        for field_name in (
            "settlement_id",
            "prediction_id",
            "prediction_run_id",
            "model_id",
            "canonical_event_id",
            "provider_event_id",
            "player_id",
            "settlement_source_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_identifier(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "provider_name", _normalize_provider_name(self.provider_name))
        object.__setattr__(self, "home_team", _normalize_team(self.home_team, "home_team"))
        object.__setattr__(self, "away_team", _normalize_team(self.away_team, "away_team"))
        object.__setattr__(self, "team", _normalize_team(self.team, "team"))
        object.__setattr__(self, "opponent", _normalize_team(self.opponent, "opponent"))
        object.__setattr__(
            self,
            "canonical_player_name",
            _require_text(self.canonical_player_name, "canonical_player_name"),
        )
        for field_name in (
            "commence_time_utc",
            "settlement_timestamp_utc",
            "settlement_source_timestamp_utc",
        ):
            object.__setattr__(
                self,
                field_name,
                _coerce_utc_datetime(getattr(self, field_name), field_name),
            )
        if self.operating_date != toronto_operating_date(self.commence_time_utc):
            raise NBAPlayerPointsSettlementSchemaError(
                "operating_date must equal the America/Toronto date for commence_time_utc"
            )
        _require_identity_status(self.player_identity_status, "player_identity_status")
        _require_identity_status(self.event_identity_status, "event_identity_status")
        object.__setattr__(self, "game_status", _normalize_game_status(self.game_status))
        if not isinstance(self.game_final, bool):
            raise NBAPlayerPointsSettlementSchemaError("game_final must be boolean")
        if self.final_points is not None:
            object.__setattr__(
                self,
                "final_points",
                _require_nonnegative_number(self.final_points, "final_points"),
            )
        if self.actual_minutes is not None:
            object.__setattr__(
                self,
                "actual_minutes",
                _require_nonnegative_number(self.actual_minutes, "actual_minutes"),
            )
        object.__setattr__(
            self,
            "participation_status",
            _normalize_participation_status(
                self.participation_status,
                actual_minutes=self.actual_minutes,
            ),
        )
        _require_status(
            self.settlement_status,
            "settlement_status",
            NBA_PLAYER_POINTS_SETTLEMENT_STATUSES,
        )
        object.__setattr__(self, "exclusion_reason", _require_text(self.exclusion_reason, "exclusion_reason"))
        _require_status(
            self.manual_review_status,
            "manual_review_status",
            NBA_PLAYER_POINTS_MANUAL_REVIEW_STATUSES,
        )
        object.__setattr__(self, "settlement_provider", _normalize_provider_name(self.settlement_provider))
        object.__setattr__(
            self,
            "settlement_source_hash",
            _require_sha256(self.settlement_source_hash, "settlement_source_hash"),
        )
        object.__setattr__(
            self,
            "prediction_artifact_hash",
            _require_sha256(self.prediction_artifact_hash, "prediction_artifact_hash"),
        )
        commit_sha = _require_text(self.repository_commit_sha, "repository_commit_sha").casefold()
        if _COMMIT_SHA_RE.fullmatch(commit_sha) is None:
            raise NBAPlayerPointsSettlementSchemaError(
                "repository_commit_sha must be a 7-40 character lowercase git SHA"
            )
        object.__setattr__(self, "repository_commit_sha", commit_sha)
        if self.research_label != NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL:
            raise NBAPlayerPointsSettlementSchemaError("research_label is unsupported")
        self._validate_status_contract()
        object.__setattr__(self, "settlement_record_hash", _canonical_payload_sha256(self._to_payload(False)))

    @property
    def prediction_identity(self) -> tuple[str, str, str]:
        return (self.prediction_id, self.prediction_run_id, self.model_id)

    def _validate_status_contract(self) -> None:
        if self.settlement_status == "settled":
            if self.event_identity_status != "resolved" or self.player_identity_status != "resolved":
                raise NBAPlayerPointsSettlementSchemaError("settled rows require resolved identities")
            if self.game_status != "final" or self.game_final is not True:
                raise NBAPlayerPointsSettlementSchemaError("settled rows require final game status")
            if self.final_points is None:
                raise NBAPlayerPointsSettlementSchemaError("settled rows require final_points")
            if self.actual_minutes is None:
                raise NBAPlayerPointsSettlementSchemaError("settled rows require actual_minutes")
            if self.participation_status not in {"participated", "zero_minutes"}:
                raise NBAPlayerPointsSettlementSchemaError(
                    "settled rows require participated or zero_minutes participation"
                )
            if self.manual_review_status != "not_required":
                raise NBAPlayerPointsSettlementSchemaError("settled rows cannot require manual review")
            if self.exclusion_reason != "none":
                raise NBAPlayerPointsSettlementSchemaError("settled rows require exclusion_reason='none'")
        if self.settlement_status == "pending" and self.game_final is True:
            raise NBAPlayerPointsSettlementSchemaError("pending rows cannot have game_final=true")
        if self.settlement_status in {"conflicting", "ambiguous"}:
            if self.manual_review_status != "quarantined":
                raise NBAPlayerPointsSettlementSchemaError(
                    "conflicting and ambiguous rows must be quarantined"
                )
        if self.settlement_status == "manual_review_required":
            if self.manual_review_status != "required":
                raise NBAPlayerPointsSettlementSchemaError(
                    "manual_review_required rows require manual_review_status='required'"
                )
        if self.settlement_status == "void" and self.exclusion_reason == "none":
            raise NBAPlayerPointsSettlementSchemaError("void rows require an exclusion_reason")

    def _to_payload(self, include_record_hash: bool) -> dict[str, object]:
        payload = {
            "settlement_id": self.settlement_id,
            "prediction_id": self.prediction_id,
            "prediction_run_id": self.prediction_run_id,
            "model_id": self.model_id,
            "canonical_event_id": self.canonical_event_id,
            "provider_event_id": self.provider_event_id,
            "provider_name": self.provider_name,
            "operating_date": self.operating_date.isoformat(),
            "commence_time_utc": _format_utc(self.commence_time_utc),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "player_id": self.player_id,
            "canonical_player_name": self.canonical_player_name,
            "team": self.team,
            "opponent": self.opponent,
            "player_identity_status": self.player_identity_status,
            "event_identity_status": self.event_identity_status,
            "game_status": self.game_status,
            "game_final": self.game_final,
            "final_points": self.final_points,
            "actual_minutes": self.actual_minutes,
            "participation_status": self.participation_status,
            "settlement_status": self.settlement_status,
            "exclusion_reason": self.exclusion_reason,
            "manual_review_status": self.manual_review_status,
            "settlement_timestamp_utc": _format_utc(self.settlement_timestamp_utc),
            "settlement_provider": self.settlement_provider,
            "settlement_source_id": self.settlement_source_id,
            "settlement_source_timestamp_utc": _format_utc(self.settlement_source_timestamp_utc),
            "settlement_source_hash": self.settlement_source_hash,
            "settlement_schema_version": self.settlement_schema_version,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "repository_commit_sha": self.repository_commit_sha,
            "research_label": self.research_label,
        }
        if include_record_hash:
            payload["settlement_record_hash"] = self.settlement_record_hash
        return payload

    def to_dict(self) -> dict[str, object]:
        return self._to_payload(True)


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSettlementResult:
    """Pure offline settlement result with quarantined diagnostics."""

    rows: tuple[NBAPlayerPointsSettlementRow, ...]
    diagnostics: Mapping[str, tuple[Mapping[str, object], ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        for row in self.rows:
            if not isinstance(row, NBAPlayerPointsSettlementRow):
                raise TypeError("rows must contain NBAPlayerPointsSettlementRow values")
        normalized: dict[str, tuple[Mapping[str, object], ...]] = {}
        for key, value in dict(self.diagnostics).items():
            if isinstance(value, tuple):
                entries = value
            elif isinstance(value, list):
                entries = tuple(value)
            else:
                entries = (value,)  # type: ignore[assignment]
            normalized[_require_text(key, "diagnostics key")] = tuple(
                MappingProxyType(dict(entry)) for entry in entries if isinstance(entry, Mapping)
            )
        object.__setattr__(self, "diagnostics", MappingProxyType(normalized))

    @property
    def settled_rows(self) -> tuple[NBAPlayerPointsSettlementRow, ...]:
        return tuple(row for row in self.rows if row.settlement_status == "settled")

    @property
    def quarantined_rows(self) -> tuple[NBAPlayerPointsSettlementRow, ...]:
        return tuple(
            row
            for row in self.rows
            if row.settlement_status in {"ambiguous", "conflicting", "manual_review_required"}
        )

    def to_dicts(self) -> tuple[dict[str, object], ...]:
        return tuple(row.to_dict() for row in self.rows)


def settlement_schema_definition() -> dict[str, object]:
    """Return the versioned settlement schema contract."""

    return {
        "schema_version": NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        "operating_timezone": NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
        "utc_timestamp_fields": list(NBA_PLAYER_POINTS_SETTLEMENT_UTC_TIMESTAMP_FIELDS),
        "required_fields": list(NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS),
        "settlement_statuses": list(NBA_PLAYER_POINTS_SETTLEMENT_STATUSES),
        "participation_statuses": list(NBA_PLAYER_POINTS_PARTICIPATION_STATUSES),
        "matching_priority": [
            "prediction_id_exact",
            "canonical_event_id_plus_player_id",
            "approved_provider_event_id_mapping_plus_player_id",
            "unresolved",
        ],
        "research_label": NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
    }


def settlement_provider_capability_matrix() -> dict[str, dict[str, object]]:
    """Return explicit offline settlement provider field support."""

    return {
        capability.provider_name: capability.to_matrix_row()
        for capability in NBA_PLAYER_POINTS_SETTLEMENT_PROVIDER_CAPABILITIES
    }


def map_balldontlie_final_stats_fixture(
    payload: Mapping[str, object],
) -> NBAPlayerPointsSettlementProviderMappingResult:
    """Map BallDontLie-shaped offline stat rows without provider calls."""

    return _map_final_stats_payload(payload, BALLDONTLIE_SETTLEMENT_CAPABILITY)


def map_api_nba_final_stats_fixture(
    payload: Mapping[str, object],
) -> NBAPlayerPointsSettlementProviderMappingResult:
    """Map API-NBA-shaped offline stat rows without provider calls."""

    return _map_final_stats_payload(payload, API_NBA_SETTLEMENT_CAPABILITY)


def map_sportsdataio_final_stats_fixture(
    payload: Mapping[str, object],
) -> NBAPlayerPointsSettlementProviderMappingResult:
    """Map SportsDataIO-shaped offline stat rows without provider calls."""

    return _map_final_stats_payload(payload, SPORTSDATAIO_SETTLEMENT_CAPABILITY)


def settle_nba_player_points_predictions(
    prediction_rows: Sequence[NBAPlayerPointsResearchRow | Mapping[str, object]],
    crosswalk_rows: Sequence[object],
    final_stat_rows: Sequence[NBAPlayerPointsFinalStatSettlementEvidence | Mapping[str, object]],
    *,
    settlement_timestamp_utc: datetime | str,
    repository_commit_sha: str | None = None,
    research_label: str = NBA_PLAYER_POINTS_RESEARCH_ONLY_LABEL,
) -> NBAPlayerPointsSettlementResult:
    """Settle predictions from offline evidence only.

    The matching hierarchy is exact prediction ID, exact canonical event/player,
    approved provider-event mapping/player, then unresolved. Name-only rows are
    never eligible settlement matches.
    """

    predictions = tuple(_prediction_from_row(row) for row in prediction_rows)
    final_rows = tuple(_final_stat_from_row(row) for row in final_stat_rows)
    settlement_time = _coerce_utc_datetime(settlement_timestamp_utc, "settlement_timestamp_utc")
    repo_sha = _resolve_repository_commit_sha(repository_commit_sha, predictions)
    provider_event_map = _provider_event_mappings(crosswalk_rows)
    identity_rows = tuple(_crosswalk_identity(row) for row in crosswalk_rows)

    diagnostics: dict[str, list[Mapping[str, object]]] = {}
    settlements: list[NBAPlayerPointsSettlementRow] = []
    seen_prediction_ids: set[str] = set()
    for prediction in predictions:
        if prediction.prediction_id in seen_prediction_ids:
            raise NBAPlayerPointsSettlementSchemaError(
                f"duplicate prediction reference: {prediction.prediction_id}"
            )
        seen_prediction_ids.add(prediction.prediction_id)

        identity = _identity_for_prediction(prediction, identity_rows)
        row, row_diagnostics = _settle_one_prediction(
            prediction=prediction,
            identity=identity,
            final_rows=final_rows,
            provider_event_map=provider_event_map,
            settlement_timestamp_utc=settlement_time,
            repository_commit_sha=repo_sha,
            research_label=research_label,
        )
        settlements.append(row)
        for key, entries in row_diagnostics.items():
            diagnostics.setdefault(key, []).extend(entries)

    return NBAPlayerPointsSettlementResult(
        rows=validate_settlement_rows(settlements),
        diagnostics={key: tuple(value) for key, value in diagnostics.items()},
    )


def validate_settlement_rows(
    rows: Sequence[NBAPlayerPointsSettlementRow],
) -> tuple[NBAPlayerPointsSettlementRow, ...]:
    """Validate settlement collection uniqueness without writing ledgers."""

    normalized = tuple(rows)
    settlement_ids: set[str] = set()
    prediction_ids: set[str] = set()
    for index, row in enumerate(normalized):
        if not isinstance(row, NBAPlayerPointsSettlementRow):
            raise TypeError("rows must contain NBAPlayerPointsSettlementRow values")
        if row.settlement_id in settlement_ids:
            raise NBAPlayerPointsSettlementSchemaError(
                f"duplicate settlement_id at rows[{index}]"
            )
        settlement_ids.add(row.settlement_id)
        if row.prediction_id in prediction_ids:
            raise NBAPlayerPointsSettlementSchemaError(
                f"multiple settlements for prediction_id at rows[{index}]"
            )
        prediction_ids.add(row.prediction_id)
    return normalized


def validate_settlement_prediction_link(
    settlement: NBAPlayerPointsSettlementRow,
    prediction: NBAPlayerPointsResearchRow | Mapping[str, object],
) -> NBAPlayerPointsSettlementRow:
    """Validate that settlement integrity fields still match the prediction."""

    if not isinstance(settlement, NBAPlayerPointsSettlementRow):
        raise TypeError("settlement must be an NBAPlayerPointsSettlementRow")
    prediction_view = _prediction_from_row(prediction)
    if settlement.prediction_id != prediction_view.prediction_id:
        raise NBAPlayerPointsSettlementSchemaError("prediction_id mismatch")
    if settlement.prediction_run_id != prediction_view.prediction_run_id:
        raise NBAPlayerPointsSettlementSchemaError("prediction_run_id mismatch")
    if settlement.model_id != prediction_view.model_id:
        raise NBAPlayerPointsSettlementSchemaError("model_id mismatch")
    if settlement.prediction_artifact_hash != prediction_view.artifact_hash:
        raise NBAPlayerPointsSettlementSchemaError("prediction_artifact_hash mismatch")
    return settlement


def source_fixture_hash(payload: Mapping[str, object]) -> str:
    """Return a deterministic SHA-256 for an offline source fixture payload."""

    return _canonical_payload_sha256(payload)


def _settle_one_prediction(
    *,
    prediction: _PredictionView,
    identity: _CrosswalkIdentity,
    final_rows: tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...],
    provider_event_map: Mapping[str, str],
    settlement_timestamp_utc: datetime,
    repository_commit_sha: str,
    research_label: str,
) -> tuple[NBAPlayerPointsSettlementRow, dict[str, list[Mapping[str, object]]]]:
    diagnostics: dict[str, list[Mapping[str, object]]] = {}

    if identity.event_identity_status != "resolved" or identity.player_identity_status != "resolved":
        return (
            _build_nonfinal_settlement_row(
                prediction=prediction,
                identity=identity,
                status="unresolved",
                exclusion_reason="identity_unresolved",
                manual_review_status="required",
                settlement_timestamp_utc=settlement_timestamp_utc,
                repository_commit_sha=repository_commit_sha,
                research_label=research_label,
            ),
            diagnostics,
        )

    candidates, method, candidate_diagnostics = _matching_candidates(
        prediction,
        final_rows,
        provider_event_map,
    )
    for key, entries in candidate_diagnostics.items():
        diagnostics.setdefault(key, []).extend(entries)

    if not candidates:
        return (
            _build_nonfinal_settlement_row(
                prediction=prediction,
                identity=identity,
                status="unresolved",
                exclusion_reason=_missing_match_reason(prediction, final_rows, provider_event_map),
                manual_review_status="required",
                settlement_timestamp_utc=settlement_timestamp_utc,
                repository_commit_sha=repository_commit_sha,
                research_label=research_label,
            ),
            diagnostics,
        )

    if method == "prediction_id_identity_conflict":
        return (
            _build_conflict_settlement_row(
                prediction=prediction,
                identity=identity,
                candidates=candidates,
                status="conflicting",
                exclusion_reason="prediction_id_identity_conflict",
                settlement_timestamp_utc=settlement_timestamp_utc,
                repository_commit_sha=repository_commit_sha,
                research_label=research_label,
            ),
            diagnostics,
        )

    unique_candidates = _collapse_identical_candidates(candidates)
    if len(unique_candidates) < len(candidates):
        diagnostics.setdefault("duplicate_identical_replay", []).append(
            {
                "prediction_id": prediction.prediction_id,
                "source_row_ids": [row.source_row_id for row in candidates],
                "matching_method": method,
            }
        )

    if len(unique_candidates) > 1:
        conflict_reason = _candidate_conflict_reason(unique_candidates)
        status = "conflicting" if conflict_reason != "multiple_final_stat_candidates" else "ambiguous"
        diagnostics.setdefault(status, []).append(
            {
                "prediction_id": prediction.prediction_id,
                "matching_method": method,
                "conflict_reason": conflict_reason,
                "source_row_ids": [row.source_row_id for row in unique_candidates],
                "source_hashes": [row.source_hash for row in unique_candidates],
            }
        )
        return (
            _build_conflict_settlement_row(
                prediction=prediction,
                identity=identity,
                candidates=unique_candidates,
                status=status,
                exclusion_reason=conflict_reason,
                settlement_timestamp_utc=settlement_timestamp_utc,
                repository_commit_sha=repository_commit_sha,
                research_label=research_label,
            ),
            diagnostics,
        )

    candidate = unique_candidates[0]
    return (
        _build_settlement_from_final_stat(
            prediction=prediction,
            identity=identity,
            final_row=candidate,
            matching_method=method,
            settlement_timestamp_utc=settlement_timestamp_utc,
            repository_commit_sha=repository_commit_sha,
            research_label=research_label,
        ),
        diagnostics,
    )


def _build_settlement_from_final_stat(
    *,
    prediction: _PredictionView,
    identity: _CrosswalkIdentity,
    final_row: NBAPlayerPointsFinalStatSettlementEvidence,
    matching_method: str,
    settlement_timestamp_utc: datetime,
    repository_commit_sha: str,
    research_label: str,
) -> NBAPlayerPointsSettlementRow:
    status, exclusion_reason, manual_review_status = _settlement_status_for_final_row(final_row)
    settlement_source_id = final_row.source_row_id
    settlement_id = _settlement_id(
        prediction.prediction_id,
        prediction.artifact_hash,
        final_row.provider_name,
        settlement_source_id,
        matching_method,
    )
    return NBAPlayerPointsSettlementRow(
        settlement_id=settlement_id,
        prediction_id=prediction.prediction_id,
        prediction_run_id=prediction.prediction_run_id,
        model_id=prediction.model_id,
        canonical_event_id=prediction.canonical_event_id,
        provider_event_id=prediction.provider_event_id,
        provider_name=prediction.provider_name,
        operating_date=prediction.operating_date,
        commence_time_utc=prediction.commence_time_utc,
        home_team=identity.home_team or prediction.team,
        away_team=identity.away_team or prediction.opponent,
        player_id=prediction.player_id,
        canonical_player_name=final_row.canonical_player_name or prediction.canonical_player_name,
        team=prediction.team,
        opponent=prediction.opponent,
        player_identity_status=identity.player_identity_status,
        event_identity_status=identity.event_identity_status,
        game_status=final_row.game_status,
        game_final=final_row.game_final,
        final_points=final_row.final_points,
        actual_minutes=final_row.actual_minutes,
        participation_status=final_row.participation_status,
        settlement_status=status,
        exclusion_reason=exclusion_reason,
        manual_review_status=manual_review_status,
        settlement_timestamp_utc=settlement_timestamp_utc,
        settlement_provider=final_row.provider_name,
        settlement_source_id=settlement_source_id,
        settlement_source_timestamp_utc=final_row.source_timestamp_utc,
        settlement_source_hash=final_row.source_hash,
        settlement_schema_version=NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        prediction_artifact_hash=prediction.artifact_hash,
        repository_commit_sha=repository_commit_sha,
        research_label=research_label,
    )


def _build_nonfinal_settlement_row(
    *,
    prediction: _PredictionView,
    identity: _CrosswalkIdentity,
    status: str,
    exclusion_reason: str,
    manual_review_status: str,
    settlement_timestamp_utc: datetime,
    repository_commit_sha: str,
    research_label: str,
) -> NBAPlayerPointsSettlementRow:
    source_id = f"{status}:{prediction.prediction_id}:{exclusion_reason}"
    source_hash = _canonical_payload_sha256(
        {
            "prediction_id": prediction.prediction_id,
            "prediction_artifact_hash": prediction.artifact_hash,
            "status": status,
            "exclusion_reason": exclusion_reason,
            "event_identity_status": identity.event_identity_status,
            "player_identity_status": identity.player_identity_status,
        }
    )
    return NBAPlayerPointsSettlementRow(
        settlement_id=_settlement_id(
            prediction.prediction_id,
            prediction.artifact_hash,
            "offline_settlement_contract",
            source_id,
            status,
        ),
        prediction_id=prediction.prediction_id,
        prediction_run_id=prediction.prediction_run_id,
        model_id=prediction.model_id,
        canonical_event_id=prediction.canonical_event_id,
        provider_event_id=prediction.provider_event_id,
        provider_name=prediction.provider_name,
        operating_date=prediction.operating_date,
        commence_time_utc=prediction.commence_time_utc,
        home_team=identity.home_team or prediction.team,
        away_team=identity.away_team or prediction.opponent,
        player_id=prediction.player_id,
        canonical_player_name=prediction.canonical_player_name,
        team=prediction.team,
        opponent=prediction.opponent,
        player_identity_status=identity.player_identity_status,
        event_identity_status=identity.event_identity_status,
        game_status="unknown",
        game_final=False,
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        settlement_status=status,
        exclusion_reason=exclusion_reason,
        manual_review_status=manual_review_status,
        settlement_timestamp_utc=settlement_timestamp_utc,
        settlement_provider="offline_settlement_contract",
        settlement_source_id=source_id,
        settlement_source_timestamp_utc=settlement_timestamp_utc,
        settlement_source_hash=source_hash,
        settlement_schema_version=NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        prediction_artifact_hash=prediction.artifact_hash,
        repository_commit_sha=repository_commit_sha,
        research_label=research_label,
    )


def _build_conflict_settlement_row(
    *,
    prediction: _PredictionView,
    identity: _CrosswalkIdentity,
    candidates: tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...],
    status: str,
    exclusion_reason: str,
    settlement_timestamp_utc: datetime,
    repository_commit_sha: str,
    research_label: str,
) -> NBAPlayerPointsSettlementRow:
    source_id = "conflict:" + "|".join(row.source_row_id for row in candidates)
    source_hash = _canonical_payload_sha256(
        {
            "prediction_id": prediction.prediction_id,
            "prediction_artifact_hash": prediction.artifact_hash,
            "candidate_source_hashes": [row.source_hash for row in candidates],
            "candidate_source_ids": [row.source_row_id for row in candidates],
            "exclusion_reason": exclusion_reason,
        }
    )
    first = candidates[0]
    return NBAPlayerPointsSettlementRow(
        settlement_id=_settlement_id(
            prediction.prediction_id,
            prediction.artifact_hash,
            "offline_settlement_contract",
            source_id,
            status,
        ),
        prediction_id=prediction.prediction_id,
        prediction_run_id=prediction.prediction_run_id,
        model_id=prediction.model_id,
        canonical_event_id=prediction.canonical_event_id,
        provider_event_id=prediction.provider_event_id,
        provider_name=prediction.provider_name,
        operating_date=prediction.operating_date,
        commence_time_utc=prediction.commence_time_utc,
        home_team=identity.home_team or prediction.team,
        away_team=identity.away_team or prediction.opponent,
        player_id=prediction.player_id,
        canonical_player_name=prediction.canonical_player_name,
        team=prediction.team,
        opponent=prediction.opponent,
        player_identity_status=identity.player_identity_status,
        event_identity_status=identity.event_identity_status,
        game_status=first.game_status,
        game_final=first.game_final,
        final_points=None,
        actual_minutes=None,
        participation_status="unknown",
        settlement_status=status,
        exclusion_reason=exclusion_reason,
        manual_review_status="quarantined",
        settlement_timestamp_utc=settlement_timestamp_utc,
        settlement_provider="offline_settlement_contract",
        settlement_source_id=source_id,
        settlement_source_timestamp_utc=max(row.source_timestamp_utc for row in candidates),
        settlement_source_hash=source_hash,
        settlement_schema_version=NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        prediction_artifact_hash=prediction.artifact_hash,
        repository_commit_sha=repository_commit_sha,
        research_label=research_label,
    )


def _settlement_status_for_final_row(
    final_row: NBAPlayerPointsFinalStatSettlementEvidence,
) -> tuple[str, str, str]:
    if final_row.game_status == "postponed":
        return ("void", "game_postponed", "not_required")
    if final_row.game_status == "cancelled":
        return ("void", "game_cancelled", "not_required")
    if final_row.game_status == "suspended":
        return ("pending", "game_suspended", "required")
    if final_row.game_final is not True:
        return ("pending", "game_not_final", "required")
    if final_row.participation_status == "did_not_participate":
        return ("void", "did_not_participate", "not_required")
    if final_row.final_points is None:
        return ("manual_review_required", "missing_final_points", "required")
    if final_row.actual_minutes is None:
        return ("manual_review_required", "missing_actual_minutes", "required")
    if final_row.participation_status == "unknown":
        return ("manual_review_required", "unknown_participation", "required")
    return ("settled", "none", "not_required")


def _matching_candidates(
    prediction: _PredictionView,
    final_rows: tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...],
    provider_event_map: Mapping[str, str],
) -> tuple[
    tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...],
    str,
    dict[str, list[Mapping[str, object]]],
]:
    diagnostics: dict[str, list[Mapping[str, object]]] = {}
    exact = tuple(row for row in final_rows if row.prediction_id == prediction.prediction_id)
    if exact:
        invalid = tuple(row for row in exact if _identity_conflict(prediction, row, provider_event_map))
        if invalid:
            diagnostics.setdefault("conflicting", []).append(
                {
                    "prediction_id": prediction.prediction_id,
                    "matching_method": "prediction_id_identity_conflict",
                    "conflict_reason": "prediction_id_identity_conflict",
                    "source_row_ids": [row.source_row_id for row in invalid],
                    "source_hashes": [row.source_hash for row in invalid],
                }
            )
            return invalid, "prediction_id_identity_conflict", diagnostics
        return exact, "prediction_id_exact", diagnostics

    canonical = tuple(
        row
        for row in final_rows
        if row.canonical_event_id == prediction.canonical_event_id
        and row.player_id == prediction.player_id
    )
    if canonical:
        return canonical, "canonical_event_id_plus_player_id", diagnostics

    provider_mapped = tuple(
        row
        for row in final_rows
        if row.player_id == prediction.player_id
        and row.provider_event_id is not None
        and provider_event_map.get(row.provider_event_id) == prediction.canonical_event_id
    )
    if provider_mapped:
        return provider_mapped, "approved_provider_event_id_mapping_plus_player_id", diagnostics

    return (), "unresolved", diagnostics


def _missing_match_reason(
    prediction: _PredictionView,
    final_rows: tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...],
    provider_event_map: Mapping[str, str],
) -> str:
    normalized_prediction_name = normalize_player_name(prediction.canonical_player_name)
    has_event_without_player_id = any(
        row.player_id is None
        and (
            row.canonical_event_id == prediction.canonical_event_id
            or (
                row.provider_event_id is not None
                and provider_event_map.get(row.provider_event_id) == prediction.canonical_event_id
            )
        )
        and row.canonical_player_name is not None
        and normalize_player_name(row.canonical_player_name) == normalized_prediction_name
        for row in final_rows
    )
    if has_event_without_player_id:
        return "missing_player_id"
    has_player_without_event_id = any(
        row.player_id == prediction.player_id
        and row.canonical_event_id is None
        and row.provider_event_id is None
        for row in final_rows
    )
    if has_player_without_event_id:
        return "missing_event_id"
    return "no_final_stat_match"


def _identity_conflict(
    prediction: _PredictionView,
    final_row: NBAPlayerPointsFinalStatSettlementEvidence,
    provider_event_map: Mapping[str, str],
) -> bool:
    if final_row.player_id is not None and final_row.player_id != prediction.player_id:
        return True
    event_id = final_row.canonical_event_id
    if event_id is None and final_row.provider_event_id is not None:
        event_id = provider_event_map.get(final_row.provider_event_id)
    if event_id is not None and event_id != prediction.canonical_event_id:
        return True
    return False


def _collapse_identical_candidates(
    rows: tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...],
) -> tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...]:
    unique: list[NBAPlayerPointsFinalStatSettlementEvidence] = []
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        key = (
            row.provider_name,
            row.provider_event_id,
            row.canonical_event_id,
            row.prediction_id,
            row.player_id,
            row.game_status,
            row.game_final,
            row.final_points,
            row.actual_minutes,
            row.participation_status,
            row.source_row_id,
            row.source_hash,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return tuple(unique)


def _candidate_conflict_reason(
    rows: tuple[NBAPlayerPointsFinalStatSettlementEvidence, ...],
) -> str:
    if len({row.player_id for row in rows}) > 1:
        return "conflicting_player_id"
    if len({row.canonical_event_id or row.provider_event_id for row in rows}) > 1:
        return "conflicting_event_id"
    if len({row.game_final for row in rows}) > 1:
        return "conflicting_game_finality"
    if len({row.final_points for row in rows}) > 1:
        return "conflicting_final_points"
    if len({row.actual_minutes for row in rows}) > 1:
        return "conflicting_actual_minutes"
    if len({row.source_row_id for row in rows}) > 1:
        return "multiple_final_stat_candidates"
    return "multiple_final_stat_candidates"


def _map_final_stats_payload(
    payload: Mapping[str, object],
    capability: NBAPlayerPointsProviderCapability,
) -> NBAPlayerPointsSettlementProviderMappingResult:
    provider_name = _normalize_provider_name(payload.get("provider_name", capability.provider_name))
    if provider_name != capability.provider_name:
        raise NBAPlayerPointsSettlementSchemaError("unsupported settlement final-stat fixture provider")
    default_source_timestamp = _coerce_utc_datetime(
        _first_value(payload.get("source_timestamp_utc"), payload.get("stats_timestamp_utc")),
        "source_timestamp_utc",
    )
    rows_value = payload.get("rows")
    if not isinstance(rows_value, list):
        raise NBAPlayerPointsSettlementSchemaError("rows must be a list")
    rows: list[NBAPlayerPointsFinalStatSettlementEvidence] = []
    warnings: list[str] = []
    for index, row in enumerate(rows_value):
        if not isinstance(row, Mapping):
            warnings.append(f"rows[{index}] ignored: not an object")
            continue
        rows.append(_map_provider_row(row, capability, default_source_timestamp, index))
    return NBAPlayerPointsSettlementProviderMappingResult(
        provider=capability,
        rows=tuple(rows),
        warnings=tuple(warnings),
    )


def _map_provider_row(
    row: Mapping[str, object],
    capability: NBAPlayerPointsProviderCapability,
    default_source_timestamp: datetime,
    index: int,
) -> NBAPlayerPointsFinalStatSettlementEvidence:
    raw = dict(row)
    source_hash = _source_hash_with_optional_validation(raw)
    provider_event_id = _optional_identifier(
        _first_value(
            raw.get("provider_event_id"),
            raw.get("game_id"),
            raw.get("GameID"),
            _path_value(raw, "game.id"),
        ),
        "provider_event_id",
    )
    canonical_event_id = _optional_identifier(raw.get("canonical_event_id"), "canonical_event_id")
    prediction_id = _optional_identifier(raw.get("prediction_id"), "prediction_id")
    commence_time = _coerce_utc_datetime(
        _first_value(
            raw.get("commence_time_utc"),
            raw.get("commence_time"),
            raw.get("game_datetime_utc"),
            raw.get("date_time_utc"),
            _path_value(raw, "game.date_time_utc"),
            _path_value(raw, "game.datetime_utc"),
        ),
        "commence_time_utc",
    )
    operating_date = _coerce_operating_date(raw.get("operating_date"), commence_time)
    team = _optional_team(
        _first_value(
            raw.get("team"),
            raw.get("Team"),
            raw.get("team_abbreviation"),
            raw.get("teamAbbreviation"),
            _path_value(raw, "team.abbreviation"),
            _path_value(raw, "team.code"),
        ),
        "team",
    )
    opponent = _optional_team(
        _first_value(raw.get("opponent"), raw.get("Opponent"), raw.get("opponent_abbreviation")),
        "opponent",
    )
    home_team = _normalize_team(
        _first_value(
            raw.get("home_team"),
            raw.get("HomeTeam"),
            _path_value(raw, "game.home_team.abbreviation"),
            team,
        ),
        "home_team",
    )
    away_team = _normalize_team(
        _first_value(
            raw.get("away_team"),
            raw.get("visitor_team"),
            raw.get("AwayTeam"),
            _path_value(raw, "game.visitor_team.abbreviation"),
            _path_value(raw, "game.away_team.abbreviation"),
            opponent,
        ),
        "away_team",
    )
    player_id = _optional_identifier(
        _first_value(raw.get("player_id"), raw.get("PlayerID"), _path_value(raw, "player.id")),
        "player_id",
    )
    player_name = _optional_text(
        _first_value(
            raw.get("canonical_player_name"),
            raw.get("player_name"),
            raw.get("Name"),
            raw.get("name"),
            _join_name(_path_value(raw, "player.first_name"), _path_value(raw, "player.last_name")),
            _join_name(_path_value(raw, "player.firstname"), _path_value(raw, "player.lastname")),
        )
    )
    final_points = _optional_nonnegative_number(
        _first_present(raw, "final_points", "points", "pts", "Points"),
        "final_points",
    )
    actual_minutes = _parse_minutes_value(
        _first_present(raw, "actual_minutes", "minutes", "min", "Minutes"),
        "actual_minutes",
    )
    game_status = _normalize_game_status(
        _first_value(
            raw.get("game_status"),
            raw.get("status"),
            raw.get("Status"),
            _path_value(raw, "game.status"),
            "unknown",
        )
    )
    game_final = _coerce_game_final(
        _first_value(raw.get("game_final"), raw.get("is_final"), raw.get("IsClosed")),
        game_status,
    )
    participation = _normalize_participation_status(
        _first_value(
            raw.get("participation_status"),
            raw.get("player_status"),
            raw.get("PlayerStatus"),
            "unknown",
        ),
        actual_minutes=actual_minutes,
    )
    source_timestamp = _coerce_utc_datetime(
        _first_value(raw.get("source_timestamp_utc"), raw.get("updated_at"), default_source_timestamp),
        "source_timestamp_utc",
    )
    source_row_id = _require_identifier(
        _first_value(raw.get("source_row_id"), raw.get("id"), raw.get("StatID"), f"{capability.provider_name}:{index}"),
        "source_row_id",
    )
    return NBAPlayerPointsFinalStatSettlementEvidence(
        provider_name=capability.provider_name,
        provider_event_id=provider_event_id,
        canonical_event_id=canonical_event_id,
        prediction_id=prediction_id,
        operating_date=operating_date,
        commence_time_utc=commence_time,
        home_team=home_team,
        away_team=away_team,
        player_id=player_id,
        canonical_player_name=player_name,
        team=team,
        opponent=opponent,
        game_status=game_status,
        game_final=game_final,
        final_points=final_points,
        actual_minutes=actual_minutes,
        participation_status=participation,
        source_timestamp_utc=source_timestamp,
        source_row_id=source_row_id,
        source_hash=source_hash,
        unsupported_field_reasons=capability.unsupported_field_reasons,
        raw_evidence=raw,
    )


@dataclass(frozen=True, slots=True)
class _PredictionView:
    prediction_id: str
    prediction_run_id: str
    model_id: str
    provider_event_id: str
    provider_name: str
    canonical_event_id: str
    operating_date: date
    commence_time_utc: datetime
    team: str
    opponent: str
    player_id: str
    canonical_player_name: str
    artifact_hash: str
    repository_commit_sha: str


@dataclass(frozen=True, slots=True)
class _CrosswalkIdentity:
    canonical_event_id: str | None
    canonical_player_id: str | None
    event_identity_status: str
    player_identity_status: str
    home_team: str | None
    away_team: str | None
    provider_event_id: str | None


def _prediction_from_row(row: NBAPlayerPointsResearchRow | Mapping[str, object]) -> _PredictionView:
    payload = _row_to_dict(row)
    commence_time = _coerce_utc_datetime(payload.get("commence_time_utc"), "commence_time_utc")
    operating_date = _coerce_operating_date(payload.get("operating_date"), commence_time)
    artifact_hash = _require_sha256(payload.get("artifact_hash"), "prediction artifact_hash")
    return _PredictionView(
        prediction_id=_require_identifier(payload.get("prediction_id"), "prediction_id"),
        prediction_run_id=_require_identifier(payload.get("prediction_run_id"), "prediction_run_id"),
        model_id=_require_identifier(payload.get("model_id"), "model_id"),
        provider_event_id=_require_identifier(payload.get("provider_event_id"), "provider_event_id"),
        provider_name=_normalize_provider_name(payload.get("provider_name", "the_odds_api_nba")),
        canonical_event_id=_require_identifier(payload.get("canonical_event_id"), "canonical_event_id"),
        operating_date=operating_date,
        commence_time_utc=commence_time,
        team=_normalize_team(payload.get("team"), "team"),
        opponent=_normalize_team(payload.get("opponent"), "opponent"),
        player_id=_require_identifier(payload.get("player_id"), "player_id"),
        canonical_player_name=_require_text(
            _first_value(payload.get("canonical_player_name"), payload.get("player_name")),
            "canonical_player_name",
        ),
        artifact_hash=artifact_hash,
        repository_commit_sha=_require_text(payload.get("repository_commit_sha"), "repository_commit_sha"),
    )


def _final_stat_from_row(
    row: NBAPlayerPointsFinalStatSettlementEvidence | Mapping[str, object],
) -> NBAPlayerPointsFinalStatSettlementEvidence:
    if isinstance(row, NBAPlayerPointsFinalStatSettlementEvidence):
        return row
    if not isinstance(row, Mapping):
        raise TypeError("final_stat_rows must contain settlement evidence or mappings")
    source_hash = _require_sha256(row.get("source_hash"), "source_hash")
    commence_time = _coerce_utc_datetime(row.get("commence_time_utc"), "commence_time_utc")
    return NBAPlayerPointsFinalStatSettlementEvidence(
        provider_name=_required_mapping_value(row, "provider_name"),
        provider_event_id=_optional_identifier(row.get("provider_event_id"), "provider_event_id"),
        canonical_event_id=_optional_identifier(row.get("canonical_event_id"), "canonical_event_id"),
        prediction_id=_optional_identifier(row.get("prediction_id"), "prediction_id"),
        operating_date=_coerce_operating_date(row.get("operating_date"), commence_time),
        commence_time_utc=commence_time,
        home_team=_required_mapping_value(row, "home_team"),
        away_team=_required_mapping_value(row, "away_team"),
        player_id=_optional_identifier(row.get("player_id"), "player_id"),
        canonical_player_name=_optional_text(row.get("canonical_player_name")),
        team=_optional_team(row.get("team"), "team"),
        opponent=_optional_team(row.get("opponent"), "opponent"),
        game_status=_required_mapping_value(row, "game_status"),
        game_final=_required_mapping_value(row, "game_final"),
        final_points=_optional_nonnegative_number(row.get("final_points"), "final_points"),
        actual_minutes=_optional_nonnegative_number(row.get("actual_minutes"), "actual_minutes"),
        participation_status=_required_mapping_value(row, "participation_status"),
        source_timestamp_utc=_coerce_utc_datetime(row.get("source_timestamp_utc"), "source_timestamp_utc"),
        source_row_id=_required_mapping_value(row, "source_row_id"),
        source_hash=source_hash,
        unsupported_field_reasons=row.get("unsupported_fields", {}),
        raw_evidence=row,
    )


def _row_to_dict(row: Mapping[str, object] | object) -> dict[str, object]:
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "to_dict"):
        result = row.to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    raise TypeError("row must be a mapping or expose to_dict()")


def _crosswalk_identity(row: object) -> _CrosswalkIdentity:
    payload = _row_to_dict(row)
    event_identity = _row_to_dict(payload.get("event_identity", {}))
    player_identity = _row_to_dict(payload.get("player_identity", {}))
    original = _row_to_dict(payload.get("original_odds_row", {}))
    return _CrosswalkIdentity(
        canonical_event_id=_optional_identifier(payload.get("canonical_event_id"), "canonical_event_id"),
        canonical_player_id=_optional_identifier(payload.get("canonical_player_id"), "canonical_player_id"),
        event_identity_status=_require_text(
            event_identity.get("event_identity_status", "unresolved"),
            "event_identity_status",
        ),
        player_identity_status=_require_text(
            player_identity.get("player_identity_status", "unresolved"),
            "player_identity_status",
        ),
        home_team=_optional_team(
            _first_value(event_identity.get("canonical_home_team"), event_identity.get("home_team")),
            "home_team",
        ),
        away_team=_optional_team(
            _first_value(event_identity.get("canonical_away_team"), event_identity.get("away_team")),
            "away_team",
        ),
        provider_event_id=_optional_identifier(
            _first_value(original.get("provider_event_id"), event_identity.get("provider_event_id")),
            "provider_event_id",
        ),
    )


def _identity_for_prediction(
    prediction: _PredictionView,
    identities: tuple[_CrosswalkIdentity, ...],
) -> _CrosswalkIdentity:
    for identity in identities:
        if (
            identity.canonical_event_id == prediction.canonical_event_id
            and identity.canonical_player_id == prediction.player_id
        ):
            return identity
        if identity.provider_event_id == prediction.provider_event_id:
            return identity
    return _CrosswalkIdentity(
        canonical_event_id=None,
        canonical_player_id=None,
        event_identity_status="unresolved",
        player_identity_status="unresolved",
        home_team=None,
        away_team=None,
        provider_event_id=None,
    )


def _provider_event_mappings(rows: Sequence[object]) -> Mapping[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        identity = _crosswalk_identity(row)
        if identity.event_identity_status == "resolved" and identity.canonical_event_id:
            if identity.provider_event_id:
                existing = mapping.get(identity.provider_event_id)
                if existing is not None and existing != identity.canonical_event_id:
                    raise NBAPlayerPointsSettlementSchemaError(
                        "provider_event_id maps to multiple canonical events"
                    )
                mapping[identity.provider_event_id] = identity.canonical_event_id
    return MappingProxyType(mapping)


def _resolve_repository_commit_sha(
    value: str | None,
    predictions: tuple[_PredictionView, ...],
) -> str:
    sha = _require_text(value or (predictions[0].repository_commit_sha if predictions else ""), "repository_commit_sha")
    sha = sha.casefold()
    if _COMMIT_SHA_RE.fullmatch(sha) is None:
        raise NBAPlayerPointsSettlementSchemaError(
            "repository_commit_sha must be a 7-40 character lowercase git SHA"
        )
    return sha


def _settlement_id(
    prediction_id: str,
    prediction_artifact_hash: str,
    provider_name: str,
    source_id: str,
    matching_method: str,
) -> str:
    digest = _canonical_payload_sha256(
        {
            "prediction_id": prediction_id,
            "prediction_artifact_hash": prediction_artifact_hash,
            "provider_name": provider_name,
            "source_id": source_id,
            "matching_method": matching_method,
            "settlement_schema_version": NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION,
        }
    )
    return f"nba-pps-{digest[:32]}"


def _source_hash_with_optional_validation(row: Mapping[str, object]) -> str:
    provided = _optional_text(row.get("source_hash"))
    payload = {key: value for key, value in row.items() if key != "source_hash"}
    computed = _canonical_payload_sha256(payload)
    if provided is None:
        return computed
    provided_hash = _require_sha256(provided, "source_hash")
    if provided_hash != computed:
        raise NBAPlayerPointsSettlementSchemaError("source_hash mismatch")
    return provided_hash


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        _json_ready(payload),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _path_value(payload: Mapping[str, object], dotted_path: str) -> object:
    current: object = payload
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _join_name(first: object, last: object) -> str | None:
    text = f"{_clean_text(first)} {_clean_text(last)}".strip()
    return text or None


def _first_present(payload: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _first_value(*values: object) -> object:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _required_mapping_value(payload: Mapping[str, object], field_name: str) -> object:
    if field_name not in payload:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} is required")
    return payload[field_name]


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
            raise NBAPlayerPointsSettlementSchemaError("operating_date must be ISO date") from exc
    else:
        raise NBAPlayerPointsSettlementSchemaError("operating_date must be a date")
    if parsed != expected:
        raise NBAPlayerPointsSettlementSchemaError(
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
            raise NBAPlayerPointsSettlementSchemaError(
                f"{field_name} must be an ISO-8601 UTC timestamp"
            ) from exc
    else:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be an ISO-8601 UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be UTC")
    return parsed.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    return _coerce_utc_datetime(value, "timestamp").isoformat().replace("+00:00", "Z")


def _normalize_provider_name(value: object) -> str:
    provider = _require_text(value, "provider_name").casefold()
    return _PROVIDER_NAME_RE.sub("_", provider).strip("_")


def _normalize_team(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    return re.sub(r"\s+", " ", text).strip().upper()


def _optional_team(value: object, field_name: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return _normalize_team(text, field_name)


def _normalize_game_status(value: object) -> str:
    text = _require_text(value, "game_status").casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    aliases = {
        "closed": "final",
        "complete": "final",
        "completed": "final",
        "final": "final",
        "final_ot": "final",
        "not_final": "not_final",
        "scheduled": "scheduled",
        "pre_game": "scheduled",
        "pregame": "scheduled",
        "in_progress": "in_progress",
        "live": "in_progress",
        "postponed": "postponed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "suspended": "suspended",
        "unknown": "unknown",
    }
    status = aliases.get(normalized, normalized)
    if status not in NBA_PLAYER_POINTS_GAME_STATUSES:
        raise NBAPlayerPointsSettlementSchemaError(f"unsupported game_status: {status!r}")
    return status


def _coerce_game_final(value: object, game_status: str) -> bool:
    if isinstance(value, bool):
        return value
    if value not in (None, ""):
        text = str(value).casefold().strip()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
        raise NBAPlayerPointsSettlementSchemaError("game_final must be boolean")
    return game_status == "final"


def _normalize_participation_status(value: object, *, actual_minutes: float | None) -> str:
    text = _require_text(value, "participation_status").casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    aliases = {
        "active": "participated",
        "played": "participated",
        "participated": "participated",
        "dnp": "did_not_participate",
        "did_not_dress": "did_not_participate",
        "did_not_play": "did_not_participate",
        "did_not_participate": "did_not_participate",
        "inactive": "did_not_participate",
        "listed_but_did_not_participate": "did_not_participate",
        "zero_minutes": "zero_minutes",
        "unknown": "unknown",
    }
    status = aliases.get(normalized, normalized)
    if status == "participated" and actual_minutes == 0:
        status = "zero_minutes"
    if status not in NBA_PLAYER_POINTS_PARTICIPATION_STATUSES:
        raise NBAPlayerPointsSettlementSchemaError(
            f"unsupported participation_status: {status!r}"
        )
    return status


def _parse_minutes_value(value: object, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be numeric")
    if isinstance(value, (int, float)):
        return _require_nonnegative_number(value, field_name)
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        minutes_text, seconds_text = text.split(":", 1)
        try:
            parsed = float(minutes_text) + float(seconds_text) / 60.0
        except ValueError as exc:
            raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be numeric") from exc
        return _require_nonnegative_number(parsed, field_name)
    try:
        return _require_nonnegative_number(float(text), field_name)
    except ValueError as exc:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be numeric") from exc


def _optional_nonnegative_number(value: object, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    return _require_nonnegative_number(value, field_name)


def _require_nonnegative_number(value: object, field_name: str) -> float:
    parsed = _require_finite_number(value, field_name)
    if parsed < 0:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be non-negative")
    return parsed


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be finite")
    return parsed


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} is required")
    return text


def _optional_identifier(value: object, field_name: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return _require_identifier(text, field_name)


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    text = _clean_text(value)
    return text or None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerPointsSettlementSchemaError(f"{field_name} must be lowercase SHA-256")
    return text


def _require_status(value: object, field_name: str, allowed: tuple[str, ...]) -> str:
    status = _require_text(value, field_name)
    if status not in allowed:
        raise NBAPlayerPointsSettlementSchemaError(f"unsupported {field_name}: {status!r}")
    return status


def _require_identity_status(value: object, field_name: str) -> str:
    status = _require_text(value, field_name)
    if status not in {"resolved", "unresolved", "ambiguous", "conflicting", "quarantined"}:
        raise NBAPlayerPointsSettlementSchemaError(f"unsupported {field_name}: {status!r}")
    return status


def _normalized_reasons(reasons: Mapping[str, str]) -> Mapping[str, str]:
    normalized = {
        _require_text(field_name, "unsupported field name"): _require_text(
            reason,
            f"unsupported_field_reasons.{field_name}",
        )
        for field_name, reason in dict(reasons).items()
    }
    return MappingProxyType(normalized)


BALLDONTLIE_SETTLEMENT_CAPABILITY: Final = NBAPlayerPointsProviderCapability(
    provider_name="balldontlie",
    provider_role="final_stats",
    source_type="fixture",
    mode="offline_test_fixture",
    supports_live_calls=False,
    available_fields=(
        "provider_event_id",
        "player_id",
        "final_points",
        "actual_minutes",
        "game_finality",
        "player_participation",
        "source_timestamp",
        "source_row_id",
    ),
    unsupported_field_reasons={
        "canonical_event_id": "BallDontLie fixture rows require crosswalk or explicit fixture canonical IDs",
    },
)

API_NBA_SETTLEMENT_CAPABILITY: Final = NBAPlayerPointsProviderCapability(
    provider_name="api_nba",
    provider_role="final_stats",
    source_type="fixture",
    mode="offline_test_fixture",
    supports_live_calls=False,
    available_fields=(
        "provider_event_id",
        "player_id",
        "final_points",
        "actual_minutes",
        "game_finality",
        "source_timestamp",
        "source_row_id",
    ),
    unsupported_field_reasons={
        "canonical_event_id": "API-NBA fixture rows require crosswalk or explicit fixture canonical IDs",
        "player_participation": "API-NBA stats rows do not independently prove inactive/DNP status",
    },
)

SPORTSDATAIO_SETTLEMENT_CAPABILITY: Final = NBAPlayerPointsProviderCapability(
    provider_name="sportsdataio",
    provider_role="final_stats",
    source_type="fixture",
    mode="offline_test_fixture",
    supports_live_calls=False,
    available_fields=(
        "provider_event_id",
        "player_id",
        "final_points",
        "actual_minutes",
        "game_finality",
        "player_participation",
        "source_timestamp",
        "source_row_id",
    ),
    unsupported_field_reasons={
        "canonical_event_id": "SportsDataIO fixture rows require crosswalk or explicit fixture canonical IDs",
    },
)

NBA_PLAYER_POINTS_SETTLEMENT_PROVIDER_CAPABILITIES: Final = (
    BALLDONTLIE_SETTLEMENT_CAPABILITY,
    API_NBA_SETTLEMENT_CAPABILITY,
    SPORTSDATAIO_SETTLEMENT_CAPABILITY,
)


__all__ = [
    "API_NBA_SETTLEMENT_CAPABILITY",
    "BALLDONTLIE_SETTLEMENT_CAPABILITY",
    "NBA_PLAYER_POINTS_GAME_STATUSES",
    "NBA_PLAYER_POINTS_MANUAL_REVIEW_STATUSES",
    "NBA_PLAYER_POINTS_PARTICIPATION_STATUSES",
    "NBA_PLAYER_POINTS_SETTLEMENT_PROVIDER_CAPABILITIES",
    "NBA_PLAYER_POINTS_SETTLEMENT_ROW_FIELDS",
    "NBA_PLAYER_POINTS_SETTLEMENT_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SETTLEMENT_STATUSES",
    "NBA_PLAYER_POINTS_SETTLEMENT_UTC_TIMESTAMP_FIELDS",
    "NBAPlayerPointsFinalStatSettlementEvidence",
    "NBAPlayerPointsSettlementProviderMappingResult",
    "NBAPlayerPointsSettlementResult",
    "NBAPlayerPointsSettlementRow",
    "NBAPlayerPointsSettlementSchemaError",
    "SPORTSDATAIO_SETTLEMENT_CAPABILITY",
    "map_api_nba_final_stats_fixture",
    "map_balldontlie_final_stats_fixture",
    "map_sportsdataio_final_stats_fixture",
    "settle_nba_player_points_predictions",
    "settlement_provider_capability_matrix",
    "settlement_schema_definition",
    "source_fixture_hash",
    "validate_settlement_prediction_link",
    "validate_settlement_rows",
]
