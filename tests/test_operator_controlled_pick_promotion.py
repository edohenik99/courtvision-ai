from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, asdict, dataclass, replace
from datetime import UTC, datetime
import inspect
import json
from multiprocessing import get_context
import os
from pathlib import Path
from queue import Empty
from threading import Barrier
import tempfile
import time
import traceback
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator
import pytest

import courtvision.official_picks.review as review_service
import courtvision.official_picks.service as publication_service
import courtvision.lifecycle.writer as writer_service
from courtvision.lifecycle.canonical import (
    FrozenJSONDict,
    canonical_equality_sha256,
    deterministic_id,
    format_utc_datetime,
    payload_sha256,
)
from courtvision.lifecycle.clock import FixedClock
from courtvision.lifecycle.models import EventEnvelope, EventType, RunManifest
from courtvision.lifecycle.writer import (
    LifecycleIntegrityError,
    LifecycleWriter,
    LifecycleWriterBusyError,
    LifecycleWriterError,
    LifecycleWriterLock,
    LifecycleWriterReentrancyError,
    completed_segment_directories,
    read_segment_events,
)
from courtvision.official_picks import (
    OfficialPick,
    OfficialPickConflictError,
    OfficialPickLedgerIntegrityError,
    OfficialPickPromotionAuthorizationError,
    OfficialPickPromotionRequest,
    OfficialPickReviewConflictError,
    OfficialPickReviewLedgerIntegrityError,
    OfficialPickReviewTransitionError,
    OfficialPickReviewValidationError,
    OfficialPickValidationError,
    promote_candidate_to_official_pick,
    promote_observation_to_official_pick,
    read_official_pick_candidate_reviews,
    read_official_picks,
    review_official_pick_candidate,
)
from courtvision.official_picks.reporting import (
    OfficialPickReportBoundaryError,
    build_official_pick_operator_review_dataset,
    require_official_pick_roi_rows,
)


NOW = datetime(2026, 7, 26, 14, 0, tzinfo=UTC)
EVENT_START = datetime(2026, 7, 26, 23, 0, tzinfo=UTC)
REVIEW_ID = "review_0123456789abcdef0123456789abcdef"
PICK_ID = "pick_0123456789abcdef0123456789abcdef"


def _candidate(
    source_candidate_id: object = "nba-candidate-001",
    **updates: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "record_kind": "MODEL_CANDIDATE",
        "sport": "basketball",
        "league": "NBA",
        "event_id": "nba-game-001",
        "event_start_time": EVENT_START,
        "prediction_date": "2026-07-26",
        "market_key": "player_points",
        "selection": "OVER",
        "line": 24.5,
        "odds": -110,
        "sportsbook": "DraftKings",
        "player_id": "nba-player-23",
        "player_name": "Test Player",
        "team_id": "nba-team-lal",
        "model_name": "nba-props",
        "model_version": "2026.07",
        "run_id": "nba-run-001",
        "designation": "PAPER",
        "source_candidate_id": source_candidate_id,
        "provenance": {
            "git_commit_sha": "a" * 40,
            "input_manifest_hash": "b" * 64,
        },
    }
    value.update(updates)
    return value


def _review(
    tmp_path: Path,
    candidate: dict[str, object] | None = None,
    *,
    decision: str = "APPROVED",
    review_run_id: str = "review-run-001",
    review_id: str = REVIEW_ID,
    **kwargs: Any,
):
    options: dict[str, Any] = {
        "operator_decision": decision,
        "operator_id": "operator.alice",
        "decision_reason": f"fixture {decision.lower()} decision",
        "review_run_id": review_run_id,
        "lifecycle_root": tmp_path / "data" / "lifecycle",
        "clock": FixedClock(NOW),
        "review_id_factory": lambda: review_id,
        "transaction_id_factory": (
            lambda: f"official-pick-review-{review_run_id}"
        ),
    }
    options.update(kwargs)
    return review_official_pick_candidate(
        candidate or _candidate(),
        **options,
    )


def _promote(
    tmp_path: Path,
    review_id: str = REVIEW_ID,
    candidate: dict[str, object] | None = None,
    **kwargs: Any,
):
    options: dict[str, Any] = {
        "review_id": review_id,
        "lifecycle_root": tmp_path / "data" / "lifecycle",
        "clock": FixedClock(NOW),
        "pick_id_factory": lambda: PICK_ID,
        "transaction_id_factory": lambda: "official-pick-promotion-001",
    }
    options.update(kwargs)
    return promote_candidate_to_official_pick(
        candidate or _candidate(),
        **options,
    )


def _payload(result: Any) -> dict[str, Any]:
    segment = result.ledger_segment_directory  # type: ignore[attr-defined]
    event = read_segment_events(segment)[0]
    return json.loads(event.payload_json)


def _schema(name: str) -> dict[str, Any]:
    path = (
        Path(__file__).parents[1]
        / "courtvision"
        / "lifecycle"
        / "schemas"
        / name
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {
            str(key).casefold()
            for key in value
        } | {
            nested
            for item in value.values()
            for nested in _nested_keys(item)
        }
    if isinstance(value, (list, tuple)):
        return {
            nested
            for item in value
            for nested in _nested_keys(item)
        }
    return set()


def _event_with_payload(
    event: EventEnvelope,
    payload: dict[str, Any],
    *,
    payload_schema_version: int | None = None,
    idempotency_key: str | None = None,
    event_sequence: int | None = None,
    previous_event_hash: str | None = None,
) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=event.event_type,
        payload=payload,
        payload_schema_version=(
            event.payload_schema_version
            if payload_schema_version is None
            else payload_schema_version
        ),
        prediction_run_id=event.prediction_run_id,
        event_sequence=(
            event.event_sequence
            if event_sequence is None
            else event_sequence
        ),
        occurred_at_utc=event.occurred_at_utc,
        recorded_at_utc=event.recorded_at_utc,
        operating_date=event.operating_date,
        operating_timezone=event.operating_timezone,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        correlation_id=event.correlation_id,
        idempotency_key=idempotency_key or event.idempotency_key,
        prediction_id=event.prediction_id,
        prediction_key=event.prediction_key,
        market_subject_key=event.market_subject_key,
        provider_reported_at_utc=event.provider_reported_at_utc,
        source_refs=event.source_refs,
        source_hashes=event.source_hashes,
        code_sha=event.code_sha,
        config_hash=event.config_hash,
        model_id=event.model_id,
        model_version=event.model_version,
        previous_event_hash=(
            event.previous_event_hash
            if event_sequence is None
            else previous_event_hash
        ),
        corrects_event_id=event.corrects_event_id,
    )


def _commit_forged_reviewed_pick(
    tmp_path: Path,
    *,
    candidate_updates: dict[str, object],
) -> None:
    review_result = _review(tmp_path)
    forged_candidate = OfficialPickPromotionRequest.from_mapping(
        _candidate(**candidate_updates)
    )
    pick = publication_service._build_pick(
        forged_candidate,
        pick_id=PICK_ID,
        published_at=NOW,
        idempotency_key=publication_service.official_pick_idempotency_key(
            forged_candidate,
            review_id=review_result.review.review_id,
        ),
        promotion_actor="operator.alice",
        review=review_result.review,
    )
    transaction_id = f"forged-promotion-{next(iter(candidate_updates))}"
    manifest = publication_service._promotion_manifest(
        pick,
        transaction_id=transaction_id,
        published_at=NOW,
    )
    event = publication_service._promotion_event(
        pick,
        transaction_id=transaction_id,
        published_at=NOW,
        promotion_content_sha256=(
            publication_service._promotion_content_sha256(pick)
        ),
    )
    LifecycleWriter(
        tmp_path / "data" / "lifecycle",
        clock=FixedClock(NOW),
    ).commit_segment(manifest, (event,))


def _prepared_publication(
    candidate: dict[str, object] | OfficialPickPromotionRequest,
    review: Any,
    *,
    transaction_id: str,
    pick_id: str = PICK_ID,
) -> tuple[Any, EventEnvelope]:
    request = (
        candidate
        if isinstance(candidate, OfficialPickPromotionRequest)
        else OfficialPickPromotionRequest.from_mapping(candidate)
    )
    pick = publication_service._build_pick(
        request,
        pick_id=pick_id,
        published_at=NOW,
        idempotency_key=publication_service.official_pick_idempotency_key(
            request,
            review_id=review.review_id,
        ),
        promotion_actor="operator.alice",
        review=review,
    )
    manifest = publication_service._promotion_manifest(
        pick,
        transaction_id=transaction_id,
        published_at=NOW,
    )
    event = publication_service._promotion_event(
        pick,
        transaction_id=transaction_id,
        published_at=NOW,
        promotion_content_sha256=(
            publication_service._promotion_content_sha256(pick)
        ),
    )
    return manifest, event


def _assert_no_publication_artifacts(lifecycle_root: Path) -> None:
    assert read_official_picks(lifecycle_root) == ()
    assert not (lifecycle_root / ".writer.lock").exists()
    assert not tuple(lifecycle_root.rglob(".*.tmp-*"))
    assert all(
        event.event_type != EventType.OFFICIAL_PICK_PUBLISHED.value
        for segment in lifecycle_root.glob("ledger/*/*/*/*")
        if segment.is_dir()
        for event in read_segment_events(segment)
    )


def _benign_segment(
    reviewed: Any,
    *,
    transaction_id: str,
) -> tuple[RunManifest, EventEnvelope]:
    original_manifest = RunManifest.from_dict(
        json.loads(
            (
                reviewed.ledger_segment_directory / "run_manifest.json"
            ).read_text(encoding="utf-8")
        )
    )
    manifest = replace(
        original_manifest,
        prediction_run_id=transaction_id,
        parent_run_id=original_manifest.prediction_run_id,
    )
    event = EventEnvelope.create(
        event_type=EventType.RUN_STARTED,
        payload={"status": "STARTED"},
        payload_schema_version=1,
        prediction_run_id=transaction_id,
        event_sequence=1,
        occurred_at_utc=NOW,
        recorded_at_utc=NOW,
        operating_date=manifest.operating_date,
        operating_timezone=manifest.operating_timezone,
        actor_type="SYSTEM",
        actor_id="capability.test",
        correlation_id=transaction_id,
        idempotency_key=f"RUN_STARTED:{transaction_id}",
    )
    return manifest, event


SUCCESS = "SUCCESS"
EXPECTED_CONFLICT = "EXPECTED_CONFLICT"
EXPECTED_TRANSITION_REJECTION = "EXPECTED_TRANSITION_REJECTION"
EXPECTED_WRITER_BUSY = "EXPECTED_WRITER_BUSY"
UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


@dataclass(frozen=True, slots=True)
class _RaceWorkerSpec:
    case_name: str
    attempt_id: str
    worker_id: str
    operation_requested: str
    lifecycle_root: str
    candidate: dict[str, object]
    review_id: str
    candidate_id: str
    decision_attempted: str
    review_run_id: str
    transaction_id: str
    pick_id: str | None = None
    injected_behavior: str | None = None


@dataclass(frozen=True, slots=True)
class _RaceWorkerResult:
    case_name: str
    attempt_id: str
    worker_id: str
    process_id: int
    operation_requested: str
    outcome_category: str
    success_value: dict[str, Any] | None
    exception_module: str | None
    exception_class: str | None
    exception_message: str | None
    full_traceback: str | None
    process_started_at_utc: str
    process_completed_at_utc: str
    lifecycle_root: str
    review_id: str
    candidate_id: str
    decision_attempted: str


@dataclass(frozen=True, slots=True)
class _RaceExpectation:
    case_name: str
    attempt_id: str
    worker_specs: tuple[_RaceWorkerSpec, ...]
    permitted_non_successes: frozenset[tuple[str, str, str]]
    valid_success_counts: frozenset[int]
    valid_completed_segment_counts: frozenset[int]
    final_state_kind: str


@dataclass(frozen=True, slots=True)
class _RaceAttempt:
    expectation: _RaceExpectation
    results: tuple[Any, ...]
    exit_codes: dict[str, int | None]
    timed_out_worker_ids: tuple[str, ...]
    start_failures: dict[str, str]
    started_worker_ids: tuple[str, ...]
    attempt_started_at_utc: str
    attempt_completed_at_utc: str
    reconstructed_state: dict[str, Any]


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _exact_exception_outcome(exc: Exception) -> str:
    exact_type = type(exc)
    if exact_type in {
        OfficialPickReviewConflictError,
        OfficialPickConflictError,
    }:
        return EXPECTED_CONFLICT
    if exact_type is OfficialPickReviewTransitionError:
        return EXPECTED_TRANSITION_REJECTION
    if exact_type is LifecycleWriterBusyError:
        return EXPECTED_WRITER_BUSY
    return UNEXPECTED_ERROR


def _worker_exception_result(
    spec: _RaceWorkerSpec,
    *,
    process_id: int,
    process_started_at_utc: str,
    exc: Exception,
    full_traceback: str,
) -> _RaceWorkerResult:
    return _RaceWorkerResult(
        case_name=spec.case_name,
        attempt_id=spec.attempt_id,
        worker_id=spec.worker_id,
        process_id=process_id,
        operation_requested=spec.operation_requested,
        outcome_category=_exact_exception_outcome(exc),
        success_value=None,
        exception_module=type(exc).__module__,
        exception_class=type(exc).__name__,
        exception_message=str(exc),
        full_traceback=full_traceback,
        process_started_at_utc=process_started_at_utc,
        process_completed_at_utc=_utc_timestamp(),
        lifecycle_root=spec.lifecycle_root,
        review_id=spec.review_id,
        candidate_id=spec.candidate_id,
        decision_attempted=spec.decision_attempted,
    )


