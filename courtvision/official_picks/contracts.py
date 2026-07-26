"""Canonical immutable contracts for official-pick publication and settlement."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import math
import re
from typing import Any, Mapping

from courtvision.lifecycle.canonical import format_utc_datetime, parse_utc_datetime
from courtvision.lifecycle.identity import (
    canonical_event_id,
    canonical_participant_id,
    canonical_team_id,
    normalize_bookmaker_id,
    normalize_line,
    normalize_market_id,
    normalize_selection,
)


OFFICIAL_PICK_SCHEMA_VERSION = 1
OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION = 1
OFFICIAL_PICK_PROMOTION_POLICY_VERSION = "1.0"
OFFICIAL_PICK_SETTLEMENT_SCHEMA_VERSION = 1
OFFICIAL_PICK_SETTLEMENT_PAYLOAD_SCHEMA_VERSION = 1
OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION = "1.0"
OFFICIAL_PICK_SETTLEMENT_CORRECTION_SCHEMA_VERSION = 1
OFFICIAL_PICK_SETTLEMENT_CORRECTION_PAYLOAD_SCHEMA_VERSION = 1


class OfficialPickValidationError(ValueError):
    """Raised before a malformed official pick can enter the ledger."""


class OfficialPickSettlementValidationError(ValueError):
    """Raised before a malformed official-pick settlement can enter the ledger."""


class PickRecordKind(str, Enum):
    MARKET_OBSERVATION = "MARKET_OBSERVATION"
    MODEL_CANDIDATE = "MODEL_CANDIDATE"
    OFFICIAL_PICK = "OFFICIAL_PICK"
    SETTLED_OFFICIAL_PICK = "SETTLED_OFFICIAL_PICK"
    LEGACY_UNIDENTIFIED = "LEGACY_UNIDENTIFIED"


class OfficialPickDesignation(str, Enum):
    PAPER = "PAPER"
    RESEARCH = "RESEARCH"
    LIVE = "LIVE"


class OfficialPickStatus(str, Enum):
    PUBLISHED = "PUBLISHED"


class OfficialPickSourceType(str, Enum):
    CANDIDATE = "CANDIDATE"
    OBSERVATION = "OBSERVATION"


class OfficialPickSettlementStatus(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    FINAL = "FINAL"


class OfficialPickSettlementOutcome(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    PUSH = "PUSH"
    VOID = "VOID"
    CANCELLED = "CANCELLED"
    UNRESOLVED = "UNRESOLVED"


class OfficialPickSettlementTransitionSlot(str, Enum):
    INITIAL = "INITIAL"
    FINALIZATION = "FINALIZATION"


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise OfficialPickValidationError(f"{field_name} is required")
    text = str(value).strip()
    if not text or text.casefold() in {
        "nan",
        "none",
        "null",
        "unknown",
        "unresolved",
    }:
        raise OfficialPickValidationError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null"}:
        return None
    return text


def _utc(value: str | datetime, field_name: str) -> datetime:
    try:
        parsed = parse_utc_datetime(value)
    except (TypeError, ValueError) as exc:
        raise OfficialPickValidationError(
            f"{field_name} must be a timezone-aware ISO-8601 datetime"
        ) from exc
    if parsed is None:
        raise OfficialPickValidationError(f"{field_name} is required")
    return parsed.astimezone(UTC)


def _prediction_date(value: str | date) -> str:
    text = value.isoformat() if isinstance(value, date) else _required_text(
        value, "prediction_date"
    )
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise OfficialPickValidationError(
            "prediction_date must be YYYY-MM-DD"
        ) from exc
    if parsed.isoformat() != text:
        raise OfficialPickValidationError("prediction_date must be YYYY-MM-DD")
    return text


def _odds(value: Any) -> int | float:
    if isinstance(value, bool):
        raise OfficialPickValidationError("odds must be numeric")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise OfficialPickValidationError("odds must be numeric") from exc
    if not number.is_finite() or number == 0:
        raise OfficialPickValidationError("odds must be finite and non-zero")
    if number == number.to_integral_value():
        return int(number)
    result = float(format(number.normalize(), "f"))
    if not math.isfinite(result):
        raise OfficialPickValidationError("odds must be finite")
    return result


def _enum_value(value: Any, enum_type: type[Enum], field_name: str) -> str:
    raw = value.value if isinstance(value, enum_type) else str(value).strip().upper()
    allowed = {item.value for item in enum_type}
    if raw not in allowed:
        raise OfficialPickValidationError(
            f"{field_name} must be one of {sorted(allowed)}"
        )
    return raw


def _settlement_required_text(value: Any, field_name: str) -> str:
    try:
        return _required_text(value, field_name)
    except OfficialPickValidationError as exc:
        raise OfficialPickSettlementValidationError(str(exc)) from exc


def _settlement_utc(value: str | datetime, field_name: str) -> datetime:
    try:
        return _utc(value, field_name)
    except OfficialPickValidationError as exc:
        raise OfficialPickSettlementValidationError(str(exc)) from exc


def _settlement_enum_value(
    value: Any, enum_type: type[Enum], field_name: str
) -> str:
    try:
        return _enum_value(value, enum_type, field_name)
    except OfficialPickValidationError as exc:
        raise OfficialPickSettlementValidationError(str(exc)) from exc


def _evidence_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise OfficialPickSettlementValidationError(
            f"{field_name} must be a non-empty mapping"
        )
    result = dict(value)
    if any(not str(key).strip() for key in result):
        raise OfficialPickSettlementValidationError(
            f"{field_name} keys must be non-empty"
        )
    return result


def _final_score(value: Any) -> str | dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if not value:
            raise OfficialPickSettlementValidationError(
                "final_score mapping must not be empty"
            )
        return dict(value)
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null", "unknown"}:
        raise OfficialPickSettlementValidationError(
            "final_score must be meaningful when supplied"
        )
    return text


def _canonical_market_key(value: Any, *, sport: str, league: str) -> str | None:
    text = _required_text(value, "market_key")
    prefix = f"courtvision:{sport.lower()}:{league.lower()}:market:"
    if text.casefold().startswith("courtvision:"):
        if not text.lower().startswith(prefix):
            return None
        text = text[len(prefix) :]
    return normalize_market_id(text, sport=sport, league=league)


@dataclass(frozen=True, slots=True)
class OfficialPick:
    """One immutable official paper/research pick as published to the ledger."""

    pick_id: str
    sport: str
    league: str
    event_id: str
    event_start_time: datetime
    prediction_date: str
    market_key: str
    selection: str
    line: str
    odds: int | float
    sportsbook: str
    model_name: str
    model_version: str
    run_id: str
    published_at: datetime
    status: str
    designation: str
    idempotency_key: str
    provenance: Mapping[str, Any]
    player_id: str | None = None
    player_name: str | None = None
    team_id: str | None = None
    source_candidate_id: str | None = None
    source_observation_id: str | None = None
    schema_version: int = OFFICIAL_PICK_SCHEMA_VERSION
    record_kind: str = field(
        default=PickRecordKind.OFFICIAL_PICK.value,
        init=False,
    )

    def __post_init__(self) -> None:
        pick_id = _required_text(self.pick_id, "pick_id")
        if re.fullmatch(r"pick_[0-9a-f]{32}", pick_id) is None:
            raise OfficialPickValidationError(
                "pick_id must be an assigned pick_<uuid4 hex> identifier"
            )
        sport = _required_text(self.sport, "sport").lower()
        league = _required_text(self.league, "league").upper()
        event_id = canonical_event_id(self.event_id, sport=sport, league=league)
        if event_id is None:
            raise OfficialPickValidationError("event_id is malformed or unresolved")
        market = _canonical_market_key(
            self.market_key, sport=sport, league=league
        )
        if market is None:
            raise OfficialPickValidationError("market_key is malformed or unsupported")
        selection = normalize_selection(self.selection)
        if selection is None:
            raise OfficialPickValidationError("selection is malformed or unsupported")
        try:
            line = normalize_line(self.line)
        except ValueError as exc:
            raise OfficialPickValidationError("line must be finite and numeric") from exc
        if line is None:
            raise OfficialPickValidationError("line is required")
        sportsbook = normalize_bookmaker_id(self.sportsbook)
        if sportsbook is None:
            raise OfficialPickValidationError(
                "sportsbook is malformed or unsupported"
            )
        event_start = _utc(self.event_start_time, "event_start_time")
        published = _utc(self.published_at, "published_at")
        prediction_date = _prediction_date(self.prediction_date)
        if published > event_start:
            raise OfficialPickValidationError(
                "published_at must not be after event_start_time"
            )
        if date.fromisoformat(prediction_date) > event_start.date():
            raise OfficialPickValidationError(
                "prediction_date must not be after the event date"
            )
        player_id = _optional_text(self.player_id)
        player_name = _optional_text(self.player_name)
        if ":market:player_" in market:
            if player_id is None:
                raise OfficialPickValidationError(
                    "player_id is required for player markets"
                )
            if player_name is None:
                raise OfficialPickValidationError(
                    "player_name is required for player markets"
                )
            player_id = canonical_participant_id(
                player_id, sport=sport, league=league
            )
            if player_id is None:
                raise OfficialPickValidationError("player_id is malformed")
        team_id = _optional_text(self.team_id)
        if ":market:team_" in market and team_id is None:
            raise OfficialPickValidationError(
                "team_id is required for team markets"
            )
        if team_id is not None:
            team_id = canonical_team_id(team_id, sport=sport, league=league)
            if team_id is None:
                raise OfficialPickValidationError("team_id is malformed")
        source_candidate_id = _optional_text(self.source_candidate_id)
        source_observation_id = _optional_text(self.source_observation_id)
        if (source_candidate_id is None) == (source_observation_id is None):
            raise OfficialPickValidationError(
                "exactly one source_candidate_id or source_observation_id is required"
            )
        if source_candidate_id is not None:
            source_candidate_id = _required_text(
                source_candidate_id, "source_candidate_id"
            )
        if source_observation_id is not None:
            source_observation_id = _required_text(
                source_observation_id, "source_observation_id"
            )
        if self.schema_version != OFFICIAL_PICK_SCHEMA_VERSION:
            raise OfficialPickValidationError("unsupported official-pick schema_version")
        if self.record_kind != PickRecordKind.OFFICIAL_PICK.value:
            raise OfficialPickValidationError("record_kind must be OFFICIAL_PICK")
        status = _enum_value(self.status, OfficialPickStatus, "status")
        designation = _enum_value(
            self.designation, OfficialPickDesignation, "designation"
        )
        if designation == OfficialPickDesignation.LIVE.value:
            raise OfficialPickValidationError(
                "LIVE designation is not supported by official-pick schema v1"
            )
        idempotency_key = _required_text(
            self.idempotency_key, "idempotency_key"
        )
        if re.fullmatch(r"opidem_[0-9a-f]{64}", idempotency_key) is None:
            raise OfficialPickValidationError(
                "idempotency_key must be a generated official-pick key"
            )
        if not isinstance(self.provenance, Mapping):
            raise OfficialPickValidationError("provenance must be a mapping")
        provenance = dict(self.provenance)
        source_type = (
            OfficialPickSourceType.CANDIDATE.value
            if source_candidate_id is not None
            else OfficialPickSourceType.OBSERVATION.value
        )
        source_id = source_candidate_id or source_observation_id
        required_provenance = {
            "source_type": source_type,
            "source_id": source_id,
            "promotion_service": "courtvision.official_picks",
        }
        for key, expected in required_provenance.items():
            if provenance.get(key) != expected:
                raise OfficialPickValidationError(
                    f"provenance.{key} must equal {expected!r}"
                )
        _required_text(provenance.get("promotion_actor"), "provenance.promotion_actor")

        object.__setattr__(self, "pick_id", pick_id)
        object.__setattr__(self, "sport", sport)
        object.__setattr__(self, "league", league)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_start_time", event_start)
        object.__setattr__(self, "prediction_date", prediction_date)
        object.__setattr__(self, "market_key", market)
        object.__setattr__(self, "selection", selection)
        object.__setattr__(self, "line", line)
        object.__setattr__(self, "odds", _odds(self.odds))
        object.__setattr__(self, "sportsbook", sportsbook)
        object.__setattr__(self, "model_name", _required_text(self.model_name, "model_name"))
        object.__setattr__(
            self, "model_version", _required_text(self.model_version, "model_version")
        )
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "published_at", published)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "designation", designation)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "player_id", player_id)
        object.__setattr__(self, "player_name", player_name)
        object.__setattr__(self, "team_id", team_id)
        object.__setattr__(self, "source_candidate_id", source_candidate_id)
        object.__setattr__(self, "source_observation_id", source_observation_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pick_id": self.pick_id,
            "sport": self.sport,
            "league": self.league,
            "event_id": self.event_id,
            "event_start_time": format_utc_datetime(self.event_start_time),
            "prediction_date": self.prediction_date,
            "market_key": self.market_key,
            "selection": self.selection,
            "line": self.line,
            "odds": self.odds,
            "sportsbook": self.sportsbook,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team_id": self.team_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "run_id": self.run_id,
            "published_at": format_utc_datetime(self.published_at),
            "source_candidate_id": self.source_candidate_id,
            "source_observation_id": self.source_observation_id,
            "status": self.status,
            "designation": self.designation,
            "idempotency_key": self.idempotency_key,
            "provenance": dict(self.provenance),
            "schema_version": self.schema_version,
            "record_kind": self.record_kind,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OfficialPick":
        data = dict(value)
        record_kind = data.pop("record_kind", PickRecordKind.OFFICIAL_PICK.value)
        if record_kind != PickRecordKind.OFFICIAL_PICK.value:
            raise OfficialPickValidationError("record_kind must be OFFICIAL_PICK")
        data["event_start_time"] = _utc(
            data.get("event_start_time"), "event_start_time"
        )
        data["published_at"] = _utc(data.get("published_at"), "published_at")
        try:
            return cls(**data)
        except TypeError as exc:
            raise OfficialPickValidationError(
                f"official-pick schema mismatch: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class OfficialPickPromotionRequest:
    """Validated source material that may be explicitly promoted once."""

    sport: str
    league: str
    event_id: str
    event_start_time: str | datetime
    prediction_date: str
    market_key: str
    selection: str
    line: Any
    odds: Any
    sportsbook: str
    model_name: str
    model_version: str
    run_id: str
    player_id: str | None = None
    player_name: str | None = None
    team_id: str | None = None
    source_candidate_id: str | None = None
    source_observation_id: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "OfficialPickPromotionRequest":
        allowed = {
            "sport",
            "league",
            "event_id",
            "event_start_time",
            "prediction_date",
            "market_key",
            "selection",
            "line",
            "odds",
            "sportsbook",
            "model_name",
            "model_version",
            "run_id",
            "player_id",
            "player_name",
            "team_id",
            "source_candidate_id",
            "source_observation_id",
            "provenance",
        }
        data = {key: item for key, item in value.items() if key in allowed}
        try:
            return cls(**data)
        except TypeError as exc:
            raise OfficialPickValidationError(
                f"promotion request schema mismatch: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class OfficialPickSettlement:
    """One immutable settlement decision for a committed official pick."""

    settlement_id: str
    pick_id: str
    settlement_status: str
    outcome: str
    final_score: str | Mapping[str, Any] | None
    result_evidence: Mapping[str, Any]
    settled_at: datetime
    result_source: str
    source_record_id: str
    settlement_run_id: str
    idempotency_key: str
    provenance: Mapping[str, Any]
    schema_version: int = OFFICIAL_PICK_SETTLEMENT_SCHEMA_VERSION
    record_kind: str = field(
        default=PickRecordKind.SETTLED_OFFICIAL_PICK.value,
        init=False,
    )

    def __post_init__(self) -> None:
        settlement_id = _settlement_required_text(
            self.settlement_id, "settlement_id"
        )
        if re.fullmatch(r"settlement_[0-9a-f]{32}", settlement_id) is None:
            raise OfficialPickSettlementValidationError(
                "settlement_id must be an assigned settlement_<uuid4 hex> identifier"
            )
        pick_id = _settlement_required_text(self.pick_id, "pick_id")
        if re.fullmatch(r"pick_[0-9a-f]{32}", pick_id) is None:
            raise OfficialPickSettlementValidationError(
                "pick_id must be a committed pick_<uuid4 hex> identifier"
            )
        status = _settlement_enum_value(
            self.settlement_status,
            OfficialPickSettlementStatus,
            "settlement_status",
        )
        outcome = _settlement_enum_value(
            self.outcome,
            OfficialPickSettlementOutcome,
            "outcome",
        )
        if (
            status == OfficialPickSettlementStatus.UNRESOLVED.value
        ) != (outcome == OfficialPickSettlementOutcome.UNRESOLVED.value):
            raise OfficialPickSettlementValidationError(
                "UNRESOLVED status and outcome must be used together"
            )
        final_score = _final_score(self.final_score)
        if (
            status == OfficialPickSettlementStatus.UNRESOLVED.value
            and final_score is not None
        ):
            raise OfficialPickSettlementValidationError(
                "UNRESOLVED settlement must not contain final_score"
            )
        if self.schema_version != OFFICIAL_PICK_SETTLEMENT_SCHEMA_VERSION:
            raise OfficialPickSettlementValidationError(
                "unsupported official-pick settlement schema_version"
            )
        if self.record_kind != PickRecordKind.SETTLED_OFFICIAL_PICK.value:
            raise OfficialPickSettlementValidationError(
                "record_kind must be SETTLED_OFFICIAL_PICK"
            )
        idempotency_key = _settlement_required_text(
            self.idempotency_key, "idempotency_key"
        )
        if re.fullmatch(r"opsetidem_[0-9a-f]{64}", idempotency_key) is None:
            raise OfficialPickSettlementValidationError(
                "idempotency_key must be a generated official-pick settlement key"
            )
        if not isinstance(self.provenance, Mapping):
            raise OfficialPickSettlementValidationError(
                "provenance must be a mapping"
            )
        provenance = dict(self.provenance)
        required_provenance = {
            "settlement_service": "courtvision.official_picks.settlement",
            "settlement_policy_version": OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
        }
        for key, expected in required_provenance.items():
            if provenance.get(key) != expected:
                raise OfficialPickSettlementValidationError(
                    f"provenance.{key} must equal {expected!r}"
                )
        _settlement_required_text(
            provenance.get("settlement_actor"),
            "provenance.settlement_actor",
        )

        object.__setattr__(self, "settlement_id", settlement_id)
        object.__setattr__(self, "pick_id", pick_id)
        object.__setattr__(self, "settlement_status", status)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "final_score", final_score)
        object.__setattr__(
            self,
            "result_evidence",
            _evidence_mapping(self.result_evidence, "result_evidence"),
        )
        object.__setattr__(
            self, "settled_at", _settlement_utc(self.settled_at, "settled_at")
        )
        object.__setattr__(
            self,
            "result_source",
            _settlement_required_text(self.result_source, "result_source"),
        )
        object.__setattr__(
            self,
            "source_record_id",
            _settlement_required_text(
                self.source_record_id, "source_record_id"
            ),
        )
        object.__setattr__(
            self,
            "settlement_run_id",
            _settlement_required_text(
                self.settlement_run_id, "settlement_run_id"
            ),
        )
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "provenance", provenance)

    def to_dict(self) -> dict[str, Any]:
        final_score = (
            dict(self.final_score)
            if isinstance(self.final_score, Mapping)
            else self.final_score
        )
        return {
            "settlement_id": self.settlement_id,
            "pick_id": self.pick_id,
            "settlement_status": self.settlement_status,
            "outcome": self.outcome,
            "final_score": final_score,
            "result_evidence": dict(self.result_evidence),
            "settled_at": format_utc_datetime(self.settled_at),
            "result_source": self.result_source,
            "source_record_id": self.source_record_id,
            "settlement_run_id": self.settlement_run_id,
            "idempotency_key": self.idempotency_key,
            "provenance": dict(self.provenance),
            "schema_version": self.schema_version,
            "record_kind": self.record_kind,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OfficialPickSettlement":
        data = dict(value)
        record_kind = data.pop(
            "record_kind", PickRecordKind.SETTLED_OFFICIAL_PICK.value
        )
        if record_kind != PickRecordKind.SETTLED_OFFICIAL_PICK.value:
            raise OfficialPickSettlementValidationError(
                "record_kind must be SETTLED_OFFICIAL_PICK"
            )
        data["settled_at"] = _settlement_utc(
            data.get("settled_at"), "settled_at"
        )
        try:
            return cls(**data)
        except TypeError as exc:
            raise OfficialPickSettlementValidationError(
                f"official-pick settlement schema mismatch: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class OfficialPickSettlementCorrection:
    """Append-only correction to one final official-pick settlement."""

    correction_id: str
    original_settlement_id: str
    pick_id: str
    correction_reason: str
    corrected_outcome: str
    corrected_final_score: str | Mapping[str, Any] | None
    corrected_result_evidence: Mapping[str, Any]
    corrected_at: datetime
    result_source: str
    source_record_id: str
    correction_run_id: str
    idempotency_key: str
    provenance: Mapping[str, Any]
    schema_version: int = OFFICIAL_PICK_SETTLEMENT_CORRECTION_SCHEMA_VERSION
    record_kind: str = field(
        default="OFFICIAL_PICK_SETTLEMENT_CORRECTION",
        init=False,
    )

    def __post_init__(self) -> None:
        correction_id = _settlement_required_text(
            self.correction_id, "correction_id"
        )
        if re.fullmatch(
            r"settlement_correction_[0-9a-f]{32}", correction_id
        ) is None:
            raise OfficialPickSettlementValidationError(
                "correction_id must be an assigned settlement_correction_<uuid4 hex> identifier"
            )
        original_settlement_id = _settlement_required_text(
            self.original_settlement_id, "original_settlement_id"
        )
        if re.fullmatch(
            r"settlement_[0-9a-f]{32}", original_settlement_id
        ) is None:
            raise OfficialPickSettlementValidationError(
                "original_settlement_id must reference a settlement_<uuid4 hex>"
            )
        pick_id = _settlement_required_text(self.pick_id, "pick_id")
        if re.fullmatch(r"pick_[0-9a-f]{32}", pick_id) is None:
            raise OfficialPickSettlementValidationError(
                "pick_id must be a committed pick_<uuid4 hex> identifier"
            )
        corrected_outcome = _settlement_enum_value(
            self.corrected_outcome,
            OfficialPickSettlementOutcome,
            "corrected_outcome",
        )
        if corrected_outcome == OfficialPickSettlementOutcome.UNRESOLVED.value:
            raise OfficialPickSettlementValidationError(
                "a correction must contain a final corrected_outcome"
            )
        idempotency_key = _settlement_required_text(
            self.idempotency_key, "idempotency_key"
        )
        if re.fullmatch(r"opcoridem_[0-9a-f]{64}", idempotency_key) is None:
            raise OfficialPickSettlementValidationError(
                "idempotency_key must be a generated settlement correction key"
            )
        if self.schema_version != OFFICIAL_PICK_SETTLEMENT_CORRECTION_SCHEMA_VERSION:
            raise OfficialPickSettlementValidationError(
                "unsupported settlement correction schema_version"
            )
        if self.record_kind != "OFFICIAL_PICK_SETTLEMENT_CORRECTION":
            raise OfficialPickSettlementValidationError(
                "record_kind must be OFFICIAL_PICK_SETTLEMENT_CORRECTION"
            )
        if not isinstance(self.provenance, Mapping):
            raise OfficialPickSettlementValidationError(
                "provenance must be a mapping"
            )
        provenance = dict(self.provenance)
        required_provenance = {
            "correction_service": "courtvision.official_picks.settlement",
            "settlement_policy_version": OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
        }
        for key, expected in required_provenance.items():
            if provenance.get(key) != expected:
                raise OfficialPickSettlementValidationError(
                    f"provenance.{key} must equal {expected!r}"
                )
        _settlement_required_text(
            provenance.get("correction_actor"),
            "provenance.correction_actor",
        )

        object.__setattr__(self, "correction_id", correction_id)
        object.__setattr__(
            self, "original_settlement_id", original_settlement_id
        )
        object.__setattr__(self, "pick_id", pick_id)
        object.__setattr__(
            self,
            "correction_reason",
            _settlement_required_text(
                self.correction_reason, "correction_reason"
            ),
        )
        object.__setattr__(self, "corrected_outcome", corrected_outcome)
        object.__setattr__(
            self,
            "corrected_final_score",
            _final_score(self.corrected_final_score),
        )
        object.__setattr__(
            self,
            "corrected_result_evidence",
            _evidence_mapping(
                self.corrected_result_evidence,
                "corrected_result_evidence",
            ),
        )
        object.__setattr__(
            self, "corrected_at", _settlement_utc(self.corrected_at, "corrected_at")
        )
        object.__setattr__(
            self,
            "result_source",
            _settlement_required_text(self.result_source, "result_source"),
        )
        object.__setattr__(
            self,
            "source_record_id",
            _settlement_required_text(
                self.source_record_id, "source_record_id"
            ),
        )
        object.__setattr__(
            self,
            "correction_run_id",
            _settlement_required_text(
                self.correction_run_id, "correction_run_id"
            ),
        )
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "provenance", provenance)

    def to_dict(self) -> dict[str, Any]:
        corrected_final_score = (
            dict(self.corrected_final_score)
            if isinstance(self.corrected_final_score, Mapping)
            else self.corrected_final_score
        )
        return {
            "correction_id": self.correction_id,
            "original_settlement_id": self.original_settlement_id,
            "pick_id": self.pick_id,
            "correction_reason": self.correction_reason,
            "corrected_outcome": self.corrected_outcome,
            "corrected_final_score": corrected_final_score,
            "corrected_result_evidence": dict(
                self.corrected_result_evidence
            ),
            "corrected_at": format_utc_datetime(self.corrected_at),
            "result_source": self.result_source,
            "source_record_id": self.source_record_id,
            "correction_run_id": self.correction_run_id,
            "idempotency_key": self.idempotency_key,
            "provenance": dict(self.provenance),
            "schema_version": self.schema_version,
            "record_kind": self.record_kind,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "OfficialPickSettlementCorrection":
        data = dict(value)
        record_kind = data.pop(
            "record_kind", "OFFICIAL_PICK_SETTLEMENT_CORRECTION"
        )
        if record_kind != "OFFICIAL_PICK_SETTLEMENT_CORRECTION":
            raise OfficialPickSettlementValidationError(
                "record_kind must be OFFICIAL_PICK_SETTLEMENT_CORRECTION"
            )
        data["corrected_at"] = _settlement_utc(
            data.get("corrected_at"), "corrected_at"
        )
        try:
            return cls(**data)
        except TypeError as exc:
            raise OfficialPickSettlementValidationError(
                f"settlement correction schema mismatch: {exc}"
            ) from exc


__all__ = [
    "OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION",
    "OFFICIAL_PICK_PROMOTION_POLICY_VERSION",
    "OFFICIAL_PICK_SCHEMA_VERSION",
    "OFFICIAL_PICK_SETTLEMENT_CORRECTION_PAYLOAD_SCHEMA_VERSION",
    "OFFICIAL_PICK_SETTLEMENT_CORRECTION_SCHEMA_VERSION",
    "OFFICIAL_PICK_SETTLEMENT_PAYLOAD_SCHEMA_VERSION",
    "OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION",
    "OFFICIAL_PICK_SETTLEMENT_SCHEMA_VERSION",
    "OfficialPick",
    "OfficialPickDesignation",
    "OfficialPickPromotionRequest",
    "OfficialPickSettlement",
    "OfficialPickSettlementCorrection",
    "OfficialPickSettlementOutcome",
    "OfficialPickSettlementStatus",
    "OfficialPickSettlementTransitionSlot",
    "OfficialPickSettlementValidationError",
    "OfficialPickSourceType",
    "OfficialPickStatus",
    "OfficialPickValidationError",
    "PickRecordKind",
]
