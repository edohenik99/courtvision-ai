"""Offline NBA player-points provider source adapters.

This module normalizes frozen, provider-shaped fixtures into in-memory records
accepted by the NBA player-points research contracts. It performs no provider
I/O, reads no credentials, writes no files, calculates no predictions, and does
not touch dashboards, runners, production selection, or betting-accounting
paths.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
from courtvision.sports.nba.player_points_assembly import (
    NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION,
)
from courtvision.sports.nba.player_points_crosswalk import (
    NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION,
)
from courtvision.sports.nba.player_points_research import (
    NBA_PLAYER_POINTS_MARKET,
    NBA_PLAYER_POINTS_OPERATING_TIMEZONE,
    decimal_odds_from_american,
    implied_probability_from_american,
    normalize_player_name,
    toronto_operating_date,
)


NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION: Final = (
    "nba-player-points-source-adapter-v1"
)
NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION: Final = "1.0.0"

NBA_PLAYER_POINTS_PREGAME_ODDS_FIXTURE_SCHEMA_VERSION: Final = (
    "nba-player-points-provider-pregame-odds-fixture-v1"
)
NBA_PLAYER_POINTS_CLOSING_ODDS_FIXTURE_SCHEMA_VERSION: Final = (
    "nba-player-points-provider-closing-odds-fixture-v1"
)
NBA_PLAYER_POINTS_SCHEDULE_IDENTITY_FIXTURE_SCHEMA_VERSION: Final = (
    "nba-player-points-provider-schedule-identity-fixture-v1"
)
NBA_PLAYER_POINTS_MINUTES_INPUT_FIXTURE_SCHEMA_VERSION: Final = (
    "nba-player-points-provider-minutes-input-fixture-v1"
)
NBA_PLAYER_POINTS_FINAL_STATS_FIXTURE_SCHEMA_VERSION: Final = (
    "nba-player-points-provider-final-stats-fixture-v1"
)

_UTC: Final = timezone.utc
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE: Final = re.compile(r"[^a-z0-9]+")
_SUPPORTED_MARKET_KEYS: Final = frozenset({"player_points"})
_LEAKAGE_KEYS: Final = frozenset(
    {
        "actual_minutes",
        "target_game_actual_minutes",
        "final_points",
        "target_game_final_points",
        "final_stats",
        "box_score",
    }
)
_SUPPORTED_GAME_STATUSES: Final = frozenset(
    {
        "final",
        "in_progress",
        "scheduled",
        "postponed",
        "cancelled",
        "suspended",
    }
)
_HASH_SELF_REFERENCE_KEYS: Final = frozenset({"source_hash"})


class NBAPlayerPointsSourceAdapterError(ValueError):
    """Raised when an adapter cannot safely normalize provider-shaped input."""


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSourceAdapterDiagnostic:
    """Preserved fail-closed diagnostic for a source adapter record."""

    category: str
    source_type: str
    reason: str
    source_id: str | None = None
    source_hash: str | None = None
    record: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _require_text(self.category, "category"))
        object.__setattr__(self, "source_type", _require_text(self.source_type, "source_type"))
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        if self.source_id is not None:
            object.__setattr__(self, "source_id", _require_text(self.source_id, "source_id"))
        if self.source_hash is not None:
            object.__setattr__(self, "source_hash", _require_sha256(self.source_hash, "source_hash"))
        object.__setattr__(self, "record", MappingProxyType(_json_clone_mapping(self.record)))

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "source_type": self.source_type,
            "reason": self.reason,
            "source_id": self.source_id,
            "source_hash": self.source_hash,
            "record": _json_clone(self.record),
        }


@dataclass(frozen=True, slots=True)
class NBAPlayerPointsSourceAdapterBatchResult:
    """Pure batch adapter result with preserved diagnostics."""

    normalized_records: tuple[Mapping[str, object], ...] = ()
    invalid_records: tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...] = ()
    unresolved_records: tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...] = ()
    ambiguous_records: tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...] = ()
    quarantined_records: tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...] = ()
    conflicting_records: tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...] = ()
    duplicate_diagnostics: tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...] = ()
    source_summary: Mapping[str, object] = field(default_factory=dict)
    capability_summary: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "normalized_records",
            tuple(MappingProxyType(_json_clone_mapping(record)) for record in self.normalized_records),
        )
        for field_name in (
            "invalid_records",
            "unresolved_records",
            "ambiguous_records",
            "quarantined_records",
            "conflicting_records",
            "duplicate_diagnostics",
        ):
            values = tuple(getattr(self, field_name))
            for item in values:
                if not isinstance(item, NBAPlayerPointsSourceAdapterDiagnostic):
                    raise TypeError(f"{field_name} must contain adapter diagnostics")
            object.__setattr__(self, field_name, values)
        object.__setattr__(self, "source_summary", MappingProxyType(_json_clone_mapping(self.source_summary)))
        object.__setattr__(
            self,
            "capability_summary",
            MappingProxyType(_json_clone_mapping(self.capability_summary)),
        )

    @property
    def ok(self) -> bool:
        return not (
            self.invalid_records
            or self.conflicting_records
            or self.quarantined_records
        )

    def records_by_type(self, source_type: str) -> tuple[Mapping[str, object], ...]:
        requested = _require_text(source_type, "source_type")
        return tuple(
            record for record in self.normalized_records if record.get("source_type") == requested
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "normalized_records": [_json_clone(record) for record in self.normalized_records],
            "invalid_records": [item.to_dict() for item in self.invalid_records],
            "unresolved_records": [item.to_dict() for item in self.unresolved_records],
            "ambiguous_records": [item.to_dict() for item in self.ambiguous_records],
            "quarantined_records": [item.to_dict() for item in self.quarantined_records],
            "conflicting_records": [item.to_dict() for item in self.conflicting_records],
            "duplicate_diagnostics": [item.to_dict() for item in self.duplicate_diagnostics],
            "source_summary": _json_clone(self.source_summary),
            "capability_summary": _json_clone(self.capability_summary),
        }


def normalize_pregame_player_points_odds(
    payload: Mapping[str, object] | bytes | bytearray | str,
) -> NBAPlayerPointsSourceAdapterBatchResult:
    """Normalize provider-shaped pregame player-points odds fixtures."""

    source_type = "pregame_player_points_odds"
    capability = _capability("fixture_pregame_odds_provider", source_type)
    raw_payload, schema_error = _load_payload(
        payload,
        expected_schema_version=NBA_PLAYER_POINTS_PREGAME_ODDS_FIXTURE_SCHEMA_VERSION,
    )
    if schema_error is not None:
        return _schema_failure_result(source_type, capability, schema_error, payload)

    provider = _normalize_provider(raw_payload.get("provider", "fixture_pregame_odds_provider"))
    source_schema_version = _require_text(raw_payload.get("schema_version"), "schema_version")
    records: list[Mapping[str, object]] = []
    diagnostics: list[NBAPlayerPointsSourceAdapterDiagnostic] = []
    events = _required_list(raw_payload, "events")
    for event_index, event in enumerate(events):
        if not isinstance(event, Mapping):
            diagnostics.append(_diagnostic("invalid", source_type, "event must be an object", record={"index": event_index}))
            continue
        try:
            fragments = _iter_market_fragments(event)
        except Exception as exc:
            diagnostics.append(_diagnostic("invalid", source_type, str(exc), record={"index": event_index, "event": event}))
            continue
        for record_index, fragment in enumerate(fragments):
            try:
                records.append(
                    _pregame_market_record(
                        provider=provider,
                        source_schema_version=source_schema_version,
                        fragment=fragment,
                    )
                )
            except Exception as exc:
                diagnostics.append(
                    _diagnostic(
                        "invalid",
                        source_type,
                        str(exc),
                        record={"event_index": event_index, "record_index": record_index, "fragment": fragment},
                    )
                )
    return _batch_result(records, diagnostics, capability)


def normalize_schedule_identity_sources(
    payload: Mapping[str, object] | bytes | bytearray | str,
) -> NBAPlayerPointsSourceAdapterBatchResult:
    """Normalize schedule, roster, and reviewed identity mapping fixtures."""

    source_type = "schedule_identity"
    capability = _capability("fixture_schedule_identity_provider", source_type)
    raw_payload, schema_error = _load_payload(
        payload,
        expected_schema_version=NBA_PLAYER_POINTS_SCHEDULE_IDENTITY_FIXTURE_SCHEMA_VERSION,
    )
    if schema_error is not None:
        return _schema_failure_result(source_type, capability, schema_error, payload)

    provider = _normalize_provider(raw_payload.get("provider", "fixture_schedule_identity_provider"))
    source_schema_version = _require_text(raw_payload.get("schema_version"), "schema_version")
    default_timestamp = _coerce_utc(raw_payload.get("source_timestamp_utc"), "source_timestamp_utc")
    records: list[Mapping[str, object]] = []
    diagnostics: list[NBAPlayerPointsSourceAdapterDiagnostic] = []

    for index, event in enumerate(_required_list(raw_payload, "events")):
        try:
            if not isinstance(event, Mapping):
                raise NBAPlayerPointsSourceAdapterError("event must be an object")
            record = _schedule_event_record(
                provider=provider,
                source_schema_version=source_schema_version,
                default_timestamp=default_timestamp,
                event=event,
            )
            if record.get("identity_status") == "resolved":
                records.append(record)
            else:
                diagnostics.append(
                    _identity_status_diagnostic(
                        record,
                        source_type=str(record["source_type"]),
                        status=str(record["identity_status"]),
                        reason=str(record.get("identity_reason") or record["identity_status"]),
                    )
                )
        except Exception as exc:
            diagnostics.append(_diagnostic("invalid", source_type, str(exc), record={"index": index, "event": event}))

    for index, player in enumerate(_required_list(raw_payload, "players")):
        try:
            if not isinstance(player, Mapping):
                raise NBAPlayerPointsSourceAdapterError("player must be an object")
            record = _roster_player_record(
                provider=provider,
                source_schema_version=source_schema_version,
                default_timestamp=default_timestamp,
                player=player,
            )
            if record.get("identity_status") == "resolved":
                records.append(record)
            else:
                diagnostics.append(
                    _identity_status_diagnostic(
                        record,
                        source_type=str(record["source_type"]),
                        status=str(record["identity_status"]),
                        reason=str(record.get("identity_reason") or record["identity_status"]),
                    )
                )
        except Exception as exc:
            diagnostics.append(_diagnostic("invalid", source_type, str(exc), record={"index": index, "player": player}))

    try:
        mapping_record, mapping_diagnostics = _reviewed_mapping_record(
            provider=provider,
            source_schema_version=source_schema_version,
            default_timestamp=default_timestamp,
            mapping_payload=raw_payload.get("reviewed_mappings", {}),
        )
        diagnostics.extend(mapping_diagnostics)
        if mapping_record is not None:
            records.append(mapping_record)
    except Exception as exc:
        diagnostics.append(_diagnostic("invalid", "reviewed_mapping_artifact", str(exc), record={"reviewed_mappings": raw_payload.get("reviewed_mappings")}))

    return _batch_result(records, diagnostics, capability)


def normalize_minutes_feature_inputs(
    payload: Mapping[str, object] | bytes | bytearray | str,
    *,
    feature_cutoff_timestamp_utc: datetime | str | None = None,
) -> NBAPlayerPointsSourceAdapterBatchResult:
    """Normalize pregame minutes input fixtures without projecting minutes."""

    source_type = "minutes_feature_input"
    capability = _capability("fixture_minutes_input_provider", source_type)
    raw_payload, schema_error = _load_payload(
        payload,
        expected_schema_version=NBA_PLAYER_POINTS_MINUTES_INPUT_FIXTURE_SCHEMA_VERSION,
    )
    if schema_error is not None:
        return _schema_failure_result(source_type, capability, schema_error, payload)

    provider = _normalize_provider(raw_payload.get("provider", "fixture_minutes_input_provider"))
    source_schema_version = _require_text(raw_payload.get("schema_version"), "schema_version")
    cutoff = _coerce_utc(
        feature_cutoff_timestamp_utc or raw_payload.get("feature_cutoff_timestamp_utc"),
        "feature_cutoff_timestamp_utc",
    )
    feature_timestamp = _coerce_utc(raw_payload.get("feature_timestamp_utc"), "feature_timestamp_utc")
    source_manifest_id = _require_text(raw_payload.get("source_manifest_id"), "source_manifest_id")
    repository_commit_sha = _require_text(raw_payload.get("repository_commit_sha"), "repository_commit_sha")
    records: list[Mapping[str, object]] = []
    diagnostics: list[NBAPlayerPointsSourceAdapterDiagnostic] = []

    for index, player in enumerate(_required_list(raw_payload, "players")):
        try:
            if not isinstance(player, Mapping):
                raise NBAPlayerPointsSourceAdapterError("player must be an object")
            if _contains_leakage(player):
                diagnostics.append(
                    _diagnostic(
                        "quarantined",
                        source_type,
                        "target-game actual or final-stat leakage is prohibited",
                        record={"index": index, "player": player},
                    )
                )
                continue
            record = _minutes_input_record(
                provider=provider,
                source_schema_version=source_schema_version,
                cutoff=cutoff,
                feature_timestamp=feature_timestamp,
                source_manifest_id=source_manifest_id,
                repository_commit_sha=repository_commit_sha,
                player=player,
            )
            records.append(record)
        except Exception as exc:
            diagnostics.append(_diagnostic("invalid", source_type, str(exc), record={"index": index, "player": player}))

    return _batch_result(records, diagnostics, capability)


def normalize_closing_player_points_odds(
    payload: Mapping[str, object] | bytes | bytearray | str,
    *,
    prediction_references: Mapping[str, Mapping[str, object]],
) -> NBAPlayerPointsSourceAdapterBatchResult:
    """Normalize provider-shaped market updates into closing observation inputs."""

    source_type = "closing_player_points_odds"
    capability = _capability("fixture_closing_odds_provider", source_type)
    raw_payload, schema_error = _load_payload(
        payload,
        expected_schema_version=NBA_PLAYER_POINTS_CLOSING_ODDS_FIXTURE_SCHEMA_VERSION,
    )
    if schema_error is not None:
        return _schema_failure_result(source_type, capability, schema_error, payload)

    provider = _normalize_provider(raw_payload.get("provider", "fixture_closing_odds_provider"))
    source_schema_version = _require_text(raw_payload.get("schema_version"), "schema_version")
    records: list[Mapping[str, object]] = []
    diagnostics: list[NBAPlayerPointsSourceAdapterDiagnostic] = []
    for event_index, event in enumerate(_required_list(raw_payload, "events")):
        if not isinstance(event, Mapping):
            diagnostics.append(_diagnostic("invalid", source_type, "event must be an object", record={"index": event_index}))
            continue
        try:
            fragments = _iter_market_fragments(event)
        except Exception as exc:
            diagnostics.append(_diagnostic("invalid", source_type, str(exc), record={"index": event_index, "event": event}))
            continue
        for record_index, fragment in enumerate(fragments):
            try:
                records.append(
                    _closing_record(
                        provider=provider,
                        source_schema_version=source_schema_version,
                        fragment=fragment,
                        prediction_references=prediction_references,
                    )
                )
            except Exception as exc:
                diagnostics.append(
                    _diagnostic(
                        "invalid",
                        source_type,
                        str(exc),
                        record={"event_index": event_index, "record_index": record_index, "fragment": fragment},
                    )
                )
    return _batch_result(records, diagnostics, capability)


def normalize_final_stat_sources(
    payload: Mapping[str, object] | bytes | bytearray | str,
) -> NBAPlayerPointsSourceAdapterBatchResult:
    """Normalize provider-shaped final game and box-score fixtures."""

    source_type = "final_stat"
    capability = _capability("fixture_final_stats_provider", source_type)
    raw_payload, schema_error = _load_payload(
        payload,
        expected_schema_version=NBA_PLAYER_POINTS_FINAL_STATS_FIXTURE_SCHEMA_VERSION,
    )
    if schema_error is not None:
        return _schema_failure_result(source_type, capability, schema_error, payload)

    provider = _normalize_provider(raw_payload.get("provider", "fixture_final_stats_provider"))
    source_schema_version = _require_text(raw_payload.get("schema_version"), "schema_version")
    default_timestamp = _coerce_utc(raw_payload.get("source_timestamp_utc"), "source_timestamp_utc")
    records: list[Mapping[str, object]] = []
    diagnostics: list[NBAPlayerPointsSourceAdapterDiagnostic] = []
    for game_index, game in enumerate(_required_list(raw_payload, "games")):
        try:
            if not isinstance(game, Mapping):
                raise NBAPlayerPointsSourceAdapterError("game must be an object")
            records.extend(
                _final_stat_records_for_game(
                    provider=provider,
                    source_schema_version=source_schema_version,
                    default_timestamp=default_timestamp,
                    game=game,
                )
            )
        except Exception as exc:
            diagnostics.append(_diagnostic("invalid", source_type, str(exc), record={"index": game_index, "game": game}))
    return _batch_result(records, diagnostics, capability)


def source_fixture_hash(payload: object) -> str:
    """Return the canonical SHA-256 hash used by source adapters."""

    return _source_hash(payload)


def adapter_capability_summary() -> dict[str, object]:
    """Return explicit offline capabilities for all source adapters."""

    capabilities = [
        _capability("fixture_pregame_odds_provider", "pregame_player_points_odds"),
        _capability("fixture_closing_odds_provider", "closing_player_points_odds"),
        _capability("fixture_schedule_identity_provider", "schedule_identity"),
        _capability("fixture_minutes_input_provider", "minutes_feature_input"),
        _capability("fixture_final_stats_provider", "final_stat"),
    ]
    return {
        "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
        "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        "capabilities": capabilities,
        "supports_live_calls": False,
        "reads_credentials": False,
        "writes_files": False,
    }


def _pregame_market_record(
    *,
    provider: str,
    source_schema_version: str,
    fragment: Mapping[str, object],
) -> Mapping[str, object]:
    market = _normalize_market_key(fragment.get("market_key"))
    line = _require_nonnegative_number(fragment.get("line"), "line")
    american_odds = _require_american_odds(fragment.get("american_odds"), "american_odds")
    timestamp = _coerce_utc(
        _first_value(fragment.get("market_timestamp_utc"), fragment.get("source_timestamp_utc")),
        "market_timestamp_utc",
    )
    provider_event_id = _require_identifier(fragment.get("provider_event_id"), "provider_event_id")
    sportsbook = _require_text(fragment.get("sportsbook"), "sportsbook")
    player_name = _require_text(fragment.get("provider_player_name"), "provider_player_name")
    source_id = _market_source_id(provider, fragment, prefix="market")
    source_hash = _source_hash(
        _source_hash_envelope(
            provider=provider,
            source_type="pregame_player_points_odds",
            source_schema_version=source_schema_version,
            fragment=_required_mapping(fragment, "raw_provider_fragment"),
        )
    )
    record = {
        "provider_name": provider,
        "provider": provider,
        "source_type": "pregame_player_points_odds",
        "source_id": source_id,
        "source_timestamp_utc": _format_utc(timestamp),
        "source_schema_version": source_schema_version,
        "source_hash": source_hash,
        "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
        "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        "provider_capability": _capability(provider, "pregame_player_points_odds"),
        "provider_event_id": provider_event_id,
        "provider_player_id": _optional_text(fragment.get("provider_player_id")),
        "provider_player_name": player_name,
        "player_name": player_name,
        "normalized_player_name": normalize_player_name(player_name),
        "sportsbook": sportsbook,
        "market": market,
        "side": _normalize_side(fragment.get("side")),
        "line": line,
        "american_odds": american_odds,
        "decimal_odds": decimal_odds_from_american(american_odds),
        "implied_probability": implied_probability_from_american(american_odds),
        "market_timestamp_utc": _format_utc(timestamp),
        "market_source_id": source_id,
        "market_source_hash": source_hash,
        "market_schema_version": NBA_PLAYER_POINTS_MARKET_SCHEMA_VERSION,
        "commence_time_utc": _format_utc(_coerce_utc(fragment.get("commence_time_utc"), "commence_time_utc")),
        "operating_date": toronto_operating_date(_coerce_utc(fragment.get("commence_time_utc"), "commence_time_utc")).isoformat(),
        "home_team": _require_text(fragment.get("home_team"), "home_team"),
        "away_team": _require_text(fragment.get("away_team"), "away_team"),
        "team": _require_text(fragment.get("team"), "team"),
    }
    opponent = _optional_text(fragment.get("opponent"))
    if opponent is not None:
        record["opponent"] = opponent
    return MappingProxyType(record)


def _schedule_event_record(
    *,
    provider: str,
    source_schema_version: str,
    default_timestamp: datetime,
    event: Mapping[str, object],
) -> Mapping[str, object]:
    provider_event_id = _require_identifier(event.get("provider_event_id"), "provider_event_id")
    commence = _coerce_utc(event.get("commence_time_utc"), "commence_time_utc")
    candidates = _candidate_list(event.get("canonical_candidates"))
    status = _identity_status(event.get("identity_status"), len(candidates))
    source_timestamp = _coerce_utc(event.get("source_timestamp_utc", default_timestamp), "source_timestamp_utc")
    fragment = {
        "provider": provider,
        "schema_version": source_schema_version,
        "event": _json_clone(event),
    }
    source_id = f"{provider}:schedule_event:{provider_event_id}"
    record: dict[str, object] = {
        "provider": provider,
        "source_type": "schedule_event",
        "source_id": source_id,
        "source_timestamp_utc": _format_utc(source_timestamp),
        "source_schema_version": source_schema_version,
        "source_hash": _source_hash(fragment),
        "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
        "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        "provider_capability": _capability(provider, "schedule_event"),
        "provider_event_id": provider_event_id,
        "provider_home_team": _require_text(event.get("home_team"), "home_team"),
        "provider_away_team": _require_text(event.get("away_team"), "away_team"),
        "provider_status": _require_text(event.get("status", "scheduled"), "status"),
        "commence_time_utc": _format_utc(commence),
        "operating_date": toronto_operating_date(commence).isoformat(),
        "canonical_candidates": candidates,
        "identity_status": status,
        "identity_reason": _identity_reason(status, candidates),
    }
    if status == "resolved":
        candidate = candidates[0]
        record.update(
            {
                "canonical_event_id": _require_identifier(candidate.get("canonical_event_id"), "canonical_event_id"),
                "home_team": _require_text(candidate.get("home_team"), "home_team"),
                "away_team": _require_text(candidate.get("away_team"), "away_team"),
                "canonical_home_team": _require_text(candidate.get("home_team"), "home_team"),
                "canonical_away_team": _require_text(candidate.get("away_team"), "away_team"),
            }
        )
    return MappingProxyType(record)


def _roster_player_record(
    *,
    provider: str,
    source_schema_version: str,
    default_timestamp: datetime,
    player: Mapping[str, object],
) -> Mapping[str, object]:
    provider_player_id = _optional_text(player.get("provider_player_id"))
    provider_player_name = _require_text(player.get("provider_player_name"), "provider_player_name")
    candidates = _candidate_list(player.get("canonical_candidates"))
    status = _identity_status(player.get("identity_status"), len(candidates))
    source_timestamp = _coerce_utc(player.get("source_timestamp_utc", default_timestamp), "source_timestamp_utc")
    source_id = f"{provider}:roster_player:{provider_player_id or normalize_player_name(provider_player_name)}"
    fragment = {
        "provider": provider,
        "schema_version": source_schema_version,
        "player": _json_clone(player),
    }
    record: dict[str, object] = {
        "provider": provider,
        "source_type": "roster_player",
        "source_id": source_id,
        "source_timestamp_utc": _format_utc(source_timestamp),
        "source_schema_version": source_schema_version,
        "source_hash": _source_hash(fragment),
        "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
        "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        "provider_capability": _capability(provider, "roster_player"),
        "provider_player_id": provider_player_id,
        "provider_player_name": provider_player_name,
        "normalized_provider_player_name": normalize_player_name(provider_player_name),
        "provider_team": _require_text(player.get("team"), "team"),
        "canonical_candidates": candidates,
        "identity_status": status,
        "identity_reason": _identity_reason(status, candidates),
    }
    if status == "resolved":
        candidate = candidates[0]
        record.update(
            {
                "player_id": _require_identifier(candidate.get("player_id"), "player_id"),
                "canonical_player_name": _require_text(candidate.get("canonical_player_name"), "canonical_player_name"),
                "canonical_team": _require_text(candidate.get("canonical_team"), "canonical_team"),
                "canonical_event_id": _optional_text(candidate.get("canonical_event_id")),
                "team": _require_text(candidate.get("canonical_team"), "canonical_team"),
            }
        )
    return MappingProxyType(record)


def _reviewed_mapping_record(
    *,
    provider: str,
    source_schema_version: str,
    default_timestamp: datetime,
    mapping_payload: object,
) -> tuple[Mapping[str, object] | None, list[NBAPlayerPointsSourceAdapterDiagnostic]]:
    if mapping_payload in (None, "", {}):
        return None, []
    if not isinstance(mapping_payload, Mapping):
        raise NBAPlayerPointsSourceAdapterError("reviewed_mappings must be an object")
    mapping_version = _require_text(mapping_payload.get("mapping_version"), "mapping_version")
    event_mappings, event_diags = _dedupe_mapping_entries(
        entries=_required_list(mapping_payload, "event_mappings"),
        key_fields=("provider_name", "provider_event_id"),
        value_fields=("canonical_event_id", "canonical_home_team", "canonical_away_team"),
        source_type="reviewed_event_mapping",
    )
    player_mappings, player_diags = _dedupe_mapping_entries(
        entries=_required_list(mapping_payload, "player_mappings"),
        key_fields=("provider_name", "provider_player_id", "provider_player_name", "mapping_type", "canonical_team"),
        value_fields=("player_id", "canonical_player_name", "canonical_team"),
        source_type="reviewed_player_mapping",
    )
    artifact = {
        "schema_version": NBA_PLAYER_POINTS_CROSSWALK_MAPPING_SCHEMA_VERSION,
        "mapping_version": mapping_version,
        "event_mappings": event_mappings,
        "player_mappings": player_mappings,
    }
    source_timestamp = _coerce_utc(mapping_payload.get("source_timestamp_utc", default_timestamp), "source_timestamp_utc")
    source_id = f"{provider}:reviewed_mapping:{mapping_version}"
    record = {
        "provider": provider,
        "source_type": "reviewed_mapping_artifact",
        "source_id": source_id,
        "source_timestamp_utc": _format_utc(source_timestamp),
        "source_schema_version": source_schema_version,
        "source_hash": _source_hash({"provider": provider, "reviewed_mappings": _json_clone(mapping_payload)}),
        "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
        "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        "provider_capability": _capability(provider, "reviewed_mapping_artifact"),
        "mapping_artifact": artifact,
    }
    return MappingProxyType(record), [*event_diags, *player_diags]


def _minutes_input_record(
    *,
    provider: str,
    source_schema_version: str,
    cutoff: datetime,
    feature_timestamp: datetime,
    source_manifest_id: str,
    repository_commit_sha: str,
    player: Mapping[str, object],
) -> Mapping[str, object]:
    provider_event_id = _require_identifier(player.get("provider_event_id"), "provider_event_id")
    player_id = _optional_text(player.get("player_id"))
    if player_id is None:
        raise NBAPlayerPointsSourceAdapterError("missing player identity")
    canonical_event_id = _optional_text(player.get("canonical_event_id"))
    if canonical_event_id is None:
        raise NBAPlayerPointsSourceAdapterError("missing event ID")
    commence = _coerce_utc(player.get("commence_time_utc"), "commence_time_utc")
    if not (feature_timestamp <= cutoff < commence):
        raise NBAPlayerPointsSourceAdapterError(
            "feature_timestamp_utc <= feature_cutoff_timestamp_utc < commence_time_utc is required"
        )

    baseline = _minutes_section(
        player,
        "baseline",
        cutoff=cutoff,
        required_fields=("season_minutes", "recent_minutes"),
        aliases={"season_minutes": "min_avg", "recent_minutes": "min_recent"},
    )
    baseline.update(
        {
            "provider_event_id": provider_event_id,
            "canonical_event_id": canonical_event_id,
            "player_id": player_id,
            "canonical_player_name": _require_text(player.get("canonical_player_name"), "canonical_player_name"),
            "team": _require_text(player.get("team"), "team"),
            "opponent": _require_text(player.get("opponent"), "opponent"),
            "commence_time_utc": _format_utc(commence),
            "event_identity_status": _require_text(player.get("event_identity_status", "resolved"), "event_identity_status"),
            "player_identity_status": _require_text(player.get("player_identity_status", "resolved"), "player_identity_status"),
        }
    )
    lineup = _minutes_section(player, "lineup", cutoff=cutoff)
    injury_availability = _minutes_section(player, "injury_availability", cutoff=cutoff)
    schedule = _minutes_section(player, "schedule", cutoff=cutoff)
    role_context = _minutes_section(player, "role_context", cutoff=cutoff)
    record = {
        "provider": provider,
        "source_type": "minutes_feature_input",
        "source_id": f"{provider}:minutes_feature_input:{provider_event_id}:{player_id}",
        "source_timestamp_utc": _format_utc(
            max(
                _coerce_utc(baseline["source_timestamp_utc"], "baseline.source_timestamp_utc"),
                _coerce_utc(lineup["source_timestamp_utc"], "lineup.source_timestamp_utc"),
                _coerce_utc(injury_availability["source_timestamp_utc"], "injury_availability.source_timestamp_utc"),
                _coerce_utc(schedule["source_timestamp_utc"], "schedule.source_timestamp_utc"),
                _coerce_utc(role_context["source_timestamp_utc"], "role_context.source_timestamp_utc"),
            )
        ),
        "source_schema_version": source_schema_version,
        "source_hash": _source_hash({"provider": provider, "player": _json_clone(player)}),
        "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
        "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        "provider_capability": _capability(provider, "minutes_feature_input"),
        "feature_timestamp_utc": _format_utc(feature_timestamp),
        "feature_cutoff_timestamp_utc": _format_utc(cutoff),
        "source_manifest_id": source_manifest_id,
        "repository_commit_sha": repository_commit_sha,
        "baseline": baseline,
        "lineup": lineup,
        "injury_availability": injury_availability,
        "schedule": schedule,
        "role_context": role_context,
        "feature_schema_version": NBA_PLAYER_MINUTES_FEATURE_SCHEMA_VERSION,
    }
    return MappingProxyType(record)


def _closing_record(
    *,
    provider: str,
    source_schema_version: str,
    fragment: Mapping[str, object],
    prediction_references: Mapping[str, Mapping[str, object]],
) -> Mapping[str, object]:
    market = _normalize_market_key(fragment.get("market_key"))
    line = _require_nonnegative_number(fragment.get("line"), "closing_line")
    american_odds = _require_american_odds(fragment.get("american_odds"), "closing_american_odds")
    timestamp = _coerce_utc(
        _first_value(fragment.get("observation_timestamp_utc"), fragment.get("source_timestamp_utc")),
        "observation_timestamp_utc",
    )
    update_timestamp = _coerce_utc(
        _first_value(fragment.get("source_market_update_timestamp_utc"), fragment.get("market_timestamp_utc"), timestamp),
        "source_market_update_timestamp_utc",
    )
    prediction_key = _require_text(fragment.get("prediction_reference_key"), "prediction_reference_key")
    reference_payload = prediction_references.get(prediction_key)
    if not isinstance(reference_payload, Mapping):
        raise NBAPlayerPointsSourceAdapterError("missing prediction reference")
    source_id = _market_source_id(provider, fragment, prefix="closing")
    source_hash = _source_hash(
        _source_hash_envelope(
            provider=provider,
            source_type="closing_player_points_odds",
            source_schema_version=source_schema_version,
            fragment=_required_mapping(fragment, "raw_provider_fragment"),
        )
    )
    record = {
        "provider": provider,
        "source_type": "closing_player_points_odds",
        "source_id": source_id,
        "source_timestamp_utc": _format_utc(update_timestamp),
        "source_schema_version": source_schema_version,
        "source_hash": source_hash,
        "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
        "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        "provider_capability": _capability(provider, "closing_player_points_odds"),
        "prediction_reference": _required_mapping(reference_payload, "prediction_reference"),
        "canonical_event_id": _require_identifier(reference_payload.get("canonical_event_id"), "canonical_event_id"),
        "provider_event_id": _require_identifier(fragment.get("provider_event_id"), "provider_event_id"),
        "player_id": _require_identifier(reference_payload.get("player_id"), "player_id"),
        "sportsbook": _require_text(fragment.get("sportsbook"), "sportsbook"),
        "market": market,
        "operating_date": _require_text(reference_payload.get("operating_date"), "operating_date"),
        "commence_time_utc": _format_utc(_coerce_utc(reference_payload.get("commence_time_utc"), "commence_time_utc")),
        "closing_line": line,
        "closing_american_odds": american_odds,
        "closing_market_status": _require_text(fragment.get("market_status", "open"), "closing_market_status"),
        "observation_timestamp_utc": _format_utc(timestamp),
        "source_market_update_timestamp_utc": _format_utc(update_timestamp),
        "closing_provider": provider,
        "closing_source_id": source_id,
        "closing_source_hash": source_hash,
    }
    return MappingProxyType(record)


def _final_stat_records_for_game(
    *,
    provider: str,
    source_schema_version: str,
    default_timestamp: datetime,
    game: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    provider_event_id = _require_identifier(game.get("provider_event_id"), "provider_event_id")
    commence = _coerce_utc(game.get("commence_time_utc"), "commence_time_utc")
    status = _normalize_game_status(game.get("game_status"))
    game_final = _coerce_game_final(game.get("game_final"), status)
    source_timestamp = _coerce_utc(game.get("source_timestamp_utc", default_timestamp), "source_timestamp_utc")
    players = _required_list(game, "players")
    rows: list[Mapping[str, object]] = []
    for index, player in enumerate(players):
        if not isinstance(player, Mapping):
            raise NBAPlayerPointsSourceAdapterError("player stat row must be an object")
        fragment = {"provider": provider, "schema_version": source_schema_version, "game": _json_clone(game), "player": _json_clone(player)}
        source_row_id = _require_identifier(
            _first_value(player.get("source_row_id"), f"{provider_event_id}:{player.get('player_id', index)}"),
            "source_row_id",
        )
        actual_minutes = _optional_nonnegative_number(player.get("actual_minutes"), "actual_minutes")
        participation = _normalize_participation_status(
            _first_value(player.get("participation_status"), "unknown"),
            actual_minutes=actual_minutes,
        )
        rows.append(
            MappingProxyType(
                {
                    "provider_name": provider,
                    "provider": provider,
                    "source_type": "final_stat",
                    "source_id": f"{provider}:final_stat:{source_row_id}",
                    "source_timestamp_utc": _format_utc(source_timestamp),
                    "source_schema_version": source_schema_version,
                    "source_hash": _source_hash(fragment),
                    "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
                    "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
                    "provider_capability": _capability(provider, "final_stat"),
                    "provider_event_id": provider_event_id,
                    "canonical_event_id": _optional_text(game.get("canonical_event_id")),
                    "prediction_id": _optional_text(player.get("prediction_id")),
                    "operating_date": toronto_operating_date(commence).isoformat(),
                    "commence_time_utc": _format_utc(commence),
                    "home_team": _require_text(game.get("home_team"), "home_team"),
                    "away_team": _require_text(game.get("away_team"), "away_team"),
                    "player_id": _optional_text(player.get("player_id")),
                    "canonical_player_name": _optional_text(player.get("canonical_player_name")),
                    "team": _optional_text(player.get("team")),
                    "opponent": _optional_text(player.get("opponent")),
                    "game_status": status,
                    "game_final": game_final,
                    "final_points": _optional_nonnegative_number(player.get("final_points"), "final_points"),
                    "actual_minutes": actual_minutes,
                    "participation_status": participation,
                    "source_row_id": source_row_id,
                    "unsupported_fields": {},
                }
            )
        )
    return tuple(rows)


def _iter_market_fragments(event: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    provider_event_id = _require_identifier(event.get("provider_event_id"), "provider_event_id")
    commence = _format_utc(_coerce_utc(event.get("commence_time_utc"), "commence_time_utc"))
    home_team = _require_text(event.get("home_team"), "home_team")
    away_team = _require_text(event.get("away_team"), "away_team")
    default_timestamp = _optional_text(event.get("source_timestamp_utc"))
    fragments: list[Mapping[str, object]] = []
    bookmakers = _required_list(event, "bookmakers")
    for book in bookmakers:
        if not isinstance(book, Mapping):
            raise NBAPlayerPointsSourceAdapterError("bookmaker must be an object")
        sportsbook = _require_text(_first_value(book.get("title"), book.get("key")), "sportsbook")
        for market in _required_list(book, "markets"):
            if not isinstance(market, Mapping):
                raise NBAPlayerPointsSourceAdapterError("market must be an object")
            market_key = _require_text(market.get("key"), "market_key")
            market_timestamp = _first_value(market.get("last_update"), book.get("last_update"), default_timestamp)
            for outcome in _required_list(market, "outcomes"):
                if not isinstance(outcome, Mapping):
                    raise NBAPlayerPointsSourceAdapterError("market outcome must be an object")
                provider_player_name = _first_value(outcome.get("description"), outcome.get("player_name"), outcome.get("name"))
                fragments.append(
                    MappingProxyType(
                        {
                            "provider_event_id": provider_event_id,
                            "commence_time_utc": commence,
                            "home_team": home_team,
                            "away_team": away_team,
                            "sportsbook": sportsbook,
                            "bookmaker_key": _optional_text(book.get("key")),
                            "market_key": market_key,
                            "market_timestamp_utc": market_timestamp,
                            "source_market_update_timestamp_utc": _first_value(
                                market.get("source_market_update_timestamp_utc"),
                                market.get("last_update"),
                                book.get("last_update"),
                                default_timestamp,
                            ),
                            "observation_timestamp_utc": _first_value(
                                outcome.get("observation_timestamp_utc"),
                                market.get("observation_timestamp_utc"),
                                market_timestamp,
                            ),
                            "provider_player_id": _optional_text(outcome.get("provider_player_id")),
                            "provider_player_name": provider_player_name,
                            "team": _first_value(outcome.get("team"), outcome.get("provider_team"), event.get("default_team")),
                            "opponent": _first_value(outcome.get("opponent"), event.get("default_opponent")),
                            "side": _first_value(outcome.get("side"), outcome.get("name")),
                            "line": _first_value(outcome.get("point"), outcome.get("line")),
                            "american_odds": _first_value(outcome.get("price"), outcome.get("american_odds")),
                            "market_status": _first_value(outcome.get("market_status"), market.get("status"), "open"),
                            "prediction_reference_key": outcome.get("prediction_reference_key"),
                            "source_id": outcome.get("source_id"),
                            "raw_provider_fragment": _market_raw_source_fragment(
                                event=event,
                                bookmaker=book,
                                market=market,
                                outcome=outcome,
                            ),
                        }
                    )
                )
    return tuple(fragments)


def _market_raw_source_fragment(
    *,
    event: Mapping[str, object],
    bookmaker: Mapping[str, object],
    market: Mapping[str, object],
    outcome: Mapping[str, object],
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "event": _mapping_without(event, "bookmakers"),
            "bookmaker": _mapping_without(bookmaker, "markets"),
            "market": _mapping_without(market, "outcomes"),
            "outcome": _json_clone_mapping(outcome),
        }
    )


def _mapping_without(payload: Mapping[str, object], *excluded_keys: str) -> dict[str, object]:
    excluded = set(excluded_keys)
    return {
        str(key): _json_clone(value)
        for key, value in payload.items()
        if str(key) not in excluded
    }


def _minutes_section(
    player: Mapping[str, object],
    section_name: str,
    *,
    cutoff: datetime,
    required_fields: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> dict[str, object]:
    section = _required_mapping(player, section_name)
    source_timestamp = _coerce_utc(section.get("source_timestamp_utc"), f"{section_name}.source_timestamp_utc")
    if source_timestamp > cutoff:
        raise NBAPlayerPointsSourceAdapterError(f"{section_name}.source_timestamp_utc must be at or before feature cutoff")
    output = _json_clone_mapping(section)
    output["source_timestamp_utc"] = _format_utc(source_timestamp)
    for field_name in required_fields:
        if field_name not in output and aliases and field_name in aliases:
            output[aliases[field_name]] = section.get(field_name)
        if aliases and aliases[field_name] not in output and field_name in section:
            output[aliases[field_name]] = section[field_name]
    output.setdefault("source_reference", f"adapter:{section_name}")
    output["source_hash"] = _source_hash({"section": section_name, "payload": _json_clone(section)})
    return output


def _batch_result(
    candidate_records: Sequence[Mapping[str, object]],
    diagnostics: Sequence[NBAPlayerPointsSourceAdapterDiagnostic],
    capability: Mapping[str, object],
) -> NBAPlayerPointsSourceAdapterBatchResult:
    normalized, duplicate_diags, conflict_diags = _dedupe_records(candidate_records)
    diagnostic_duplicates = [diag for diag in diagnostics if diag.category == "duplicate"]
    all_diagnostics = [*diagnostics, *conflict_diags]
    invalid = [diag for diag in all_diagnostics if diag.category == "invalid"]
    unresolved = [diag for diag in all_diagnostics if diag.category == "unresolved"]
    ambiguous = [diag for diag in all_diagnostics if diag.category == "ambiguous"]
    quarantined = [diag for diag in all_diagnostics if diag.category == "quarantined"]
    conflicting = [diag for diag in all_diagnostics if diag.category == "conflicting"]
    return NBAPlayerPointsSourceAdapterBatchResult(
        normalized_records=tuple(_canonical_sort_records(normalized)),
        invalid_records=tuple(invalid),
        unresolved_records=tuple(unresolved),
        ambiguous_records=tuple(ambiguous),
        quarantined_records=tuple(quarantined),
        conflicting_records=tuple(conflicting),
        duplicate_diagnostics=tuple([*duplicate_diags, *diagnostic_duplicates]),
        source_summary=_source_summary(
            normalized,
            diagnostics,
            tuple([*duplicate_diags, *diagnostic_duplicates]),
            conflict_diags,
        ),
        capability_summary=capability,
    )


def _dedupe_records(
    records: Sequence[Mapping[str, object]],
) -> tuple[
    tuple[Mapping[str, object], ...],
    tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...],
    tuple[NBAPlayerPointsSourceAdapterDiagnostic, ...],
]:
    by_id: dict[tuple[str, str, str, str], Mapping[str, object]] = {}
    duplicates: list[NBAPlayerPointsSourceAdapterDiagnostic] = []
    conflicts: list[NBAPlayerPointsSourceAdapterDiagnostic] = []
    conflicting_source_identities: set[tuple[str, str, str, str]] = set()
    for record in records:
        source_identity = _source_identity_key(record)
        source_id = _require_text(record.get("source_id"), "source_id")
        source_hash = _require_sha256(record.get("source_hash"), "source_hash")
        prior = by_id.get(source_identity)
        if prior is None:
            by_id[source_identity] = record
            continue
        if prior.get("source_hash") == source_hash:
            duplicates.append(
                _diagnostic(
                    "duplicate",
                    str(record.get("source_type") or "unknown"),
                    "identical scoped source identity replay collapsed",
                    source_id=source_id,
                    source_hash=source_hash,
                    record=record,
                )
            )
            continue
        conflicting_source_identities.add(source_identity)
        conflicts.append(
            _diagnostic(
                "conflicting",
                str(record.get("source_type") or "unknown"),
                "same scoped source identity has different canonical content",
                source_id=source_id,
                source_hash=source_hash,
                record=record,
            )
        )
        conflicts.append(
            _diagnostic(
                "conflicting",
                str(prior.get("source_type") or "unknown"),
                "same scoped source identity has different canonical content",
                source_id=source_id,
                source_hash=str(prior.get("source_hash")),
                record=prior,
            )
        )
    normalized = tuple(
        record
        for source_identity, record in by_id.items()
        if source_identity not in conflicting_source_identities
    )
    return normalized, tuple(duplicates), tuple(conflicts)


def _source_identity_key(record: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        _require_text(record.get("provider"), "provider"),
        _require_text(record.get("source_type"), "source_type"),
        _require_text(record.get("source_id"), "source_id"),
        _require_text(record.get("source_schema_version"), "source_schema_version"),
    )


def _source_summary(
    normalized: Sequence[Mapping[str, object]],
    diagnostics: Sequence[NBAPlayerPointsSourceAdapterDiagnostic],
    duplicate_diags: Sequence[NBAPlayerPointsSourceAdapterDiagnostic],
    conflict_diags: Sequence[NBAPlayerPointsSourceAdapterDiagnostic],
) -> Mapping[str, object]:
    by_type: dict[str, int] = {}
    for record in normalized:
        source_type = str(record.get("source_type") or "unknown")
        by_type[source_type] = by_type.get(source_type, 0) + 1
    invalid_count = sum(1 for diag in diagnostics if diag.category == "invalid")
    unresolved_count = sum(1 for diag in diagnostics if diag.category == "unresolved")
    ambiguous_count = sum(1 for diag in diagnostics if diag.category == "ambiguous")
    quarantined_count = sum(1 for diag in diagnostics if diag.category == "quarantined")
    represented_records = (
        len(normalized)
        + invalid_count
        + unresolved_count
        + ambiguous_count
        + quarantined_count
        + len(conflict_diags)
        + len(duplicate_diags)
    )
    return MappingProxyType(
        {
            "normalized_records": len(normalized),
            "invalid_records": invalid_count,
            "unresolved_records": unresolved_count,
            "ambiguous_records": ambiguous_count,
            "quarantined_records": quarantined_count,
            "conflicting_records": len(conflict_diags),
            "duplicate_diagnostics": len(duplicate_diags),
            "represented_records": represented_records,
            "records_by_type": by_type,
            "source_ids": sorted(str(record.get("source_id")) for record in normalized),
            "source_identities": sorted(
                ":".join(_source_identity_key(record)) for record in normalized
            ),
            "source_hashes": sorted(str(record.get("source_hash")) for record in normalized),
        }
    )


def _schema_failure_result(
    source_type: str,
    capability: Mapping[str, object],
    reason: str,
    payload: object,
) -> NBAPlayerPointsSourceAdapterBatchResult:
    diag = _diagnostic(
        "invalid",
        source_type,
        reason,
        record={"payload": _safe_json_clone(payload)},
    )
    return NBAPlayerPointsSourceAdapterBatchResult(
        invalid_records=(diag,),
        source_summary={"normalized_records": 0, "invalid_records": 1},
        capability_summary=capability,
    )


def _diagnostic(
    category: str,
    source_type: str,
    reason: str,
    *,
    source_id: str | None = None,
    source_hash: str | None = None,
    record: Mapping[str, object] | None = None,
) -> NBAPlayerPointsSourceAdapterDiagnostic:
    return NBAPlayerPointsSourceAdapterDiagnostic(
        category=category,
        source_type=source_type,
        reason=reason,
        source_id=source_id,
        source_hash=source_hash,
        record=record or {},
    )


def _identity_status_diagnostic(
    record: Mapping[str, object],
    *,
    source_type: str,
    status: str,
    reason: str,
) -> NBAPlayerPointsSourceAdapterDiagnostic:
    category = status if status in {"unresolved", "ambiguous", "quarantined", "conflicting"} else "invalid"
    return _diagnostic(
        category,
        source_type,
        reason,
        source_id=str(record.get("source_id")),
        source_hash=str(record.get("source_hash")),
        record=record,
    )


def _dedupe_mapping_entries(
    *,
    entries: Sequence[object],
    key_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
    source_type: str,
) -> tuple[list[dict[str, object]], list[NBAPlayerPointsSourceAdapterDiagnostic]]:
    output: list[dict[str, object]] = []
    diagnostics: list[NBAPlayerPointsSourceAdapterDiagnostic] = []
    by_key: dict[tuple[str, ...], dict[str, object]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            diagnostics.append(_diagnostic("invalid", source_type, "mapping entry must be an object", record={"index": index}))
            continue
        normalized = _json_clone_mapping(entry)
        key = tuple(str(normalized.get(field) or "") for field in key_fields)
        if not any(key):
            diagnostics.append(_diagnostic("invalid", source_type, "mapping key is required", record=normalized))
            continue
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = normalized
            output.append(normalized)
            continue
        prior_values = tuple(prior.get(field) for field in value_fields)
        values = tuple(normalized.get(field) for field in value_fields)
        if prior_values == values:
            diagnostics.append(_diagnostic("duplicate", source_type, "identical reviewed mapping replay collapsed", record=normalized))
            continue
        diagnostics.append(_diagnostic("conflicting", source_type, "reviewed mapping key has conflicting canonical identity", record=normalized))
    return output, diagnostics


def _load_payload(
    payload: Mapping[str, object] | bytes | bytearray | str,
    *,
    expected_schema_version: str,
) -> tuple[Mapping[str, object], str | None]:
    try:
        if isinstance(payload, (bytes, bytearray)):
            decoded = json.loads(bytes(payload).decode("utf-8"))
        elif isinstance(payload, str):
            decoded = json.loads(payload)
        elif isinstance(payload, Mapping):
            decoded = _json_clone_mapping(payload)
        else:
            return MappingProxyType({}), "payload must be JSON bytes, JSON text, or a mapping"
        if not isinstance(decoded, Mapping):
            return MappingProxyType({}), "payload must contain an object"
        cloned = _json_clone_mapping(decoded)
        schema_version = _require_text(cloned.get("schema_version"), "schema_version")
        if schema_version != expected_schema_version:
            return MappingProxyType(cloned), f"unsupported provider schema: {schema_version!r}"
        return MappingProxyType(cloned), None
    except Exception as exc:
        return MappingProxyType({}), str(exc)


def _capability(provider: str, source_type: str) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "provider": _normalize_provider(provider),
            "source_type": _require_text(source_type, "source_type"),
            "mode": "offline",
            "supports_live_calls": False,
            "reads_credentials": False,
            "writes_files": False,
            "supported_market_keys": sorted(_SUPPORTED_MARKET_KEYS),
            "adapter_schema_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION,
            "adapter_version": NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION,
        }
    )


def _candidate_list(value: object) -> list[dict[str, object]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise NBAPlayerPointsSourceAdapterError("canonical_candidates must be a list")
    candidates: list[dict[str, object]] = []
    for candidate in value:
        if not isinstance(candidate, Mapping):
            raise NBAPlayerPointsSourceAdapterError("canonical candidate must be an object")
        candidates.append(_json_clone_mapping(candidate))
    return candidates


def _identity_status(value: object, candidate_count: int) -> str:
    if value not in (None, ""):
        status = _normalize_key(value, "identity_status")
    elif candidate_count == 0:
        status = "unresolved"
    elif candidate_count == 1:
        status = "resolved"
    else:
        status = "ambiguous"
    if status not in {"resolved", "unresolved", "ambiguous", "conflicting", "quarantined"}:
        raise NBAPlayerPointsSourceAdapterError(f"unsupported identity_status: {status!r}")
    if status == "resolved" and candidate_count != 1:
        raise NBAPlayerPointsSourceAdapterError("resolved identity requires exactly one canonical candidate")
    return status


def _identity_reason(status: str, candidates: Sequence[Mapping[str, object]]) -> str:
    if status == "resolved":
        return "explicit_canonical_candidate"
    if status == "ambiguous" or len(candidates) > 1:
        return "multiple_canonical_candidates"
    if status == "quarantined":
        return "provider_identity_quarantined"
    if status == "conflicting":
        return "provider_identity_conflicting"
    return "missing_canonical_candidate"


def _market_source_id(provider: str, fragment: Mapping[str, object], *, prefix: str) -> str:
    explicit = _optional_text(fragment.get("source_id"))
    if explicit is not None:
        return explicit
    player_key = _optional_text(fragment.get("provider_player_id")) or normalize_player_name(
        _require_text(fragment.get("provider_player_name"), "provider_player_name")
    )
    parts = (
        provider,
        prefix,
        _require_identifier(fragment.get("provider_event_id"), "provider_event_id"),
        _require_text(fragment.get("sportsbook"), "sportsbook"),
        _normalize_market_key(fragment.get("market_key")),
        player_key,
        _normalize_side(fragment.get("side")),
    )
    return ":".join(_source_id_part(part) for part in parts)


def _source_hash(fragment: object) -> str:
    return _sha256_bytes(_stable_json_bytes(_strip_hash_self_references(fragment)))


def _source_hash_envelope(
    *,
    provider: str,
    source_type: str,
    source_schema_version: str,
    fragment: Mapping[str, object],
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "provider": provider,
            "source_type": source_type,
            "source_schema_version": source_schema_version,
            "fragment": _json_clone_mapping(fragment),
        }
    )


def _strip_hash_self_references(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_hash_self_references(item)
            for key, item in value.items()
            if str(key) not in _HASH_SELF_REFERENCE_KEYS
        }
    if isinstance(value, tuple | list):
        return [_strip_hash_self_references(item) for item in value]
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_market_key(value: object) -> str:
    market = _normalize_key(value, "market")
    if market not in _SUPPORTED_MARKET_KEYS:
        raise NBAPlayerPointsSourceAdapterError(f"unsupported market: {market!r}")
    return NBA_PLAYER_POINTS_MARKET


def _normalize_side(value: object) -> str:
    side = _normalize_key(value, "side")
    if side not in {"over", "under"}:
        raise NBAPlayerPointsSourceAdapterError(f"unsupported side: {side!r}")
    return side


def _normalize_game_status(value: object) -> str:
    status = _normalize_key(value, "game_status")
    aliases = {
        "closed": "final",
        "complete": "final",
        "completed": "final",
        "final": "final",
        "live": "in_progress",
        "in_progress": "in_progress",
        "pregame": "scheduled",
        "pre_game": "scheduled",
        "scheduled": "scheduled",
        "postponed": "postponed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "suspended": "suspended",
    }
    normalized = aliases.get(status, status)
    if normalized not in _SUPPORTED_GAME_STATUSES:
        raise NBAPlayerPointsSourceAdapterError(f"unsupported game_status: {normalized!r}")
    return normalized


def _coerce_game_final(value: object, status: str) -> bool:
    if isinstance(value, bool):
        parsed = value
    elif value in (None, ""):
        parsed = status == "final"
    else:
        text = str(value).strip().casefold()
        if text in {"true", "1", "yes", "y"}:
            parsed = True
        elif text in {"false", "0", "no", "n"}:
            parsed = False
        else:
            raise NBAPlayerPointsSourceAdapterError("game_final must be boolean")
    if status == "final" and parsed is not True:
        raise NBAPlayerPointsSourceAdapterError("final game_status requires game_final=true")
    if status != "final" and parsed is True:
        raise NBAPlayerPointsSourceAdapterError("game_final=true requires final game_status")
    return parsed


def _normalize_participation_status(value: object, *, actual_minutes: float | None) -> str:
    status = _normalize_key(value, "participation_status")
    aliases = {
        "active": "participated",
        "played": "participated",
        "participated": "participated",
        "dnp": "did_not_participate",
        "did_not_play": "did_not_participate",
        "did_not_dress": "did_not_participate",
        "inactive": "did_not_participate",
        "did_not_participate": "did_not_participate",
        "zero_minutes": "zero_minutes",
        "unknown": "unknown",
    }
    normalized = aliases.get(status, status)
    if normalized == "participated" and actual_minutes == 0:
        normalized = "zero_minutes"
    if normalized not in {"participated", "did_not_participate", "zero_minutes", "unknown"}:
        raise NBAPlayerPointsSourceAdapterError(f"unsupported participation_status: {normalized!r}")
    return normalized


def _contains_leakage(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _LEAKAGE_KEYS:
                return True
            if _contains_leakage(item):
                return True
    elif isinstance(value, list | tuple):
        return any(_contains_leakage(item) for item in value)
    return False


def _required_list(payload: Mapping[str, object], field_name: str) -> list[object]:
    value = payload.get(field_name)
    if not isinstance(value, list):
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be a list")
    return value


def _required_mapping(payload: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    value = payload.get(field_name)
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be an object")
    return value


def _first_value(*values: object) -> object:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _coerce_utc(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise NBAPlayerPointsSourceAdapterError(
                f"{field_name} must be an ISO-8601 timezone-aware timestamp"
            ) from exc
    else:
        raise NBAPlayerPointsSourceAdapterError(
            f"{field_name} must be an ISO-8601 timezone-aware timestamp"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(_UTC)


def _format_utc(value: datetime) -> str:
    parsed = _coerce_utc(value, "timestamp")
    return parsed.isoformat().replace("+00:00", "Z")


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text == "0":
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} is required")
    return text


def _require_text(value: object, field_name: str) -> str:
    if value is None:
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_provider(value: object) -> str:
    return _normalize_key(value, "provider")


def _normalize_key(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold().strip()
    normalized = _KEY_RE.sub("_", text).strip("_")
    if not normalized:
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} is required")
    return normalized


def _source_id_part(value: object) -> str:
    return _KEY_RE.sub("-", _require_text(value, "source_id part").casefold()).strip("-")


def _require_nonnegative_number(value: object, field_name: str) -> float:
    parsed = _require_finite_number(value, field_name)
    if parsed < 0:
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be non-negative")
    return parsed


def _optional_nonnegative_number(value: object, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    return _require_nonnegative_number(value, field_name)


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be finite")
    return parsed


def _require_american_odds(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be an integer")
    if value == 0:
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} cannot be 0")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_text(value, field_name).casefold()
    if _SHA256_RE.fullmatch(text) is None:
        raise NBAPlayerPointsSourceAdapterError(f"{field_name} must be lowercase SHA-256")
    return text


def _canonical_sort_records(records: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    return tuple(
        sorted(
            records,
            key=lambda record: (
                str(record.get("source_type") or ""),
                str(record.get("source_id") or ""),
                str(record.get("source_hash") or ""),
            ),
        )
    )


def _stable_json_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            _json_ready(payload),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except ValueError as exc:
        raise NBAPlayerPointsSourceAdapterError("canonical JSON cannot contain NaN or infinity") from exc


def _json_ready(value: object) -> object:
    if isinstance(value, datetime):
        return _format_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NBAPlayerPointsSourceAdapterError("numeric values must be finite")
        return value
    return value


def _json_clone(value: object) -> object:
    return json.loads(_stable_json_bytes(value).decode("utf-8"))


def _safe_json_clone(value: object) -> object:
    try:
        return _json_clone(value)
    except Exception:
        return str(value)


def _json_clone_mapping(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NBAPlayerPointsSourceAdapterError("value must be an object")
    cloned = _json_clone(value)
    if not isinstance(cloned, dict):
        raise NBAPlayerPointsSourceAdapterError("value must be an object")
    return cloned


__all__ = [
    "NBA_PLAYER_POINTS_CLOSING_ODDS_FIXTURE_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_FINAL_STATS_FIXTURE_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_MINUTES_INPUT_FIXTURE_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_PREGAME_ODDS_FIXTURE_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SCHEDULE_IDENTITY_FIXTURE_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SOURCE_ADAPTER_SCHEMA_VERSION",
    "NBA_PLAYER_POINTS_SOURCE_ADAPTER_VERSION",
    "NBAPlayerPointsSourceAdapterBatchResult",
    "NBAPlayerPointsSourceAdapterDiagnostic",
    "NBAPlayerPointsSourceAdapterError",
    "adapter_capability_summary",
    "normalize_closing_player_points_odds",
    "normalize_final_stat_sources",
    "normalize_minutes_feature_inputs",
    "normalize_pregame_player_points_odds",
    "normalize_schedule_identity_sources",
    "source_fixture_hash",
]
