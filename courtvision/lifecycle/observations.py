"""Prospective, shadow-only schedule, market, and availability observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import math
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from courtvision.lifecycle.canonical import (
    deterministic_id,
    parse_utc_datetime,
    payload_sha256,
)
from courtvision.lifecycle.clock import Clock
from courtvision.lifecycle.evidence import (
    PreparedEvidenceObject,
    prepare_evidence_object,
    sanitize_evidence,
)
from courtvision.lifecycle.identity import (
    canonical_event_id,
    canonical_participant_id,
    canonical_team_id,
    derive_publication_identity,
    normalize_bookmaker_id,
    normalize_line,
    normalize_market_id,
    normalize_selection,
)
from courtvision.lifecycle.models import EventEnvelope, EventType, RunManifest


OBSERVATION_PAYLOAD_SCHEMA_VERSION = 1
OBSERVATION_NORMALIZATION_VERSION = "courtvision_observation_normalization_v1"
EVIDENCE_RETENTION_LEVELS = frozenset(
    {
        "FULL_RAW",
        "SANITIZED_RAW",
        "NORMALIZED_ONLY",
        "HASH_REFERENCE_ONLY",
    }
)

_SCHEDULE_STATUS_MAP = {
    "scheduled": "SCHEDULED",
    "not started": "SCHEDULED",
    "delayed": "DELAYED",
    "delay": "DELAYED",
    "in progress": "IN_PROGRESS",
    "in_progress": "IN_PROGRESS",
    "live": "IN_PROGRESS",
    "halftime": "IN_PROGRESS",
    "final": "FINAL",
    "final/ot": "FINAL",
    "postponed": "POSTPONED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "suspended": "SUSPENDED",
}

_AVAILABILITY_STATUS_MAP = {
    "active": "ACTIVE",
    "available": "AVAILABLE",
    "questionable": "QUESTIONABLE",
    "doubtful": "DOUBTFUL",
    "out": "OUT",
    "inactive": "INACTIVE",
    "starting": "STARTING",
    "not starting": "NOT_STARTING",
    "not_starting": "NOT_STARTING",
}


class ObservationValidationError(ValueError):
    """Raised when an observation cannot be represented without invention."""


@dataclass(frozen=True, slots=True)
class PreparedObservation:
    event_type: str
    payload_schema_version: int
    observation_identity: str
    payload: Mapping[str, Any]
    occurred_at_utc: datetime
    provider_reported_at_utc: datetime | None
    evidence: PreparedEvidenceObject


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    prediction_run_id: str
    prepared_at_utc: datetime
    observations: tuple[PreparedObservation, ...]
    source_counts: Mapping[str, int]
    capture_errors: tuple[str, ...] = ()

    @property
    def schedule_count(self) -> int:
        return sum(
            item.event_type == EventType.SCHEDULE_OBSERVED.value
            for item in self.observations
        )

    @property
    def market_count(self) -> int:
        return sum(
            item.event_type == EventType.MARKET_QUOTE_OBSERVED.value
            for item in self.observations
        )

    @property
    def availability_count(self) -> int:
        return sum(
            item.event_type == EventType.PLAYER_AVAILABILITY_OBSERVED.value
            for item in self.observations
        )


def prepare_observation_batch(
    *,
    prediction_run_id: str,
    prediction_date: str,
    clock: Clock,
    games_raw: pd.DataFrame | None,
    games: pd.DataFrame | None,
    odds_provider_rows: pd.DataFrame | None,
    odds: pd.DataFrame | None,
    injuries_raw: pd.DataFrame | None,
    injuries: pd.DataFrame | None,
    schedule_provider_name: str,
    market_provider_name: str,
    availability_provider_name: str,
) -> ObservationBatch:
    """Prepare one run-bounded observation batch outside the writer lock."""

    ingested_at = clock.now()
    schedule_raw_records = _records(games_raw)
    schedule_records = _records(games)
    odds_provider_records = _records(odds_provider_rows)
    canonical_odds_records = _records(odds)
    injury_raw_records = _records(injuries_raw)
    injury_records = _records(injuries)

    prepared: list[PreparedObservation] = []
    capture_errors: list[str] = []

    for row in schedule_records:
        raw_row = _matching_row(
            schedule_raw_records,
            row,
            keys=(("id", "game_id"), ("game_id", "game_id")),
        )
        try:
            prepared.append(
                prepare_schedule_observation(
                    provider_name=schedule_provider_name,
                    operating_date=prediction_date,
                    source_row=raw_row or row,
                    normalized_row=row,
                    ingested_at_utc=ingested_at,
                    evidence_retention_level=(
                        "SANITIZED_RAW" if raw_row else "NORMALIZED_ONLY"
                    ),
                )
            )
        except Exception as exc:
            capture_errors.append(
                f"SCHEDULE_OBSERVED:{type(exc).__name__}:{str(exc)[:300]}"
            )

    # The returned provider rows are already the canonical BallDontLie adapter
    # boundary. They are intentionally retained even if canonical filtering
    # later excludes a row.
    for row in odds_provider_records:
        canonical_row = _matching_market_row(canonical_odds_records, row)
        try:
            prepared.append(
                prepare_market_quote_observation(
                    provider_name=market_provider_name,
                    source_row=row,
                    normalized_row=canonical_row or row,
                    ingested_at_utc=ingested_at,
                    evidence_retention_level="NORMALIZED_ONLY",
                )
            )
        except Exception as exc:
            capture_errors.append(
                f"MARKET_QUOTE_OBSERVED:{type(exc).__name__}:{str(exc)[:300]}"
            )

    for row in injury_records:
        raw_row = _matching_row(
            injury_raw_records,
            row,
            keys=(
                ("player.id", "player_id"),
                ("player_id", "player_id"),
                ("player_name", "player_name"),
            ),
        )
        try:
            prepared.append(
                prepare_player_availability_observation(
                    provider_name=availability_provider_name,
                    source_row=raw_row or row,
                    normalized_row=row,
                    ingested_at_utc=ingested_at,
                    evidence_retention_level=(
                        "SANITIZED_RAW" if raw_row else "NORMALIZED_ONLY"
                    ),
                )
            )
        except Exception as exc:
            capture_errors.append(
                "PLAYER_AVAILABILITY_OBSERVED:"
                f"{type(exc).__name__}:{str(exc)[:300]}"
            )

    deduplicated: dict[tuple[str, str], PreparedObservation] = {}
    for item in prepared:
        key = (item.event_type, item.observation_identity)
        prior = deduplicated.get(key)
        if prior is not None:
            if (
                prior.payload != item.payload
                or prior.evidence.sha256 != item.evidence.sha256
            ):
                raise ObservationValidationError(
                    "same observation identity prepared with different content"
                )
            continue
        deduplicated[key] = item

    return ObservationBatch(
        prediction_run_id=str(prediction_run_id),
        prepared_at_utc=ingested_at,
        observations=tuple(deduplicated.values()),
        source_counts={
            "schedule_provider_rows": len(schedule_raw_records),
            "schedule_normalized_rows": len(schedule_records),
            "market_provider_rows": len(odds_provider_records),
            "market_canonical_rows": len(canonical_odds_records),
            "availability_provider_rows": len(injury_raw_records),
            "availability_normalized_rows": len(injury_records),
        },
        capture_errors=tuple(capture_errors),
    )


def prepare_schedule_observation(
    *,
    provider_name: str,
    operating_date: str,
    source_row: Mapping[str, Any],
    normalized_row: Mapping[str, Any],
    ingested_at_utc: datetime,
    evidence_retention_level: str,
) -> PreparedObservation:
    _require_aware(ingested_at_utc, "ingested_at_utc")
    retention = _retention(evidence_retention_level)
    source = _clean_mapping(source_row)
    normalized = _clean_mapping(normalized_row)
    provider_event_id = _first_value(normalized, "game_id", "id")
    if provider_event_id is None:
        provider_event_id = _first_value(source, "id", "game_id")
    canonical_event = canonical_event_id(
        provider_event_id, sport="basketball", league="NBA"
    )
    raw_status = _first_value(source, "status", "game_status", "raw_status")
    normalized_status = _first_value(
        normalized, "game_status", "status", "raw_status"
    )
    if normalized_status is None:
        normalized_status = raw_status
    scheduled_start = _first_aware_utc(
        normalized,
        ("game_datetime", "datetime", "game_date", "date"),
    )
    if scheduled_start is None:
        scheduled_start = _first_aware_utc(
            source,
            ("datetime", "game_datetime", "date", "game_date", "status"),
        )
    provider_reported = _first_aware_utc(
        source,
        (
            "provider_reported_at_utc",
            "updated_at",
            "last_updated",
            "lastUpdated",
        ),
    )
    home = _team_values(source, normalized, "home")
    away = _team_values(source, normalized, "visitor")
    if away == (None, None, None):
        away = _team_values(source, normalized, "away")
    venue = _venue(source)
    doubleheader_sequence = _first_value(
        source, "doubleheader_sequence", "double_header_sequence"
    )
    source_snapshot = {
        "evidence_retention_level": retention,
        "provider_name": _required_text(provider_name, "provider_name"),
        "source_claim": source,
        "normalized_interpretation": {
            "provider_event_id": provider_event_id,
            "scheduled_start_at_utc": scheduled_start,
            "game_status_raw": _nullable_text(raw_status),
            "game_status_normalized": normalize_schedule_status(
                normalized_status
            ),
        },
    }
    clean_snapshot = sanitize_evidence(source_snapshot)
    source_payload_hash = payload_sha256(clean_snapshot)
    evidence = prepare_evidence_object("schedule_observation_source", clean_snapshot)
    payload = {
        "payload_schema_version": OBSERVATION_PAYLOAD_SCHEMA_VERSION,
        "canonical_event_id": canonical_event,
        "event_identity_resolution_status": (
            "RESOLVED" if canonical_event else "UNRESOLVED"
        ),
        "provider_name": _required_text(provider_name, "provider_name"),
        "provider_event_id": provider_event_id,
        "league": "NBA",
        "sport": "BASKETBALL",
        "season": _first_value(source, "season"),
        "operating_date": str(operating_date),
        "home_team_id": home[0],
        "away_team_id": away[0],
        "home_team_name": home[2],
        "away_team_name": away[2],
        "scheduled_start_at_utc": scheduled_start,
        "provider_reported_at_utc": provider_reported,
        "ingested_at_utc": ingested_at_utc,
        "game_status_raw": _nullable_text(raw_status),
        "game_status_normalized": normalize_schedule_status(
            normalized_status
        ),
        "doubleheader_sequence": doubleheader_sequence,
        "venue": venue,
        "source_payload_ref": _evidence_ref(evidence),
        "source_payload_sha256": source_payload_hash,
        "evidence_retention_level": retention,
        "normalization_version": OBSERVATION_NORMALIZATION_VERSION,
    }
    identity = deterministic_id(
        "obs",
        "courtvision.schedule_observed.v1",
        {
            "provider_name": payload["provider_name"],
            "provider_event_id": provider_event_id,
            "scheduled_start_at_utc": scheduled_start,
            "game_status_raw": payload["game_status_raw"],
            "game_status_normalized": payload["game_status_normalized"],
            "provider_reported_at_utc": provider_reported,
            "source_payload_sha256": source_payload_hash,
        },
    )
    payload["observation_identity"] = identity
    return PreparedObservation(
        event_type=EventType.SCHEDULE_OBSERVED.value,
        payload_schema_version=OBSERVATION_PAYLOAD_SCHEMA_VERSION,
        observation_identity=identity,
        payload=payload,
        occurred_at_utc=provider_reported or ingested_at_utc,
        provider_reported_at_utc=provider_reported,
        evidence=evidence,
    )


def prepare_market_quote_observation(
    *,
    provider_name: str,
    source_row: Mapping[str, Any],
    normalized_row: Mapping[str, Any],
    ingested_at_utc: datetime,
    evidence_retention_level: str,
) -> PreparedObservation:
    _require_aware(ingested_at_utc, "ingested_at_utc")
    retention = _retention(evidence_retention_level)
    source = _clean_mapping(source_row)
    normalized = _clean_mapping(normalized_row)
    provider_event_id = _first_value(normalized, "game_id", "provider_event_id")
    provider_participant_id = _first_value(
        normalized, "player_id", "provider_participant_id"
    )
    market_raw = _first_value(
        source, "raw_market_name", "raw_prop_type", "raw_market_type", "market"
    )
    market_normalized = _first_value(
        normalized, "market_type", "market", "prop_type"
    )
    selection_raw = _first_value(normalized, "selection", "side")
    provider_bookmaker_key = _first_value(
        source, "vendor", "bookmaker", "sportsbook", "provider_bookmaker_key"
    )
    provider_market_key = _first_value(
        source, "raw_prop_type", "raw_market_name", "raw_market_type"
    )
    line = normalize_observation_numeric(
        _first_value(normalized, "line", "line_value", "sportsbook_line"),
        field_name="line",
    )
    odds = normalize_observation_numeric(
        _first_value(normalized, "odds", "price", "entry_odds"),
        field_name="odds",
    )
    if odds == 0:
        raise ObservationValidationError("American odds cannot be zero")
    provider_reported = _first_aware_utc(
        source,
        (
            "provider_reported_at_utc",
            "updated_at",
            "last_updated",
            "lastUpdated",
        ),
    )
    market_observed = provider_reported
    canonical_event = canonical_event_id(
        provider_event_id, sport="basketball", league="NBA"
    )
    canonical_participant = canonical_participant_id(
        provider_participant_id, sport="basketball", league="NBA"
    )
    canonical_market = normalize_market_id(
        market_normalized, sport="basketball", league="NBA"
    )
    canonical_bookmaker = normalize_bookmaker_id(provider_bookmaker_key)
    selection = normalize_selection(selection_raw)
    try:
        canonical_line = normalize_line(line)
    except Exception as exc:
        raise ObservationValidationError(str(exc)) from exc
    identity_values = {
        "canonical_event_id": canonical_event,
        "canonical_participant_id": canonical_participant,
        "canonical_market_id": canonical_market,
        "canonical_bookmaker_id": canonical_bookmaker,
        "selection": selection,
        "line": canonical_line,
    }
    unresolved = tuple(
        name for name, value in identity_values.items() if value is None
    )
    source_snapshot = {
        "evidence_retention_level": retention,
        "provider_name": _required_text(provider_name, "provider_name"),
        "provider_quote": source,
        "normalized_interpretation": {
            "provider_event_id": provider_event_id,
            "provider_participant_id": provider_participant_id,
            "market_raw": _nullable_text(market_raw),
            "market_normalized": _nullable_text(market_normalized),
            "selection": selection or _nullable_text(selection_raw),
            "line": line,
            "odds": odds,
        },
    }
    clean_snapshot = sanitize_evidence(source_snapshot)
    source_payload_hash = payload_sha256(clean_snapshot)
    evidence = prepare_evidence_object("market_quote_observation_source", clean_snapshot)
    payload = {
        "payload_schema_version": OBSERVATION_PAYLOAD_SCHEMA_VERSION,
        "canonical_event_id": canonical_event,
        "canonical_participant_id": canonical_participant,
        "canonical_market_id": canonical_market,
        "canonical_bookmaker_id": canonical_bookmaker,
        "identity_resolution_status": (
            "RESOLVED" if not unresolved else "UNRESOLVED"
        ),
        "unresolved_identity_fields": list(unresolved),
        "provider_name": _required_text(provider_name, "provider_name"),
        "provider_event_id": provider_event_id,
        "provider_participant_id": provider_participant_id,
        "provider_market_key": provider_market_key,
        "provider_bookmaker_key": provider_bookmaker_key,
        "sport": "BASKETBALL",
        "league": "NBA",
        "market_raw": _nullable_text(market_raw),
        "market_normalized": _nullable_text(market_normalized),
        "selection": selection or _nullable_text(selection_raw),
        "line": line,
        "odds": odds,
        "odds_format": "AMERICAN" if odds is not None else None,
        "implied_probability": implied_probability_from_american(odds),
        "is_live_market": True,
        "is_synthetic": False,
        "line_source": _first_value(source, "line_source"),
        "provider_reported_at_utc": provider_reported,
        "market_observed_at_utc": market_observed,
        "ingested_at_utc": ingested_at_utc,
        "source_payload_ref": _evidence_ref(evidence),
        "source_payload_sha256": source_payload_hash,
        "evidence_retention_level": retention,
        "normalization_version": OBSERVATION_NORMALIZATION_VERSION,
    }
    identity = deterministic_id(
        "obs",
        "courtvision.market_quote_observed.v1",
        {
            "provider_name": payload["provider_name"],
            "provider_event_id": provider_event_id,
            "provider_participant_id": provider_participant_id,
            "provider_market_key": provider_market_key,
            "provider_bookmaker_key": provider_bookmaker_key,
            "market_raw": payload["market_raw"],
            "market_normalized": payload["market_normalized"],
            "selection": payload["selection"],
            "line": line,
            "odds": odds,
            "provider_reported_at_utc": provider_reported,
            "source_payload_sha256": source_payload_hash,
        },
    )
    payload["observation_identity"] = identity
    return PreparedObservation(
        event_type=EventType.MARKET_QUOTE_OBSERVED.value,
        payload_schema_version=OBSERVATION_PAYLOAD_SCHEMA_VERSION,
        observation_identity=identity,
        payload=payload,
        occurred_at_utc=market_observed or ingested_at_utc,
        provider_reported_at_utc=provider_reported,
        evidence=evidence,
    )


def prepare_player_availability_observation(
    *,
    provider_name: str,
    source_row: Mapping[str, Any],
    normalized_row: Mapping[str, Any],
    ingested_at_utc: datetime,
    evidence_retention_level: str,
) -> PreparedObservation:
    _require_aware(ingested_at_utc, "ingested_at_utc")
    retention = _retention(evidence_retention_level)
    source = _clean_mapping(source_row)
    normalized = _clean_mapping(normalized_row)
    provider_participant_id = _first_value(
        normalized, "player_id", "provider_participant_id", "player.id"
    )
    provider_event_id = _first_value(
        source, "provider_event_id", "game_id", "event_id"
    )
    team_id = _first_value(normalized, "team_id", "player.team_id")
    conflict = _has_identity_conflict(source, normalized)
    canonical_event = canonical_event_id(
        provider_event_id, sport="basketball", league="NBA"
    )
    canonical_participant = (
        None
        if conflict
        else canonical_participant_id(
            provider_participant_id, sport="basketball", league="NBA"
        )
    )
    canonical_team = (
        None
        if conflict
        else canonical_team_id(team_id, sport="basketball", league="NBA")
    )
    unresolved: list[str] = []
    if canonical_participant is None:
        unresolved.append("canonical_participant_id")
    if canonical_team is None:
        unresolved.append("canonical_team_id")
    if canonical_event is None:
        unresolved.append("canonical_event_id")
    resolution = (
        "CONFLICT"
        if conflict
        else ("RESOLVED" if not unresolved else "UNRESOLVED")
    )
    raw_status = _first_value(
        source, "status", "injury_status", "availability_status"
    )
    normalized_status = _first_value(
        normalized, "status", "injury_status", "availability_status"
    )
    if normalized_status is None:
        normalized_status = raw_status
    provider_reported = _first_aware_utc(
        source,
        (
            "provider_reported_at_utc",
            "updated_at",
            "last_updated",
            "lastUpdated",
        ),
    )
    effective_at = _first_aware_utc(
        source, ("effective_at_utc", "effective_at")
    )
    source_snapshot = {
        "evidence_retention_level": retention,
        "provider_name": _required_text(provider_name, "provider_name"),
        "source_claim": source,
        "normalized_interpretation": {
            "provider_participant_id": provider_participant_id,
            "availability_status_raw": _nullable_text(raw_status),
            "availability_status_normalized": normalize_availability_status(
                normalized_status
            ),
            "identity_resolution_status": resolution,
        },
    }
    clean_snapshot = sanitize_evidence(source_snapshot)
    source_payload_hash = payload_sha256(clean_snapshot)
    evidence = prepare_evidence_object(
        "player_availability_observation_source", clean_snapshot
    )
    payload = {
        "payload_schema_version": OBSERVATION_PAYLOAD_SCHEMA_VERSION,
        "canonical_event_id": canonical_event,
        "canonical_participant_id": canonical_participant,
        "canonical_team_id": canonical_team,
        "identity_resolution_status": resolution,
        "unresolved_identity_fields": unresolved,
        "provider_name": _required_text(provider_name, "provider_name"),
        "provider_event_id": provider_event_id,
        "provider_participant_id": provider_participant_id,
        "sport": "BASKETBALL",
        "league": "NBA",
        "player_name": _first_value(
            normalized, "player_name", "canonical_player_name"
        ),
        "team_name": _first_value(normalized, "team_name"),
        "team_abbr": _first_value(normalized, "team_abbr", "team"),
        "availability_status_raw": _nullable_text(raw_status),
        "availability_status_normalized": normalize_availability_status(
            normalized_status
        ),
        "injury_type": _first_value(source, "injury_type", "type"),
        "injury_detail": _first_value(
            normalized, "description", "injury_description", "note", "notes"
        ),
        "lineup_status": _first_value(source, "lineup_status"),
        "starter_status": _first_value(source, "starter_status"),
        "participation_status": _first_value(source, "participation_status"),
        "provider_reported_at_utc": provider_reported,
        "effective_at_utc": effective_at,
        "ingested_at_utc": ingested_at_utc,
        "source_payload_ref": _evidence_ref(evidence),
        "source_payload_sha256": source_payload_hash,
        "evidence_retention_level": retention,
        "normalization_version": OBSERVATION_NORMALIZATION_VERSION,
    }
    identity = deterministic_id(
        "obs",
        "courtvision.player_availability_observed.v1",
        {
            "provider_name": payload["provider_name"],
            "provider_event_id": provider_event_id,
            "provider_participant_id": provider_participant_id,
            "availability_status_raw": payload["availability_status_raw"],
            "availability_status_normalized": payload[
                "availability_status_normalized"
            ],
            "injury_type": payload["injury_type"],
            "injury_detail": payload["injury_detail"],
            "lineup_status": payload["lineup_status"],
            "starter_status": payload["starter_status"],
            "participation_status": payload["participation_status"],
            "provider_reported_at_utc": provider_reported,
            "effective_at_utc": effective_at,
            "source_payload_sha256": source_payload_hash,
        },
    )
    payload["observation_identity"] = identity
    return PreparedObservation(
        event_type=EventType.PLAYER_AVAILABILITY_OBSERVED.value,
        payload_schema_version=OBSERVATION_PAYLOAD_SCHEMA_VERSION,
        observation_identity=identity,
        payload=payload,
        occurred_at_utc=effective_at or provider_reported or ingested_at_utc,
        provider_reported_at_utc=provider_reported,
        evidence=evidence,
    )


def materialize_observation_events(
    batch: ObservationBatch,
    *,
    run_manifest: RunManifest,
    recorded_at_utc: datetime,
    starting_sequence: int,
    previous_event_hash: str | None,
) -> tuple[tuple[EventEnvelope, ...], tuple[PreparedEvidenceObject, ...]]:
    if batch.prediction_run_id != run_manifest.prediction_run_id:
        raise ObservationValidationError("observation batch/run ID mismatch")
    events: list[EventEnvelope] = []
    previous = previous_event_hash
    sequence = starting_sequence
    evidence: dict[str, PreparedEvidenceObject] = {}
    for item in batch.observations:
        payload = {
            **dict(item.payload),
            "prediction_run_id": run_manifest.prediction_run_id,
            "correlation_id": run_manifest.prediction_run_id,
        }
        prediction_key = None
        market_subject_key = None
        if item.event_type == EventType.MARKET_QUOTE_OBSERVED.value:
            identity = derive_publication_identity(
                sport="basketball",
                league="NBA",
                event_id=payload.get("canonical_event_id")
                or payload.get("provider_event_id"),
                participant_id=payload.get("canonical_participant_id")
                or payload.get("provider_participant_id"),
                market_id=payload.get("market_normalized")
                or payload.get("canonical_market_id"),
                selection=payload.get("selection"),
                line=payload.get("line"),
                bookmaker=payload.get("canonical_bookmaker_id")
                or payload.get("provider_bookmaker_key"),
                prediction_run_id=run_manifest.prediction_run_id,
            )
            prediction_key = identity.prediction_key
            market_subject_key = identity.market_subject_key
        source_refs = {"source_payload": item.payload["source_payload_ref"]}
        source_hashes = {
            "source_evidence_sha256": item.evidence.sha256,
            "source_payload_sha256": item.payload["source_payload_sha256"],
        }
        event = EventEnvelope.create(
            event_type=item.event_type,
            payload=payload,
            payload_schema_version=item.payload_schema_version,
            prediction_run_id=run_manifest.prediction_run_id,
            prediction_id=None,
            prediction_key=prediction_key,
            market_subject_key=market_subject_key,
            event_sequence=sequence,
            occurred_at_utc=item.occurred_at_utc,
            recorded_at_utc=recorded_at_utc,
            provider_reported_at_utc=item.provider_reported_at_utc,
            operating_date=run_manifest.operating_date,
            operating_timezone=run_manifest.operating_timezone,
            actor_type="SYSTEM",
            actor_id="courtvision.lifecycle.observations",
            correlation_id=run_manifest.prediction_run_id,
            idempotency_key=(
                f"{item.event_type}:{run_manifest.prediction_run_id}:"
                f"{item.observation_identity}"
            ),
            source_refs=source_refs,
            source_hashes=source_hashes,
            code_sha=run_manifest.git_commit_sha,
            config_hash=run_manifest.config_hash,
            model_id=run_manifest.model_id,
            model_version=run_manifest.model_version,
            previous_event_hash=previous,
        )
        events.append(event)
        evidence[item.evidence.sha256] = item.evidence
        previous = event.event_hash
        sequence += 1
    return tuple(events), tuple(evidence.values())


def link_publication_observations(
    row: Mapping[str, Any],
    observation_events: Sequence[EventEnvelope],
    *,
    capture_errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Select only exact, unambiguous contemporaneous links for a board row."""

    parsed = [
        (event, json.loads(event.payload_json))
        for event in observation_events
    ]
    board_event = canonical_event_id(
        _first_value(row, "canonical_event_id", "game_id"),
        sport="basketball",
        league="NBA",
    )
    board_participant = canonical_participant_id(
        _first_value(
            row,
            "canonical_player_id",
            "canonical_participant_id",
            "player_id",
        ),
        sport="basketball",
        league="NBA",
    )
    board_market = normalize_market_id(
        _first_value(row, "canonical_market_id", "market_type", "market", "prop_type"),
        sport="basketball",
        league="NBA",
    )
    board_selection = normalize_selection(_first_value(row, "selection", "side"))
    board_line = _normalized_line_or_none(
        _first_value(row, "sportsbook_line", "line")
    )
    board_odds = normalize_observation_numeric(
        _first_value(row, "odds", "entry_odds"), field_name="odds"
    )
    board_bookmaker = normalize_bookmaker_id(
        _first_value(
            row,
            "canonical_bookmaker_id",
            "bookmaker",
            "sportsbook",
            "vendor",
        )
    )

    schedule_matches = [
        event
        for event, payload in parsed
        if event.event_type == EventType.SCHEDULE_OBSERVED.value
        and board_event is not None
        and payload.get("canonical_event_id") == board_event
    ]
    market_matches: list[EventEnvelope] = []
    for event, payload in parsed:
        if event.event_type != EventType.MARKET_QUOTE_OBSERVED.value:
            continue
        if board_event is not None and payload.get("canonical_event_id") != board_event:
            continue
        if (
            board_participant is not None
            and payload.get("canonical_participant_id") != board_participant
        ):
            continue
        if board_market is not None and payload.get("canonical_market_id") != board_market:
            continue
        if board_selection is not None and payload.get("selection") != board_selection:
            continue
        if board_line is not None and _normalized_line_or_none(payload.get("line")) != board_line:
            continue
        if board_odds is not None and normalize_observation_numeric(
            payload.get("odds"), field_name="odds"
        ) != board_odds:
            continue
        if (
            board_bookmaker is not None
            and payload.get("canonical_bookmaker_id") != board_bookmaker
        ):
            continue
        market_matches.append(event)

    availability_matches = [
        event
        for event, payload in parsed
        if event.event_type
        == EventType.PLAYER_AVAILABILITY_OBSERVED.value
        and board_participant is not None
        and payload.get("canonical_participant_id") == board_participant
    ]

    missing: list[str] = []
    schedule_id = _single_unambiguous_event_id(
        schedule_matches, "SCHEDULE_OBSERVATION", missing
    )
    market_id = _single_unambiguous_event_id(
        market_matches, "MARKET_QUOTE_OBSERVATION", missing
    )
    if not availability_matches:
        missing.append("AVAILABILITY_OBSERVATION_UNAVAILABLE")
    if capture_errors:
        missing.append("OBSERVATION_CAPTURE_DEGRADED")
    return {
        "observation_link_schema_version": 1,
        "link_status": "COMPLETE" if not missing else "DEGRADED",
        "schedule_observation_event_id": schedule_id,
        "market_quote_observation_event_id": market_id,
        "availability_observation_event_ids": [
            event.event_id for event in availability_matches
        ],
        "missing_or_unavailable_reasons": sorted(set(missing)),
        "capture_errors": [str(item)[:500] for item in capture_errors],
    }


