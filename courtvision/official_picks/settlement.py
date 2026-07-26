"""Append-only settlement lifecycle for committed paper/research official picks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import platform
import re
from typing import Any, Callable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from courtvision.lifecycle.canonical import deterministic_id, payload_sha256
from courtvision.lifecycle.clock import Clock, SystemClock
from courtvision.lifecycle.models import (
    EventEnvelope,
    EventType,
    ReproducibilityLevel,
    RunManifest,
    RunMode,
    RunReason,
)
from courtvision.lifecycle.writer import (
    FailureHook,
    IdempotencyConflictError,
    LifecycleWriter,
    completed_segment_directories,
    read_segment_events,
    verify_segment,
)
from courtvision.official_picks.contracts import (
    OFFICIAL_PICK_SETTLEMENT_CORRECTION_PAYLOAD_SCHEMA_VERSION,
    OFFICIAL_PICK_SETTLEMENT_PAYLOAD_SCHEMA_VERSION,
    OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
    OfficialPick,
    OfficialPickSettlement,
    OfficialPickSettlementCorrection,
    OfficialPickSettlementOutcome,
    OfficialPickSettlementStatus,
    OfficialPickSettlementTransitionSlot,
    OfficialPickSettlementValidationError,
)
from courtvision.official_picks.service import (
    OfficialPickLedgerIntegrityError,
    read_official_pick,
    read_official_picks,
)


SettlementIdFactory = Callable[[], str]
CorrectionIdFactory = Callable[[], str]
TransactionIdFactory = Callable[[], str]

_TORONTO = ZoneInfo("America/Toronto")


class OfficialPickSettlementError(RuntimeError):
    """Base error for official-pick settlement publication."""


class OfficialPickSettlementReferenceError(OfficialPickSettlementError):
    """Settlement did not reference a committed official pick."""


class OfficialPickSettlementConflictError(OfficialPickSettlementError):
    """A deterministic settlement identity was replayed with different content."""


class OfficialPickSettlementTransitionError(OfficialPickSettlementError):
    """The requested append-only settlement state transition is illegal."""


class OfficialPickSettlementLedgerIntegrityError(OfficialPickSettlementError):
    """Committed settlement data failed strict reconstruction."""


@dataclass(frozen=True, slots=True)
class OfficialPickSettlementResult:
    settlement: OfficialPickSettlement
    publication_status: str
    ledger_segment_directory: Path
    event_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class OfficialPickSettlementCorrectionResult:
    correction: OfficialPickSettlementCorrection
    publication_status: str
    ledger_segment_directory: Path
    event_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class OfficialPickSettlementState:
    pick_id: str
    unresolved_settlement: OfficialPickSettlement | None
    final_settlement: OfficialPickSettlement | None
    correction: OfficialPickSettlementCorrection | None

    @property
    def settlement_status(self) -> str:
        if self.final_settlement is not None:
            return OfficialPickSettlementStatus.FINAL.value
        return OfficialPickSettlementStatus.UNRESOLVED.value

    @property
    def effective_outcome(self) -> str:
        if self.correction is not None:
            return self.correction.corrected_outcome
        if self.final_settlement is not None:
            return self.final_settlement.outcome
        return OfficialPickSettlementOutcome.UNRESOLVED.value


@dataclass(frozen=True, slots=True)
class _SettlementRecord:
    settlement: OfficialPickSettlement
    event: EventEnvelope
    segment_directory: Path
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _CorrectionRecord:
    correction: OfficialPickSettlementCorrection
    event: EventEnvelope
    segment_directory: Path
    content_sha256: str


def generate_settlement_id() -> str:
    return f"settlement_{uuid4().hex}"


def generate_settlement_correction_id() -> str:
    return f"settlement_correction_{uuid4().hex}"


def generate_settlement_transaction_id() -> str:
    return f"official-pick-settlement-{uuid4().hex}"


def generate_settlement_correction_transaction_id() -> str:
    return f"official-pick-settlement-correction-{uuid4().hex}"


def official_pick_settlement_idempotency_key(
    pick_id: str,
    transition_slot: str | OfficialPickSettlementTransitionSlot,
) -> str:
    target = _pick_id_text(pick_id)
    slot = (
        transition_slot.value
        if isinstance(transition_slot, OfficialPickSettlementTransitionSlot)
        else str(transition_slot).strip().upper()
    )
    if slot not in {
        item.value for item in OfficialPickSettlementTransitionSlot
    }:
        raise OfficialPickSettlementValidationError(
            "unsupported official-pick settlement transition slot"
        )
    return deterministic_id(
        "opsetidem",
        "courtvision.official_pick_settlement.v1",
        {
            "settlement_policy_version": OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
            "pick_id": target,
            "transition_slot": slot,
        },
    )


def official_pick_settlement_correction_idempotency_key(
    original_settlement_id: str,
) -> str:
    target = str(original_settlement_id).strip()
    if re.fullmatch(r"settlement_[0-9a-f]{32}", target) is None:
        raise OfficialPickSettlementValidationError(
            "original_settlement_id must reference a settlement_<uuid4 hex>"
        )
    return deterministic_id(
        "opcoridem",
        "courtvision.official_pick_settlement_correction.v1",
        {
            "settlement_policy_version": OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
            "original_settlement_id": target,
        },
    )


def settle_official_pick(
    pick_id: str,
    *,
    outcome: str | OfficialPickSettlementOutcome,
    result_source: str,
    source_record_id: str,
    settlement_run_id: str,
    result_evidence: Mapping[str, Any],
    lifecycle_root: str | Path,
    final_score: str | Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    settlement_actor: str = "courtvision.operator",
    clock: Clock | None = None,
    settlement_id_factory: SettlementIdFactory = generate_settlement_id,
    transaction_id_factory: TransactionIdFactory = generate_settlement_transaction_id,
    failure_hook: FailureHook | None = None,
) -> OfficialPickSettlementResult:
    """Append one unresolved or final decision for a committed official pick."""

    root = Path(lifecycle_root)
    pick = _committed_pick(root, pick_id)
    outcome_value = _outcome(outcome)
    status = (
        OfficialPickSettlementStatus.UNRESOLVED.value
        if outcome_value == OfficialPickSettlementOutcome.UNRESOLVED.value
        else OfficialPickSettlementStatus.FINAL.value
    )
    actor = _required_text(settlement_actor, "settlement_actor")
    run_id = _required_text(settlement_run_id, "settlement_run_id")
    records = _settlement_records(root)
    transition_slot = _requested_transition_slot(
        records, pick_id=pick.pick_id, status=status
    )
    idempotency_key = official_pick_settlement_idempotency_key(
        pick.pick_id, transition_slot
    )
    existing = _one_settlement_by_key(records, idempotency_key)
    if existing is not None:
        return _settlement_replay_result(
            existing,
            status=status,
            outcome=outcome_value,
            result_source=result_source,
            source_record_id=source_record_id,
            settlement_run_id=run_id,
            result_evidence=result_evidence,
            final_score=final_score,
            provenance=provenance,
            settlement_actor=actor,
        )
    _validate_new_transition(records, pick_id=pick.pick_id, status=status)

    active_clock = clock or SystemClock()
    settled_at = active_clock.now()
    if settled_at < pick.published_at:
        raise OfficialPickSettlementValidationError(
            "settled_at must not precede official-pick publication"
        )
    settlement = _build_settlement(
        pick=pick,
        settlement_id=settlement_id_factory(),
        status=status,
        outcome=outcome_value,
        final_score=final_score,
        result_evidence=result_evidence,
        settled_at=settled_at,
        result_source=result_source,
        source_record_id=source_record_id,
        settlement_run_id=run_id,
        idempotency_key=idempotency_key,
        provenance=provenance,
        settlement_actor=actor,
    )
    if any(
        item.settlement.settlement_id == settlement.settlement_id
        for item in records
    ):
        raise OfficialPickSettlementConflictError(
            f"settlement_id is already committed: {settlement.settlement_id}"
        )
    content_hash = _settlement_content_sha256(settlement)
    transaction_id = _required_text(
        transaction_id_factory(), "settlement transaction ID"
    )
    manifest = _transaction_manifest(
        pick,
        transaction_id=transaction_id,
        parent_run_id=settlement.settlement_run_id,
        occurred_at=settlement.settled_at,
        pipeline_version="official-pick-settlement-v1",
    )
    event = _settlement_event(
        pick,
        settlement,
        transaction_id=transaction_id,
        content_sha256=content_hash,
    )
    writer = LifecycleWriter(root, clock=active_clock)
    try:
        commit = writer.commit_segment(
            manifest,
            (event,),
            failure_hook=failure_hook,
            command="courtvision official-pick settle",
        )
    except IdempotencyConflictError as exc:
        raced = _one_settlement_by_key(
            _settlement_records(root), idempotency_key
        )
        if raced is None:
            raise OfficialPickSettlementConflictError(str(exc)) from exc
        return _settlement_replay_result(
            raced,
            status=status,
            outcome=outcome_value,
            result_source=result_source,
            source_record_id=source_record_id,
            settlement_run_id=run_id,
            result_evidence=result_evidence,
            final_score=final_score,
            provenance=provenance,
            settlement_actor=actor,
        )

    persisted = _settlement_record_in_segment(
        commit.segment_directory,
        idempotency_key,
        lifecycle_root=root,
        committed_picks=_committed_pick_index(root),
    )
    return OfficialPickSettlementResult(
        settlement=persisted.settlement,
        publication_status=(
            "PUBLISHED" if commit.status == "COMMITTED" else "ALREADY_PUBLISHED"
        ),
        ledger_segment_directory=commit.segment_directory,
        event_id=persisted.event.event_id,
        idempotency_key=idempotency_key,
    )


def correct_official_pick_settlement(
    original_settlement_id: str,
    *,
    pick_id: str,
    correction_reason: str,
    corrected_outcome: str | OfficialPickSettlementOutcome,
    result_source: str,
    source_record_id: str,
    correction_run_id: str,
    corrected_result_evidence: Mapping[str, Any],
    lifecycle_root: str | Path,
    corrected_final_score: str | Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    correction_actor: str = "courtvision.operator",
    clock: Clock | None = None,
    correction_id_factory: CorrectionIdFactory = generate_settlement_correction_id,
    transaction_id_factory: TransactionIdFactory = (
        generate_settlement_correction_transaction_id
    ),
    failure_hook: FailureHook | None = None,
) -> OfficialPickSettlementCorrectionResult:
    """Append an explicit correction without replacing the original settlement."""

    root = Path(lifecycle_root)
    pick = _committed_pick(root, pick_id)
    settlements = _settlement_records(root)
    original = next(
        (
            item
            for item in settlements
            if item.settlement.settlement_id == str(original_settlement_id).strip()
        ),
        None,
    )
    if original is None:
        raise OfficialPickSettlementReferenceError(
            "original_settlement_id is not present in the settlement ledger"
        )
    if original.settlement.pick_id != pick.pick_id:
        raise OfficialPickSettlementReferenceError(
            "correction pick_id does not match the original settlement"
        )
    if (
        original.settlement.settlement_status
        != OfficialPickSettlementStatus.FINAL.value
    ):
        raise OfficialPickSettlementTransitionError(
            "UNRESOLVED must transition through a new final settlement, not correction"
        )
    outcome_value = _outcome(corrected_outcome)
    if outcome_value == OfficialPickSettlementOutcome.UNRESOLVED.value:
        raise OfficialPickSettlementTransitionError(
            "a correction must contain a final corrected outcome"
        )
    actor = _required_text(correction_actor, "correction_actor")
    run_id = _required_text(correction_run_id, "correction_run_id")
    idempotency_key = official_pick_settlement_correction_idempotency_key(
        original.settlement.settlement_id
    )
    corrections = _correction_records(
        root, settlements=settlements
    )
    existing = _one_correction_by_key(corrections, idempotency_key)
    if existing is not None:
        return _correction_replay_result(
            existing,
            original=original,
            correction_reason=correction_reason,
            corrected_outcome=outcome_value,
            corrected_final_score=corrected_final_score,
            corrected_result_evidence=corrected_result_evidence,
            result_source=result_source,
            source_record_id=source_record_id,
            correction_run_id=run_id,
            provenance=provenance,
            correction_actor=actor,
        )

    active_clock = clock or SystemClock()
    corrected_at = active_clock.now()
    if corrected_at < original.settlement.settled_at:
        raise OfficialPickSettlementValidationError(
            "corrected_at must not precede the original settlement"
        )
    correction = _build_correction(
        original=original.settlement,
        correction_id=correction_id_factory(),
        correction_reason=correction_reason,
        corrected_outcome=outcome_value,
        corrected_final_score=corrected_final_score,
        corrected_result_evidence=corrected_result_evidence,
        corrected_at=corrected_at,
        result_source=result_source,
        source_record_id=source_record_id,
        correction_run_id=run_id,
        idempotency_key=idempotency_key,
        provenance=provenance,
        correction_actor=actor,
    )
    if any(item.correction.correction_id == correction.correction_id for item in corrections):
        raise OfficialPickSettlementConflictError(
            f"correction_id is already committed: {correction.correction_id}"
        )
    content_hash = _correction_content_sha256(correction)
    transaction_id = _required_text(
        transaction_id_factory(), "settlement correction transaction ID"
    )
    manifest = _transaction_manifest(
        pick,
        transaction_id=transaction_id,
        parent_run_id=correction.correction_run_id,
        occurred_at=correction.corrected_at,
        pipeline_version="official-pick-settlement-correction-v1",
    )
    event = _correction_event(
        pick,
        correction,
        original_event_id=original.event.event_id,
        transaction_id=transaction_id,
        content_sha256=content_hash,
    )
    writer = LifecycleWriter(root, clock=active_clock)
    try:
        commit = writer.commit_segment(
            manifest,
            (event,),
            failure_hook=failure_hook,
            command="courtvision official-pick settlement-correct",
        )
    except IdempotencyConflictError as exc:
        raced = _one_correction_by_key(
            _correction_records(root), idempotency_key
        )
        if raced is None:
            raise OfficialPickSettlementConflictError(str(exc)) from exc
        return _correction_replay_result(
            raced,
            original=original,
            correction_reason=correction_reason,
            corrected_outcome=outcome_value,
            corrected_final_score=corrected_final_score,
            corrected_result_evidence=corrected_result_evidence,
            result_source=result_source,
            source_record_id=source_record_id,
            correction_run_id=run_id,
            provenance=provenance,
            correction_actor=actor,
        )

    persisted = _correction_record_in_segment(
        commit.segment_directory,
        idempotency_key,
        lifecycle_root=root,
        settlements={
            item.settlement.settlement_id: item for item in _settlement_records(root)
        },
    )
    return OfficialPickSettlementCorrectionResult(
        correction=persisted.correction,
        publication_status=(
            "PUBLISHED" if commit.status == "COMMITTED" else "ALREADY_PUBLISHED"
        ),
        ledger_segment_directory=commit.segment_directory,
        event_id=persisted.event.event_id,
        idempotency_key=idempotency_key,
    )


def read_official_pick_settlements(
    lifecycle_root: str | Path,
) -> tuple[OfficialPickSettlement, ...]:
    return tuple(
        item.settlement for item in _settlement_records(Path(lifecycle_root))
    )


def read_official_pick_settlement_corrections(
    lifecycle_root: str | Path,
) -> tuple[OfficialPickSettlementCorrection, ...]:
    root = Path(lifecycle_root)
    settlements = _settlement_records(root)
    return tuple(
        item.correction
        for item in _correction_records(root, settlements=settlements)
    )


def read_official_pick_settlement_states(
    lifecycle_root: str | Path,
) -> tuple[OfficialPickSettlementState, ...]:
    root = Path(lifecycle_root)
    picks = tuple(_committed_pick_index(root).values())
    settlements = _settlement_records(root)
    corrections = _correction_records(root, settlements=settlements)
    by_pick: dict[str, list[OfficialPickSettlement]] = {}
    for item in settlements:
        by_pick.setdefault(item.settlement.pick_id, []).append(item.settlement)
    correction_by_settlement = {
        item.correction.original_settlement_id: item.correction
        for item in corrections
    }
    states: list[OfficialPickSettlementState] = []
    for pick in picks:
        pick_settlements = by_pick.get(pick.pick_id, [])
        unresolved = next(
            (
                item
                for item in pick_settlements
                if item.settlement_status
                == OfficialPickSettlementStatus.UNRESOLVED.value
            ),
            None,
        )
        final = next(
            (
                item
                for item in pick_settlements
                if item.settlement_status
                == OfficialPickSettlementStatus.FINAL.value
            ),
            None,
        )
        states.append(
            OfficialPickSettlementState(
                pick_id=pick.pick_id,
                unresolved_settlement=unresolved,
                final_settlement=final,
                correction=(
                    correction_by_settlement.get(final.settlement_id)
                    if final is not None
                    else None
                ),
            )
        )
    return tuple(states)


def read_official_pick_settlement_state(
    lifecycle_root: str | Path, pick_id: str
) -> OfficialPickSettlementState | None:
    target = str(pick_id).strip()
    return next(
        (
            item
            for item in read_official_pick_settlement_states(lifecycle_root)
            if item.pick_id == target
        ),
        None,
    )


def _committed_pick(root: Path, pick_id: str) -> OfficialPick:
    target = _pick_id_text(pick_id)
    pick = read_official_pick(root, target)
    if pick is None:
        raise OfficialPickSettlementReferenceError(
            f"pick_id is not present in the committed official-pick ledger: {target}"
        )
    return pick


def _committed_pick_index(root: Path) -> dict[str, OfficialPick]:
    try:
        return {item.pick_id: item for item in read_official_picks(root)}
    except OfficialPickLedgerIntegrityError as exc:
        raise OfficialPickSettlementLedgerIntegrityError(str(exc)) from exc


def _pick_id_text(value: Any) -> str:
    target = str(value or "").strip()
    if re.fullmatch(r"pick_[0-9a-f]{32}", target) is None:
        raise OfficialPickSettlementReferenceError(
            "settlement requires a committed pick_<uuid4 hex> pick_id"
        )
    return target


def _outcome(value: str | OfficialPickSettlementOutcome) -> str:
    raw = value.value if isinstance(value, OfficialPickSettlementOutcome) else str(value)
    normalized = raw.strip().upper()
    if normalized not in {item.value for item in OfficialPickSettlementOutcome}:
        raise OfficialPickSettlementValidationError(
            "unsupported official-pick settlement outcome"
        )
    return normalized


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() in {
        "nan",
        "none",
        "null",
        "unknown",
        "unresolved",
    }:
        raise OfficialPickSettlementValidationError(f"{field_name} is required")
    return text


def _provenance(
    value: Mapping[str, Any] | None,
    *,
    actor_key: str,
    actor: str,
    service_key: str,
) -> dict[str, Any]:
    if value is not None and not isinstance(value, Mapping):
        raise OfficialPickSettlementValidationError("provenance must be a mapping")
    return {
        **dict(value or {}),
        service_key: "courtvision.official_picks.settlement",
        actor_key: actor,
        "settlement_policy_version": OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
    }


def _build_settlement(
    *,
    pick: OfficialPick,
    settlement_id: str,
    status: str,
    outcome: str,
    final_score: str | Mapping[str, Any] | None,
    result_evidence: Mapping[str, Any],
    settled_at: datetime,
    result_source: str,
    source_record_id: str,
    settlement_run_id: str,
    idempotency_key: str,
    provenance: Mapping[str, Any] | None,
    settlement_actor: str,
) -> OfficialPickSettlement:
    return OfficialPickSettlement(
        settlement_id=settlement_id,
        pick_id=pick.pick_id,
        settlement_status=status,
        outcome=outcome,
        final_score=final_score,
        result_evidence=result_evidence,
        settled_at=settled_at,
        result_source=result_source,
        source_record_id=source_record_id,
        settlement_run_id=settlement_run_id,
        idempotency_key=idempotency_key,
        provenance=_provenance(
            provenance,
            actor_key="settlement_actor",
            actor=settlement_actor,
            service_key="settlement_service",
        ),
    )


def _build_correction(
    *,
    original: OfficialPickSettlement,
    correction_id: str,
    correction_reason: str,
    corrected_outcome: str,
    corrected_final_score: str | Mapping[str, Any] | None,
    corrected_result_evidence: Mapping[str, Any],
    corrected_at: datetime,
    result_source: str,
    source_record_id: str,
    correction_run_id: str,
    idempotency_key: str,
    provenance: Mapping[str, Any] | None,
    correction_actor: str,
) -> OfficialPickSettlementCorrection:
    return OfficialPickSettlementCorrection(
        correction_id=correction_id,
        original_settlement_id=original.settlement_id,
        pick_id=original.pick_id,
        correction_reason=correction_reason,
        corrected_outcome=corrected_outcome,
        corrected_final_score=corrected_final_score,
        corrected_result_evidence=corrected_result_evidence,
        corrected_at=corrected_at,
        result_source=result_source,
        source_record_id=source_record_id,
        correction_run_id=correction_run_id,
        idempotency_key=idempotency_key,
        provenance=_provenance(
            provenance,
            actor_key="correction_actor",
            actor=correction_actor,
            service_key="correction_service",
        ),
    )


def _settlement_content(value: OfficialPickSettlement) -> dict[str, Any]:
    result = value.to_dict()
    for generated in ("settlement_id", "settled_at", "idempotency_key"):
        result.pop(generated, None)
    return result


def _settlement_content_sha256(value: OfficialPickSettlement) -> str:
    return payload_sha256(_settlement_content(value))


def _correction_content(value: OfficialPickSettlementCorrection) -> dict[str, Any]:
    result = value.to_dict()
    for generated in ("correction_id", "corrected_at", "idempotency_key"):
        result.pop(generated, None)
    return result


def _correction_content_sha256(value: OfficialPickSettlementCorrection) -> str:
    return payload_sha256(_correction_content(value))


def _transaction_manifest(
    pick: OfficialPick,
    *,
    transaction_id: str,
    parent_run_id: str,
    occurred_at: datetime,
    pipeline_version: str,
) -> RunManifest:
    provenance = dict(pick.provenance)
    return RunManifest(
        prediction_run_id=transaction_id,
        run_mode=RunMode.RESEARCH.value,
        run_reason=RunReason.MANUAL.value,
        parent_run_id=parent_run_id,
        started_at_utc=occurred_at,
        completed_at_utc=occurred_at,
        operating_date=occurred_at.astimezone(_TORONTO).date().isoformat(),
        operating_timezone="America/Toronto",
        git_commit_sha=_nullable(provenance.get("git_commit_sha")),
        git_dirty=None,
        working_tree_hash=None,
        config_hash=_nullable(provenance.get("config_hash")),
        model_id=pick.model_name,
        model_version=pick.model_version,
        model_bundle_hash=_nullable(provenance.get("model_bundle_hash")),
        calibration_id=None,
        calibration_version=None,
        calibration_hash=None,
        strategy_version=None,
        pipeline_version=pipeline_version,
        python_version=platform.python_version(),
        dependency_fingerprint=None,
        input_manifest_hash=_nullable(provenance.get("input_manifest_hash")),
        reproducibility_level=ReproducibilityLevel.PARTIAL.value,
    )


def _settlement_event(
    pick: OfficialPick,
    settlement: OfficialPickSettlement,
    *,
    transaction_id: str,
    content_sha256: str,
) -> EventEnvelope:
    payload = {
        "payload_schema_version": OFFICIAL_PICK_SETTLEMENT_PAYLOAD_SCHEMA_VERSION,
        "settlement_policy_version": OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
        "settlement_authority": "PAPER_RESEARCH_ONLY",
        "settlement_content_sha256": content_sha256,
        "official_pick_settlement": settlement.to_dict(),
    }
    return EventEnvelope.create(
        event_type=EventType.OFFICIAL_PICK_SETTLED,
        payload=payload,
        payload_schema_version=OFFICIAL_PICK_SETTLEMENT_PAYLOAD_SCHEMA_VERSION,
        prediction_run_id=transaction_id,
        event_sequence=1,
        occurred_at_utc=settlement.settled_at,
        recorded_at_utc=settlement.settled_at,
        operating_date=settlement.settled_at.astimezone(_TORONTO).date().isoformat(),
        operating_timezone="America/Toronto",
        actor_type="OPERATOR",
        actor_id=str(settlement.provenance["settlement_actor"]),
        correlation_id=settlement.settlement_run_id,
        idempotency_key=settlement.idempotency_key,
        source_refs={
            "pick_id": pick.pick_id,
            "source_record_id": settlement.source_record_id,
        },
        source_hashes={"settlement_content_sha256": content_sha256},
        model_id=pick.model_name,
        model_version=pick.model_version,
    )


def _correction_event(
    pick: OfficialPick,
    correction: OfficialPickSettlementCorrection,
    *,
    original_event_id: str,
    transaction_id: str,
    content_sha256: str,
) -> EventEnvelope:
    payload = {
        "payload_schema_version": (
            OFFICIAL_PICK_SETTLEMENT_CORRECTION_PAYLOAD_SCHEMA_VERSION
        ),
        "settlement_policy_version": OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION,
        "settlement_authority": "PAPER_RESEARCH_ONLY",
        "correction_content_sha256": content_sha256,
        "official_pick_settlement_correction": correction.to_dict(),
    }
    return EventEnvelope.create(
        event_type=EventType.OFFICIAL_PICK_SETTLEMENT_CORRECTION_RECORDED,
        payload=payload,
        payload_schema_version=(
            OFFICIAL_PICK_SETTLEMENT_CORRECTION_PAYLOAD_SCHEMA_VERSION
        ),
        prediction_run_id=transaction_id,
        event_sequence=1,
        occurred_at_utc=correction.corrected_at,
        recorded_at_utc=correction.corrected_at,
        operating_date=correction.corrected_at.astimezone(_TORONTO).date().isoformat(),
        operating_timezone="America/Toronto",
        actor_type="OPERATOR",
        actor_id=str(correction.provenance["correction_actor"]),
        correlation_id=correction.correction_run_id,
        idempotency_key=correction.idempotency_key,
        source_refs={
            "pick_id": pick.pick_id,
            "original_settlement_id": correction.original_settlement_id,
            "source_record_id": correction.source_record_id,
        },
        source_hashes={"correction_content_sha256": content_sha256},
        model_id=pick.model_name,
        model_version=pick.model_version,
        corrects_event_id=original_event_id,
    )


def _settlement_records(root: Path) -> tuple[_SettlementRecord, ...]:
    picks = _committed_pick_index(root)
    records: list[_SettlementRecord] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for segment in completed_segment_directories(root):
        for event in _verified_segment_events(segment, lifecycle_root=root):
            if event.event_type != EventType.OFFICIAL_PICK_SETTLED.value:
                continue
            record = _event_settlement(event, segment, committed_picks=picks)
            settlement = record.settlement
            if settlement.settlement_id in seen_ids:
                raise OfficialPickSettlementLedgerIntegrityError(
                    f"duplicate settlement_id in ledger: {settlement.settlement_id}"
                )
            if settlement.idempotency_key in seen_keys:
                raise OfficialPickSettlementLedgerIntegrityError(
                    "duplicate official-pick settlement idempotency key in ledger: "
                    f"{settlement.idempotency_key}"
                )
            seen_ids.add(settlement.settlement_id)
            seen_keys.add(settlement.idempotency_key)
            records.append(record)
    result = tuple(
        sorted(
            records,
            key=lambda item: (
                item.settlement.settled_at,
                item.settlement.settlement_id,
            ),
        )
    )
    _validate_committed_transitions(result)
    return result


def _correction_records(
    root: Path,
    *,
    settlements: tuple[_SettlementRecord, ...] | None = None,
) -> tuple[_CorrectionRecord, ...]:
    settlement_records = settlements or _settlement_records(root)
    settlement_index = {
        item.settlement.settlement_id: item for item in settlement_records
    }
    records: list[_CorrectionRecord] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    corrected_settlements: set[str] = set()
    for segment in completed_segment_directories(root):
        for event in _verified_segment_events(segment, lifecycle_root=root):
            if (
                event.event_type
                != EventType.OFFICIAL_PICK_SETTLEMENT_CORRECTION_RECORDED.value
            ):
                continue
            record = _event_correction(
                event, segment, settlements=settlement_index
            )
            correction = record.correction
            if correction.correction_id in seen_ids:
                raise OfficialPickSettlementLedgerIntegrityError(
                    f"duplicate settlement correction_id: {correction.correction_id}"
                )
            if correction.idempotency_key in seen_keys:
                raise OfficialPickSettlementLedgerIntegrityError(
                    "duplicate settlement correction idempotency key: "
                    f"{correction.idempotency_key}"
                )
            if correction.original_settlement_id in corrected_settlements:
                raise OfficialPickSettlementLedgerIntegrityError(
                    "multiple corrections reference one original settlement: "
                    f"{correction.original_settlement_id}"
                )
            seen_ids.add(correction.correction_id)
            seen_keys.add(correction.idempotency_key)
            corrected_settlements.add(correction.original_settlement_id)
            records.append(record)
    return tuple(
        sorted(
            records,
            key=lambda item: (
                item.correction.corrected_at,
                item.correction.correction_id,
            ),
        )
    )


def _event_settlement(
    event: EventEnvelope,
    segment: Path,
    *,
    committed_picks: Mapping[str, OfficialPick],
) -> _SettlementRecord:
    payload = _event_payload(
        event,
        expected_schema_version=OFFICIAL_PICK_SETTLEMENT_PAYLOAD_SCHEMA_VERSION,
        label="official-pick settlement",
    )
    try:
        settlement = OfficialPickSettlement.from_dict(
            payload["official_pick_settlement"]
        )
    except (KeyError, OfficialPickSettlementValidationError) as exc:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"malformed official-pick settlement in event {event.event_id}"
        ) from exc
    pick = committed_picks.get(settlement.pick_id)
    if pick is None:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"settlement references unknown committed pick_id: {settlement.pick_id}"
        )
    if settlement.settled_at < pick.published_at:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"settlement precedes pick publication: {settlement.settlement_id}"
        )
    expected_content_hash = _settlement_content_sha256(settlement)
    if settlement.idempotency_key != event.idempotency_key:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"settlement/event idempotency mismatch: {event.event_id}"
        )
    if payload.get("settlement_content_sha256") != expected_content_hash:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"settlement content hash mismatch: {event.event_id}"
        )
    if event.source_refs.get("pick_id") != settlement.pick_id:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"settlement source pick_id mismatch: {event.event_id}"
        )
    if event.corrects_event_id is not None:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"settlement event must not correct another event: {event.event_id}"
        )
    return _SettlementRecord(
        settlement=settlement,
        event=event,
        segment_directory=segment,
        content_sha256=expected_content_hash,
    )


def _event_correction(
    event: EventEnvelope,
    segment: Path,
    *,
    settlements: Mapping[str, _SettlementRecord],
) -> _CorrectionRecord:
    payload = _event_payload(
        event,
        expected_schema_version=(
            OFFICIAL_PICK_SETTLEMENT_CORRECTION_PAYLOAD_SCHEMA_VERSION
        ),
        label="official-pick settlement correction",
    )
    try:
        correction = OfficialPickSettlementCorrection.from_dict(
            payload["official_pick_settlement_correction"]
        )
    except (KeyError, OfficialPickSettlementValidationError) as exc:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"malformed settlement correction in event {event.event_id}"
        ) from exc
    original = settlements.get(correction.original_settlement_id)
    if original is None:
        raise OfficialPickSettlementLedgerIntegrityError(
            "correction references unknown original settlement: "
            f"{correction.original_settlement_id}"
        )
    if original.settlement.pick_id != correction.pick_id:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"correction pick_id mismatch: {event.event_id}"
        )
    if (
        original.settlement.settlement_status
        != OfficialPickSettlementStatus.FINAL.value
    ):
        raise OfficialPickSettlementLedgerIntegrityError(
            f"correction references non-final settlement: {event.event_id}"
        )
    if correction.corrected_at < original.settlement.settled_at:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"correction precedes original settlement: {event.event_id}"
        )
    expected_key = official_pick_settlement_correction_idempotency_key(
        correction.original_settlement_id
    )
    expected_content_hash = _correction_content_sha256(correction)
    if correction.idempotency_key != expected_key or event.idempotency_key != expected_key:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"correction/event idempotency mismatch: {event.event_id}"
        )
    if payload.get("correction_content_sha256") != expected_content_hash:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"correction content hash mismatch: {event.event_id}"
        )
    if event.corrects_event_id != original.event.event_id:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"correction event reference mismatch: {event.event_id}"
        )
    return _CorrectionRecord(
        correction=correction,
        event=event,
        segment_directory=segment,
        content_sha256=expected_content_hash,
    )


def _event_payload(
    event: EventEnvelope,
    *,
    expected_schema_version: int,
    label: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"{label} payload is invalid: {event.event_id}"
        ) from exc
    if (
        event.payload_schema_version != expected_schema_version
        or payload.get("payload_schema_version") != expected_schema_version
        or payload.get("settlement_policy_version")
        != OFFICIAL_PICK_SETTLEMENT_POLICY_VERSION
        or payload.get("settlement_authority") != "PAPER_RESEARCH_ONLY"
    ):
        raise OfficialPickSettlementLedgerIntegrityError(
            f"unsupported {label} payload: {event.event_id}"
        )
    return payload


def _verified_segment_events(
    segment_directory: Path,
    *,
    lifecycle_root: Path,
) -> tuple[EventEnvelope, ...]:
    verification = verify_segment(
        segment_directory, lifecycle_root=lifecycle_root
    )
    if not verification.ok:
        raise OfficialPickSettlementLedgerIntegrityError(
            "committed lifecycle segment failed verification: "
            + "; ".join(verification.violations)
        )
    return read_segment_events(segment_directory)


def _validate_committed_transitions(
    records: tuple[_SettlementRecord, ...],
) -> None:
    by_pick: dict[str, list[OfficialPickSettlement]] = {}
    for item in records:
        by_pick.setdefault(item.settlement.pick_id, []).append(item.settlement)
    for pick_id, settlements in by_pick.items():
        unresolved = [
            item
            for item in settlements
            if item.settlement_status
            == OfficialPickSettlementStatus.UNRESOLVED.value
        ]
        finals = [
            item
            for item in settlements
            if item.settlement_status == OfficialPickSettlementStatus.FINAL.value
        ]
        if len(unresolved) > 1 or len(finals) > 1:
            raise OfficialPickSettlementLedgerIntegrityError(
                f"illegal duplicate settlement transition for pick_id: {pick_id}"
            )
        if unresolved and finals and finals[0].settled_at < unresolved[0].settled_at:
            raise OfficialPickSettlementLedgerIntegrityError(
                f"final settlement precedes unresolved event for pick_id: {pick_id}"
            )
        if unresolved:
            expected_unresolved_key = official_pick_settlement_idempotency_key(
                pick_id, OfficialPickSettlementTransitionSlot.INITIAL
            )
            if unresolved[0].idempotency_key != expected_unresolved_key:
                raise OfficialPickSettlementLedgerIntegrityError(
                    f"invalid unresolved transition identity for pick_id: {pick_id}"
                )
        if finals:
            final_slot = (
                OfficialPickSettlementTransitionSlot.FINALIZATION
                if unresolved
                else OfficialPickSettlementTransitionSlot.INITIAL
            )
            expected_final_key = official_pick_settlement_idempotency_key(
                pick_id, final_slot
            )
            if finals[0].idempotency_key != expected_final_key:
                raise OfficialPickSettlementLedgerIntegrityError(
                    f"invalid final transition identity for pick_id: {pick_id}"
                )


def _requested_transition_slot(
    records: tuple[_SettlementRecord, ...],
    *,
    pick_id: str,
    status: str,
) -> OfficialPickSettlementTransitionSlot:
    has_unresolved = any(
        item.settlement.pick_id == pick_id
        and item.settlement.settlement_status
        == OfficialPickSettlementStatus.UNRESOLVED.value
        for item in records
    )
    if (
        status == OfficialPickSettlementStatus.FINAL.value
        and has_unresolved
    ):
        return OfficialPickSettlementTransitionSlot.FINALIZATION
    return OfficialPickSettlementTransitionSlot.INITIAL


def _validate_new_transition(
    records: tuple[_SettlementRecord, ...],
    *,
    pick_id: str,
    status: str,
) -> None:
    current = [
        item.settlement for item in records if item.settlement.pick_id == pick_id
    ]
    has_unresolved = any(
        item.settlement_status
        == OfficialPickSettlementStatus.UNRESOLVED.value
        for item in current
    )
    has_final = any(
        item.settlement_status == OfficialPickSettlementStatus.FINAL.value
        for item in current
    )
    if has_final:
        raise OfficialPickSettlementTransitionError(
            "final settlement cannot be silently replaced; use an explicit correction"
        )
    if (
        status == OfficialPickSettlementStatus.UNRESOLVED.value
        and has_unresolved
    ):
        raise OfficialPickSettlementTransitionError(
            "unresolved settlement already exists for pick_id"
        )


def _one_settlement_by_key(
    records: tuple[_SettlementRecord, ...], idempotency_key: str
) -> _SettlementRecord | None:
    matching = [
        item
        for item in records
        if item.settlement.idempotency_key == idempotency_key
    ]
    if len(matching) > 1:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"duplicate settlement idempotency key: {idempotency_key}"
        )
    return matching[0] if matching else None


def _one_correction_by_key(
    records: tuple[_CorrectionRecord, ...], idempotency_key: str
) -> _CorrectionRecord | None:
    matching = [
        item
        for item in records
        if item.correction.idempotency_key == idempotency_key
    ]
    if len(matching) > 1:
        raise OfficialPickSettlementLedgerIntegrityError(
            f"duplicate correction idempotency key: {idempotency_key}"
        )
    return matching[0] if matching else None


def _settlement_replay_result(
    existing: _SettlementRecord,
    *,
    status: str,
    outcome: str,
    result_source: str,
    source_record_id: str,
    settlement_run_id: str,
    result_evidence: Mapping[str, Any],
    final_score: str | Mapping[str, Any] | None,
    provenance: Mapping[str, Any] | None,
    settlement_actor: str,
) -> OfficialPickSettlementResult:
    if existing.settlement.settlement_status != status:
        if (
            existing.settlement.settlement_status
            == OfficialPickSettlementStatus.FINAL.value
        ):
            raise OfficialPickSettlementTransitionError(
                "final settlement cannot be silently replaced; use an explicit correction"
            )
        raise OfficialPickSettlementConflictError(
            "IDEMPOTENCY_CONFLICT: initial settlement transition differs"
        )
    requested = OfficialPickSettlement(
        settlement_id=existing.settlement.settlement_id,
        pick_id=existing.settlement.pick_id,
        settlement_status=status,
        outcome=outcome,
        final_score=final_score,
        result_evidence=result_evidence,
        settled_at=existing.settlement.settled_at,
        result_source=result_source,
        source_record_id=source_record_id,
        settlement_run_id=settlement_run_id,
        idempotency_key=existing.settlement.idempotency_key,
        provenance=_provenance(
            provenance,
            actor_key="settlement_actor",
            actor=settlement_actor,
            service_key="settlement_service",
        ),
    )
    if _settlement_content_sha256(requested) != existing.content_sha256:
        raise OfficialPickSettlementConflictError(
            "IDEMPOTENCY_CONFLICT: official-pick settlement content differs"
        )
    return OfficialPickSettlementResult(
        settlement=existing.settlement,
        publication_status="ALREADY_PUBLISHED",
        ledger_segment_directory=existing.segment_directory,
        event_id=existing.event.event_id,
        idempotency_key=existing.settlement.idempotency_key,
    )


def _correction_replay_result(
    existing: _CorrectionRecord,
    *,
    original: _SettlementRecord,
    correction_reason: str,
    corrected_outcome: str,
    corrected_final_score: str | Mapping[str, Any] | None,
    corrected_result_evidence: Mapping[str, Any],
    result_source: str,
    source_record_id: str,
    correction_run_id: str,
    provenance: Mapping[str, Any] | None,
    correction_actor: str,
) -> OfficialPickSettlementCorrectionResult:
    requested = _build_correction(
        original=original.settlement,
        correction_id=existing.correction.correction_id,
        correction_reason=correction_reason,
        corrected_outcome=corrected_outcome,
        corrected_final_score=corrected_final_score,
        corrected_result_evidence=corrected_result_evidence,
        corrected_at=existing.correction.corrected_at,
        result_source=result_source,
        source_record_id=source_record_id,
        correction_run_id=correction_run_id,
        idempotency_key=existing.correction.idempotency_key,
        provenance=provenance,
        correction_actor=correction_actor,
    )
    if _correction_content_sha256(requested) != existing.content_sha256:
        raise OfficialPickSettlementConflictError(
            "IDEMPOTENCY_CONFLICT: settlement correction content differs"
        )
    return OfficialPickSettlementCorrectionResult(
        correction=existing.correction,
        publication_status="ALREADY_PUBLISHED",
        ledger_segment_directory=existing.segment_directory,
        event_id=existing.event.event_id,
        idempotency_key=existing.correction.idempotency_key,
    )


def _settlement_record_in_segment(
    segment_directory: Path,
    idempotency_key: str,
    *,
    lifecycle_root: Path,
    committed_picks: Mapping[str, OfficialPick],
) -> _SettlementRecord:
    matching = [
        _event_settlement(event, segment_directory, committed_picks=committed_picks)
        for event in _verified_segment_events(
            segment_directory, lifecycle_root=lifecycle_root
        )
        if event.event_type == EventType.OFFICIAL_PICK_SETTLED.value
        and event.idempotency_key == idempotency_key
    ]
    if len(matching) != 1:
        raise OfficialPickSettlementLedgerIntegrityError(
            "committed segment does not contain exactly one requested settlement"
        )
    return matching[0]


def _correction_record_in_segment(
    segment_directory: Path,
    idempotency_key: str,
    *,
    lifecycle_root: Path,
    settlements: Mapping[str, _SettlementRecord],
) -> _CorrectionRecord:
    matching = [
        _event_correction(event, segment_directory, settlements=settlements)
        for event in _verified_segment_events(
            segment_directory, lifecycle_root=lifecycle_root
        )
        if event.event_type
        == EventType.OFFICIAL_PICK_SETTLEMENT_CORRECTION_RECORDED.value
        and event.idempotency_key == idempotency_key
    ]
    if len(matching) != 1:
        raise OfficialPickSettlementLedgerIntegrityError(
            "committed segment does not contain exactly one requested correction"
        )
    return matching[0]


def _nullable(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "OfficialPickSettlementConflictError",
    "OfficialPickSettlementCorrectionResult",
    "OfficialPickSettlementError",
    "OfficialPickSettlementLedgerIntegrityError",
    "OfficialPickSettlementReferenceError",
    "OfficialPickSettlementResult",
    "OfficialPickSettlementState",
    "OfficialPickSettlementTransitionError",
    "correct_official_pick_settlement",
    "generate_settlement_correction_id",
    "generate_settlement_id",
    "official_pick_settlement_correction_idempotency_key",
    "official_pick_settlement_idempotency_key",
    "read_official_pick_settlement_corrections",
    "read_official_pick_settlement_state",
    "read_official_pick_settlement_states",
    "read_official_pick_settlements",
    "settle_official_pick",
]
