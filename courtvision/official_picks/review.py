"""Append-only operator review service for official-pick candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import platform
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from courtvision.lifecycle.canonical import (
    canonical_equal,
    canonical_equality_bytes,
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
    OFFICIAL_PICK_REVIEW_PAYLOAD_SCHEMA_VERSION,
    OFFICIAL_PICK_REVIEW_POLICY_VERSION,
    OfficialPickCandidateReview,
    OfficialPickOperatorDecision,
    OfficialPickPromotionRequest,
    OfficialPickReviewStatus,
    OfficialPickReviewValidationError,
    OfficialPickValidationError,
    PickRecordKind,
)
from courtvision.official_picks.authorization import (
    OFFICIAL_PICK_PROCESS_TRANSACTION_LOCK,
)


ReviewIdFactory = Callable[[], str]
TransactionIdFactory = Callable[[], str]


class OfficialPickReviewError(RuntimeError):
    """Base error for operator-review publication."""


class OfficialPickReviewConflictError(OfficialPickReviewError):
    """A review slot or candidate identity conflicts with committed content."""


class OfficialPickReviewTransitionError(OfficialPickReviewError):
    """A requested review would replace a final operator decision."""


class OfficialPickReviewLedgerIntegrityError(OfficialPickReviewError):
    """Committed operator-review data failed strict reconstruction."""


@dataclass(frozen=True, slots=True)
class OfficialPickReviewResult:
    review: OfficialPickCandidateReview
    publication_status: str
    ledger_segment_directory: Path
    event_id: str
    idempotency_key: str


def generate_review_id() -> str:
    """Assign a globally unique review identity once at publication time."""

    return f"review_{uuid4().hex}"


def generate_review_transaction_id() -> str:
    return f"official-pick-review-{uuid4().hex}"


def official_pick_review_idempotency_key(
    candidate: OfficialPickPromotionRequest | Mapping[str, Any],
    *,
    review_run_id: str,
    operator_decision: str | OfficialPickOperatorDecision,
) -> str:
    request = _candidate(candidate)
    run_id = _required_text(review_run_id, "review_run_id")
    decision = _decision(operator_decision)
    review_slot = (
        f"DEFERRED:{run_id}"
        if decision == OfficialPickOperatorDecision.DEFERRED.value
        else "FINAL"
    )
    return deterministic_id(
        "oprevidem",
        "courtvision.official_pick_review.v1",
        {
            "review_policy_version": OFFICIAL_PICK_REVIEW_POLICY_VERSION,
            "source_candidate_id": request.source_candidate_id,
            "approved_designation": request.designation,
            "review_slot": review_slot,
        },
    )


def review_official_pick_candidate(
    candidate: OfficialPickPromotionRequest | Mapping[str, Any],
    *,
    operator_decision: str | OfficialPickOperatorDecision,
    operator_id: str,
    decision_reason: str,
    review_run_id: str,
    lifecycle_root: str | Path,
    provenance: Mapping[str, Any] | None = None,
    clock: Clock | None = None,
    review_id_factory: ReviewIdFactory = generate_review_id,
    transaction_id_factory: TransactionIdFactory = generate_review_transaction_id,
    failure_hook: FailureHook | None = None,
) -> OfficialPickReviewResult:
    """Commit one explicit operator decision over a frozen model candidate."""

    request = _candidate(candidate)
    decision = _decision(operator_decision)
    actor = _required_text(operator_id, "operator_id")
    reason = _required_text(decision_reason, "decision_reason")
    review_run = _required_text(review_run_id, "review_run_id")
    if provenance is not None and not isinstance(provenance, Mapping):
        raise OfficialPickReviewValidationError("provenance must be a mapping")
    root = Path(lifecycle_root).resolve()
    active_clock = clock or SystemClock()
    snapshot = request.to_candidate_snapshot()
    snapshot_hash = canonical_equality_sha256(snapshot)
    idempotency_key = official_pick_review_idempotency_key(
        request,
        review_run_id=review_run,
        operator_decision=decision,
    )

    requested_content = _review_content(
        source_candidate_id=str(request.source_candidate_id),
        operator_decision=decision,
        approved_designation=request.designation,
        operator_id=actor,
        decision_reason=reason,
        review_run_id=review_run,
        candidate_snapshot=snapshot,
        candidate_snapshot_sha256=snapshot_hash,
        provenance=provenance,
    )
    requested_hash = canonical_equality_sha256(requested_content)
    transaction_id = _required_text(
        transaction_id_factory(), "review transaction ID"
    )
    writer = LifecycleWriter(root, clock=active_clock)
    replay_result: OfficialPickReviewResult | None = None

    def prepare_segment() -> tuple[
        RunManifest,
        tuple[EventEnvelope, ...],
        tuple[Any, ...],
    ] | None:
        nonlocal replay_result
        all_reviews = read_official_pick_candidate_reviews(root)
        existing = _find_by_idempotency_key(root, idempotency_key)
        if existing is not None:
            if existing.review.candidate_snapshot_sha256 != snapshot_hash:
                raise OfficialPickReviewConflictError(
                    "SOURCE_CANDIDATE_ID_REUSE: committed candidate content differs"
                )
            replay_result = _replay_result(
                existing,
                requested_content_sha256=requested_hash,
            )
            return None

        prior_reviews = tuple(
            item
            for item in all_reviews
            if item.source_candidate_id == request.source_candidate_id
        )
        reviewed_at = active_clock.now()
        _validate_requested_transition(
            prior_reviews,
            snapshot=snapshot,
            snapshot_sha256=snapshot_hash,
            operator_decision=decision,
            approved_designation=request.designation,
            review_run_id=review_run,
            reviewed_at=reviewed_at,
        )
        assigned_review_id = review_id_factory()
        if any(item.review_id == assigned_review_id for item in all_reviews):
            raise OfficialPickReviewConflictError(
                f"review_id is already committed: {assigned_review_id}"
            )
        review = OfficialPickCandidateReview(
            review_id=assigned_review_id,
            source_candidate_id=str(request.source_candidate_id),
            source_record_kind=PickRecordKind.MODEL_CANDIDATE.value,
            review_status=OfficialPickReviewStatus.COMMITTED.value,
            operator_decision=decision,
            approved_designation=request.designation,
            operator_id=actor,
            decision_reason=reason,
            reviewed_at=reviewed_at,
            review_run_id=review_run,
            candidate_snapshot=snapshot,
            candidate_snapshot_sha256=snapshot_hash,
            provenance={
                **dict(provenance or {}),
                "review_service": "courtvision.official_picks.review",
                "review_policy_version": OFFICIAL_PICK_REVIEW_POLICY_VERSION,
                "source_candidate_id": request.source_candidate_id,
            },
            idempotency_key=idempotency_key,
        )
        content_hash = _review_content_sha256(review)
        manifest = _review_manifest(
            review,
            request=request,
            transaction_id=transaction_id,
        )
        event = _review_event(
            review,
            request=request,
            transaction_id=transaction_id,
            review_content_sha256=content_hash,
        )
        return manifest, (event,), ()

    try:
        with OFFICIAL_PICK_PROCESS_TRANSACTION_LOCK:
            commit = writer.run_locked_transaction(
                prediction_run_id=transaction_id,
                prepare=prepare_segment,
                failure_hook=failure_hook,
                command="courtvision official-pick candidate review",
            )
    except IdempotencyConflictError as exc:
        raced = _find_by_idempotency_key(root, idempotency_key)
        if raced is None:
            raise OfficialPickReviewConflictError(str(exc)) from exc
        return _replay_result(
            raced,
            requested_content_sha256=requested_hash,
        )
    except LifecycleIntegrityError as exc:
        raise OfficialPickReviewLedgerIntegrityError(str(exc)) from exc

    if commit is None:
        if replay_result is None:
            raise OfficialPickReviewLedgerIntegrityError(
                "locked review transaction completed without a result"
            )
        return replay_result
    persisted_event = _review_event_in_segment(
        commit.segment_directory,
        idempotency_key,
        lifecycle_root=root,
    )
    persisted = _event_review(persisted_event)
    read_official_pick_candidate_reviews(root)
    return OfficialPickReviewResult(
        review=persisted,
        publication_status=(
            "PUBLISHED"
            if commit.status == "COMMITTED"
            else "ALREADY_PUBLISHED"
        ),
        ledger_segment_directory=commit.segment_directory,
        event_id=persisted_event.event_id,
        idempotency_key=idempotency_key,
    )


def read_official_pick_candidate_reviews(
    lifecycle_root: str | Path,
) -> tuple[OfficialPickCandidateReview, ...]:
    root = Path(lifecycle_root).resolve()
    reviews: list[OfficialPickCandidateReview] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    for segment in completed_segment_directories(root):
        for event in _verified_segment_events(segment, lifecycle_root=root):
            if (
                event.event_type
                != EventType.OFFICIAL_PICK_CANDIDATE_REVIEWED.value
            ):
                continue
            review = _event_review(event)
            if review.review_id in seen_ids:
                raise OfficialPickReviewLedgerIntegrityError(
                    f"duplicate review_id in ledger: {review.review_id}"
                )
            if review.idempotency_key in seen_keys:
                raise OfficialPickReviewLedgerIntegrityError(
                    "duplicate operator-review idempotency key in ledger: "
                    f"{review.idempotency_key}"
                )
            seen_ids.add(review.review_id)
            seen_keys.add(review.idempotency_key)
            reviews.append(review)

    ordered = tuple(
        sorted(reviews, key=lambda item: (item.reviewed_at, item.review_id))
    )
    _validate_committed_review_state(ordered)
    return ordered


def _validate_committed_review_state(
    reviews: tuple[OfficialPickCandidateReview, ...],
) -> None:
    by_candidate: dict[str, list[OfficialPickCandidateReview]] = {}
    for review in reviews:
        by_candidate.setdefault(review.source_candidate_id, []).append(review)
    for source_candidate_id, candidate_reviews in by_candidate.items():
        hashes = {
            item.candidate_snapshot_sha256 for item in candidate_reviews
        }
        snapshots = {
            canonical_equality_bytes(item.candidate_snapshot)
            for item in candidate_reviews
        }
        if len(hashes) != 1 or len(snapshots) != 1:
            raise OfficialPickReviewLedgerIntegrityError(
                "source_candidate_id is reused with different committed content: "
                f"{source_candidate_id}"
            )
        deferred_reviews = [
            item
            for item in candidate_reviews
            if item.operator_decision
            == OfficialPickOperatorDecision.DEFERRED.value
        ]
        final_reviews = [
            item
            for item in candidate_reviews
            if item.operator_decision
            != OfficialPickOperatorDecision.DEFERRED.value
        ]
        if len(deferred_reviews) > 1:
            raise OfficialPickReviewLedgerIntegrityError(
                "candidate has multiple deferred operator reviews: "
                f"{source_candidate_id}"
            )
        if len(final_reviews) > 1:
            raise OfficialPickReviewLedgerIntegrityError(
                "candidate has multiple final operator reviews: "
                f"{source_candidate_id}"
            )
        if deferred_reviews and final_reviews:
            deferred = deferred_reviews[0]
            final = final_reviews[0]
            if final.reviewed_at < deferred.reviewed_at:
                raise OfficialPickReviewLedgerIntegrityError(
                    "final operator review precedes deferred review: "
                    f"{source_candidate_id}"
                )
            if final.review_run_id == deferred.review_run_id:
                raise OfficialPickReviewLedgerIntegrityError(
                    "deferred-to-final transition reused review_run_id: "
                    f"{source_candidate_id}"
                )


def _validate_requested_transition(
    prior_reviews: tuple[OfficialPickCandidateReview, ...],
    *,
    snapshot: Mapping[str, Any],
    snapshot_sha256: str,
    operator_decision: str,
    approved_designation: str,
    review_run_id: str,
    reviewed_at: datetime,
) -> None:
    if any(
        item.candidate_snapshot_sha256 != snapshot_sha256
        or item.approved_designation != approved_designation
        or not canonical_equal(item.candidate_snapshot, snapshot)
        for item in prior_reviews
    ):
        raise OfficialPickReviewConflictError(
            "SOURCE_CANDIDATE_ID_REUSE: committed candidate content differs"
        )
    final_reviews = tuple(
        item
        for item in prior_reviews
        if item.operator_decision
        != OfficialPickOperatorDecision.DEFERRED.value
    )
    if final_reviews:
        raise OfficialPickReviewTransitionError(
            "candidate already has a final committed operator decision"
        )
    deferred_reviews = tuple(
        item
        for item in prior_reviews
        if item.operator_decision
        == OfficialPickOperatorDecision.DEFERRED.value
    )
    if deferred_reviews:
        deferred = deferred_reviews[0]
        if operator_decision == OfficialPickOperatorDecision.DEFERRED.value:
            raise OfficialPickReviewTransitionError(
                "DEFERRED may be followed only by a final operator decision"
            )
        if review_run_id == deferred.review_run_id:
            raise OfficialPickReviewTransitionError(
                "a deferred candidate requires a new explicit review_run_id"
            )
        if reviewed_at < deferred.reviewed_at:
            raise OfficialPickReviewTransitionError(
                "reviewed_at must be monotonic for a candidate"
            )


def read_official_pick_candidate_review(
    lifecycle_root: str | Path,
    review_id: str,
) -> OfficialPickCandidateReview | None:
    target = str(review_id).strip()
    return next(
        (
            item
            for item in read_official_pick_candidate_reviews(lifecycle_root)
            if item.review_id == target
        ),
        None,
    )


def validate_new_official_pick_review_events(
    lifecycle_root: str | Path,
    events: Sequence[EventEnvelope],
) -> None:
    """Authorize a complete pending review batch against committed state."""

    committed = read_official_pick_candidate_reviews(lifecycle_root)
    working = list(committed)
    by_review_id = {item.review_id: item for item in committed}
    by_idempotency = {item.idempotency_key: item for item in committed}
    pending_review_ids: set[str] = set()
    pending_idempotency_keys: set[str] = set()

    for event in events:
        if event.event_type != EventType.OFFICIAL_PICK_CANDIDATE_REVIEWED.value:
            raise OfficialPickReviewValidationError(
                "review authorization received a non-review event"
            )
        review = _event_review(event)
        existing_by_key = by_idempotency.get(review.idempotency_key)
        if existing_by_key is not None:
            if not canonical_equal(
                existing_by_key.to_dict(),
                review.to_dict(),
            ):
                raise OfficialPickReviewConflictError(
                    "IDEMPOTENCY_CONFLICT: operator-review content differs"
                )
            continue
        existing_by_id = by_review_id.get(review.review_id)
        if existing_by_id is not None:
            raise OfficialPickReviewConflictError(
                f"review_id is already committed: {review.review_id}"
            )
        if review.review_id in pending_review_ids:
            raise OfficialPickReviewConflictError(
                f"duplicate review_id in pending batch: {review.review_id}"
            )
        if review.idempotency_key in pending_idempotency_keys:
            raise OfficialPickReviewConflictError(
                "duplicate operator-review idempotency key in pending batch"
            )
        prior_reviews = tuple(
            item
            for item in working
            if item.source_candidate_id == review.source_candidate_id
        )
        _validate_requested_transition(
            prior_reviews,
            snapshot=review.candidate_snapshot,
            snapshot_sha256=review.candidate_snapshot_sha256,
            operator_decision=review.operator_decision,
            approved_designation=review.approved_designation,
            review_run_id=review.review_run_id,
            reviewed_at=review.reviewed_at,
        )
        pending_review_ids.add(review.review_id)
        pending_idempotency_keys.add(review.idempotency_key)
        by_review_id[review.review_id] = review
        by_idempotency[review.idempotency_key] = review
        working.append(review)

    _validate_committed_review_state(tuple(working))


@dataclass(frozen=True, slots=True)
class _ExistingReview:
    review: OfficialPickCandidateReview
    event: EventEnvelope
    segment_directory: Path
    review_content_sha256: str


def _candidate(
    value: OfficialPickPromotionRequest | Mapping[str, Any],
) -> OfficialPickPromotionRequest:
    try:
        if isinstance(value, OfficialPickPromotionRequest):
            request = value
        elif isinstance(value, Mapping):
            request = OfficialPickPromotionRequest.from_mapping(value)
        else:
            raise OfficialPickValidationError(
                "candidate must be OfficialPickPromotionRequest or a mapping"
            )
    except OfficialPickValidationError as exc:
        raise OfficialPickReviewValidationError(str(exc)) from exc
    if request.record_kind != PickRecordKind.MODEL_CANDIDATE.value:
        raise OfficialPickReviewValidationError(
            "operator review requires a MODEL_CANDIDATE record"
        )
    return request


def _decision(value: str | OfficialPickOperatorDecision) -> str:
    raw = value.value if isinstance(value, OfficialPickOperatorDecision) else str(value)
    normalized = raw.strip().upper()
    if normalized not in {
        item.value for item in OfficialPickOperatorDecision
    }:
        raise OfficialPickReviewValidationError(
            "unsupported operator_decision"
        )
    return normalized


def _required_text(value: Any, field_name: str) -> str:
    if value is None:
        raise OfficialPickReviewValidationError(f"{field_name} is required")
    text = str(value).strip()
    if not text or text.casefold() in {
        "nan",
        "none",
        "null",
        "unknown",
        "unresolved",
    }:
        raise OfficialPickReviewValidationError(f"{field_name} is required")
    return text


def _review_content(
    *,
    source_candidate_id: str,
    operator_decision: str,
    approved_designation: str,
    operator_id: str,
    decision_reason: str,
    review_run_id: str,
    candidate_snapshot: Mapping[str, Any],
    candidate_snapshot_sha256: str,
    provenance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "source_candidate_id": source_candidate_id,
        "source_record_kind": PickRecordKind.MODEL_CANDIDATE.value,
        "review_status": OfficialPickReviewStatus.COMMITTED.value,
        "operator_decision": operator_decision,
        "approved_designation": approved_designation,
        "operator_id": operator_id,
        "decision_reason": decision_reason,
        "review_run_id": review_run_id,
        "candidate_snapshot": dict(candidate_snapshot),
        "candidate_snapshot_sha256": candidate_snapshot_sha256,
        "provenance": {
            **dict(provenance or {}),
            "review_service": "courtvision.official_picks.review",
            "review_policy_version": OFFICIAL_PICK_REVIEW_POLICY_VERSION,
            "source_candidate_id": source_candidate_id,
        },
        "schema_version": 1,
        "record_kind": PickRecordKind.OFFICIAL_PICK_CANDIDATE_REVIEW.value,
    }


def _review_content_sha256(review: OfficialPickCandidateReview) -> str:
    value = review.to_dict()
    for generated in ("review_id", "reviewed_at", "idempotency_key"):
        value.pop(generated, None)
    return canonical_equality_sha256(value)


def _review_manifest(
    review: OfficialPickCandidateReview,
    *,
    request: OfficialPickPromotionRequest,
    transaction_id: str,
) -> RunManifest:
    provenance = dict(request.provenance)
    return RunManifest(
        prediction_run_id=transaction_id,
        run_mode=RunMode.RESEARCH.value,
        run_reason=RunReason.MANUAL.value,
        parent_run_id=request.run_id,
        started_at_utc=review.reviewed_at,
        completed_at_utc=review.reviewed_at,
        operating_date=request.prediction_date,
        operating_timezone="America/Toronto",
        git_commit_sha=_nullable(provenance.get("git_commit_sha")),
        git_dirty=None,
        working_tree_hash=None,
        config_hash=_nullable(provenance.get("config_hash")),
        model_id=request.model_name,
        model_version=request.model_version,
        model_bundle_hash=_nullable(provenance.get("model_bundle_hash")),
        calibration_id=None,
        calibration_version=None,
        calibration_hash=None,
        strategy_version=None,
        pipeline_version="official-pick-operator-review-v1",
        python_version=platform.python_version(),
        dependency_fingerprint=None,
        input_manifest_hash=_nullable(provenance.get("input_manifest_hash")),
        reproducibility_level=ReproducibilityLevel.PARTIAL.value,
    )


def _review_event(
    review: OfficialPickCandidateReview,
    *,
    request: OfficialPickPromotionRequest,
    transaction_id: str,
    review_content_sha256: str,
) -> EventEnvelope:
    payload = {
        "payload_schema_version": OFFICIAL_PICK_REVIEW_PAYLOAD_SCHEMA_VERSION,
        "review_policy_version": OFFICIAL_PICK_REVIEW_POLICY_VERSION,
        "review_content_sha256": review_content_sha256,
        "candidate_snapshot_sha256": review.candidate_snapshot_sha256,
        "operator_review": review.to_dict(),
    }
    return EventEnvelope.create(
        event_type=EventType.OFFICIAL_PICK_CANDIDATE_REVIEWED,
        payload=payload,
        payload_schema_version=OFFICIAL_PICK_REVIEW_PAYLOAD_SCHEMA_VERSION,
        prediction_run_id=transaction_id,
        event_sequence=1,
        occurred_at_utc=review.reviewed_at,
        recorded_at_utc=review.reviewed_at,
        operating_date=request.prediction_date,
        operating_timezone="America/Toronto",
        actor_type="OPERATOR",
        actor_id=review.operator_id,
        correlation_id=review.review_run_id,
        idempotency_key=review.idempotency_key,
        source_refs={"source_candidate_id": review.source_candidate_id},
        source_hashes={
            "candidate_snapshot_sha256": review.candidate_snapshot_sha256,
            "review_content_sha256": review_content_sha256,
        },
        model_id=request.model_name,
        model_version=request.model_version,
    )


def _find_by_idempotency_key(
    lifecycle_root: Path,
    idempotency_key: str,
) -> _ExistingReview | None:
    found: _ExistingReview | None = None
    for segment in completed_segment_directories(lifecycle_root):
        for event in _verified_segment_events(
            segment,
            lifecycle_root=lifecycle_root,
        ):
            if (
                event.event_type
                != EventType.OFFICIAL_PICK_CANDIDATE_REVIEWED.value
                or event.idempotency_key != idempotency_key
            ):
                continue
            payload = _event_payload(event)
            current = _ExistingReview(
                review=_event_review(event),
                event=event,
                segment_directory=segment,
                review_content_sha256=str(
                    payload.get("review_content_sha256") or ""
                ),
            )
            if found is not None:
                raise OfficialPickReviewLedgerIntegrityError(
                    f"duplicate operator-review idempotency key: {idempotency_key}"
                )
            found = current
    return found


def _replay_result(
    existing: _ExistingReview,
    *,
    requested_content_sha256: str,
) -> OfficialPickReviewResult:
    if (
        not existing.review_content_sha256
        or existing.review_content_sha256 != requested_content_sha256
    ):
        raise OfficialPickReviewConflictError(
            "IDEMPOTENCY_CONFLICT: operator-review content differs"
        )
    return OfficialPickReviewResult(
        review=existing.review,
        publication_status="ALREADY_PUBLISHED",
        ledger_segment_directory=existing.segment_directory,
        event_id=existing.event.event_id,
        idempotency_key=existing.review.idempotency_key,
    )


def _review_event_in_segment(
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
        if event.event_type
        == EventType.OFFICIAL_PICK_CANDIDATE_REVIEWED.value
        and event.idempotency_key == idempotency_key
    ]
    if len(matching) != 1:
        raise OfficialPickReviewLedgerIntegrityError(
            "committed review segment does not contain exactly one operator review"
        )
    return matching[0]


def _verified_segment_events(
    segment_directory: Path,
    *,
    lifecycle_root: Path,
) -> tuple[EventEnvelope, ...]:
    verification = verify_segment(
        segment_directory,
        lifecycle_root=lifecycle_root,
    )
    if not verification.ok:
        raise OfficialPickReviewLedgerIntegrityError(
            "committed lifecycle segment failed verification: "
            + "; ".join(verification.violations)
        )
    try:
        return read_segment_events(segment_directory)
    except LifecycleIntegrityError as exc:
        raise OfficialPickReviewLedgerIntegrityError(str(exc)) from exc


def _event_payload(event: EventEnvelope) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review event payload is invalid: {event.event_id}"
        ) from exc
    if (
        type(payload.get("payload_schema_version")) is not int
        or event.payload_schema_version
        != OFFICIAL_PICK_REVIEW_PAYLOAD_SCHEMA_VERSION
        or payload.get("payload_schema_version")
        != OFFICIAL_PICK_REVIEW_PAYLOAD_SCHEMA_VERSION
    ):
        raise OfficialPickReviewLedgerIntegrityError(
            f"unsupported operator-review payload schema: {event.event_id}"
        )
    expected_fields = {
        "payload_schema_version",
        "review_policy_version",
        "review_content_sha256",
        "candidate_snapshot_sha256",
        "operator_review",
    }
    if set(payload) != expected_fields:
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review payload fields are not exact: {event.event_id}"
        )
    if payload["review_policy_version"] != OFFICIAL_PICK_REVIEW_POLICY_VERSION:
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review policy mismatch: {event.event_id}"
        )
    return payload


def _event_review(event: EventEnvelope) -> OfficialPickCandidateReview:
    payload = _event_payload(event)
    try:
        review = OfficialPickCandidateReview.from_dict(
            payload["operator_review"]
        )
    except (KeyError, OfficialPickReviewValidationError) as exc:
        raise OfficialPickReviewLedgerIntegrityError(
            f"malformed operator-review row in event {event.event_id}"
        ) from exc
    if review.idempotency_key != event.idempotency_key:
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review/event idempotency mismatch: {event.event_id}"
        )
    if review.operator_id != event.actor_id:
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review/event actor mismatch: {event.event_id}"
        )
    if event.actor_type != "OPERATOR":
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review actor type mismatch: {event.event_id}"
        )
    if not canonical_equal(
        event.source_refs,
        {"source_candidate_id": review.source_candidate_id},
    ):
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review source reference mismatch: {event.event_id}"
        )
    expected_content_hash = _review_content_sha256(review)
    if payload.get("review_content_sha256") != expected_content_hash:
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review content hash mismatch: {event.event_id}"
        )
    expected_source_hashes = {
        "candidate_snapshot_sha256": review.candidate_snapshot_sha256,
        "review_content_sha256": expected_content_hash,
    }
    if (
        payload.get("candidate_snapshot_sha256")
        != review.candidate_snapshot_sha256
        or not canonical_equal(event.source_hashes, expected_source_hashes)
    ):
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review evidence hash mismatch: {event.event_id}"
        )
    snapshot = review.candidate_snapshot
    if (
        event.correlation_id != review.review_run_id
        or event.occurred_at_utc != review.reviewed_at
        or event.recorded_at_utc != review.reviewed_at
        or event.operating_date != snapshot.get("prediction_date")
        or event.model_id != snapshot.get("model_name")
        or event.model_version != snapshot.get("model_version")
    ):
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review envelope metadata mismatch: {event.event_id}"
        )
    expected_key = official_pick_review_idempotency_key(
        review.candidate_snapshot,
        review_run_id=review.review_run_id,
        operator_decision=review.operator_decision,
    )
    if expected_key != review.idempotency_key:
        raise OfficialPickReviewLedgerIntegrityError(
            f"operator-review idempotency identity mismatch: {event.event_id}"
        )
    return review


def _nullable(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "OfficialPickReviewConflictError",
    "OfficialPickReviewError",
    "OfficialPickReviewLedgerIntegrityError",
    "OfficialPickReviewResult",
    "OfficialPickReviewTransitionError",
    "generate_review_id",
    "official_pick_review_idempotency_key",
    "read_official_pick_candidate_review",
    "read_official_pick_candidate_reviews",
    "review_official_pick_candidate",
    "validate_new_official_pick_review_events",
]