def normalize_schedule_status(value: Any) -> str:
    text = _nullable_text(value)
    if text is None:
        return "UNKNOWN"
    clean = text.strip().lower()
    if _parse_optional_utc(text) is not None:
        return "UNKNOWN"
    if clean in _SCHEDULE_STATUS_MAP:
        return _SCHEDULE_STATUS_MAP[clean]
    if any(token in clean for token in ("quarter", "qtr", "period", "inning")):
        return "IN_PROGRESS"
    return "UNKNOWN"


def normalize_availability_status(value: Any) -> str:
    text = _nullable_text(value)
    if text is None:
        return "UNKNOWN"
    return _AVAILABILITY_STATUS_MAP.get(text.strip().lower(), "UNKNOWN")


def normalize_observation_numeric(
    value: Any, *, field_name: str
) -> int | float | None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ObservationValidationError(f"{field_name} must be finite")
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        raise ObservationValidationError(f"{field_name} must be numeric")
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ObservationValidationError(f"{field_name} must be numeric") from exc
    if not number.is_finite():
        raise ObservationValidationError(f"{field_name} must be finite")
    if number == Decimal("-0"):
        number = Decimal("0")
    normalized = number.normalize()
    if normalized == normalized.to_integral_value():
        return int(normalized)
    return float(format(normalized, "f"))