def _multiprocess_race_worker(
    start_event: Any,
    result_queue: Any,
    spec: _RaceWorkerSpec,
) -> None:
    process_started_at_utc = _utc_timestamp()
    process_id = os.getpid()
    if not start_event.wait(10):
        exc = TimeoutError("parent did not release the race start event")
        result_queue.put(
            _worker_exception_result(
                spec,
                process_id=process_id,
                process_started_at_utc=process_started_at_utc,
                exc=exc,
                full_traceback=(
                    "TimeoutError: parent did not release the race start event"
                ),
            )
        )
        return
    try:
        if spec.injected_behavior == "MISSING_RESULT":
            return
        if spec.injected_behavior == "NONZERO_EXIT":
            raise SystemExit(23)
        if spec.injected_behavior == "TYPE_ERROR":
            raise TypeError("injected race-harness TypeError")
        if spec.injected_behavior == "RUNTIME_ERROR":
            raise RuntimeError("injected race-harness RuntimeError")
        if spec.injected_behavior == "LIFECYCLE_WRITER_ERROR":
            raise LifecycleWriterError(
                "injected race-harness LifecycleWriterError"
            )
        if spec.operation_requested == "REVIEW":
            result = review_official_pick_candidate(
                spec.candidate,
                operator_decision=spec.decision_attempted,
                operator_id=(
                    "operator.multiprocess."
                    f"{spec.decision_attempted.lower()}"
                ),
                decision_reason=(
                    f"multiprocess {spec.decision_attempted.lower()} race"
                ),
                review_run_id=spec.review_run_id,
                lifecycle_root=Path(spec.lifecycle_root),
                clock=FixedClock(NOW),
                review_id_factory=lambda: spec.review_id,
                transaction_id_factory=lambda: spec.transaction_id,
            )
            success_value = {
                "review_id": result.review.review_id,
                "candidate_id": result.review.source_candidate_id,
                "decision": result.review.operator_decision,
                "publication_status": result.publication_status,
                "ledger_segment_directory": str(
                    result.ledger_segment_directory
                ),
            }
        elif spec.operation_requested == "PROMOTE":
            if spec.pick_id is None:
                raise AssertionError("promotion worker requires pick_id")
            result = promote_candidate_to_official_pick(
                spec.candidate,
                review_id=spec.review_id,
                lifecycle_root=Path(spec.lifecycle_root),
                clock=FixedClock(NOW),
                pick_id_factory=lambda: spec.pick_id,
                transaction_id_factory=lambda: spec.transaction_id,
            )
            success_value = {
                "pick_id": result.pick.pick_id,
                "review_id": result.pick.review_id,
                "candidate_id": result.pick.source_candidate_id,
                "designation": result.pick.designation,
                "publication_status": result.publication_status,
                "ledger_segment_directory": str(
                    result.ledger_segment_directory
                ),
            }
        else:
            raise AssertionError(
                f"unsupported race operation: {spec.operation_requested}"
            )
    except Exception as exc:
        worker_result = _worker_exception_result(
            spec,
            process_id=process_id,
            process_started_at_utc=process_started_at_utc,
            exc=exc,
            full_traceback=traceback.format_exc(),
        )
    else:
        worker_result = _RaceWorkerResult(
            case_name=spec.case_name,
            attempt_id=spec.attempt_id,
            worker_id=spec.worker_id,
            process_id=process_id,
            operation_requested=spec.operation_requested,
            outcome_category=SUCCESS,
            success_value=success_value,
            exception_module=None,
            exception_class=None,
            exception_message=None,
            full_traceback=None,
            process_started_at_utc=process_started_at_utc,
            process_completed_at_utc=_utc_timestamp(),
            lifecycle_root=spec.lifecycle_root,
            review_id=spec.review_id,
            candidate_id=spec.candidate_id,
            decision_attempted=spec.decision_attempted,
        )
    result_queue.put(worker_result)
    if spec.injected_behavior == "DUPLICATE_RESULT":
        result_queue.put(worker_result)


def test_review_requires_model_candidate_and_rejects_observations_and_legacy(
    tmp_path: Path,
) -> None:
    with pytest.raises(OfficialPickReviewValidationError, match="MODEL_CANDIDATE"):
        _review(tmp_path, _candidate(record_kind="MARKET_OBSERVATION"))
    with pytest.raises(OfficialPickReviewValidationError, match="MODEL_CANDIDATE"):
        _review(tmp_path, _candidate(record_kind=None))
    with pytest.raises(OfficialPickReviewValidationError):
        _review(
            tmp_path,
            _candidate(
                record_kind="MARKET_OBSERVATION",
                source_candidate_id=None,
                source_observation_id="quote-001",
            ),
        )
    assert read_official_pick_candidate_reviews(
        tmp_path / "data" / "lifecycle"
    ) == ()


@pytest.mark.parametrize(
    "decision",
    ["APPROVED", "REJECTED", "DEFERRED", "EXPIRED"],
)
def test_supported_decisions_publish_frozen_review(
    tmp_path: Path,
    decision: str,
) -> None:
    review_id = {
        "APPROVED": "review_11111111111111111111111111111111",
        "REJECTED": "review_22222222222222222222222222222222",
        "DEFERRED": "review_33333333333333333333333333333333",
        "EXPIRED": "review_44444444444444444444444444444444",
    }[decision]
    result = _review(
        tmp_path,
        decision=decision,
        review_id=review_id,
    )

    assert result.publication_status == "PUBLISHED"
    assert result.review.review_status == "COMMITTED"
    assert result.review.operator_decision == decision
    assert result.review.approved_designation == "PAPER"
    assert result.review.record_kind == "OFFICIAL_PICK_CANDIDATE_REVIEW"
    assert result.review.candidate_snapshot["record_kind"] == "MODEL_CANDIDATE"
    assert result.review.candidate_snapshot["designation"] == "PAPER"


@pytest.mark.parametrize("designation", ["PAPER", "RESEARCH"])
def test_approved_designation_is_frozen_and_published_exactly(
    tmp_path: Path,
    designation: str,
) -> None:
    candidate = _candidate(designation=designation)
    reviewed = _review(tmp_path, candidate)
    promoted = _promote(
        tmp_path,
        reviewed.review.review_id,
        candidate,
    )

    assert reviewed.review.approved_designation == designation
    assert reviewed.review.candidate_snapshot["designation"] == designation
    assert promoted.pick.designation == designation


@pytest.mark.parametrize(
    ("approved", "requested"),
    [("PAPER", "RESEARCH"), ("RESEARCH", "PAPER")],
)
def test_promotion_cannot_change_the_approved_designation(
    tmp_path: Path,
    approved: str,
    requested: str,
) -> None:
    approved_candidate = _candidate(designation=approved)
    reviewed = _review(tmp_path, approved_candidate)

    with pytest.raises(
        OfficialPickPromotionAuthorizationError,
        match="approved frozen snapshot",
    ):
        _promote(
            tmp_path,
            reviewed.review.review_id,
            _candidate(designation=requested),
        )
    assert read_official_picks(tmp_path / "data" / "lifecycle") == ()


def test_designation_participates_in_snapshot_hash_and_idempotency() -> None:
    paper = OfficialPickPromotionRequest.from_mapping(
        _candidate(designation="PAPER")
    )
    research = OfficialPickPromotionRequest.from_mapping(
        _candidate(designation="RESEARCH")
    )

    assert canonical_equality_sha256(
        paper.to_candidate_snapshot()
    ) != canonical_equality_sha256(research.to_candidate_snapshot())
    assert review_service.official_pick_review_idempotency_key(
        paper,
        review_run_id="review-run-001",
        operator_decision="APPROVED",
    ) != review_service.official_pick_review_idempotency_key(
        research,
        review_run_id="review-run-001",
        operator_decision="APPROVED",
    )
    assert publication_service.official_pick_idempotency_key(
        paper,
        review_id=REVIEW_ID,
    ) != publication_service.official_pick_idempotency_key(
        research,
        review_id=REVIEW_ID,
    )


def test_designation_change_requires_a_new_candidate_and_review(
    tmp_path: Path,
) -> None:
    paper = _candidate(designation="PAPER")
    paper_review = _review(tmp_path, paper)
    paper_pick = _promote(tmp_path, paper_review.review.review_id, paper)
    research = _candidate(
        source_candidate_id="nba-candidate-002",
        designation="RESEARCH",
    )
    research_review = _review(
        tmp_path,
        research,
        review_run_id="review-run-002",
        review_id="review_22222222222222222222222222222222",
        transaction_id_factory=lambda: "official-pick-review-research-002",
    )
    research_pick = _promote(
        tmp_path,
        research_review.review.review_id,
        research,
        pick_id_factory=lambda: "pick_22222222222222222222222222222222",
        transaction_id_factory=lambda: "official-pick-promotion-002",
    )

    assert paper_pick.pick.designation == "PAPER"
    assert research_pick.pick.designation == "RESEARCH"
    assert len(read_official_picks(tmp_path / "data" / "lifecycle")) == 2


def test_review_identity_and_snapshot_are_immutable_and_detached(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    result = _review(tmp_path, candidate)
    candidate["odds"] = -105

    assert result.review.candidate_snapshot["odds"] == -110
    assert result.review.review_id == REVIEW_ID
    with pytest.raises(FrozenInstanceError):
        result.review.review_id = "review_ffffffffffffffffffffffffffffffff"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.review.candidate_snapshot["odds"] = -105  # type: ignore[index]


def test_review_replay_is_no_op_and_conflicting_replay_fails(
    tmp_path: Path,
) -> None:
    first = _review(tmp_path)
    second = _review(
        tmp_path,
        review_id="review_ffffffffffffffffffffffffffffffff",
    )

    assert second.publication_status == "ALREADY_PUBLISHED"
    assert second.review == first.review
    with pytest.raises(
        OfficialPickReviewConflictError,
        match="IDEMPOTENCY_CONFLICT",
    ):
        _review(tmp_path, decision="REJECTED")
    assert len(
        read_official_pick_candidate_reviews(
            tmp_path / "data" / "lifecycle"
        )
    ) == 1


def test_candidate_id_reuse_and_mutation_after_review_are_rejected(
    tmp_path: Path,
) -> None:
    _review(tmp_path, decision="APPROVED")
    mutated = _candidate(odds=-105)

    with pytest.raises(
        OfficialPickReviewConflictError,
        match="SOURCE_CANDIDATE_ID_REUSE",
    ):
        _review(
            tmp_path,
            mutated,
            decision="APPROVED",
            review_run_id="review-run-002",
        )
    with pytest.raises(
        OfficialPickPromotionAuthorizationError,
        match="frozen snapshot",
    ):
        _promote(tmp_path, candidate=mutated)


@pytest.mark.parametrize(
    ("reviewed_value", "replayed_value"),
    [
        (1, 1.0),
        (True, 1),
        ("1", 1),
        ({"nested": [1]}, {"nested": [1.0]}),
    ],
)
def test_candidate_reuse_uses_type_preserving_canonical_equality(
    tmp_path: Path,
    reviewed_value: object,
    replayed_value: object,
) -> None:
    reviewed_candidate = _candidate(
        provenance={"adversarial": reviewed_value}
    )
    _review(tmp_path, reviewed_candidate)

    with pytest.raises(
        OfficialPickReviewConflictError,
        match="SOURCE_CANDIDATE_ID_REUSE",
    ):
        _review(
            tmp_path,
            _candidate(provenance={"adversarial": replayed_value}),
        )


def test_candidate_replay_treats_list_tuple_and_dict_order_as_equivalent(
    tmp_path: Path,
) -> None:
    first = _review(
        tmp_path,
        _candidate(
            provenance={
                "ordered": {"left": 1, "right": 2},
                "sequence": ["a", "b"],
            }
        ),
    )
    replay = _review(
        tmp_path,
        _candidate(
            provenance={
                "sequence": ("a", "b"),
                "ordered": {"right": 2, "left": 1},
            }
        ),
        review_id="review_ffffffffffffffffffffffffffffffff",
    )

    assert replay.publication_status == "ALREADY_PUBLISHED"
    assert replay.review == first.review


def test_promotion_authorization_rejects_nested_numeric_type_change(
    tmp_path: Path,
) -> None:
    reviewed = _review(
        tmp_path,
        _candidate(provenance={"nested": {"value": 1}}),
    )

    with pytest.raises(
        OfficialPickPromotionAuthorizationError,
        match="approved frozen snapshot",
    ):
        _promote(
            tmp_path,
            reviewed.review.review_id,
            _candidate(provenance={"nested": {"value": 1.0}}),
        )


def test_deferred_requires_new_review_slot_and_final_review_cannot_be_replaced(
    tmp_path: Path,
) -> None:
    _review(tmp_path, decision="DEFERRED")
    with pytest.raises(OfficialPickReviewTransitionError):
        _review(tmp_path, decision="APPROVED")

    approved = _review(
        tmp_path,
        decision="APPROVED",
        review_run_id="review-run-002",
        review_id="review_22222222222222222222222222222222",
    )
    assert approved.review.operator_decision == "APPROVED"
    with pytest.raises(OfficialPickReviewConflictError, match="IDEMPOTENCY_CONFLICT"):
        _review(
            tmp_path,
            decision="REJECTED",
            review_run_id="review-run-003",
        )


def test_review_publication_rolls_back_and_rereads_committed_event(
    tmp_path: Path,
) -> None:
    def fail_at(stage: str) -> None:
        if stage == "after_data_files_written":
            raise RuntimeError("injected review failure")

    with pytest.raises(RuntimeError, match="injected review failure"):
        _review(tmp_path, failure_hook=fail_at)

    root = tmp_path / "data" / "lifecycle"
    assert read_official_pick_candidate_reviews(root) == ()
    assert list(root.rglob("COMPLETE")) == []


def test_tampered_review_segment_is_rejected(
    tmp_path: Path,
) -> None:
    result = _review(tmp_path)
    events_path = result.ledger_segment_directory / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace(
            "fixture approved decision",
            "tampered decision",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        OfficialPickReviewLedgerIntegrityError,
        match="failed verification",
    ):
        read_official_pick_candidate_reviews(
            tmp_path / "data" / "lifecycle"
        )


def test_review_service_rereads_and_verifies_after_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = review_service._review_event_in_segment

    def read_then_tamper(*args, **kwargs):
        event = original_read(*args, **kwargs)
        path = Path(args[0]) / "events.jsonl"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "fixture approved decision",
                "tampered after commit",
            ),
            encoding="utf-8",
        )
        return event

    monkeypatch.setattr(
        review_service,
        "_review_event_in_segment",
        read_then_tamper,
    )
    with pytest.raises(
        OfficialPickReviewLedgerIntegrityError,
        match="failed verification",
    ):
        _review(tmp_path)


def test_promotion_requires_committed_approved_matching_review(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        OfficialPickPromotionAuthorizationError,
        match="review_id",
    ):
        promote_candidate_to_official_pick(
            _candidate(),
            lifecycle_root=tmp_path / "data" / "lifecycle",
        )
    with pytest.raises(
        OfficialPickPromotionAuthorizationError,
        match="not committed",
    ):
        _promote(
            tmp_path,
            review_id="review_ffffffffffffffffffffffffffffffff",
        )

    for decision in ("REJECTED", "DEFERRED", "EXPIRED"):
        case = tmp_path / decision.lower()
        result = _review(case, decision=decision)
        with pytest.raises(
            OfficialPickPromotionAuthorizationError,
            match="APPROVED",
        ):
            _promote(case, review_id=result.review.review_id)


def test_direct_writer_rejects_review_missing_from_target_root(
    tmp_path: Path,
) -> None:
    source_review = _review(tmp_path / "source")
    target_root = tmp_path / "target" / "data" / "lifecycle"
    manifest, event = _prepared_publication(
        _candidate(),
        source_review.review,
        transaction_id="unauthorized-other-root",
    )

    with pytest.raises(
        LifecycleIntegrityError,
        match="unauthorized",
    ):
        LifecycleWriter(
            target_root,
            clock=FixedClock(NOW),
        ).commit_segment(manifest, (event,))

    _assert_no_publication_artifacts(target_root)


