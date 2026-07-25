"""Frozen storage-neutral lifecycle v1 models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

from courtvision.lifecycle.canonical import (
    CANONICALIZATION_VERSION,
    canonical_json_v1,
    deterministic_id,
    format_utc_datetime,
    parse_utc_datetime,
    payload_sha256,
)
from courtvision.lifecycle.identity import IDENTITY_SCHEMA_VERSION


EVENT_SCHEMA_VERSION = 1
RUN_MANIFEST_SCHEMA_VERSION = 1
PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION = 1
PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION_V2 = 2
RECONCILIATION_SCHEMA_VERSION = 1


class RunMode(str, Enum):
    LIVE = "LIVE"
    SHADOW = "SHADOW"
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    COUNTERFACTUAL = "COUNTERFACTUAL"


class RunReason(str, Enum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    RETRY = "RETRY"
    RECOVERY = "RECOVERY"


class ReproducibilityLevel(str, Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class EventType(str, Enum):
    RUN_STARTED = "RUN_STARTED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    PREDICTION_PUBLISHED = "PREDICTION_PUBLISHED"
    SCHEDULE_OBSERVED = "SCHEDULE_OBSERVED"
    MARKET_QUOTE_OBSERVED = "MARKET_QUOTE_OBSERVED"
    PLAYER_AVAILABILITY_OBSERVED = "PLAYER_AVAILABILITY_OBSERVED"
    SHADOW_RECONCILIATION_COMPLETED = "SHADOW_RECONCILIATION_COMPLETED"


class ReconciliationStatus(str, Enum):
    PASS = "PASS"
    DEGRADED = "DEGRADED"
    FAIL = "FAIL"


def _optional_utc(value: datetime | None) -> str | None:
    return format_utc_datetime(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class RunManifest:
    prediction_run_id: str
    run_mode: str
    run_reason: str | None
    parent_run_id: str | None
    started_at_utc: datetime
    completed_at_utc: datetime | None
    operating_date: str
    operating_timezone: str
    git_commit_sha: str | None
    git_dirty: bool | None
    working_tree_hash: str | None
    config_hash: str | None
    model_id: str | None
    model_version: str | None
    model_bundle_hash: str | None
    calibration_id: str | None
    calibration_version: str | None
    calibration_hash: str | None
    strategy_version: str | None
    pipeline_version: str | None
    python_version: str
    dependency_fingerprint: str | None
    input_manifest_hash: str | None
    reproducibility_level: str
    canonical_runtime_mode: str = "LIVE"
    lifecycle_authority: str = "SHADOW_ONLY"
    run_manifest_schema_version: int = RUN_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not str(self.prediction_run_id).strip():
            raise ValueError("prediction_run_id is required")
        if self.run_mode not in {item.value for item in RunMode}:
            raise ValueError(f"unsupported run_mode: {self.run_mode}")
        if self.run_reason is not None and self.run_reason not in {
            item.value for item in RunReason
        }:
            raise ValueError(f"unsupported run_reason: {self.run_reason}")
        format_utc_datetime(self.started_at_utc)
        if self.completed_at_utc is not None:
            format_utc_datetime(self.completed_at_utc)
            if self.completed_at_utc < self.started_at_utc:
                raise ValueError("completed_at_utc precedes started_at_utc")
        if self.reproducibility_level not in {
            item.value for item in ReproducibilityLevel
        }:
            raise ValueError("invalid reproducibility_level")
        try:
            if date.fromisoformat(self.operating_date).isoformat() != self.operating_date:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("operating_date must be YYYY-MM-DD") from exc
        if self.operating_timezone != "America/Toronto":
            raise ValueError("unsupported operating_timezone")
        if self.run_manifest_schema_version != RUN_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported run_manifest_schema_version")
        if self.canonical_runtime_mode != "LIVE":
            raise ValueError("canonical_runtime_mode must be LIVE")
        if self.lifecycle_authority != "SHADOW_ONLY":
            raise ValueError("lifecycle_authority must be SHADOW_ONLY")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at_utc"] = format_utc_datetime(self.started_at_utc)
        data["completed_at_utc"] = _optional_utc(self.completed_at_utc)
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunManifest":
        data = dict(value)
        data["started_at_utc"] = parse_utc_datetime(data["started_at_utc"])
        data["completed_at_utc"] = parse_utc_datetime(data.get("completed_at_utc"))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    event_schema_version: int
    payload_schema_version: int
    identity_schema_version: int
    canonicalization_version: str
    prediction_run_id: str
    prediction_id: str | None
    prediction_key: str | None
    market_subject_key: str | None
    event_sequence: int
    occurred_at_utc: datetime
    recorded_at_utc: datetime
    provider_reported_at_utc: datetime | None
    operating_date: str
    operating_timezone: str
    actor_type: str
    actor_id: str
    correlation_id: str
    idempotency_key: str
    payload_json: str
    payload_sha256: str
    source_refs: Mapping[str, Any]
    source_hashes: Mapping[str, Any]
    code_sha: str | None
    config_hash: str | None
    model_id: str | None
    model_version: str | None
    previous_event_hash: str | None
    event_hash: str
    corrects_event_id: str | None

    @classmethod
    def create(
        cls,
        *,
        event_type: EventType | str,
        payload: Mapping[str, Any],
        payload_schema_version: int,
        prediction_run_id: str,
        event_sequence: int,
        occurred_at_utc: datetime,
        recorded_at_utc: datetime,
        operating_date: str,
        operating_timezone: str,
        actor_type: str,
        actor_id: str,
        correlation_id: str,
        idempotency_key: str,
        prediction_id: str | None = None,
        prediction_key: str | None = None,
        market_subject_key: str | None = None,
        provider_reported_at_utc: datetime | None = None,
        source_refs: Mapping[str, Any] | None = None,
        source_hashes: Mapping[str, Any] | None = None,
        code_sha: str | None = None,
        config_hash: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        previous_event_hash: str | None = None,
        corrects_event_id: str | None = None,
    ) -> "EventEnvelope":
        event_type_value = (
            event_type.value if isinstance(event_type, EventType) else str(event_type)
        )
        if event_type_value not in {item.value for item in EventType}:
            raise ValueError(f"unsupported event_type: {event_type_value}")
        if event_sequence < 1:
            raise ValueError("event_sequence must be positive")
        canonical_payload = canonical_json_v1(dict(payload))
        canonical_payload_hash = payload_sha256(dict(payload))
        event_id = deterministic_id(
            "evt",
            "courtvision.event.v1",
            {
                "event_type": event_type_value,
                "prediction_run_id": prediction_run_id,
                "event_sequence": event_sequence,
                "idempotency_key": idempotency_key,
                "payload_sha256": canonical_payload_hash,
            },
        )
        values: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type_value,
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "payload_schema_version": int(payload_schema_version),
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "prediction_run_id": prediction_run_id,
            "prediction_id": prediction_id,
            "prediction_key": prediction_key,
            "market_subject_key": market_subject_key,
            "event_sequence": event_sequence,
            "occurred_at_utc": occurred_at_utc,
            "recorded_at_utc": recorded_at_utc,
            "provider_reported_at_utc": provider_reported_at_utc,
            "operating_date": operating_date,
            "operating_timezone": operating_timezone,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "payload_json": canonical_payload,
            "payload_sha256": canonical_payload_hash,
            "source_refs": dict(source_refs or {}),
            "source_hashes": dict(source_hashes or {}),
            "code_sha": code_sha,
            "config_hash": config_hash,
            "model_id": model_id,
            "model_version": model_version,
            "previous_event_hash": previous_event_hash,
            "corrects_event_id": corrects_event_id,
        }
        event_hash = payload_sha256(_event_hash_payload(values))
        return cls(**values, event_hash=event_hash)

    def __post_init__(self) -> None:
        format_utc_datetime(self.occurred_at_utc)
        format_utc_datetime(self.recorded_at_utc)
        if self.provider_reported_at_utc is not None:
            format_utc_datetime(self.provider_reported_at_utc)
        if self.canonicalization_version != CANONICALIZATION_VERSION:
            raise ValueError("unsupported canonicalization_version")
        if self.event_schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported event_schema_version")
        if self.event_type not in {item.value for item in EventType}:
            raise ValueError("unsupported event_type")
        if self.payload_schema_version < 1:
            raise ValueError("payload_schema_version must be positive")
        if self.identity_schema_version != IDENTITY_SCHEMA_VERSION:
            raise ValueError("unsupported identity_schema_version")
        if self.event_sequence < 1:
            raise ValueError("event_sequence must be positive")
        if self.operating_timezone != "America/Toronto":
            raise ValueError("unsupported operating_timezone")
        try:
            if date.fromisoformat(self.operating_date).isoformat() != self.operating_date:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("operating_date must be YYYY-MM-DD") from exc
        if payload_sha256_from_json(self.payload_json) != self.payload_sha256:
            raise ValueError("payload_json does not match payload_sha256")
        expected = payload_sha256(_event_hash_payload(self.to_dict(include_hash=False)))
        if expected != self.event_hash:
            raise ValueError("event_hash is invalid")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["occurred_at_utc"] = format_utc_datetime(self.occurred_at_utc)
        data["recorded_at_utc"] = format_utc_datetime(self.recorded_at_utc)
        data["provider_reported_at_utc"] = _optional_utc(
            self.provider_reported_at_utc
        )
        if not include_hash:
            data.pop("event_hash", None)
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EventEnvelope":
        data = dict(value)
        data["occurred_at_utc"] = parse_utc_datetime(data["occurred_at_utc"])
        data["recorded_at_utc"] = parse_utc_datetime(data["recorded_at_utc"])
        data["provider_reported_at_utc"] = parse_utc_datetime(
            data.get("provider_reported_at_utc")
        )
        return cls(**data)


def payload_sha256_from_json(payload_json: str) -> str:
    import json

    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("payload_json is invalid") from exc
    if canonical_json_v1(payload) != payload_json:
        raise ValueError("payload_json is not canonical JSON v1")
    return payload_sha256(payload)


def _event_hash_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(values)
    data.pop("event_hash", None)
    return data


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    prediction_run_id: str
    operating_date: str
    status: str
    board_published: bool
    board_path: str | None
    board_sha256: str | None
    expected_row_count: int
    committed_event_count: int
    matched_row_count: int
    unresolved_identity_count: int
    mismatches: tuple[Mapping[str, Any], ...]
    errors: tuple[str, ...]
    verified_at_utc: datetime
    reconciliation_schema_version: int = RECONCILIATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in {item.value for item in ReconciliationStatus}:
            raise ValueError("invalid reconciliation status")
        format_utc_datetime(self.verified_at_utc)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verified_at_utc"] = format_utc_datetime(self.verified_at_utc)
        data["mismatches"] = [dict(item) for item in self.mismatches]
        data["errors"] = list(self.errors)
        return data


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "EventEnvelope",
    "EventType",
    "PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION",
    "PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION_V2",
    "RECONCILIATION_SCHEMA_VERSION",
    "RUN_MANIFEST_SCHEMA_VERSION",
    "ReconciliationReport",
    "ReconciliationStatus",
    "ReproducibilityLevel",
    "RunManifest",
    "RunMode",
    "RunReason",
]