def implied_probability_from_american(
    odds: int | float | None,
) -> int | float | None:
    if odds is None:
        return None
    numeric = Decimal(str(odds))
    if numeric == 0:
        raise ObservationValidationError("American odds cannot be zero")
    probability = (
        (-numeric) / ((-numeric) + Decimal("100"))
        if numeric < 0
        else Decimal("100") / (numeric + Decimal("100"))
    )
    quantized = probability.quantize(Decimal("0.000000000001"))
    return normalize_observation_numeric(
        format(quantized, "f"), field_name="implied_probability"
    )


def _single_unambiguous_event_id(
    events: Sequence[EventEnvelope],
    label: str,
    missing: list[str],
) -> str | None:
    if len(events) == 1:
        return events[0].event_id
    missing.append(
        f"{label}_UNAVAILABLE" if not events else f"{label}_AMBIGUOUS"
    )
    return None


def _normalized_line_or_none(value: Any) -> str | None:
    try:
        return normalize_line(value)
    except Exception:
        return None


def _has_identity_conflict(
    source: Mapping[str, Any], normalized: Mapping[str, Any]
) -> bool:
    for row in (source, normalized):
        status = _nullable_text(
            _first_value(
                row,
                "identity_resolution_status",
                "player_identity_status",
                "resolution_status",
            )
        )
        if status and status.upper() == "CONFLICT":
            return True
        for name in (
            "identity_conflict",
            "player_identity_conflict",
            "identity_team_conflict",
        ):
            value = row.get(name)
            if value is True or str(value).strip().lower() in {"1", "true", "yes"}:
                return True
    return False