def test_direct_writer_rejects_unknown_review_id_before_commit(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    candidate = OfficialPickPromotionRequest.from_mapping(_candidate())
    _, valid_event = _prepared_publication(
        candidate,
        reviewed.review,
        transaction_id="unknown-review-id",
    )
    payload = json.loads(valid_event.payload_json)
    unknown_review_id = "review_ffffffffffffffffffffffffffffffff"
    payload["official_pick"]["review_id"] = unknown_review_id
    payload["official_pick"]["provenance"]["review_id"] = unknown_review_id
    altered_pick = OfficialPick.from_dict(payload["official_pick"])
    manifest = publication_service._promotion_manifest(
        altered_pick,
        transaction_id=valid_event.prediction_run_id,
        published_at=NOW,
    )
    event = publication_service._promotion_event(
        altered_pick,
        transaction_id=valid_event.prediction_run_id,
        published_at=NOW,
        promotion_content_sha256=(
            publication_service._promotion_content_sha256(altered_pick)
        ),
    )
    root = tmp_path / "data" / "lifecycle"

    with pytest.raises(
        LifecycleIntegrityError,
        match="unauthorized",
    ):
        LifecycleWriter(root, clock=FixedClock(NOW)).commit_segment(
            manifest,
            (event,),
        )

    _assert_no_publication_artifacts(root)


@pytest.mark.parametrize("decision", ["REJECTED", "DEFERRED", "EXPIRED"])
def test_direct_writer_rejects_nonapproved_committed_review(
    tmp_path: Path,
    decision: str,
) -> None:
    reviewed = _review(tmp_path, decision=decision)
    approved_claim = replace(
        reviewed.review,
        operator_decision="APPROVED",
    )
    manifest, event = _prepared_publication(
        _candidate(),
        approved_claim,
        transaction_id=f"unauthorized-{decision.lower()}",
    )
    root = tmp_path / "data" / "lifecycle"

    with pytest.raises(
        LifecycleIntegrityError,
        match="unauthorized",
    ):
        LifecycleWriter(root, clock=FixedClock(NOW)).commit_segment(
            manifest,
            (event,),
        )

    _assert_no_publication_artifacts(root)


def test_direct_writer_rejects_tampered_review_before_staging(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    manifest, event = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id="unauthorized-tampered-review",
    )
    root = tmp_path / "data" / "lifecycle"
    completed_before = tuple(root.rglob("COMPLETE"))
    events_path = reviewed.ledger_segment_directory / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace(
            "fixture approved decision",
            "tampered review authority",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        LifecycleIntegrityError,
        match="unauthorized",
    ):
        LifecycleWriter(root, clock=FixedClock(NOW)).commit_segment(
            manifest,
            (event,),
        )

    assert tuple(root.rglob("COMPLETE")) == completed_before
    assert not (root / ".writer.lock").exists()
    assert not tuple(root.rglob(".*.tmp-*"))
    assert all(
        '"event_type":"OFFICIAL_PICK_PUBLISHED"' not in events_file.read_text(
            encoding="utf-8"
        )
        for events_file in root.glob("ledger/*/*/*/*/events.jsonl")
    )


def test_writer_enforces_one_review_one_pick_across_segments_and_exact_replay(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root = tmp_path / "data" / "lifecycle"
    first_manifest, first_event = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id="writer-one-review-first",
        pick_id=PICK_ID,
    )
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    first = writer.commit_segment(first_manifest, (first_event,))
    replay = writer.commit_segment(first_manifest, (first_event,))

    assert first.status == "COMMITTED"
    assert replay.status == "ALREADY_COMMITTED"
    assert replay.segment_directory == first.segment_directory

    second_manifest, second_event = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id="writer-one-review-second",
        pick_id="pick_11111111111111111111111111111111",
    )
    with pytest.raises(
        LifecycleIntegrityError,
        match="invalid or unauthorized",
    ):
        writer.commit_segment(second_manifest, (second_event,))

    assert [item.pick_id for item in read_official_picks(root)] == [PICK_ID]
    assert not (root / ".writer.lock").exists()
    assert not tuple(root.rglob(".*.tmp-*"))


def test_writer_rejects_duplicate_review_use_and_pick_identity_in_batches(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root = tmp_path / "data" / "lifecycle"
    manifest, first = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id="writer-duplicate-review-batch",
        pick_id=PICK_ID,
    )
    _, second_raw = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id=manifest.prediction_run_id,
        pick_id="pick_22222222222222222222222222222222",
    )
    second = _event_with_payload(
        second_raw,
        json.loads(second_raw.payload_json),
        event_sequence=2,
        previous_event_hash=first.event_hash,
    )
    with pytest.raises(LifecycleIntegrityError, match="duplicate idempotency"):
        LifecycleWriter(root, clock=FixedClock(NOW)).commit_segment(
            manifest,
            (first, second),
        )
    _assert_no_publication_artifacts(root)

    reviewed_two = _review(
        tmp_path,
        _candidate(
            "nba-candidate-002",
            event_id="nba-game-002",
        ),
        review_run_id="review-run-002",
        review_id="review_22222222222222222222222222222222",
    )
    identity_manifest, identity_first = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id="writer-duplicate-pick-id-batch",
        pick_id=PICK_ID,
    )
    _, identity_second_raw = _prepared_publication(
        _candidate(
            "nba-candidate-002",
            event_id="nba-game-002",
        ),
        reviewed_two.review,
        transaction_id=identity_manifest.prediction_run_id,
        pick_id=PICK_ID,
    )
    identity_second = _event_with_payload(
        identity_second_raw,
        json.loads(identity_second_raw.payload_json),
        event_sequence=2,
        previous_event_hash=identity_first.event_hash,
    )
    with pytest.raises(
        LifecycleIntegrityError,
        match="invalid or unauthorized",
    ):
        LifecycleWriter(root, clock=FixedClock(NOW)).commit_segment(
            identity_manifest,
            (identity_first, identity_second),
        )
    _assert_no_publication_artifacts(root)


@pytest.mark.parametrize("mode", ["wrong_idempotency", "extra", "missing"])
def test_writer_rejects_incorrect_promotion_identity_and_provenance(
    tmp_path: Path,
    mode: str,
) -> None:
    reviewed = _review(tmp_path)
    manifest, event = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id=f"writer-authorization-{mode}",
    )
    pick = OfficialPick.from_dict(
        json.loads(event.payload_json)["official_pick"]
    )
    if mode == "wrong_idempotency":
        forged = replace(
            pick,
            idempotency_key=f"opidem_{'f' * 64}",
        )
    else:
        provenance = dict(pick.provenance)
        if mode == "extra":
            provenance["unreviewed_extra"] = {"bankroll": 1000}
        else:
            provenance.pop("git_commit_sha")
        forged = replace(pick, provenance=provenance)
    forged_hash = publication_service._promotion_content_sha256(forged)
    forged_event = publication_service._promotion_event(
        forged,
        transaction_id=manifest.prediction_run_id,
        published_at=NOW,
        promotion_content_sha256=forged_hash,
    )
    forged_manifest = publication_service._promotion_manifest(
        forged,
        transaction_id=manifest.prediction_run_id,
        published_at=NOW,
    )
    root = tmp_path / "data" / "lifecycle"
    with pytest.raises(
        LifecycleIntegrityError,
        match="invalid or unauthorized",
    ):
        LifecycleWriter(root, clock=FixedClock(NOW)).commit_segment(
            forged_manifest,
            (forged_event,),
        )
    _assert_no_publication_artifacts(root)


def test_generic_writer_enforces_review_state_machine_and_idempotency(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    approved = _review(
        source,
        review_run_id="direct-approved-run",
        review_id="review_33333333333333333333333333333333",
    )
    approved_event = read_segment_events(
        approved.ledger_segment_directory
    )[0]
    approved_manifest = RunManifest.from_dict(
        json.loads(
            (
                approved.ledger_segment_directory / "run_manifest.json"
            ).read_text(encoding="utf-8")
        )
    )

    invalid_payload = json.loads(approved_event.payload_json)
    wrong_key = f"oprevidem_{'f' * 64}"
    invalid_payload["operator_review"]["idempotency_key"] = wrong_key
    invalid_event = _event_with_payload(
        approved_event,
        invalid_payload,
        idempotency_key=wrong_key,
    )
    invalid_root = tmp_path / "invalid-target"
    with pytest.raises(
        LifecycleIntegrityError,
        match="invalid or unauthorized OfficialPick review",
    ):
        LifecycleWriter(
            invalid_root,
            clock=FixedClock(NOW),
        ).commit_segment(approved_manifest, (invalid_event,))
    assert not tuple(completed_segment_directories(invalid_root))
    assert not (invalid_root / ".writer.lock").exists()
    assert not tuple(invalid_root.rglob(".*.tmp-*"))

    target = tmp_path / "target"
    committed = LifecycleWriter(
        target,
        clock=FixedClock(NOW),
    ).commit_segment(approved_manifest, (approved_event,))
    assert committed.status == "COMMITTED"

    conflicting_source = tmp_path / "conflicting-source"
    rejected = _review(
        conflicting_source,
        decision="REJECTED",
        review_run_id="direct-rejected-run",
        review_id="review_44444444444444444444444444444444",
    )
    rejected_event = read_segment_events(
        rejected.ledger_segment_directory
    )[0]
    rejected_manifest = RunManifest.from_dict(
        json.loads(
            (
                rejected.ledger_segment_directory / "run_manifest.json"
            ).read_text(encoding="utf-8")
        )
    )
    with pytest.raises(
        LifecycleIntegrityError,
        match="invalid or unauthorized OfficialPick review",
    ):
        LifecycleWriter(
            target,
            clock=FixedClock(NOW),
        ).commit_segment(rejected_manifest, (rejected_event,))
    assert len(read_official_pick_candidate_reviews(target)) == 1
    assert not (target / ".writer.lock").exists()
    assert not tuple(target.rglob(".*.tmp-*"))


def test_observation_direct_promotion_is_always_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        OfficialPickPromotionAuthorizationError,
        match="MARKET_OBSERVATION",
    ):
        promote_observation_to_official_pick(
            {"record_kind": "MARKET_OBSERVATION"},
            lifecycle_root=tmp_path / "data" / "lifecycle",
        )


def test_approved_candidate_promotes_with_review_evidence(
    tmp_path: Path,
) -> None:
    review = _review(tmp_path)
    result = _promote(tmp_path, review.review.review_id)

    assert result.pick.review_id == review.review.review_id
    assert (
        result.pick.candidate_snapshot_sha256
        == review.review.candidate_snapshot_sha256
    )
    assert result.pick.provenance["review_operator_id"] == "operator.alice"
    assert result.pick.provenance["review_run_id"] == "review-run-001"
    assert result.pick.provenance["review_decision"] == "APPROVED"
    assert result.pick.designation == "PAPER"


def test_promotion_replay_is_no_op_and_conflicting_content_fails(
    tmp_path: Path,
) -> None:
    review = _review(tmp_path)
    first = _promote(tmp_path, review.review.review_id)
    second = _promote(
        tmp_path,
        review.review.review_id,
        pick_id_factory=lambda: "pick_ffffffffffffffffffffffffffffffff",
        transaction_id_factory=lambda: "official-pick-promotion-002",
    )

    assert second.publication_status == "ALREADY_PUBLISHED"
    assert second.pick == first.pick
    with pytest.raises(
        OfficialPickPromotionAuthorizationError,
        match="approved frozen snapshot",
    ):
        _promote(
            tmp_path,
            review.review.review_id,
            _candidate(designation="RESEARCH"),
        )
    assert read_official_picks(tmp_path / "data" / "lifecycle") == (
        first.pick,
    )


def test_concurrent_duplicate_promotion_commits_one_pick(
    tmp_path: Path,
) -> None:
    review = _review(tmp_path)
    barrier = Barrier(2)

    def racing_pick_id() -> str:
        barrier.wait(timeout=5)
        return f"pick_{uuid4().hex}"

    def promote():
        return promote_candidate_to_official_pick(
            _candidate(),
            review_id=review.review.review_id,
            lifecycle_root=tmp_path / "data" / "lifecycle",
            clock=FixedClock(NOW),
            pick_id_factory=racing_pick_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: promote(), range(2)))

    assert sorted(item.publication_status for item in results) == [
        "ALREADY_PUBLISHED",
        "PUBLISHED",
    ]
    assert results[0].pick == results[1].pick
    assert len(read_official_picks(tmp_path / "data" / "lifecycle")) == 1


def test_reporting_separates_review_and_promotion_states(
    tmp_path: Path,
) -> None:
    cases = (
        ("approved-promoted", "APPROVED"),
        ("approved-waiting", "APPROVED"),
        ("rejected", "REJECTED"),
        ("deferred", "DEFERRED"),
        ("expired", "EXPIRED"),
    )
    reviews: dict[str, object] = {}
    for index, (candidate_id, decision) in enumerate(cases, start=1):
        candidate = _candidate(
            candidate_id,
            event_id=f"nba-game-{index:03d}",
        )
        reviews[candidate_id] = _review(
            tmp_path,
            candidate,
            decision=decision,
            review_run_id=f"review-run-{index:03d}",
            review_id=f"review_{index:032x}",
        )
    promoted_review = reviews["approved-promoted"]
    _promote(
        tmp_path,
        promoted_review.review.review_id,  # type: ignore[attr-defined]
        _candidate(
            "approved-promoted",
            event_id="nba-game-001",
        ),
    )

    dataset = build_official_pick_operator_review_dataset(
        lifecycle_root=tmp_path / "data" / "lifecycle"
    )

    assert len(dataset.approved_candidates) == 2
    assert len(dataset.rejected_candidates) == 1
    assert len(dataset.deferred_candidates) == 1
    assert len(dataset.expired_candidates) == 1
    assert len(dataset.approved_not_promoted_candidates) == 1
    assert len(dataset.approved_promoted_candidates) == 1
    promoted = dataset.approved_promoted_candidates[0]
    assert promoted.promoted is True
    assert promoted.pick_id == PICK_ID
    assert promoted.review_id == promoted_review.review.review_id  # type: ignore[attr-defined]
    assert promoted.source_candidate_id == "approved-promoted"
    keys = _nested_keys(dataset.to_dict())
    assert not keys.intersection(
        {
            "roi",
            "expected_profit",
            "bankroll",
            "kelly_fraction",
            "stake",
            "wager_amount",
            "wagering_metadata",
            "live_bet",
            "execution_instructions",
        }
    )
    with pytest.raises(OfficialPickReportBoundaryError):
        require_official_pick_roi_rows(
            (
                read_official_pick_candidate_reviews(
                    tmp_path / "data" / "lifecycle"
                )[0].to_dict(),
            )
        )


