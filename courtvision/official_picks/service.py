"""Explicit transactional promotion into the canonical official-pick ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import platform
from typing import Any, Callable, Mapping
from uuid import uuid4

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
    LifecycleIntegrityError,
    LifecycleWriter,
    completed_segment_directories,
    read_segment_events,
    verify_segment,
)
from courtvision.official_picks.contracts import (
    OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION,
    OFFICIAL_PICK_PROMOTION_POLICY_VERSION,
    OfficialPick,
    OfficialPickDesignation,
    OfficialPickPromotionRequest,
    OfficialPickSourceType,
    OfficialPickStatus,
    OfficialPickValidationError,
)


PickIdFactory = Callable[[], str]
TransactionIdFactory = Callable[[], str]


class OfficialPickPromotionError(RuntimeError):
    """Base error for official-pick publication."""


class OfficialPickConflictError(OfficialPickPromotionError):
    """The same promotion identity was submitted with conflicting content."""


class OfficialPickLedgerIntegrityError(OfficialPickPromotionError):
    """Committed official-pick data failed strict reconstruction."""


class LiveOfficialPickBlockedError(OfficialPickPromotionError):
    """Live official picks are not authorized in this foundation phase."""


@dataclass(frozen=True, slots=True)
class OfficialPickPromotionResult:
    pick: OfficialPick
    publication_status: str
    ledger_segment_directory: Path
    event_id: str
    idempotency_key: str


def generate_pick_id() -> str:
    """Assign a globally unique ID once at explicit promotion time."""

    return f"pick_{uuid4().hex}"


def generate_promotion_transaction_id() -> str:
    return f"official-pick-promotion-{uuid4().hex}"


def official_pick_idempotency_key(
    request: OfficialPickPromotionRequest | Mapping[str, Any],
    *,
    designation: str | OfficialPickDesignation = OfficialPickDesignation.PAPER,
) -> str:
    candidate = _request(request)
    source_type, source_id = _source_reference(candidate)
    designation_value = _designation(designation)
    return deterministic_id(
        "opidem",
        "courtvision.official_pick_promotion.v1",
        {
            "promotion_policy_version": OFFICIAL_PICK_PROMOTION_POLICY_VERSION,
            "sport": str(candidate.sport).strip().lower(),
            "league": str(candidate.league).strip().upper(),
            "source_type": source_type,
            "source_id": source_id,
            "designation": designation_value,
        },
    )


def promote_candidate_to_official_pick(
    request: OfficialPickPromotionRequest | Mapping[str, Any],
    *,
    lifecycle_root: str | Path,
    designation: str | OfficialPickDesignation = OfficialPickDesignation.PAPER,
    promotion_actor: str = "courtvision.operator",
    clock: Clock | None = None,
    pick_id_factory: PickIdFactory = generate_pick_id,
    transaction_id_factory: TransactionIdFactory = generate_promotion_transaction_id,
    failure_hook: FailureHook | None = None,
) -> OfficialPickPromotionResult:
    """Explicitly promote one candidate or observation reference.

    Nothing calls this service automatically. In particular, model boards and
    sportsbook observations remain non-picks until an operator or audited
    workflow invokes this function.
    """

    candidate = _request(request)
    designation_value = _designation(designation)
    if designation_value == OfficialPickDesignation.LIVE.value:
        raise LiveOfficialPickBlockedError(
            "live official-pick designation is blocked; no live promotion gate is active"
        )
    actor = str(promotion_actor).strip()
    if not actor:
        raise OfficialPickValidationError("promotion_actor is required")
    active_clock = clock or SystemClock()
    root = Path(lifecycle_root)
    idempotency_key = official_pick_idempotency_key(
        candidate, designation=designation_value
    )

    existing = _find_by_idempotency_key(root, idempotency_key)
    if existing is not None:
        return _replay_result(
            existing,
            candidate=candidate,
            designation=designation_value,
            promotion_actor=actor,
        )

    published_at = active_clock.now()
    pick = _build_pick(
        candidate,
        pick_id=pick_id_factory(),
        published_at=published_at,
        designation=designation_value,
        idempotency_key=idempotency_key,
        promotion_actor=actor,
    )
    pick_id_owner = read_official_pick(root, pick.pick_id)
    if pick_id_owner is not None:
        raise OfficialPickConflictError(
            f"pick_id is already committed to another promotion: {pick.pick_id}"
        )
    content_hash = _promotion_content_sha256(pick)
    transaction_id = str(transaction_id_factory()).strip()
    if not transaction_id:
        raise OfficialPickValidationError("promotion transaction ID is required")
    manifest = _promotion_manifest(
        pick,
        transaction_id=transaction_id,
        published_at=published_at,
    )
    event = _promotion_event(
        pick,
        transaction_id=transaction_id,
        published_at=published_at,
        promotion_content_sha256=content_hash,
    )
    writer = LifecycleWriter(root, clock=active_clock)
    try:
        commit = writer.commit_segment(
            manifest,
            (event,),
            failure_hook=failure_hook,
            command="courtvision official-pick promote",
        )
    except IdempotencyConflictError as exc:
        raced = _find_by_idempotency_key(root, idempotency_key)
        if raced is None:
            raise OfficialPickConflictError(str(exc)) from exc
        return _replay_result(
            raced,
            candidate=candidate,
            designation=designation_value,
            promotion_actor=actor,
        )
    except LifecycleIntegrityError:
        raise

    persisted_event = _official_pick_event_in_segment(
        commit.segment_directory,
        idempotency_key,
        lifecycle_root=root,
    )
    persisted = _event_pick(persisted_event)
    event = persisted_event
    return OfficialPickPromotionResult(
        pick=persisted,
        publication_status=(
            "PUBLISHED" if commit.status == "COMMITTED" else "ALREADY_PUBLISHED"
        ),
        ledger_segment_directory=commit.segment_directory,
        event_id=event.event_id,
        idempotency_key=idempotency_key,
    )


def promote_observation_to_official_pick(
    request: OfficialPickPromotionRequest | Mapping[str, Any],
    **kwargs: Any,
) -> OfficialPickPromotionResult:
    """Named explicit call site for audited observation promotion."""

    candidate = _request(request)
    if not candidate.source_observation_id or candidate.source_candidate_id:
        raise OfficialPickValidationError(
            "observation promotion requires only source_observation_id"
        )
    return promote_candidate_to_official_pick(candidate, **kwargs)


def read_official_picks(lifecycle_root: str | Path) -> tuple[OfficialPick, ...]:
    root = Path(lifecycle_root)
    records: list[OfficialPick] = []
    seen_ids: dict[str, OfficialPick] = {}
    seen_keys: dict[str, OfficialPick] = {}
    for segment in completed_segment_directories(root):
        for event in _verified_segment_events(segment, lifecycle_root=root):
            if event.event_type != EventType.OFFICIAL_PICK_PUBLISHED.value:
                continue
            pick = _event_pick(event)
            if pick.pick_id in seen_ids:
                raise OfficialPickLedgerIntegrityError(
                    f"duplicate official pick_id in ledger: {pick.pick_id}"
                )
            if pick.idempotency_key in seen_keys:
                raise OfficialPickLedgerIntegrityError(
                    "duplicate official-pick idempotency key in ledger: "
                    f"{pick.idempotency_key}"
                )
            seen_ids[pick.pick_id] = pick
            seen_keys[pick.idempotency_key] = pick
            records.append(pick)
    return tuple(
        sorted(records, key=lambda item: (item.published_at, item.pick_id))
    )


def read_official_pick(
    lifecycle_root: str | Path, pick_id: str
) -> OfficialPick | None:
    target = str(pick_id).strip()
    return next(
        (item for item in read_official_picks(lifecycle_root) if item.pick_id == target),
        None,
    )


def _request(
    value: OfficialPickPromotionRequest | Mapping[str, Any],
) -> OfficialPickPromotionRequest:
    if isinstance(value, OfficialPickPromotionRequest):
        return value
    if isinstance(value, Mapping):
        return OfficialPickPromotionRequest.from_mapping(value)
    raise OfficialPickValidationError(
        "promotion request must be OfficialPickPromotionRequest or a mapping"
    )


def _designation(value: str | OfficialPickDesignation) -> str:
    raw = value.value if isinstance(value, OfficialPickDesignation) else str(value)
    normalized = raw.strip().upper()
    if normalized not in {item.value for item in OfficialPickDesignation}:
        raise OfficialPickValidationError("unsupported official-pick designation")
    return normalized


def _source_reference(
    request: OfficialPickPromotionRequest,
) -> tuple[str, str]:
    candidate_id = (
        str(request.source_candidate_id).strip()
        if request.source_candidate_id is not None
        else ""
    )
    observation_id = (
        str(request.source_observation_id).strip()
        if request.source_observation_id is not None
        else ""
    )
    if bool(candidate_id) == bool(observation_id):
        raise OfficialPickValidationError(
            "exactly one source_candidate_id or source_observation_id is required"
        )
    if candidate_id:
        if candidate_id.casefold() in {"unknown", "unresolved", "none", "null", "nan"}:
            raise OfficialPickValidationError("source_candidate_id is unresolved")
        return OfficialPickSourceType.CANDIDATE.value, candidate_id
    if observation_id.casefold() in {"unknown", "unresolved", "none", "null", "nan"}:
        raise OfficialPickValidationError("source_observation_id is unresolved")
    return OfficialPickSourceType.OBSERVATION.value, observation_id


def _build_pick(
    request: OfficialPickPromotionRequest,
    *,
    pick_id: str,
    published_at: datetime,
    designation: str,
    idempotency_key: str,
    promotion_actor: str,
) -> OfficialPick:
    source_type, source_id = _source_reference(request)
    if not isinstance(request.provenance, Mapping):
        raise OfficialPickValidationError("provenance must be a mapping")
    provenance = {
        **dict(request.provenance),
        "source_type": source_type,
        "source_id": source_id,
        "promotion_service": "courtvision.official_picks",
        "promotion_actor": promotion_actor,
        "promotion_policy_version": OFFICIAL_PICK_PROMOTION_POLICY_VERSION,
    }
    return OfficialPick(
        pick_id=pick_id,
        sport=request.sport,
        league=request.league,
        event_id=request.event_id,
        event_start_time=request.event_start_time,
        prediction_date=request.prediction_date,
        market_key=request.market_key,
        selection=request.selection,
        line=request.line,
        odds=request.odds,
        sportsbook=request.sportsbook,
        player_id=request.player_id,
        player_name=request.player_name,
        team_id=request.team_id,
        model_name=request.model_name,
        model_version=request.model_version,
        run_id=request.run_id,
        published_at=published_at,
        source_candidate_id=request.source_candidate_id,
        source_observation_id=request.source_observation_id,
        status=OfficialPickStatus.PUBLISHED.value,
        designation=designation,
        idempotency_key=idempotency_key,
        provenance=provenance,
    )


def _promotion_content(pick: OfficialPick) -> dict[str, Any]:
    value = pick.to_dict()
    for generated in ("pick_id", "published_at", "idempotency_key"):
        value.pop(generated, None)
    return value


def _promotion_content_sha256(pick: OfficialPick) -> str:
    return payload_sha256(_promotion_content(pick))


def _promotion_manifest(
    pick: OfficialPick,
    *,
    transaction_id: str,
    published_at: datetime,
) -> RunManifest:
    provenance = dict(pick.provenance)
    return RunManifest(
        prediction_run_id=transaction_id,
        run_mode=RunMode.RESEARCH.value,
        run_reason=RunReason.MANUAL.value,
        parent_run_id=pick.run_id,
        started_at_utc=published_at,
        completed_at_utc=published_at,
        operating_date=pick.prediction_date,
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
        pipeline_version="official-pick-promotion-v1",
        python_version=platform.python_version(),
        dependency_fingerprint=None,
        input_manifest_hash=_nullable(provenance.get("input_manifest_hash")),
        reproducibility_level=ReproducibilityLevel.PARTIAL.value,
    )


def _promotion_event(
    pick: OfficialPick,
    *,
    transaction_id: str,
    published_at: datetime,
    promotion_content_sha256: str,
) -> EventEnvelope:
    source_key = (
        "source_candidate_id"
        if pick.source_candidate_id is not None
        else "source_observation_id"
    )
    source_id = pick.source_candidate_id or pick.source_observation_id
    payload = {
        "payload_schema_version": OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION,
        "promotion_policy_version": OFFICIAL_PICK_PROMOTION_POLICY_VERSION,
        "publication_authority": "PAPER_RESEARCH_ONLY",
        "promotion_content_sha256": promotion_content_sha256,
        "official_pick": pick.to_dict(),
    }
    return EventEnvelope.create(
        event_type=EventType.OFFICIAL_PICK_PUBLISHED,
        payload=payload,
        payload_schema_version=OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION,
        prediction_run_id=transaction_id,
        event_sequence=1,
        occurred_at_utc=published_at,
        recorded_at_utc=published_at,
        operating_date=pick.prediction_date,
        operating_timezone="America/Toronto",
        actor_type="OPERATOR",
        actor_id=str(pick.provenance["promotion_actor"]),
        correlation_id=pick.run_id,
        idempotency_key=pick.idempotency_key,
        source_refs={source_key: source_id},
        source_hashes={"promotion_content_sha256": promotion_content_sha256},
        model_id=pick.model_name,
        model_version=pick.model_version,
    )


@dataclass(frozen=True, slots=True)
class _ExistingPromotion:
    pick: OfficialPick
    event: EventEnvelope
    segment_directory: Path
    promotion_content_sha256: str


def _find_by_idempotency_key(
    lifecycle_root: Path, idempotency_key: str
) -> _ExistingPromotion | None:
    found: _ExistingPromotion | None = None
    for segment in completed_segment_directories(lifecycle_root):
        for event in _verified_segment_events(
            segment, lifecycle_root=lifecycle_root
        ):
            if (
                event.event_type != EventType.OFFICIAL_PICK_PUBLISHED.value
                or event.idempotency_key != idempotency_key
            ):
                continue
            payload = _event_payload(event)
            current = _ExistingPromotion(
                pick=_event_pick(event),
                event=event,
                segment_directory=segment,
                promotion_content_sha256=str(
                    payload.get("promotion_content_sha256") or ""
                ),
            )
            if found is not None:
                raise OfficialPickLedgerIntegrityError(
                    f"duplicate official-pick idempotency key: {idempotency_key}"
                )
            found = current
    return found


def _replay_result(
    existing: _ExistingPromotion,
    *,
    candidate: OfficialPickPromotionRequest,
    designation: str,
    promotion_actor: str,
) -> OfficialPickPromotionResult:
    requested = _build_pick(
        candidate,
        pick_id=existing.pick.pick_id,
        published_at=existing.pick.published_at,
        designation=designation,
        idempotency_key=existing.pick.idempotency_key,
        promotion_actor=promotion_actor,
    )
    requested_hash = _promotion_content_sha256(requested)
    if (
        not existing.promotion_content_sha256
        or existing.promotion_content_sha256 != requested_hash
    ):
        raise OfficialPickConflictError(
            "IDEMPOTENCY_CONFLICT: official-pick promotion content differs"
        )
    return OfficialPickPromotionResult(
        pick=existing.pick,
        publication_status="ALREADY_PUBLISHED",
        ledger_segment_directory=existing.segment_directory,
        event_id=existing.event.event_id,
        idempotency_key=existing.pick.idempotency_key,
    )


def _official_pick_event_in_segment(
    segment_directory: Path,
    idempotency_key: str,
    *,
    lifecycle_root: Path,
) -> EventEnvelope:
    matching = [
        event
        for event in _verified_segment_events(
            segment_directory,
            lifecycle_root=lifecycle_root,
        )
        if event.event_type == EventType.OFFICIAL_PICK_PUBLISHED.value
        and event.idempotency_key == idempotency_key
    ]
    if len(matching) != 1:
        raise OfficialPickLedgerIntegrityError(
            "committed promotion segment does not contain exactly one official pick"
        )
    return matching[0]


def _verified_segment_events(
    segment_directory: Path,
    *,
    lifecycle_root: Path,
) -> tuple[EventEnvelope, ...]:
    verification = verify_segment(
        segment_directory, lifecycle_root=lifecycle_root
    )
    if not verification.ok:
        raise OfficialPickLedgerIntegrityError(
            "committed lifecycle segment failed verification: "
            + "; ".join(verification.violations)
        )
    return read_segment_events(segment_directory)


def _event_payload(event: EventEnvelope) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick event payload is invalid: {event.event_id}"
        ) from exc
    if (
        payload.get("payload_schema_version")
        != OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION
    ):
        raise OfficialPickLedgerIntegrityError(
            f"unsupported official-pick payload schema: {event.event_id}"
        )
    return payload


def _event_pick(event: EventEnvelope) -> OfficialPick:
    payload = _event_payload(event)
    try:
        pick = OfficialPick.from_dict(payload["official_pick"])
    except (KeyError, OfficialPickValidationError) as exc:
        raise OfficialPickLedgerIntegrityError(
            f"malformed official-pick row in event {event.event_id}"
        ) from exc
    if pick.idempotency_key != event.idempotency_key:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick/event idempotency mismatch: {event.event_id}"
        )
    expected_content_hash = _promotion_content_sha256(pick)
    if payload.get("promotion_content_sha256") != expected_content_hash:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick promotion content hash mismatch: {event.event_id}"
        )
    return pick


def _nullable(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "LiveOfficialPickBlockedError",
    "OfficialPickConflictError",
    "OfficialPickLedgerIntegrityError",
    "OfficialPickPromotionError",
    "OfficialPickPromotionResult",
    "generate_pick_id",
    "official_pick_idempotency_key",
    "promote_candidate_to_official_pick",
    "promote_observation_to_official_pick",
    "read_official_pick",
    "read_official_picks",
]