def _team_values(
    source: Mapping[str, Any],
    normalized: Mapping[str, Any],
    prefix: str,
) -> tuple[Any, Any, Any]:
    nested = source.get(f"{prefix}_team")
    nested_map = nested if isinstance(nested, Mapping) else {}
    team_id = (
        nested_map.get("id")
        or _first_value(source, f"{prefix}_team_id")
        or _first_value(normalized, f"{prefix}_team_id")
    )
    abbr = (
        nested_map.get("abbreviation")
        or nested_map.get("abbr")
        or _first_value(source, f"{prefix}_team_abbr")
        or _first_value(normalized, f"{prefix}_team_abbr")
    )
    name = (
        nested_map.get("full_name")
        or nested_map.get("name")
        or _first_value(source, f"{prefix}_team_name")
        or _first_value(normalized, f"{prefix}_team_name")
    )
    return team_id, _nullable_text(abbr), _nullable_text(name)


def _venue(source: Mapping[str, Any]) -> Any:
    value = source.get("venue")
    if isinstance(value, Mapping):
        return _first_value(value, "name", "title", "id")
    return _nullable_text(value)


def _matching_market_row(
    rows: Sequence[Mapping[str, Any]], target: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    keys = (
        "game_id",
        "player_id",
        "market_type",
        "selection",
        "line",
        "odds",
        "vendor",
    )
    for row in rows:
        if all(_equivalent(row.get(key), target.get(key)) for key in keys):
            return row
    return None


def _matching_row(
    rows: Sequence[Mapping[str, Any]],
    target: Mapping[str, Any],
    *,
    keys: Sequence[tuple[str, str]],
) -> Mapping[str, Any] | None:
    for source_key, target_key in keys:
        target_value = target.get(target_key)
        if _is_missing(target_value):
            continue
        for row in rows:
            if _equivalent(row.get(source_key), target_value):
                return row
    return None


def _equivalent(left: Any, right: Any) -> bool:
    if _is_missing(left) and _is_missing(right):
        return True
    return str(left).strip() == str(right).strip()


def _records(frame: pd.DataFrame | None) -> tuple[dict[str, Any], ...]:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return ()
    object_frame = frame.astype(object).where(pd.notna(frame), None)
    return tuple(
        _clean_mapping(row) for row in object_frame.to_dict(orient="records")
    )


def _clean_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _clean_source_value(item) for key, item in value.items()}