def test_review_and_promotion_payloads_match_frozen_json_schemas(
    tmp_path: Path,
) -> None:
    review = _review(tmp_path)
    promotion = _promote(tmp_path, review.review.review_id)

    review_schema = _schema("official_pick_candidate_reviewed_payload_v1.json")
    promotion_schema = _schema("official_pick_published_payload_v2.json")
    envelope_schema = _schema("event_envelope_v1.json")
    for schema in (review_schema, promotion_schema, envelope_schema):
        Draft202012Validator.check_schema(schema)
    review_validator = Draft202012Validator(review_schema)
    promotion_validator = Draft202012Validator(promotion_schema)
    envelope_validator = Draft202012Validator(envelope_schema)
    assert list(review_validator.iter_errors(_payload(review))) == []
    assert list(promotion_validator.iter_errors(_payload(promotion))) == []
    review_event = read_segment_events(review.ledger_segment_directory)[0]
    promotion_event = read_segment_events(
        promotion.ledger_segment_directory
    )[0]
    assert list(envelope_validator.iter_errors(review_event.to_dict())) == []
    assert list(
        envelope_validator.iter_errors(promotion_event.to_dict())
    ) == []
    assert (
        review_event.event_type
        == EventType.OFFICIAL_PICK_CANDIDATE_REVIEWED.value
    )
    assert (
        promotion_event.event_type
        == EventType.OFFICIAL_PICK_PUBLISHED.value
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"record_kind": None},
        {"event_id": ""},
        {"event_start_time": "not-a-time"},
        {"market_key": "mystery_market"},
        {"selection": ""},
        {"line": "nan"},
        {"odds": 0},
        {"sportsbook": "unknown book"},
        {"player_id": None},
        {"player_name": None},
        {"model_name": ""},
        {"model_version": ""},
        {"run_id": ""},
        {"source_candidate_id": None},
    ],
)
def test_malformed_candidate_never_enters_review_ledger(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    with pytest.raises(
        (OfficialPickReviewValidationError, OfficialPickValidationError)
    ):
        _review(tmp_path, _candidate(**updates))
    assert read_official_pick_candidate_reviews(
        tmp_path / "data" / "lifecycle"
    ) == ()


@pytest.mark.parametrize(
    "candidate_updates",
    [
        {"event_id": "nba-game-altered"},
        {"player_id": "nba-player-99"},
        {"designation": "RESEARCH"},
        {"source_candidate_id": "nba-candidate-altered"},
        {"odds": -105},
        {"market_key": "player_rebounds"},
        {"line": 25.5},
        {"selection": "UNDER"},
        {"sportsbook": "FanDuel"},
        {"model_version": "2026.08"},
        {"run_id": "nba-run-altered"},
    ],
)
def test_direct_writer_rejects_pick_fields_altered_after_approval(
    tmp_path: Path,
    candidate_updates: dict[str, object],
) -> None:
    with pytest.raises(
        LifecycleIntegrityError,
        match="unauthorized",
    ):
        _commit_forged_reviewed_pick(
            tmp_path,
            candidate_updates=candidate_updates,
        )

    lifecycle_root = tmp_path / "data" / "lifecycle"
    assert read_official_picks(lifecycle_root) == ()
    assert not (lifecycle_root / ".writer.lock").exists()
    assert not tuple(lifecycle_root.rglob(".*.tmp-*"))


def test_nested_list_provenance_promotes_and_public_contracts_are_deeply_immutable(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        player_name="Kelly O'Neil",
        model_name="kelly-safe-research-model",
        provenance={
            "git_commit_sha": "a" * 40,
            "input_manifest_hash": "b" * 64,
            "lineage": {
                "providers": ["model", "market"],
                "windows": [{"name": "recent", "days": [7, 14]}],
            },
        }
    )
    reviewed = _review(
        tmp_path,
        candidate,
        provenance={"review_notes": {"flags": ["checked", "approved"]}},
    )
    promoted = _promote(
        tmp_path,
        reviewed.review.review_id,
        candidate,
    )

    assert promoted.pick.pick_id == PICK_ID
    assert deepcopy(reviewed.review) == reviewed.review
    assert asdict(reviewed.review)["candidate_snapshot"]["provenance"][
        "lineage"
    ]["providers"] == ["model", "market"]
    with pytest.raises(TypeError):
        reviewed.review.provenance["changed"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        promoted.pick.provenance["changed"] = True  # type: ignore[index]


def test_frozen_json_mapping_has_no_writable_instance_storage(
    tmp_path: Path,
) -> None:
    reviewed = _review(
        tmp_path,
        _candidate(
            provenance={
                "nested": {
                    "numbers": [1, 2],
                    "metadata": {"approved": True},
                }
            }
        ),
    )
    frozen = reviewed.review.candidate_snapshot
    replacement = (("odds", -105),)
    baseline_hash = canonical_equality_sha256(reviewed.review.to_dict())

    assert not isinstance(frozen, dict)
    assert not hasattr(frozen, "__dict__")
    assert not hasattr(frozen, "_data")
    with pytest.raises(AttributeError):
        frozen._data = {}  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(
            frozen,
            "_FrozenJSONDict__entries",
            replacement,
        )
    internal_names = {
        "_FrozenJSONDict__entries",
        "_FrozenJSONDict__sealed",
        "__entries",
        "__sealed",
        "_entries",
        "_data",
        "entries",
        *(
            name
            for name in dir(frozen)
            if name.startswith("_")
        ),
    }
    for internal_name in sorted(internal_names):
        with pytest.raises((AttributeError, TypeError)):
            object.__setattr__(frozen, internal_name, replacement)
    with pytest.raises(TypeError):
        hash(frozen)
    with pytest.raises(TypeError):
        frozen["odds"] = -105  # type: ignore[index]
    for method_name, args in (
        ("update", ({"odds": -105},)),
        ("setdefault", ("late", True)),
        ("pop", ("odds",)),
        ("clear", ()),
    ):
        with pytest.raises((AttributeError, TypeError)):
            getattr(frozen, method_name)(*args)
    with pytest.raises(TypeError):
        dict.__setitem__(frozen, "odds", -105)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        frozen["provenance"]["nested"]["metadata"]["approved"] = False  # type: ignore[index]
    with pytest.raises((AttributeError, TypeError)):
        frozen["provenance"]["nested"]["numbers"].append(3)  # type: ignore[union-attr]
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(
            reviewed.review.provenance,
            "_FrozenJSONDict__entries",
            replacement,
        )

    detached = reviewed.review.to_dict()
    detached["candidate_snapshot"]["provenance"]["nested"]["numbers"].append(3)
    detached["candidate_snapshot"]["odds"] = -105
    assert reviewed.review.candidate_snapshot["odds"] == -110
    assert reviewed.review.candidate_snapshot["provenance"]["nested"][
        "numbers"
    ] == (1, 2)

    copied = deepcopy(reviewed.review)
    assert copied == reviewed.review
    assert copied.candidate_snapshot is not reviewed.review.candidate_snapshot
    with pytest.raises(TypeError):
        copied.candidate_snapshot["odds"] = -105  # type: ignore[index]
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(
            copied.candidate_snapshot,
            "_FrozenJSONDict__entries",
            replacement,
        )
    detached_copy = deepcopy(reviewed.review.to_dict())
    detached_copy["candidate_snapshot"]["odds"] = -105
    detached_copy["provenance"]["copied"] = True
    assert copied.candidate_snapshot["odds"] == -110
    assert "copied" not in reviewed.review.provenance
    shallow_mapping = copy(frozen)
    assert shallow_mapping == frozen
    assert shallow_mapping is not frozen
    converted = asdict(reviewed.review)
    assert type(converted["candidate_snapshot"]) is dict
    assert type(converted["provenance"]) is dict
    assert type(
        converted["candidate_snapshot"]["provenance"]["nested"]["numbers"]
    ) is list
    assert converted["candidate_snapshot"] is not reviewed.review.candidate_snapshot
    decoded = json.loads(json.dumps(converted))
    assert type(decoded["candidate_snapshot"]) is dict
    assert type(decoded["provenance"]) is dict
    json.loads(json.dumps(reviewed.review.to_dict()))
    assert canonical_equality_sha256(reviewed.review.to_dict()) == baseline_hash


def test_frozen_json_mapping_is_not_text_and_rejects_non_string_keys(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    frozen = reviewed.review.candidate_snapshot

    assert isinstance(frozen, FrozenJSONDict)
    assert not isinstance(frozen, str)
    assert frozen["odds"] == -110
    assert "odds" in frozen
    assert 0 not in frozen
    assert list(frozen) == sorted(reviewed.review.to_dict()["candidate_snapshot"])
    assert len(frozen) == len(reviewed.review.to_dict()["candidate_snapshot"])
    assert dict(frozen)["odds"] == -110
    assert frozen == reviewed.review.to_dict()["candidate_snapshot"]
    assert frozen == dict(reversed(tuple(frozen.items())))

    with pytest.raises(KeyError):
        frozen["missing"]
    for key in (0, slice(0, 1), b"odds", ("odds",), object()):
        with pytest.raises(TypeError, match="keys must be strings"):
            frozen[key]  # type: ignore[index]
    with pytest.raises(TypeError):
        json.dumps(frozen)


def test_review_pick_and_reporting_serialization_routes_emit_json_objects(
    tmp_path: Path,
) -> None:
    reviewed = _review(
        tmp_path,
        _candidate(provenance={"nested": {"values": [1, 2]}}),
        provenance={"operator_context": {"checks": ["identity", "market"]}},
    )
    promoted = _promote(
        tmp_path,
        reviewed.review.review_id,
        _candidate(provenance={"nested": {"values": [1, 2]}}),
    )
    report = build_official_pick_operator_review_dataset(
        lifecycle_root=tmp_path / "data" / "lifecycle"
    )

    review_dict = reviewed.review.to_dict()
    pick_dict = promoted.pick.to_dict()
    report_dict = report.to_dict()
    review_dataclass = asdict(reviewed.review)
    report_dataclass = asdict(report)

    assert type(review_dict["candidate_snapshot"]) is dict
    assert type(review_dict["provenance"]) is dict
    assert type(pick_dict["provenance"]) is dict
    assert type(review_dataclass["candidate_snapshot"]) is dict
    assert type(review_dataclass["provenance"]) is dict
    assert type(
        review_dataclass["candidate_snapshot"]["provenance"]["nested"]["values"]
    ) is list
    assert type(report_dict) is dict
    assert type(report_dataclass) is dict

    for value in (
        review_dict,
        pick_dict,
        report_dict,
        review_dataclass,
        report_dataclass,
    ):
        assert json.loads(json.dumps(value))

    with pytest.raises(TypeError):
        json.dumps(reviewed.review.candidate_snapshot)
    with pytest.raises(TypeError):
        json.dumps(reviewed.review.provenance)


def test_public_commit_api_rejects_unentered_and_forged_lock_objects(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    manifest, event = _benign_segment(
        reviewed,
        transaction_id="forged-held-lock",
    )
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    lock = LifecycleWriterLock(
        root,
        prediction_run_id=manifest.prediction_run_id,
        command="forged lock test",
        clock=FixedClock(NOW),
    )

    assert "held_lock" not in inspect.signature(
        LifecycleWriter.commit_segment
    ).parameters
    with pytest.raises(TypeError, match="held_lock"):
        getattr(writer, "commit_segment")(
            manifest,
            (event,),
            held_lock=lock,
        )
    lock._fd = 12345
    with pytest.raises(TypeError, match="held_lock"):
        getattr(writer, "commit_segment")(
            manifest,
            (event,),
            held_lock=lock,
        )
    lock._fd = None
    assert not (root / ".writer.lock").exists()


def test_unlocked_internal_commit_primitive_is_absent_and_cannot_commit(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    manifest, event = _benign_segment(
        reviewed,
        transaction_id="outside-lock-commit-attempt",
    )
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    before = completed_segment_directories(root)

    assert "_commit_prepared_while_locked" not in vars(LifecycleWriter)
    with pytest.raises(AttributeError):
        getattr(writer, "_commit_prepared_while_locked")(
            manifest,
            (event,),
            evidence_objects=(),
            failure_hook=None,
        )

    assert completed_segment_directories(root) == before
    assert not (root / ".writer.lock").exists()
    assert not tuple(root.rglob(".*.tmp-*"))


def test_preparation_callback_cannot_commit_inner_segment_or_create_two(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(
        root,
        clock=FixedClock(NOW),
        lock_timeout_seconds=0.01,
    )
    inner_manifest, inner_event = _benign_segment(
        reviewed,
        transaction_id="callback-inner-commit",
    )
    outer_manifest, outer_event = _benign_segment(
        reviewed,
        transaction_id="callback-outer-commit",
    )
    callback_calls = 0

    def prepare():
        nonlocal callback_calls
        callback_calls += 1
        with pytest.raises(LifecycleWriterBusyError):
            writer.commit_segment(inner_manifest, (inner_event,))
        assert all(
            path.name != inner_manifest.prediction_run_id
            for path in completed_segment_directories(root)
        )
        return outer_manifest, (outer_event,), ()

    committed = writer.run_locked_transaction(
        prediction_run_id=outer_manifest.prediction_run_id,
        prepare=prepare,
        command="callback cannot commit",
    )

    assert committed is not None
    assert committed.status == "COMMITTED"
    assert callback_calls == 1
    completed = completed_segment_directories(root)
    assert sum(
        path.name == outer_manifest.prediction_run_id
        for path in completed
    ) == 1
    assert all(
        path.name != inner_manifest.prediction_run_id
        for path in completed
    )
    assert len(completed) == 2
    assert not (root / ".writer.lock").exists()


def test_transaction_rejects_unrestored_root_mutation_without_retargeting(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root_a = tmp_path / "data" / "lifecycle"
    root_b = tmp_path / "alternate" / "lifecycle"
    writer = LifecycleWriter(root_a, clock=FixedClock(NOW))
    manifest, event = _benign_segment(
        reviewed,
        transaction_id="callback-retarget-rejected",
    )
    before_a = completed_segment_directories(root_a)

    def prepare():
        assert (root_a / ".writer.lock").is_file()
        assert not (root_b / ".writer.lock").exists()
        writer.root = root_b
        return manifest, (event,), ()

    with pytest.raises(
        LifecycleIntegrityError,
        match=r"writer\.root was mutated",
    ):
        writer.run_locked_transaction(
            prediction_run_id=manifest.prediction_run_id,
            prepare=prepare,
            command="callback root mutation rejected",
        )

    assert completed_segment_directories(root_a) == before_a
    assert completed_segment_directories(root_b) == ()
    assert not tuple(root_a.rglob(".*.tmp-*"))
    assert not tuple(root_b.rglob(".*.tmp-*"))
    assert not (root_a / ".writer.lock").exists()
    assert not root_b.exists()

    writer.root = root_a
    recovered = writer.commit_segment(manifest, (event,))
    assert recovered.segment_directory.is_relative_to(root_a)


def test_transaction_remains_bound_when_callback_changes_and_restores_root(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root_a = tmp_path / "data" / "lifecycle"
    root_b = tmp_path / "alternate" / "lifecycle"
    writer = LifecycleWriter(root_a, clock=FixedClock(NOW))
    manifest, event = _benign_segment(
        reviewed,
        transaction_id="callback-retarget-restored",
    )
    before_a = len(completed_segment_directories(root_a))

    def prepare():
        assert (root_a / ".writer.lock").is_file()
        writer.root = root_b
        writer.root = root_a
        return manifest, (event,), ()

    committed = writer.run_locked_transaction(
        prediction_run_id=manifest.prediction_run_id,
        prepare=prepare,
        command="callback root mutation restored",
    )

    assert committed is not None
    assert committed.segment_directory.is_relative_to(root_a.resolve())
    assert len(completed_segment_directories(root_a)) == before_a + 1
    assert completed_segment_directories(root_b) == ()
    assert not root_b.exists()
    assert not tuple(root_a.rglob(".*.tmp-*"))


def test_same_writer_rejects_alternate_root_nested_commit_before_staging(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root_a = tmp_path / "data" / "lifecycle"
    root_b = tmp_path / "alternate" / "lifecycle"
    writer = LifecycleWriter(root_a, clock=FixedClock(NOW))
    inner_manifest, inner_event = _benign_segment(
        reviewed,
        transaction_id="alternate-root-inner",
    )
    outer_manifest, outer_event = _benign_segment(
        reviewed,
        transaction_id="alternate-root-outer",
    )
    before_a = len(completed_segment_directories(root_a))

    def prepare():
        writer.root = root_b
        try:
            with pytest.raises(
                LifecycleWriterReentrancyError,
                match="active write",
            ):
                writer.commit_segment(inner_manifest, (inner_event,))
        finally:
            writer.root = root_a
        assert not root_b.exists()
        return outer_manifest, (outer_event,), ()

    committed = writer.run_locked_transaction(
        prediction_run_id=outer_manifest.prediction_run_id,
        prepare=prepare,
        command="alternate root nested commit",
    )

    assert committed is not None
    assert committed.segment_directory.is_relative_to(root_a.resolve())
    assert len(completed_segment_directories(root_a)) == before_a + 1
    assert all(
        path.name != inner_manifest.prediction_run_id
        for path in completed_segment_directories(root_a)
    )
    assert completed_segment_directories(root_b) == ()
    assert not root_b.exists()
    assert not tuple(root_a.rglob(".*.tmp-*"))


def test_same_writer_guard_rejects_concurrent_thread_entry(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    inner_manifest, inner_event = _benign_segment(
        reviewed,
        transaction_id="same-writer-thread-inner",
    )
    outer_manifest, outer_event = _benign_segment(
        reviewed,
        transaction_id="same-writer-thread-outer",
    )
    callback_barrier = Barrier(2)
    before = len(completed_segment_directories(root))

    def prepare():
        callback_barrier.wait(timeout=5)
        callback_barrier.wait(timeout=5)
        return outer_manifest, (outer_event,), ()

    with ThreadPoolExecutor(max_workers=1) as executor:
        outer_future = executor.submit(
            writer.run_locked_transaction,
            prediction_run_id=outer_manifest.prediction_run_id,
            prepare=prepare,
        )
        callback_barrier.wait(timeout=5)
        try:
            with pytest.raises(LifecycleWriterReentrancyError):
                writer.commit_segment(inner_manifest, (inner_event,))
        finally:
            callback_barrier.wait(timeout=5)
        outer = outer_future.result(timeout=10)

    assert outer is not None
    assert len(completed_segment_directories(root)) == before + 1
    assert all(
        path.name != inner_manifest.prediction_run_id
        for path in completed_segment_directories(root)
    )


def test_root_mutation_then_callback_failure_clears_reentrancy_state(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root_a = tmp_path / "data" / "lifecycle"
    root_b = tmp_path / "alternate" / "lifecycle"
    writer = LifecycleWriter(root_a, clock=FixedClock(NOW))
    failed_manifest, failed_event = _benign_segment(
        reviewed,
        transaction_id="root-mutation-callback-failure",
    )
    recovery_manifest, recovery_event = _benign_segment(
        reviewed,
        transaction_id="root-mutation-recovery",
    )
    before_a = completed_segment_directories(root_a)

    def prepare():
        writer.root = root_b
        raise RuntimeError("callback failed after root mutation")

    with pytest.raises(
        RuntimeError,
        match="callback failed after root mutation",
    ):
        writer.run_locked_transaction(
            prediction_run_id=failed_manifest.prediction_run_id,
            prepare=prepare,
        )

    assert completed_segment_directories(root_a) == before_a
    assert completed_segment_directories(root_b) == ()
    assert not tuple(root_a.rglob(".*.tmp-*"))
    assert not root_b.exists()

    writer.root = root_a
    recovered = writer.commit_segment(
        recovery_manifest,
        (recovery_event,),
    )
    assert recovered.status == "COMMITTED"
    assert recovered.segment_directory.is_relative_to(root_a.resolve())
    assert all(
        path.name != failed_manifest.prediction_run_id
        for path in completed_segment_directories(root_a)
    )


def test_separate_writer_instances_keep_independent_transaction_guards(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root_a = tmp_path / "data" / "lifecycle"
    root_b = tmp_path / "alternate" / "lifecycle"
    writer_a = LifecycleWriter(root_a, clock=FixedClock(NOW))
    writer_b = LifecycleWriter(root_b, clock=FixedClock(NOW))
    inner_manifest, inner_event = _benign_segment(
        reviewed,
        transaction_id="separate-writer-inner",
    )
    outer_manifest, outer_event = _benign_segment(
        reviewed,
        transaction_id="separate-writer-outer",
    )
    before_a = len(completed_segment_directories(root_a))

    def prepare():
        inner = writer_b.commit_segment(inner_manifest, (inner_event,))
        assert inner.segment_directory.is_relative_to(root_b.resolve())
        return outer_manifest, (outer_event,), ()

    outer = writer_a.run_locked_transaction(
        prediction_run_id=outer_manifest.prediction_run_id,
        prepare=prepare,
    )

    assert outer is not None
    assert outer.segment_directory.is_relative_to(root_a.resolve())
    assert len(completed_segment_directories(root_a)) == before_a + 1
    assert len(completed_segment_directories(root_b)) == 1
    assert not tuple(root_a.rglob(".*.tmp-*"))
    assert not tuple(root_b.rglob(".*.tmp-*"))


def test_preparation_callback_failure_creates_no_segment_staging_or_lock(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    manifest, event = _benign_segment(
        reviewed,
        transaction_id="callback-prepares-then-fails",
    )
    prepared = manifest, (event,), ()
    before = completed_segment_directories(root)

    def prepare():
        assert prepared[0] == manifest
        assert (root / ".writer.lock").is_file()
        raise RuntimeError("preparation failed after building data")

    with pytest.raises(
        RuntimeError,
        match="preparation failed after building data",
    ):
        writer.run_locked_transaction(
            prediction_run_id=manifest.prediction_run_id,
            prepare=prepare,
            command="callback preparation failure",
        )

    assert completed_segment_directories(root) == before
    assert not (root / ".writer.lock").exists()
    assert not tuple(root.rglob(".*.tmp-*"))


@pytest.mark.parametrize(
    "failure_stage",
    (
        "after_temp_directory_created",
        "after_data_files_written",
        "before_atomic_rename",
    ),
)
def test_all_commit_failure_hooks_observe_owned_real_lock_and_roll_back(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    reviewed = _review(tmp_path)
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    manifest, event = _benign_segment(
        reviewed,
        transaction_id=f"lock-hook-{failure_stage}",
    )
    command = "lock hook ownership regression"
    observed: list[str] = []
    before = completed_segment_directories(root)

    def fail(stage: str) -> None:
        lock_path = root / ".writer.lock"
        assert lock_path.is_file()
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["lock_id"]
        assert metadata["pid"]
        assert metadata["hostname"]
        assert metadata["root"] == str(root.resolve())
        assert metadata["prediction_run_id"] == manifest.prediction_run_id
        assert metadata["command"] == command
        assert metadata["acquired_at_utc"]
        observed.append(stage)
        if stage == failure_stage:
            raise RuntimeError(f"fail at {stage}")

    with pytest.raises(RuntimeError, match=f"fail at {failure_stage}"):
        writer.commit_segment(
            manifest,
            (event,),
            failure_hook=fail,
            command=command,
        )

    assert failure_stage in observed
    assert completed_segment_directories(root) == before
    assert not (root / ".writer.lock").exists()
    assert not tuple(root.rglob(".*.tmp-*"))


def test_writer_locked_transaction_exposes_no_capability_or_registry(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    manifest, event = _benign_segment(
        reviewed,
        transaction_id="transaction-capability-validation",
    )
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    assert not hasattr(writer_service, "_LifecycleWriterTransaction")
    assert not hasattr(writer_service, "_TRANSACTION_CONSTRUCTOR_TOKEN")
    assert not hasattr(writer, "transaction")
    assert not hasattr(writer, "_transactions")
    assert not hasattr(writer, "_transaction_guard")

    # Caller-created registry-like attributes are inert because transaction
    # ownership is represented only by the active call stack and filesystem
    # lock held inside LifecycleWriter.
    writer._transactions = {"forged": {"used": False}}  # type: ignore[attr-defined]
    prepared_calls = 0

    def prepare():
        nonlocal prepared_calls
        prepared_calls += 1
        return manifest, (event,), ()

    committed = writer.run_locked_transaction(
        prediction_run_id=manifest.prediction_run_id,
        prepare=prepare,
        command="internal callback transaction",
    )
    assert committed is not None
    assert committed.status == "COMMITTED"
    assert prepared_calls == 1
    # Reusing the callback by itself returns only detached preparation data; it
    # cannot commit and exposes no reusable writer capability.
    assert prepare()[0] == manifest
    assert len(completed_segment_directories(root)) == 2
    assert not (root / ".writer.lock").exists()


def test_writer_locked_transaction_commits_once_and_releases_after_rollback(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    root = tmp_path / "data" / "lifecycle"
    writer = LifecycleWriter(root, clock=FixedClock(NOW))
    first_manifest, first_event = _benign_segment(
        reviewed,
        transaction_id="transaction-one-use",
    )

    committed = writer.run_locked_transaction(
        prediction_run_id=first_manifest.prediction_run_id,
        prepare=lambda: (first_manifest, (first_event,), ()),
        command="transaction one commit",
    )
    assert committed is not None
    assert committed.status == "COMMITTED"

    rollback_manifest, rollback_event = _benign_segment(
        reviewed,
        transaction_id="transaction-rollback",
    )

    def fail_rollback(stage: str) -> None:
        if stage == "after_data_files_written":
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError, match="rollback"):
        writer.run_locked_transaction(
            prediction_run_id=rollback_manifest.prediction_run_id,
            prepare=lambda: (rollback_manifest, (rollback_event,), ()),
            command="transaction rollback",
            failure_hook=fail_rollback,
        )

    assert not (root / ".writer.lock").exists()
    assert not tuple(root.rglob(".*.tmp-*"))


def _run_multiprocess_review_race(
    tmp_path: Path,
    decisions: tuple[str, str],
    *,
    case_name: str,
    final_state_kind: str,
    candidates: tuple[dict[str, object], dict[str, object]] | None = None,
) -> tuple[_RaceWorkerResult, ...]:
    lifecycle_root = tmp_path / "data" / "lifecycle"
    attempt_id = f"{case_name}-{uuid4().hex}"
    attempted_candidates = candidates or (_candidate(), _candidate())
    worker_specs = tuple(
        _RaceWorkerSpec(
            case_name=case_name,
            attempt_id=attempt_id,
            worker_id=f"review-worker-{index}",
            operation_requested="REVIEW",
            lifecycle_root=str(lifecycle_root),
            candidate=attempted_candidates[index],
            review_id=f"review_{index + 1:032x}",
            candidate_id=str(
                attempted_candidates[index]["source_candidate_id"]
            ),
            decision_attempted=decision,
            review_run_id=f"multiprocess-review-run-{index}",
            transaction_id=f"multiprocess-review-transaction-{index}",
        )
        for index, decision in enumerate(decisions)
    )
    permitted = {
        (
            EXPECTED_WRITER_BUSY,
            LifecycleWriterBusyError.__module__,
            LifecycleWriterBusyError.__name__,
        )
    }
    if final_state_kind == "REVIEW_FINAL_VS_DEFERRED":
        permitted.add(
            (
                EXPECTED_TRANSITION_REJECTION,
                OfficialPickReviewTransitionError.__module__,
                OfficialPickReviewTransitionError.__name__,
            )
        )
    if final_state_kind in {"REVIEW_FINAL", "REVIEW_CONFLICT"}:
        permitted.add(
            (
                EXPECTED_CONFLICT,
                OfficialPickReviewConflictError.__module__,
                OfficialPickReviewConflictError.__name__,
            )
        )
    expectation = _RaceExpectation(
        case_name=case_name,
        attempt_id=attempt_id,
        worker_specs=worker_specs,
        permitted_non_successes=frozenset(permitted),
        valid_success_counts=(
            frozenset({1, 2})
            if final_state_kind == "REVIEW_FINAL_VS_DEFERRED"
            else frozenset({1})
        ),
        valid_completed_segment_counts=(
            frozenset({1, 2})
            if final_state_kind == "REVIEW_FINAL_VS_DEFERRED"
            else frozenset({1})
        ),
        final_state_kind=final_state_kind,
    )
    return _run_multiprocess_race(expectation)


def _run_multiprocess_promotion_race(
    tmp_path: Path,
    *,
    case_name: str,
    review_id: str,
) -> tuple[_RaceWorkerResult, ...]:
    lifecycle_root = tmp_path / "data" / "lifecycle"
    attempt_id = f"{case_name}-{uuid4().hex}"
    worker_specs = tuple(
        _RaceWorkerSpec(
            case_name=case_name,
            attempt_id=attempt_id,
            worker_id=f"promotion-worker-{index}",
            operation_requested="PROMOTE",
            lifecycle_root=str(lifecycle_root),
            candidate=_candidate(),
            review_id=review_id,
            candidate_id=str(_candidate()["source_candidate_id"]),
            decision_attempted="APPROVED",
            review_run_id="review-run-001",
            transaction_id=f"multiprocess-promotion-transaction-{index}",
            pick_id=f"pick_{index + 1:032x}",
        )
        for index in range(2)
    )
    expectation = _RaceExpectation(
        case_name=case_name,
        attempt_id=attempt_id,
        worker_specs=worker_specs,
        permitted_non_successes=frozenset(
            {
                (
                    EXPECTED_WRITER_BUSY,
                    LifecycleWriterBusyError.__module__,
                    LifecycleWriterBusyError.__name__,
                )
            }
        ),
        valid_success_counts=frozenset({1, 2}),
        valid_completed_segment_counts=frozenset({2}),
        final_state_kind="PROMOTION_DUPLICATE",
    )
    return _run_multiprocess_race(expectation)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    return value


def _capture_race_state(lifecycle_root: str | Path) -> dict[str, Any]:
    root = Path(lifecycle_root)
    state: dict[str, Any] = {
        "lifecycle_root": str(root),
        "committed_segments": [],
        "completed_segment_count": 0,
        "reviews": [],
        "official_picks": [],
        "remaining_lock_paths": [],
        "remaining_staging_paths": [],
        "directory_inventory": [],
        "reconstruction_errors": [],
    }
    try:
        segments = completed_segment_directories(root)
        state["completed_segment_count"] = len(segments)
        for segment in segments:
            segment_record: dict[str, Any] = {
                "path": str(segment),
                "prediction_run_id": None,
                "event_identities": [],
            }
            try:
                manifest = json.loads(
                    (segment / "run_manifest.json").read_text(encoding="utf-8")
                )
                segment_record["prediction_run_id"] = manifest.get(
                    "prediction_run_id"
                )
                segment_record["event_identities"] = [
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "idempotency_key": event.idempotency_key,
                        "prediction_run_id": event.prediction_run_id,
                    }
                    for event in read_segment_events(segment)
                ]
            except Exception:
                state["reconstruction_errors"].append(
                    {
                        "scope": str(segment),
                        "traceback": traceback.format_exc(),
                    }
                )
            state["committed_segments"].append(segment_record)
    except Exception:
        state["reconstruction_errors"].append(
            {
                "scope": "completed segments",
                "traceback": traceback.format_exc(),
            }
        )
    try:
        state["reviews"] = [
            _json_safe(asdict(item))
            for item in read_official_pick_candidate_reviews(root)
        ]
    except Exception:
        state["reconstruction_errors"].append(
            {
                "scope": "OfficialPickCandidateReview state",
                "traceback": traceback.format_exc(),
            }
        )
    try:
        state["official_picks"] = [
            _json_safe(asdict(item))
            for item in read_official_picks(root)
        ]
    except Exception:
        state["reconstruction_errors"].append(
            {
                "scope": "OfficialPick state",
                "traceback": traceback.format_exc(),
            }
        )
    if root.exists():
        try:
            inventory = [root, *sorted(root.rglob("*"))]
            state["directory_inventory"] = [
                {
                    "path": str(path),
                    "kind": (
                        "symlink"
                        if path.is_symlink()
                        else "directory"
                        if path.is_dir()
                        else "file"
                    ),
                    "size": (
                        path.stat().st_size
                        if path.is_file() and not path.is_symlink()
                        else None
                    ),
                }
                for path in inventory
            ]
            state["remaining_lock_paths"] = [
                str(path)
                for path in inventory
                if path.name == ".writer.lock"
            ]
            state["remaining_staging_paths"] = [
                str(path)
                for path in inventory
                if path.name.startswith(".") and ".tmp-" in path.name
            ]
        except Exception:
            state["reconstruction_errors"].append(
                {
                    "scope": "directory inventory",
                    "traceback": traceback.format_exc(),
                }
            )
    return state


def _result_json(result: Any) -> Any:
    if isinstance(result, _RaceWorkerResult):
        return _json_safe(asdict(result))
    return {
        "malformed_result_type": (
            f"{type(result).__module__}.{type(result).__name__}"
        ),
        "repr": repr(result),
    }


def _race_attempt_issues(attempt: _RaceAttempt) -> list[str]:
    expectation = attempt.expectation
    expected_ids = tuple(
        spec.worker_id for spec in expectation.worker_specs
    )
    expected_id_set = set(expected_ids)
    issues: list[str] = []

    if len(expected_id_set) != len(expected_ids):
        issues.append("worker specifications contain duplicate worker IDs")
    if len(attempt.started_worker_ids) != len(expectation.worker_specs):
        issues.append(
            "expected worker count was not started: "
            f"expected={len(expectation.worker_specs)}, "
            f"started={len(attempt.started_worker_ids)}"
        )
    if set(attempt.started_worker_ids) != expected_id_set:
        issues.append(
            "started worker IDs do not match the expected worker IDs: "
            f"expected={sorted(expected_id_set)!r}, "
            f"started={sorted(attempt.started_worker_ids)!r}"
        )
    if attempt.start_failures:
        issues.append(
            f"worker start failures were recorded: {attempt.start_failures!r}"
        )
    if attempt.timed_out_worker_ids:
        issues.append(
            "workers did not join before timeout: "
            f"{attempt.timed_out_worker_ids!r}"
        )
    if set(attempt.exit_codes) != expected_id_set:
        issues.append(
            "not every expected worker has a recorded exit code: "
            f"{attempt.exit_codes!r}"
        )
    for worker_id in expected_ids:
        exit_code = attempt.exit_codes.get(worker_id)
        if exit_code is None:
            issues.append(f"worker {worker_id} has no exit code")
        elif exit_code != 0:
            issues.append(
                f"worker {worker_id} has nonzero exit code {exit_code}"
            )

    if len(attempt.results) != len(expectation.worker_specs):
        issues.append(
            "incorrect worker result count: "
            f"expected={len(expectation.worker_specs)}, "
            f"received={len(attempt.results)}"
        )
    structured = tuple(
        item
        for item in attempt.results
        if isinstance(item, _RaceWorkerResult)
    )
    if len(structured) != len(attempt.results):
        issues.append("one or more queue results are not structured worker results")
    result_ids = tuple(item.worker_id for item in structured)
    duplicate_ids = sorted(
        {
            worker_id
            for worker_id in result_ids
            if result_ids.count(worker_id) > 1
        }
    )
    if duplicate_ids:
        issues.append(f"duplicate worker results: {duplicate_ids!r}")
    missing_ids = sorted(expected_id_set - set(result_ids))
    if missing_ids:
        issues.append(f"missing worker results: {missing_ids!r}")
    unexpected_ids = sorted(set(result_ids) - expected_id_set)
    if unexpected_ids:
        issues.append(f"unexpected worker result IDs: {unexpected_ids!r}")

    specs_by_id = {
        spec.worker_id: spec for spec in expectation.worker_specs
    }
    success_results: list[_RaceWorkerResult] = []
    for result in structured:
        spec = specs_by_id.get(result.worker_id)
        if spec is None:
            continue
        if result.case_name != expectation.case_name:
            issues.append(
                f"worker {result.worker_id} returned the wrong case name"
            )
        if result.attempt_id != expectation.attempt_id:
            issues.append(
                f"worker {result.worker_id} returned the wrong attempt ID"
            )
        if result.process_id <= 0:
            issues.append(
                f"worker {result.worker_id} returned an invalid process ID"
            )
        if (
            result.operation_requested != spec.operation_requested
            or result.lifecycle_root != spec.lifecycle_root
            or result.review_id != spec.review_id
            or result.candidate_id != spec.candidate_id
            or result.decision_attempted != spec.decision_attempted
        ):
            issues.append(
                f"worker {result.worker_id} identity/operation fields "
                "do not match its specification"
            )
        try:
            started_at = datetime.fromisoformat(
                result.process_started_at_utc
            )
            completed_at = datetime.fromisoformat(
                result.process_completed_at_utc
            )
            if completed_at < started_at:
                issues.append(
                    f"worker {result.worker_id} completion precedes start"
                )
        except ValueError:
            issues.append(
                f"worker {result.worker_id} returned invalid timestamps"
            )
        if result.outcome_category == SUCCESS:
            success_results.append(result)
            if result.success_value is None:
                issues.append(
                    f"successful worker {result.worker_id} has no success value"
                )
            if any(
                value is not None
                for value in (
                    result.exception_module,
                    result.exception_class,
                    result.exception_message,
                    result.full_traceback,
                )
            ):
                issues.append(
                    f"successful worker {result.worker_id} has exception fields"
                )
            continue
        if result.success_value is not None:
            issues.append(
                f"non-success worker {result.worker_id} has a success value"
            )
        if not all(
            (
                result.exception_module,
                result.exception_class,
                result.exception_message is not None,
                result.full_traceback,
            )
        ):
            issues.append(
                f"non-success worker {result.worker_id} lacks exact "
                "exception diagnostics"
            )
        exception_tuple = (
            result.outcome_category,
            result.exception_module or "",
            result.exception_class or "",
        )
        if result.outcome_category == UNEXPECTED_ERROR:
            issues.append(
                f"worker {result.worker_id} returned UNEXPECTED_ERROR: "
                f"{result.exception_module}.{result.exception_class}: "
                f"{result.exception_message}"
            )
        elif exception_tuple not in expectation.permitted_non_successes:
            issues.append(
                f"worker {result.worker_id} returned a non-permitted "
                f"exception outcome: {exception_tuple!r}"
            )

    success_count = len(success_results)
    if success_count == 0:
        issues.append("zero successful workers is not legal for this race case")
    if success_count not in expectation.valid_success_counts:
        issues.append(
            f"invalid success count {success_count}; expected one of "
            f"{sorted(expectation.valid_success_counts)!r}"
        )

    state = attempt.reconstructed_state
    if state["reconstruction_errors"]:
        issues.append(
            "final state reconstruction or inventory failed: "
            f"{state['reconstruction_errors']!r}"
        )
    completed_count = state["completed_segment_count"]
    if completed_count not in expectation.valid_completed_segment_counts:
        issues.append(
            f"invalid completed-segment count {completed_count}; expected "
            f"one of {sorted(expectation.valid_completed_segment_counts)!r}"
        )
    if state["remaining_lock_paths"]:
        issues.append(
            f"writer lock residue remains: {state['remaining_lock_paths']!r}"
        )
    if state["remaining_staging_paths"]:
        issues.append(
            "staging directory residue remains: "
            f"{state['remaining_staging_paths']!r}"
        )

    if expectation.final_state_kind != "HARNESS_SELF_TEST":
        _append_race_state_compatibility_issues(
            issues,
            expectation=expectation,
            results=structured,
            state=state,
        )
    return issues


def _append_race_state_compatibility_issues(
    issues: list[str],
    *,
    expectation: _RaceExpectation,
    results: tuple[_RaceWorkerResult, ...],
    state: dict[str, Any],
) -> None:
    reviews = state["reviews"]
    picks = state["official_picks"]
    expected_decisions = {
        spec.decision_attempted
        for spec in expectation.worker_specs
        if spec.operation_requested == "REVIEW"
    }
    if expectation.final_state_kind in {"REVIEW_FINAL", "REVIEW_CONFLICT"}:
        if len(reviews) != 1 or picks:
            issues.append(
                "final review state must contain exactly one review and no picks"
            )
        elif reviews[0].get("operator_decision") not in expected_decisions:
            issues.append(
                "committed review decision is incompatible with attempts"
            )
    elif expectation.final_state_kind == "REVIEW_FINAL_VS_DEFERRED":
        decisions = [
            item.get("operator_decision") for item in reviews
        ]
        if picks or len(reviews) not in {1, 2}:
            issues.append(
                "final-vs-deferred state must contain one or two reviews "
                "and no picks"
            )
        elif len(decisions) != len(set(decisions)):
            issues.append("final-vs-deferred state contains duplicate decisions")
        elif not set(decisions).issubset(expected_decisions):
            issues.append(
                "final-vs-deferred review decisions are incompatible "
                "with attempts"
            )
        elif len(decisions) == 2 and set(decisions) != expected_decisions:
            issues.append(
                "two-review final-vs-deferred state lacks an attempted decision"
            )
    elif expectation.final_state_kind == "PROMOTION_DUPLICATE":
        if (
            len(reviews) != 1
            or reviews[0].get("operator_decision") != "APPROVED"
            or len(picks) != 1
        ):
            issues.append(
                "duplicate promotion final state must contain one approved "
                "review and one OfficialPick"
            )
        elif (
            picks[0].get("review_id") != reviews[0].get("review_id")
            or picks[0].get("source_candidate_id")
            != reviews[0].get("source_candidate_id")
        ):
            issues.append(
                "OfficialPick is incompatible with reconstructed review state"
            )

    committed_paths = {
        item["path"] for item in state["committed_segments"]
    }
    reviews_by_id = {
        item.get("review_id"): item for item in reviews
    }
    picks_by_id = {
        item.get("pick_id"): item for item in picks
    }
    specs_by_id = {
        spec.worker_id: spec for spec in expectation.worker_specs
    }
    for result in results:
        spec = specs_by_id[result.worker_id]
        if result.outcome_category == SUCCESS:
            value = result.success_value or {}
            if value.get("ledger_segment_directory") not in committed_paths:
                issues.append(
                    f"worker {result.worker_id} success references an "
                    "uncommitted segment"
                )
            if result.operation_requested == "REVIEW":
                review = reviews_by_id.get(value.get("review_id"))
                if (
                    review is None
                    or review.get("source_candidate_id")
                    != value.get("candidate_id")
                    or review.get("operator_decision")
                    != value.get("decision")
                ):
                    issues.append(
                        f"worker {result.worker_id} success is incompatible "
                        "with reconstructed review state"
                    )
            elif result.operation_requested == "PROMOTE":
                pick = picks_by_id.get(value.get("pick_id"))
                if (
                    pick is None
                    or pick.get("review_id") != value.get("review_id")
                    or pick.get("source_candidate_id")
                    != value.get("candidate_id")
                ):
                    issues.append(
                        f"worker {result.worker_id} success is incompatible "
                        "with reconstructed OfficialPick state"
                    )
        elif (
            result.outcome_category == EXPECTED_TRANSITION_REJECTION
            and expectation.final_state_kind == "REVIEW_FINAL_VS_DEFERRED"
        ):
            if (
                spec.decision_attempted != "DEFERRED"
                or not any(
                    review.get("operator_decision") != "DEFERRED"
                    for review in reviews
                )
            ):
                issues.append(
                    f"worker {result.worker_id} transition rejection is "
                    "incompatible with final-vs-deferred state"
                )
        elif (
            result.outcome_category == EXPECTED_CONFLICT
            and reviews
            and expectation.final_state_kind == "REVIEW_CONFLICT"
        ):
            attempted_hash = canonical_equality_sha256(
                OfficialPickPromotionRequest.from_mapping(
                    spec.candidate
                ).to_candidate_snapshot()
            )
            if reviews[0].get("candidate_snapshot_sha256") == attempted_hash:
                issues.append(
                    f"worker {result.worker_id} conflict is incompatible "
                    "with the committed candidate snapshot"
                )
        elif (
            result.outcome_category == EXPECTED_CONFLICT
            and reviews
            and expectation.final_state_kind == "REVIEW_FINAL"
            and reviews[0].get("operator_decision")
            == spec.decision_attempted
        ):
            issues.append(
                f"worker {result.worker_id} final-slot conflict is "
                "incompatible with the committed decision"
            )


def _diagnostic_payload(
    attempt: _RaceAttempt,
    issues: list[str],
) -> dict[str, Any]:
    expectation = attempt.expectation
    return {
        "case_name": expectation.case_name,
        "attempt_id": expectation.attempt_id,
        "attempt_started_at_utc": attempt.attempt_started_at_utc,
        "attempt_completed_at_utc": attempt.attempt_completed_at_utc,
        "issues": issues,
        "expected_worker_count": len(expectation.worker_specs),
        "started_worker_ids": list(attempt.started_worker_ids),
        "timed_out_worker_ids": list(attempt.timed_out_worker_ids),
        "start_failures": attempt.start_failures,
        "exit_codes": attempt.exit_codes,
        "permitted_non_successes": [
            {
                "outcome_category": item[0],
                "exception_module": item[1],
                "exception_class": item[2],
            }
            for item in sorted(expectation.permitted_non_successes)
        ],
        "valid_success_counts": sorted(expectation.valid_success_counts),
        "valid_completed_segment_counts": sorted(
            expectation.valid_completed_segment_counts
        ),
        "attempted_operations": [
            _json_safe(asdict(spec)) for spec in expectation.worker_specs
        ],
        "worker_results": [
            _result_json(result) for result in attempt.results
        ],
        "committed_segments": attempt.reconstructed_state[
            "committed_segments"
        ],
        "reconstructed_review_state": attempt.reconstructed_state["reviews"],
        "reconstructed_official_pick_state": attempt.reconstructed_state[
            "official_picks"
        ],
        "remaining_lock_paths": attempt.reconstructed_state[
            "remaining_lock_paths"
        ],
        "remaining_staging_paths": attempt.reconstructed_state[
            "remaining_staging_paths"
        ],
        "directory_inventory": attempt.reconstructed_state[
            "directory_inventory"
        ],
        "reconstruction_errors": attempt.reconstructed_state[
            "reconstruction_errors"
        ],
    }


def _write_race_diagnostic(
    attempt: _RaceAttempt,
    issues: list[str],
) -> Path:
    temporary_root = Path(
        os.environ.get("TEMP")
        or os.environ.get("TMP")
        or tempfile.gettempdir()
    )
    diagnostic_root = temporary_root / "courtvision-race-diagnostics"
    diagnostic_root.mkdir(parents=True, exist_ok=True)
    safe_case = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in attempt.expectation.case_name
    )
    diagnostic_path = (
        diagnostic_root
        / (
            f"{safe_case}-{attempt.expectation.attempt_id}-"
            f"{uuid4().hex}.json"
        )
    )
    diagnostic_path.write_text(
        json.dumps(
            _json_safe(_diagnostic_payload(attempt, issues)),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return diagnostic_path


def _assert_race_attempt(attempt: _RaceAttempt) -> None:
    issues = _race_attempt_issues(attempt)
    if not issues:
        return
    diagnostic_path = _write_race_diagnostic(attempt, issues)
    complete_results = json.dumps(
        [_result_json(result) for result in attempt.results],
        indent=2,
        sort_keys=True,
    )
    pytest.fail(
        "race attempt failed:\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + f"\ncomplete worker results:\n{complete_results}"
        + f"\ndiagnostic artifact: {diagnostic_path}",
        pytrace=False,
    )


def _run_multiprocess_race(
    expectation: _RaceExpectation,
) -> tuple[_RaceWorkerResult, ...]:
    context = get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        (
            spec,
            context.Process(
                target=_multiprocess_race_worker,
                args=(start_event, result_queue, spec),
                name=spec.worker_id,
            ),
        )
        for spec in expectation.worker_specs
    ]
    attempt_started_at_utc = _utc_timestamp()
    started_worker_ids: list[str] = []
    start_failures: dict[str, str] = {}
    for spec, process in processes:
        try:
            process.start()
        except Exception:
            start_failures[spec.worker_id] = traceback.format_exc()
        else:
            started_worker_ids.append(spec.worker_id)
    start_event.set()
    deadline = time.monotonic() + 30
    timed_out_worker_ids: list[str] = []
    for spec, process in processes:
        if spec.worker_id not in started_worker_ids:
            continue
        process.join(timeout=max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            timed_out_worker_ids.append(spec.worker_id)
            process.terminate()
            process.join(timeout=5)
    exit_codes = {
        spec.worker_id: (
            process.exitcode
            if spec.worker_id in started_worker_ids
            else None
        )
        for spec, process in processes
    }
    results: list[Any] = []
    try:
        for _ in expectation.worker_specs:
            try:
                results.append(result_queue.get(timeout=2))
            except Empty:
                break
        while True:
            try:
                results.append(result_queue.get(timeout=0.25))
            except Empty:
                break
    finally:
        result_queue.close()
        result_queue.join_thread()
    attempt_completed_at_utc = _utc_timestamp()
    lifecycle_root = expectation.worker_specs[0].lifecycle_root
    attempt = _RaceAttempt(
        expectation=expectation,
        results=tuple(results),
        exit_codes=exit_codes,
        timed_out_worker_ids=tuple(timed_out_worker_ids),
        start_failures=start_failures,
        started_worker_ids=tuple(started_worker_ids),
        attempt_started_at_utc=attempt_started_at_utc,
        attempt_completed_at_utc=attempt_completed_at_utc,
        reconstructed_state=_capture_race_state(lifecycle_root),
    )
    _assert_race_attempt(attempt)
    return tuple(
        result
        for result in attempt.results
        if isinstance(result, _RaceWorkerResult)
    )


def _synthetic_success_result(
    spec: _RaceWorkerSpec,
    success_value: dict[str, Any],
) -> _RaceWorkerResult:
    timestamp = _utc_timestamp()
    return _RaceWorkerResult(
        case_name=spec.case_name,
        attempt_id=spec.attempt_id,
        worker_id=spec.worker_id,
        process_id=os.getpid(),
        operation_requested=spec.operation_requested,
        outcome_category=SUCCESS,
        success_value=success_value,
        exception_module=None,
        exception_class=None,
        exception_message=None,
        full_traceback=None,
        process_started_at_utc=timestamp,
        process_completed_at_utc=timestamp,
        lifecycle_root=spec.lifecycle_root,
        review_id=spec.review_id,
        candidate_id=spec.candidate_id,
        decision_attempted=spec.decision_attempted,
    )


def _injected_exception_result(
    spec: _RaceWorkerSpec,
    exception_type: type[Exception],
) -> _RaceWorkerResult:
    timestamp = _utc_timestamp()
    try:
        raise exception_type(
            f"injected race-harness {exception_type.__name__}"
        )
    except Exception as exc:
        return _worker_exception_result(
            spec,
            process_id=os.getpid(),
            process_started_at_utc=timestamp,
            exc=exc,
            full_traceback=traceback.format_exc(),
        )


def _synthetic_attempt(
    expectation: _RaceExpectation,
    *,
    results: tuple[Any, ...],
    exit_codes: dict[str, int | None],
    lifecycle_root: Path,
) -> _RaceAttempt:
    timestamp = _utc_timestamp()
    return _RaceAttempt(
        expectation=expectation,
        results=results,
        exit_codes=exit_codes,
        timed_out_worker_ids=(),
        start_failures={},
        started_worker_ids=tuple(
            spec.worker_id for spec in expectation.worker_specs
        ),
        attempt_started_at_utc=timestamp,
        attempt_completed_at_utc=timestamp,
        reconstructed_state=_capture_race_state(lifecycle_root),
    )


def _diagnostic_path_from_failure(message: str) -> Path:
    marker = "diagnostic artifact: "
    assert marker in message
    return Path(message.rsplit(marker, 1)[1].splitlines()[0])


@pytest.mark.parametrize(
    "exception_type",
    [TypeError, RuntimeError, LifecycleWriterError],
)
def test_race_harness_rejects_unexpected_worker_exceptions(
    tmp_path: Path,
    exception_type: type[Exception],
) -> None:
    lifecycle_root = tmp_path / "data" / "lifecycle"
    attempt_id = f"harness-unexpected-{uuid4().hex}"
    spec = _RaceWorkerSpec(
        case_name="harness-unexpected-exception",
        attempt_id=attempt_id,
        worker_id="worker-unexpected",
        operation_requested="REVIEW",
        lifecycle_root=str(lifecycle_root),
        candidate=_candidate(),
        review_id=REVIEW_ID,
        candidate_id=str(_candidate()["source_candidate_id"]),
        decision_attempted="APPROVED",
        review_run_id="harness-review-run",
        transaction_id="harness-review-transaction",
        injected_behavior=(
            "TYPE_ERROR"
            if exception_type is TypeError
            else (
                "RUNTIME_ERROR"
                if exception_type is RuntimeError
                else "LIFECYCLE_WRITER_ERROR"
            )
        ),
    )
    expectation = _RaceExpectation(
        case_name=spec.case_name,
        attempt_id=attempt_id,
        worker_specs=(spec,),
        permitted_non_successes=frozenset(),
        valid_success_counts=frozenset({1}),
        valid_completed_segment_counts=frozenset({0}),
        final_state_kind="HARNESS_SELF_TEST",
    )

    with pytest.raises(pytest.fail.Exception) as failure:
        _run_multiprocess_race(expectation)

    message = str(failure.value)
    diagnostic_path = _diagnostic_path_from_failure(message)
    assert str(diagnostic_path) in message
    assert diagnostic_path.is_file()
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    worker = diagnostic["worker_results"][0]
    assert worker["outcome_category"] == UNEXPECTED_ERROR
    assert worker["worker_id"] == spec.worker_id
    assert worker["process_id"] != os.getpid()
    assert worker["exception_module"] == exception_type.__module__
    assert worker["exception_class"] == exception_type.__name__
    assert worker["exception_message"] == (
        f"injected race-harness {exception_type.__name__}"
    )
    assert exception_type.__name__ in worker["full_traceback"]
    assert "committed_segments" in diagnostic
    assert "reconstructed_review_state" in diagnostic
    assert "reconstructed_official_pick_state" in diagnostic
    assert "remaining_lock_paths" in diagnostic
    assert "remaining_staging_paths" in diagnostic
    assert "directory_inventory" in diagnostic
    diagnostic_path.unlink()


@pytest.mark.parametrize(
    ("fault", "expected_message"),
    [
        ("MISSING_RESULT", "missing worker results"),
        ("NONZERO_EXIT", "nonzero exit code"),
        ("DUPLICATE_RESULT", "duplicate worker results"),
    ],
)
def test_race_harness_rejects_process_accounting_failures(
    tmp_path: Path,
    fault: str,
    expected_message: str,
) -> None:
    lifecycle_root = tmp_path / "data" / "lifecycle"
    attempt_id = f"harness-accounting-{fault}-{uuid4().hex}"
    spec = _RaceWorkerSpec(
        case_name=f"harness-{fault.lower()}",
        attempt_id=attempt_id,
        worker_id="worker-accounting",
        operation_requested="REVIEW",
        lifecycle_root=str(lifecycle_root),
        candidate=_candidate(),
        review_id=REVIEW_ID,
        candidate_id=str(_candidate()["source_candidate_id"]),
        decision_attempted="APPROVED",
        review_run_id="harness-review-run",
        transaction_id="harness-transaction",
        injected_behavior=fault,
    )
    expectation = _RaceExpectation(
        case_name=spec.case_name,
        attempt_id=attempt_id,
        worker_specs=(spec,),
        permitted_non_successes=frozenset(),
        valid_success_counts=frozenset({1}),
        valid_completed_segment_counts=(
            frozenset({1})
            if fault == "DUPLICATE_RESULT"
            else frozenset({0})
        ),
        final_state_kind="HARNESS_SELF_TEST",
    )

    with pytest.raises(pytest.fail.Exception) as failure:
        _run_multiprocess_race(expectation)

    assert expected_message in str(failure.value)
    diagnostic_path = _diagnostic_path_from_failure(str(failure.value))
    assert diagnostic_path.is_file()
    diagnostic_path.unlink()


@pytest.mark.parametrize(
    ("outcome_category", "exception_type", "final_state_kind"),
    [
        (
            EXPECTED_CONFLICT,
            OfficialPickReviewConflictError,
            "REVIEW_CONFLICT",
        ),
        (
            EXPECTED_TRANSITION_REJECTION,
            OfficialPickReviewTransitionError,
            "REVIEW_FINAL",
        ),
        (
            EXPECTED_WRITER_BUSY,
            LifecycleWriterBusyError,
            "REVIEW_FINAL_VS_DEFERRED",
        ),
    ],
)
def test_race_harness_accepts_exact_expected_review_loser(
    tmp_path: Path,
    outcome_category: str,
    exception_type: type[Exception],
    final_state_kind: str,
) -> None:
    reviewed = _review(tmp_path)
    lifecycle_root = tmp_path / "data" / "lifecycle"
    attempt_id = f"harness-expected-{outcome_category}-{uuid4().hex}"
    winner = _RaceWorkerSpec(
        case_name="harness-expected-review-loser",
        attempt_id=attempt_id,
        worker_id="review-winner",
        operation_requested="REVIEW",
        lifecycle_root=str(lifecycle_root),
        candidate=_candidate(),
        review_id=reviewed.review.review_id,
        candidate_id=reviewed.review.source_candidate_id,
        decision_attempted="APPROVED",
        review_run_id=reviewed.review.review_run_id,
        transaction_id="harness-winning-transaction",
    )
    losing_candidate = (
        _candidate(odds=-105)
        if outcome_category == EXPECTED_CONFLICT
        else _candidate()
    )
    loser = replace(
        winner,
        worker_id="review-loser",
        candidate=losing_candidate,
        review_id="review_ffffffffffffffffffffffffffffffff",
        decision_attempted=(
            "APPROVED"
            if outcome_category == EXPECTED_CONFLICT
            else (
                "DEFERRED"
                if outcome_category == EXPECTED_WRITER_BUSY
                else "REJECTED"
            )
        ),
        review_run_id="harness-losing-review-run",
        transaction_id="harness-losing-transaction",
    )
    winner_result = _synthetic_success_result(
        winner,
        {
            "review_id": reviewed.review.review_id,
            "candidate_id": reviewed.review.source_candidate_id,
            "decision": reviewed.review.operator_decision,
            "publication_status": reviewed.publication_status,
            "ledger_segment_directory": str(
                reviewed.ledger_segment_directory
            ),
        },
    )
    loser_result = _injected_exception_result(loser, exception_type)
    assert loser_result.outcome_category == outcome_category
    expectation = _RaceExpectation(
        case_name=winner.case_name,
        attempt_id=attempt_id,
        worker_specs=(winner, loser),
        permitted_non_successes=frozenset(
            {
                (
                    outcome_category,
                    exception_type.__module__,
                    exception_type.__name__,
                )
            }
        ),
        valid_success_counts=frozenset({1}),
        valid_completed_segment_counts=frozenset({1}),
        final_state_kind=final_state_kind,
    )
    attempt = _synthetic_attempt(
        expectation,
        results=(winner_result, loser_result),
        exit_codes={winner.worker_id: 0, loser.worker_id: 0},
        lifecycle_root=lifecycle_root,
    )

    _assert_race_attempt(attempt)


@pytest.mark.parametrize("_repeat", range(2))
def test_multiprocess_final_review_race_commits_one_legal_final(
    tmp_path: Path,
    _repeat: int,
) -> None:
    results = _run_multiprocess_review_race(
        tmp_path,
        ("APPROVED", "REJECTED"),
        case_name="multiprocess-final-review-race",
        final_state_kind="REVIEW_FINAL",
    )
    reviews = read_official_pick_candidate_reviews(
        tmp_path / "data" / "lifecycle"
    )

    assert sum(
        result.outcome_category == SUCCESS for result in results
    ) == 1, results
    assert len(reviews) == 1
    assert reviews[0].operator_decision in {"APPROVED", "REJECTED"}


@pytest.mark.parametrize("_repeat", range(2))
def test_multiprocess_final_vs_deferred_race_is_legal_and_deadlock_free(
    tmp_path: Path,
    _repeat: int,
) -> None:
    results = _run_multiprocess_review_race(
        tmp_path,
        ("APPROVED", "DEFERRED"),
        case_name="multiprocess-final-vs-deferred-race",
        final_state_kind="REVIEW_FINAL_VS_DEFERRED",
    )
    reviews = read_official_pick_candidate_reviews(
        tmp_path / "data" / "lifecycle"
    )
    lifecycle_root = tmp_path / "data" / "lifecycle"

    assert any(
        result.outcome_category == SUCCESS for result in results
    ), results
    assert {
        result.outcome_category for result in results
    }.issubset(
        {
            SUCCESS,
            EXPECTED_TRANSITION_REJECTION,
            EXPECTED_WRITER_BUSY,
        }
    )
    assert 1 <= len(reviews) <= 2
    assert all(
        item.review_status == "COMMITTED"
        and item.operator_decision in {"APPROVED", "DEFERRED"}
        for item in reviews
    )
    if len(reviews) == 2:
        assert {item.operator_decision for item in reviews} == {
            "APPROVED",
            "DEFERRED",
        }
    assert not (lifecycle_root / ".writer.lock").exists()
    assert not tuple(lifecycle_root.rglob(".*.tmp-*"))


@pytest.mark.parametrize("_repeat", range(2))
def test_multiprocess_conflicting_candidate_review_race_is_diagnosable(
    tmp_path: Path,
    _repeat: int,
) -> None:
    results = _run_multiprocess_review_race(
        tmp_path,
        ("APPROVED", "APPROVED"),
        case_name="multiprocess-conflicting-candidate-review-race",
        final_state_kind="REVIEW_CONFLICT",
        candidates=(_candidate(), _candidate(odds=-105)),
    )

    assert sum(
        result.outcome_category == SUCCESS for result in results
    ) == 1
    assert {
        result.outcome_category for result in results
    }.issubset({SUCCESS, EXPECTED_CONFLICT, EXPECTED_WRITER_BUSY})
    assert len(
        read_official_pick_candidate_reviews(
            tmp_path / "data" / "lifecycle"
        )
    ) == 1


@pytest.mark.parametrize("_repeat", range(2))
def test_multiprocess_duplicate_promotion_race_commits_one_pick(
    tmp_path: Path,
    _repeat: int,
) -> None:
    reviewed = _review(tmp_path)
    results = _run_multiprocess_promotion_race(
        tmp_path,
        case_name="multiprocess-duplicate-promotion-race",
        review_id=reviewed.review.review_id,
    )
    successful = tuple(
        result
        for result in results
        if result.outcome_category == SUCCESS
    )

    assert successful
    assert {
        result.outcome_category for result in results
    }.issubset({SUCCESS, EXPECTED_WRITER_BUSY})
    assert len(
        {
            result.success_value["pick_id"]
            for result in successful
            if result.success_value is not None
        }
    ) == 1
    assert len(
        read_official_picks(tmp_path / "data" / "lifecycle")
    ) == 1


@pytest.mark.parametrize(
    ("first_decision", "second_decision", "same_candidate"),
    [
        ("APPROVED", "DEFERRED", True),
        ("REJECTED", "APPROVED", True),
        ("APPROVED", "APPROVED", False),
    ],
)
def test_concurrent_review_races_commit_only_one_legal_transition(
    tmp_path: Path,
    first_decision: str,
    second_decision: str,
    same_candidate: bool,
) -> None:
    barrier = Barrier(2)
    candidates = (
        _candidate(),
        _candidate() if same_candidate else _candidate(odds=-105),
    )

    def submit(index: int) -> Any:
        barrier.wait(timeout=5)
        try:
            return review_official_pick_candidate(
                candidates[index],
                operator_decision=(
                    first_decision if index == 0 else second_decision
                ),
                operator_id="operator.alice",
                decision_reason=f"race decision {index}",
                review_run_id="race-review-run",
                lifecycle_root=tmp_path / "data" / "lifecycle",
                clock=FixedClock(NOW),
                review_id_factory=lambda: f"review_{index + 1:032x}",
                transaction_id_factory=(
                    lambda: f"race-review-transaction-{index}"
                ),
            )
        except (
            OfficialPickReviewConflictError,
            OfficialPickReviewTransitionError,
        ) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(submit, range(2)))

    assert sum(
        getattr(item, "publication_status", None) == "PUBLISHED"
        for item in outcomes
    ) == 1
    assert len(
        read_official_pick_candidate_reviews(
            tmp_path / "data" / "lifecycle"
        )
    ) == 1


def test_concurrent_identical_final_review_is_one_commit_plus_replay(
    tmp_path: Path,
) -> None:
    barrier = Barrier(2)

    def submit(index: int) -> Any:
        barrier.wait(timeout=5)
        return review_official_pick_candidate(
            _candidate(),
            operator_decision="APPROVED",
            operator_id="operator.alice",
            decision_reason="identical final",
            review_run_id="identical-final-run",
            lifecycle_root=tmp_path / "data" / "lifecycle",
            clock=FixedClock(NOW),
            review_id_factory=lambda: f"review_{index + 1:032x}",
            transaction_id_factory=lambda: f"identical-final-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(submit, range(2)))

    assert sorted(item.publication_status for item in outcomes) == [
        "ALREADY_PUBLISHED",
        "PUBLISHED",
    ]
    assert len(
        read_official_pick_candidate_reviews(
            tmp_path / "data" / "lifecycle"
        )
    ) == 1


def test_direct_writer_rejects_new_schema_v1_official_pick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "COURTVISION_LEGACY_OFFICIAL_PICK_V1_ALLOWLIST",
        "allow-everything",
    )
    source_id = "legacy-candidate-new"
    idempotency_key = deterministic_id(
        "opidem",
        "courtvision.official_pick_promotion.v1",
        {
            "promotion_policy_version": "1.0",
            "sport": "basketball",
            "league": "NBA",
            "source_type": "CANDIDATE",
            "source_id": source_id,
            "designation": "PAPER",
        },
    )
    pick = {
        "pick_id": PICK_ID,
        "sport": "basketball",
        "league": "NBA",
        "event_id": "nba-game-legacy-new",
        "event_start_time": format_utc_datetime(EVENT_START),
        "prediction_date": "2026-07-26",
        "market_key": "basketball:nba:market:player_points",
        "selection": "OVER",
        "line": "24.5",
        "odds": -110,
        "sportsbook": "draftkings",
        "player_id": "basketball:nba:participant:nba-player-23",
        "player_name": "Test Player",
        "team_id": "basketball:nba:team:nba-team-lal",
        "model_name": "nba-props",
        "model_version": "2026.07",
        "run_id": "nba-run-legacy-new",
        "published_at": format_utc_datetime(NOW),
        "source_candidate_id": source_id,
        "source_observation_id": None,
        "status": "PUBLISHED",
        "designation": "PAPER",
        "idempotency_key": idempotency_key,
        "provenance": {
            "source_type": "CANDIDATE",
            "source_id": source_id,
            "promotion_service": "courtvision.official_picks",
            "promotion_actor": "operator.alice",
            "promotion_policy_version": "1.0",
        },
        "schema_version": 1,
        "record_kind": "OFFICIAL_PICK",
    }
    promotion_content = dict(pick)
    for generated in ("pick_id", "published_at", "idempotency_key"):
        promotion_content.pop(generated)
    content_hash = canonical_equality_sha256(promotion_content)
    payload = {
        "payload_schema_version": 1,
        "promotion_policy_version": "1.0",
        "publication_authority": "PAPER_RESEARCH_ONLY",
        "promotion_content_sha256": content_hash,
        "official_pick": pick,
    }
    legacy_compat_validator = Draft202012Validator(
        _schema("official_pick_published_payload_v1.json")
    )
    assert list(legacy_compat_validator.iter_errors(payload)) == []
    event = EventEnvelope.create(
        event_type=EventType.OFFICIAL_PICK_PUBLISHED,
        payload=payload,
        payload_schema_version=1,
        prediction_run_id="new-schema-v1-publication",
        event_sequence=1,
        occurred_at_utc=NOW,
        recorded_at_utc=NOW,
        operating_date="2026-07-26",
        operating_timezone="America/Toronto",
        actor_type="OPERATOR",
        actor_id="operator.alice",
        correlation_id=str(pick["run_id"]),
        idempotency_key=str(pick["idempotency_key"]),
        source_refs={"source_candidate_id": source_id},
        source_hashes={"promotion_content_sha256": content_hash},
        model_id=str(pick["model_name"]),
        model_version=str(pick["model_version"]),
    )
    reviewed = _review(
        tmp_path,
        candidate=_candidate("schema-v1-control-review"),
        review_run_id="schema-v1-control-review",
        review_id="review_99999999999999999999999999999999",
    )
    manifest, _ = _benign_segment(
        reviewed,
        transaction_id=event.prediction_run_id,
    )

    with pytest.raises(
        LifecycleIntegrityError,
        match="invalid or unauthorized new OfficialPick",
    ):
        LifecycleWriter(
            tmp_path / "data" / "lifecycle",
            clock=FixedClock(NOW),
        ).commit_segment(manifest, (event,))
    _assert_no_publication_artifacts(tmp_path / "data" / "lifecycle")


def test_exact_publication_schema_dispatch_rejects_malformed_versions_and_kinds(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    manifest, event = _prepared_publication(
        _candidate(),
        reviewed.review,
        transaction_id="malformed-schema-writer",
    )
    original = json.loads(event.payload_json)
    cases: list[tuple[dict[str, Any], int]] = []

    missing_payload_version = deepcopy(original)
    missing_payload_version.pop("payload_schema_version")
    cases.append((missing_payload_version, 2))
    unknown_payload_version = deepcopy(original)
    unknown_payload_version["payload_schema_version"] = 99
    cases.append((unknown_payload_version, 99))
    missing_pick_version = deepcopy(original)
    missing_pick_version["official_pick"].pop("schema_version")
    cases.append((missing_pick_version, 2))
    missing_record_kind = deepcopy(original)
    missing_record_kind["official_pick"].pop("record_kind")
    cases.append((missing_record_kind, 2))
    unexpected_property = deepcopy(original)
    unexpected_property["unexpected"] = True
    cases.append((unexpected_property, 2))
    wrong_policy = deepcopy(original)
    wrong_policy["promotion_policy_version"] = "1.0"
    cases.append((wrong_policy, 2))
    wrong_authority = deepcopy(original)
    wrong_authority["publication_authority"] = "UNREVIEWED"
    cases.append((wrong_authority, 2))
    v1_payload_with_v2_pick = deepcopy(original)
    v1_payload_with_v2_pick["payload_schema_version"] = 1
    v1_payload_with_v2_pick["promotion_policy_version"] = "1.0"
    cases.append((v1_payload_with_v2_pick, 1))
    v2_payload_with_v1_pick = deepcopy(original)
    v2_payload_with_v1_pick["official_pick"]["schema_version"] = 1
    v2_payload_with_v1_pick["official_pick"].pop("review_id")
    v2_payload_with_v1_pick["official_pick"].pop(
        "candidate_snapshot_sha256"
    )
    cases.append((v2_payload_with_v1_pick, 2))

    root = tmp_path / "data" / "lifecycle"
    for payload, envelope_version in cases:
        malformed = _event_with_payload(
            event,
            payload,
            payload_schema_version=envelope_version,
        )
        with pytest.raises(
            LifecycleIntegrityError,
            match="invalid or unauthorized",
        ):
            LifecycleWriter(root, clock=FixedClock(NOW)).commit_segment(
                manifest,
                (malformed,),
            )
        _assert_no_publication_artifacts(root)


def test_direct_writer_rejects_malformed_schema_v2_publication(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    candidate = OfficialPickPromotionRequest.from_mapping(_candidate())
    pick = publication_service._build_pick(
        candidate,
        pick_id=PICK_ID,
        published_at=NOW,
        idempotency_key=publication_service.official_pick_idempotency_key(
            candidate,
            review_id=reviewed.review.review_id,
        ),
        promotion_actor="operator.alice",
        review=reviewed.review,
    )
    transaction_id = "direct-malformed-v2"
    valid_event = publication_service._promotion_event(
        pick,
        transaction_id=transaction_id,
        published_at=NOW,
        promotion_content_sha256=(
            publication_service._promotion_content_sha256(pick)
        ),
    )
    malformed_payload = json.loads(valid_event.payload_json)
    malformed_payload["official_pick"].pop("record_kind")
    malformed_event = _event_with_payload(valid_event, malformed_payload)
    manifest = publication_service._promotion_manifest(
        pick,
        transaction_id=transaction_id,
        published_at=NOW,
    )

    with pytest.raises(
        LifecycleIntegrityError,
        match="invalid or unauthorized new OfficialPick",
    ):
        LifecycleWriter(
            tmp_path / "data" / "lifecycle",
            clock=FixedClock(NOW),
        ).commit_segment(manifest, (malformed_event,))


def test_operator_review_report_excludes_adversarial_provenance_everywhere(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        player_name="Kelly O'Neil",
        model_name="kelly-safe-research-model",
        provenance={
            "kelly": 0.1,
            "Kelly": 0.2,
            "BANKROLL": 1000,
            "stake": 25,
            "expected_profit": 12,
            "ROI": 0.5,
            "wagering_metadata": {"live_bet": True},
        }
    )
    reviewed = _review(
        tmp_path,
        candidate,
        decision_reason="Kelly staking is prohibited for this research pick",
        provenance={
            "betting": {"profit": 99},
            "safe_note": "operator checked",
        },
    )
    _promote(tmp_path, reviewed.review.review_id, candidate)
    dataset = build_official_pick_operator_review_dataset(
        lifecycle_root=tmp_path / "data" / "lifecycle"
    )
    row = dataset.approved_promoted_candidates[0]
    assert row.player_name == "Kelly O'Neil"
    assert row.model_name == "kelly-safe-research-model"
    assert (
        row.decision_reason
        == "Kelly staking is prohibited for this research pick"
    )

    serialized = (
        asdict(dataset),
        dataset.to_dict(),
        json.loads(json.dumps(dataset.to_dict())),
        json.loads(json.dumps(asdict(dataset))),
    )
    prohibited = {
        "bankroll",
        "stake",
        "expected_profit",
        "roi",
        "kelly_fraction",
        "wager_amount",
        "wagering_metadata",
        "live_bet",
        "execution_instructions",
    }
    for value in serialized:
        assert not (_nested_keys(value) & prohibited)


def test_full_review_event_envelope_and_payload_match_frozen_schemas(
    tmp_path: Path,
) -> None:
    reviewed = _review(tmp_path)
    event = read_segment_events(reviewed.ledger_segment_directory)[0]
    envelope_validator = Draft202012Validator(
        _schema("event_envelope_v1.json")
    )
    payload_validator = Draft202012Validator(
        _schema("official_pick_candidate_reviewed_payload_v1.json")
    )

    assert list(envelope_validator.iter_errors(event.to_dict())) == []
    assert list(
        payload_validator.iter_errors(json.loads(event.payload_json))
    ) == []
