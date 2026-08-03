"""Explicit transactional promotion into the canonical official-pick ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import platform
from typing import Any, Callable, Mapping, Sequence, cast
from uuid import uuid4

from courtvision.lifecycle.canonical import (
    canonical_equal,
    canonical_equality_sha256,
    deterministic_id,
)
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
    OFFICIAL_PICK_SCHEMA_VERSION,
    OfficialPick,
    OfficialPickCandidateReview,
    OfficialPickDesignation,
    OfficialPickOperatorDecision,
    OfficialPickPromotionRequest,
    OfficialPickReviewStatus,
    OfficialPickSourceType,
    OfficialPickStatus,
    OfficialPickValidationError,
)
from courtvision.official_picks.authorization import (
    OFFICIAL_PICK_PROCESS_TRANSACTION_LOCK,
    OfficialPickAuthorizationValidationError,
    validate_committed_schema_v2_review_authorization,
    validate_schema_v2_review_authorization,
)
from courtvision.official_picks.review import (
    read_official_pick_candidate_review,
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


class OfficialPickPromotionAuthorizationError(OfficialPickPromotionError):
    """Promotion lacks a matching committed approved operator review."""


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
    review_id: str,
) -> str:
    candidate = _request(request)
    _, source_id = _source_reference(candidate)
    review_reference = str(review_id).strip()
    if not review_reference:
        raise OfficialPickPromotionAuthorizationError(
            "promotion requires a committed approved review_id"
        )
    return deterministic_id(
        "opidem",
        "courtvision.official_pick_promotion.v2",
        {
            "promotion_policy_version": OFFICIAL_PICK_PROMOTION_POLICY_VERSION,
            "review_id": review_reference,
            "source_candidate_id": source_id,
            "approved_designation": candidate.designation,
            "candidate_snapshot_sha256": canonical_equality_sha256(
                candidate.to_candidate_snapshot()
            ),
        },
    )


def promote_candidate_to_official_pick(
    request: OfficialPickPromotionRequest | Mapping[str, Any],
    *,
    lifecycle_root: str | Path,
    review_id: str | None = None,
    promotion_actor: str | None = None,
    clock: Clock | None = None,
    pick_id_factory: PickIdFactory = generate_pick_id,
    transaction_id_factory: TransactionIdFactory = generate_promotion_transaction_id,
    failure_hook: FailureHook | None = None,
) -> OfficialPickPromotionResult:
    """Promote one candidate only through its committed approved review."""

    candidate = _request(request)
    designation_value = candidate.designation
    if designation_value == OfficialPickDesignation.LIVE.value:
        raise LiveOfficialPickBlockedError(
            "live official-pick designation is blocked; no live promotion gate is active"
        )
    review_reference = str(review_id or "").strip()
    if not review_reference:
        raise OfficialPickPromotionAuthorizationError(
            "promotion requires a committed approved review_id"
        )
    root = Path(lifecycle_root).resolve()
    review = read_official_pick_candidate_review(root, review_reference)
    if review is None:
        raise OfficialPickPromotionAuthorizationError(
            f"review_id is not committed: {review_reference}"
        )
    if review.review_status != OfficialPickReviewStatus.COMMITTED.value:
        raise OfficialPickPromotionAuthorizationError(
            "promotion review is not committed"
        )
    if review.operator_decision != OfficialPickOperatorDecision.APPROVED.value:
        raise OfficialPickPromotionAuthorizationError(
            "promotion requires an APPROVED operator review; committed "
            f"decision is {review.operator_decision}"
        )
    if review.source_candidate_id != candidate.source_candidate_id:
        raise OfficialPickPromotionAuthorizationError(
            "review source_candidate_id does not match promotion candidate"
        )
    if not canonical_equal(
        candidate.to_candidate_snapshot(),
        review.candidate_snapshot,
    ):
        raise OfficialPickPromotionAuthorizationError(
            "promotion candidate differs from the approved frozen snapshot"
        )
    actor = str(promotion_actor or review.operator_id).strip()
    if not actor:
        raise OfficialPickValidationError("promotion_actor is required")
    active_clock = clock or SystemClock()
    idempotency_key = official_pick_idempotency_key(
        candidate,
        review_id=review.review_id,
    )

    existing = _find_by_idempotency_key(root, idempotency_key)
    if existing is not None:
        return _replay_result(
            existing,
            candidate=candidate,
            promotion_actor=actor,
            review=review,
        )

    published_at = active_clock.now()
    pick = _build_pick(
        candidate,
        pick_id=pick_id_factory(),
        published_at=published_at,
        idempotency_key=idempotency_key,
        promotion_actor=actor,
        review=review,
    )
    try:
        validate_schema_v2_review_authorization(
            pick,
            review,
            candidate=candidate,
        )
    except OfficialPickAuthorizationValidationError as exc:
        raise OfficialPickPromotionAuthorizationError(str(exc)) from exc
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
    replay_result: OfficialPickPromotionResult | None = None

    def prepare_segment() -> tuple[
        RunManifest,
        tuple[EventEnvelope, ...],
        tuple[Any, ...],
    ] | None:
        nonlocal replay_result
        locked_existing = _find_by_idempotency_key(root, idempotency_key)
        if locked_existing is not None:
            replay_result = _replay_result(
                locked_existing,
                candidate=candidate,
                promotion_actor=actor,
                review=review,
            )
            return None
        return manifest, (event,), ()

    try:
        with OFFICIAL_PICK_PROCESS_TRANSACTION_LOCK:
            commit = writer.run_locked_transaction(
                prediction_run_id=transaction_id,
                prepare=prepare_segment,
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
            promotion_actor=actor,
            review=review,
        )
    except LifecycleIntegrityError:
        raise

    if commit is None:
        if replay_result is None:
            raise OfficialPickLedgerIntegrityError(
                "locked promotion transaction completed without a result"
            )
        return replay_result
    persisted_event = _official_pick_event_in_segment(
        commit.segment_directory,
        idempotency_key,
        lifecycle_root=root,
    )
    persisted = _event_pick(
        persisted_event,
        lifecycle_root=root,
    )
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
    """Reject sportsbook observations at the operator-review boundary."""

    del request, kwargs
    raise OfficialPickPromotionAuthorizationError(
        "MARKET_OBSERVATION records cannot be promoted to OfficialPick"
    )


def read_official_picks(lifecycle_root: str | Path) -> tuple[OfficialPick, ...]:
    root = Path(lifecycle_root)
    records: list[OfficialPick] = []
    seen_ids: dict[str, OfficialPick] = {}
    seen_keys: dict[str, OfficialPick] = {}
    seen_reviews: dict[str, OfficialPick] = {}
    seen_candidates: dict[str, OfficialPick] = {}
    for segment in completed_segment_directories(root):
        for event in _verified_segment_events(segment, lifecycle_root=root):
            if event.event_type != EventType.OFFICIAL_PICK_PUBLISHED.value:
                continue
            pick = _event_pick(
                event,
                segment_directory=segment,
                lifecycle_root=root,
            )
            if pick.pick_id in seen_ids:
                raise OfficialPickLedgerIntegrityError(
                    f"duplicate official pick_id in ledger: {pick.pick_id}"
                )
            if pick.idempotency_key in seen_keys:
                raise OfficialPickLedgerIntegrityError(
                    "duplicate official-pick idempotency key in ledger: "
                    f"{pick.idempotency_key}"
                )
            if pick.review_id is not None and pick.review_id in seen_reviews:
                raise OfficialPickLedgerIntegrityError(
                    "one approved review created multiple official picks: "
                    f"{pick.review_id}"
                )
            if pick.source_candidate_id in seen_candidates:
                raise OfficialPickLedgerIntegrityError(
                    "one source candidate/review identity created multiple "
                    f"official picks: {pick.source_candidate_id}"
                )
            seen_ids[pick.pick_id] = pick
            seen_keys[pick.idempotency_key] = pick
            if pick.review_id is not None:
                seen_reviews[pick.review_id] = pick
            if pick.source_candidate_id is not None:
                seen_candidates[pick.source_candidate_id] = pick
            records.append(pick)
    ordered = tuple(
        sorted(records, key=lambda item: (item.published_at, item.pick_id))
    )
    return ordered


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


def _source_reference(
    request: OfficialPickPromotionRequest,
) -> tuple[str, str]:
    candidate_id = (
        str(request.source_candidate_id).strip()
        if request.source_candidate_id is not None
        else ""
    )
    if not candidate_id or request.source_observation_id is not None:
        raise OfficialPickValidationError(
            "promotion requires only source_candidate_id"
        )
    if candidate_id.casefold() in {"unknown", "unresolved", "none", "null", "nan"}:
        raise OfficialPickValidationError("source_candidate_id is unresolved")
    return OfficialPickSourceType.CANDIDATE.value, candidate_id


def _build_pick(
    request: OfficialPickPromotionRequest,
    *,
    pick_id: str,
    published_at: datetime,
    idempotency_key: str,
    promotion_actor: str,
    review: OfficialPickCandidateReview,
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
        "review_id": review.review_id,
        "review_decision": review.operator_decision,
        "review_operator_id": review.operator_id,
        "review_run_id": review.review_run_id,
        "candidate_snapshot_sha256": review.candidate_snapshot_sha256,
    }
    return OfficialPick(
        pick_id=pick_id,
        sport=request.sport,
        league=request.league,
        event_id=request.event_id,
        event_start_time=cast(datetime, request.event_start_time),
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
        source_observation_id=None,
        review_id=review.review_id,
        candidate_snapshot_sha256=review.candidate_snapshot_sha256,
        status=OfficialPickStatus.PUBLISHED.value,
        designation=request.designation,
        idempotency_key=idempotency_key,
        provenance=provenance,
    )


def _promotion_content(pick: OfficialPick) -> dict[str, Any]:
    value = pick.to_dict()
    for generated in ("pick_id", "published_at", "idempotency_key"):
        value.pop(generated, None)
    return value


def _promotion_content_sha256(pick: OfficialPick) -> str:
    return canonical_equality_sha256(_promotion_content(pick))


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
        pipeline_version="official-pick-promotion-v2",
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
        source_refs={
            "review_id": pick.review_id,
            "source_candidate_id": pick.source_candidate_id,
        },
        source_hashes={
            "promotion_content_sha256": promotion_content_sha256,
            "candidate_snapshot_sha256": pick.candidate_snapshot_sha256,
        },
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
                pick=_event_pick(
                    event,
                    segment_directory=segment,
                    lifecycle_root=lifecycle_root,
                ),
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
    promotion_actor: str,
    review: OfficialPickCandidateReview,
) -> OfficialPickPromotionResult:
    requested = _build_pick(
        candidate,
        pick_id=existing.pick.pick_id,
        published_at=existing.pick.published_at,
        idempotency_key=existing.pick.idempotency_key,
        promotion_actor=promotion_actor,
        review=review,
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
    if not isinstance(payload, dict):
        raise OfficialPickLedgerIntegrityError(
            f"official-pick event payload must be an object: {event.event_id}"
        )
    if "payload_schema_version" not in payload:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick payload schema is missing: {event.event_id}"
        )
    payload_version = payload["payload_schema_version"]
    if type(payload_version) is not int:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick payload schema must be an integer: {event.event_id}"
        )
    expected_policy = {
        1: "1.0",
        OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION: (
            OFFICIAL_PICK_PROMOTION_POLICY_VERSION
        ),
    }.get(payload_version)
    if expected_policy is None or event.payload_schema_version != payload_version:
        raise OfficialPickLedgerIntegrityError(
            f"unsupported official-pick payload schema: {event.event_id}"
        )
    expected_fields = {
        "payload_schema_version",
        "promotion_policy_version",
        "publication_authority",
        "promotion_content_sha256",
        "official_pick",
    }
    if set(payload) != expected_fields:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick payload fields are not exact: {event.event_id}"
        )
    if payload["promotion_policy_version"] != expected_policy:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick promotion policy mismatch: {event.event_id}"
        )
    if payload["publication_authority"] != "PAPER_RESEARCH_ONLY":
        raise OfficialPickLedgerIntegrityError(
            f"official-pick publication authority mismatch: {event.event_id}"
        )
    return payload


def _event_pick(
    event: EventEnvelope,
    *,
    segment_directory: Path | None = None,
    lifecycle_root: Path | None = None,
) -> OfficialPick:
    payload = _event_payload(event)
    payload_version = payload["payload_schema_version"]
    if payload_version == 1:
        raise OfficialPickLedgerIntegrityError(
            "schema-v1 OfficialPick is rejected at the active runtime boundary; "
            "no production historical-v1 segments are registered"
        )
    try:
        pick = OfficialPick.from_dict(payload["official_pick"])
    except (KeyError, OfficialPickValidationError) as exc:
        raise OfficialPickLedgerIntegrityError(
            f"malformed official-pick row in event {event.event_id}"
        ) from exc
    if (
        payload_version == OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION
        and pick.schema_version != OFFICIAL_PICK_SCHEMA_VERSION
    ):
        raise OfficialPickLedgerIntegrityError(
            f"official-pick payload/pick schema mismatch: {event.event_id}"
        )
    del segment_directory
    if pick.idempotency_key != event.idempotency_key:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick/event idempotency mismatch: {event.event_id}"
        )
    expected_content_hash = _promotion_content_sha256(pick)
    if payload.get("promotion_content_sha256") != expected_content_hash:
        raise OfficialPickLedgerIntegrityError(
            f"official-pick promotion content hash mismatch: {event.event_id}"
        )
    if pick.schema_version == OFFICIAL_PICK_SCHEMA_VERSION:
        expected_refs = {
            "review_id": pick.review_id,
            "source_candidate_id": pick.source_candidate_id,
        }
        expected_hashes = {
            "promotion_content_sha256": expected_content_hash,
            "candidate_snapshot_sha256": pick.candidate_snapshot_sha256,
        }
        if (
            not canonical_equal(event.source_refs, expected_refs)
            or not canonical_equal(event.source_hashes, expected_hashes)
            or event.actor_type != "OPERATOR"
            or event.actor_id != pick.provenance["promotion_actor"]
            or event.correlation_id != pick.run_id
            or event.occurred_at_utc != pick.published_at
            or event.recorded_at_utc != pick.published_at
            or event.operating_date != pick.prediction_date
            or event.model_id != pick.model_name
            or event.model_version != pick.model_version
        ):
            raise OfficialPickLedgerIntegrityError(
                f"official-pick review evidence mismatch: {event.event_id}"
            )
        if lifecycle_root is not None:
            try:
                review = validate_committed_schema_v2_review_authorization(
                    lifecycle_root,
                    pick,
                )
                expected_key = official_pick_idempotency_key(
                    review.candidate_snapshot,
                    review_id=review.review_id,
                )
                if (
                    pick.idempotency_key != expected_key
                    or event.idempotency_key != expected_key
                ):
                    raise OfficialPickAuthorizationValidationError(
                        "promotion idempotency key does not match frozen "
                        "review policy inputs"
                    )
            except OfficialPickAuthorizationValidationError as exc:
                raise OfficialPickLedgerIntegrityError(
                    "official pick review authorization mismatch: "
                    f"{pick.pick_id}: {exc}"
                ) from exc
    return pick


def validate_new_official_pick_publication_events(
    lifecycle_root: str | Path,
    events: Sequence[EventEnvelope],
) -> None:
    """Authorize a complete pending publication batch against committed state."""

    try:
        committed = read_official_picks(lifecycle_root)
        by_pick_id = {item.pick_id: item for item in committed}
        by_review_id = {str(item.review_id): item for item in committed}
        by_candidate_id = {
            str(item.source_candidate_id): item for item in committed
        }
        by_idempotency = {
            item.idempotency_key: item for item in committed
        }
        for event in events:
            if event.event_type != EventType.OFFICIAL_PICK_PUBLISHED.value:
                raise OfficialPickLedgerIntegrityError(
                    "publication authorization received a non-publication event"
                )
            payload = _event_payload(event)
            if (
                payload["payload_schema_version"]
                != OFFICIAL_PICK_PAYLOAD_SCHEMA_VERSION
            ):
                raise OfficialPickLedgerIntegrityError(
                    "new OfficialPick publications require payload schema v2"
                )
            pick = _event_pick(event)
            review = validate_committed_schema_v2_review_authorization(
                lifecycle_root,
                pick,
            )
            expected_key = official_pick_idempotency_key(
                review.candidate_snapshot,
                review_id=review.review_id,
            )
            if (
                pick.idempotency_key != expected_key
                or event.idempotency_key != expected_key
            ):
                raise OfficialPickAuthorizationValidationError(
                    "promotion idempotency key does not match frozen review "
                    "policy inputs"
                )

            owners = tuple(
                owner
                for owner in (
                    by_pick_id.get(pick.pick_id),
                    by_review_id.get(str(pick.review_id)),
                    by_candidate_id.get(str(pick.source_candidate_id)),
                    by_idempotency.get(pick.idempotency_key),
                )
                if owner is not None
            )
            if owners:
                if all(
                    owner.pick_id == pick.pick_id
                    and canonical_equal(owner.to_dict(), pick.to_dict())
                    for owner in owners
                ):
                    continue
                raise OfficialPickAuthorizationValidationError(
                    "review, candidate, promotion identity, or pick_id is "
                    "already bound to another OfficialPick"
                )

            by_pick_id[pick.pick_id] = pick
            by_review_id[str(pick.review_id)] = pick
            by_candidate_id[str(pick.source_candidate_id)] = pick
            by_idempotency[pick.idempotency_key] = pick
    except (
        OfficialPickAuthorizationValidationError,
        OfficialPickLedgerIntegrityError,
    ) as exc:
        raise OfficialPickValidationError(str(exc)) from exc


def validate_new_official_pick_publication_event(
    lifecycle_root: str | Path,
    event: EventEnvelope,
) -> None:
    """Compatibility wrapper for one-event internal validation callers."""

    validate_new_official_pick_publication_events(lifecycle_root, (event,))


def _nullable(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "LiveOfficialPickBlockedError",
    "OfficialPickConflictError",
    "OfficialPickLedgerIntegrityError",
    "OfficialPickPromotionAuthorizationError",
    "OfficialPickPromotionError",
    "OfficialPickPromotionResult",
    "generate_pick_id",
    "official_pick_idempotency_key",
    "promote_candidate_to_official_pick",
    "promote_observation_to_official_pick",
    "read_official_pick",
    "read_official_picks",
    "validate_new_official_pick_publication_event",
    "validate_new_official_pick_publication_events",
]