def _clean_source_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {
            str(key): _clean_source_value(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_clean_source_value(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.isoformat()
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        converted = value.to_pydatetime()
        return (
            converted.astimezone(UTC)
            if converted.tzinfo is not None
            else converted.isoformat()
        )
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value):
            raise ObservationValidationError(
                "source payload contains non-finite numeric value"
            )
        if not math.isfinite(value):
            raise ObservationValidationError("source payload contains non-finite numeric value")
        return float(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return _clean_source_value(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, (str, bytes)):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return str(value)


def _first_value(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if not _is_missing(value):
            return value
    return None


def _first_aware_utc(
    mapping: Mapping[str, Any], names: Iterable[str]
) -> datetime | None:
    for name in names:
        value = mapping.get(name)
        parsed = _parse_optional_utc(value)
        if parsed is not None:
            return parsed
    return None


def _parse_optional_utc(value: Any) -> datetime | None:
    if _is_missing(value):
        return None
    try:
        return parse_utc_datetime(value)
    except (TypeError, ValueError):
        return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {
        "",
        "nan",
        "none",
        "null",
        "<na>",
        "nat",
    }


def _nullable_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: Any, field_name: str) -> str:
    text = _nullable_text(value)
    if text is None:
        raise ObservationValidationError(f"{field_name} is required")
    return text


def _retention(value: str) -> str:
    normalized = str(value).strip().upper()
    if normalized not in EVIDENCE_RETENTION_LEVELS:
        raise ObservationValidationError("invalid evidence_retention_level")
    return normalized


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ObservationValidationError(f"{field_name} must be timezone-aware")


def _evidence_ref(item: PreparedEvidenceObject) -> str:
    return f"evidence://sha256/{item.sha256}"


__all__ = [
    "EVIDENCE_RETENTION_LEVELS",
    "OBSERVATION_NORMALIZATION_VERSION",
    "OBSERVATION_PAYLOAD_SCHEMA_VERSION",
    "ObservationBatch",
    "ObservationValidationError",
    "PreparedObservation",
    "implied_probability_from_american",
    "link_publication_observations",
    "materialize_observation_events",
    "normalize_availability_status",
    "normalize_observation_numeric",
    "normalize_schedule_status",
    "prepare_market_quote_observation",
    "prepare_observation_batch",
    "prepare_player_availability_observation",
    "prepare_schedule_observation",
]
