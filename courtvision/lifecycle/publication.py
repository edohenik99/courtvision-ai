"""Post-board shadow publication adapter for the canonical CourtVision runtime."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from courtvision.lifecycle.canonical import (
    file_sha256,
    parse_utc_datetime,
    payload_sha256,
)
from courtvision.lifecycle.clock import Clock, SystemClock
from courtvision.lifecycle.evidence import (
    PreparedEvidenceObject,
    is_sensitive_key,
    prepare_evidence_object,
    sanitize_evidence,
)
from courtvision.lifecycle.identity import (
    derive_publication_identity,
    identity_inputs_from_board_row,
)
from courtvision.lifecycle.models import (
    EventEnvelope,
    EventType,
    PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION,
    PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION_V2,
    ReconciliationReport,
    ReconciliationStatus,
    ReproducibilityLevel,
    RunManifest,
    RunMode,
    RunReason,
)
from courtvision.lifecycle.provenance import (
    capture_git_provenance,
    dependency_fingerprint,
    model_artifact_manifest,
    python_version,
    safe_runtime_config_snapshot,
)
from courtvision.lifecycle.reconciliation import (
    degraded_reconciliation,
    read_canonical_board_rows,
    reconcile_board_with_events,
)
from courtvision.lifecycle.writer import (
    IdempotencyConflictError,
    LifecycleIntegrityError,
    LifecycleWriter,
    read_segment_events,
)
from courtvision.shadow_lifecycle import (
    LIFECYCLE_SHADOW_ENV,
    lifecycle_shadow_enabled,
)


OPERATING_TIMEZONE = "America/Toronto"


class CanonicalPublicationError(RuntimeError):
    """Raised when no successful canonical actionable publication exists."""


class UnsafeBoardEvidenceError(RuntimeError):
    """Raised when a board schema could expose credentials to lifecycle data."""


@dataclass(slots=True)
class ShadowRunContext:
    prediction_run_id: str
    prediction_date: str
    repository_root: Path
    lifecycle_root: Path
    clock: Clock
    run_manifest: RunManifest
    config_snapshot: Mapping[str, Any]
    model_manifest: Mapping[str, Any]
    git_provenance: Mapping[str, Any]
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class ShadowPublicationResult:
    status: str
    prediction_run_id: str
    commit_status: str | None
    segment_directory: Path | None
    reconciliation_path: Path | None
    reconciliation: ReconciliationReport
    message: str


def begin_shadow_run(
    runtime: Any,
    *,
    repository_root: str | Path,
    prediction_date: str,
    verbose_outputs: bool,
    force_output_overwrite: bool,
    clock: Clock | None = None,
    lifecycle_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ShadowRunContext | None:
    """Create one run identity if and only if shadow lifecycle is enabled."""

    if not lifecycle_shadow_enabled(environ):
        return None
    active_clock = clock or SystemClock()
    root = Path(repository_root).resolve()
    storage_root = (
        Path(lifecycle_root)
        if lifecycle_root is not None
        else root / "data" / "lifecycle"
    )
    started = active_clock.now()
    run_id = str(uuid4())
    git = capture_git_provenance(root)
    config_snapshot = safe_runtime_config_snapshot(
        runtime,
        prediction_date=prediction_date,
        verbose_outputs=verbose_outputs,
        force_output_overwrite=force_output_overwrite,
    )
    artifact_paths = tuple(
        Path(path)
        for path in (
            getattr(runtime, "player_baselines_path", ""),
            getattr(runtime, "team_baselines_path", ""),
            getattr(runtime, "calibration_path", ""),
        )
        if str(path)
    )
    model_manifest = model_artifact_manifest(artifact_paths)
    config_hash = payload_sha256(config_snapshot)
    model_bundle_hash = (
        payload_sha256(model_manifest)
        if any(item.get("exists") for item in model_manifest.get("artifacts", []))
        else None
    )
    calibration_entry = next(
        (
            item
            for item in model_manifest.get("artifacts", [])
            if item.get("name") == "calibration.json"
        ),
        None,
    )
    input_manifest_hash = payload_sha256(
        {
            "available_model_artifacts": model_manifest,
            "runtime_config_hash": config_hash,
        }
    )
    reproducibility = (
        ReproducibilityLevel.PARTIAL.value
        if git.get("git_commit_sha") and config_hash
        else ReproducibilityLevel.INSUFFICIENT.value
    )
    run_reason = _run_reason(environ)
    manifest = RunManifest(
        prediction_run_id=run_id,
        run_mode=RunMode.SHADOW.value,
        run_reason=run_reason,
        parent_run_id=None,
        started_at_utc=started,
        completed_at_utc=None,
        operating_date=str(prediction_date),
        operating_timezone=OPERATING_TIMEZONE,
        git_commit_sha=_optional_text(git.get("git_commit_sha")),
        git_dirty=git.get("git_dirty")
        if isinstance(git.get("git_dirty"), bool)
        else None,
        working_tree_hash=_optional_text(git.get("working_tree_hash")),
        config_hash=config_hash,
        model_id=type(runtime).__name__ if runtime is not None else None,
        model_version=None,
        model_bundle_hash=model_bundle_hash,
        calibration_id=(
            "calibration.json"
            if calibration_entry and calibration_entry.get("exists")
            else None
        ),
        calibration_version=None,
        calibration_hash=(
            _optional_text(calibration_entry.get("sha256"))
            if calibration_entry
            else None
        ),
        strategy_version=None,
        pipeline_version=None,
        python_version=python_version(),
        dependency_fingerprint=dependency_fingerprint(),
        input_manifest_hash=input_manifest_hash,
        reproducibility_level=reproducibility,
    )
    return ShadowRunContext(
        prediction_run_id=run_id,
        prediction_date=str(prediction_date),
        repository_root=root,
        lifecycle_root=storage_root,
        clock=active_clock,
        run_manifest=manifest,
        config_snapshot=config_snapshot,
        model_manifest=model_manifest,
        git_provenance=git,
    )


def publish_shadow_after_board(
    context: ShadowRunContext,
    *,
    board_path: str | Path,
    observations_enabled: bool = False,
    observation_batch: Any = None,
    observation_capture_error: str | None = None,
) -> ShadowPublicationResult:
    """Commit and reconcile shadow evidence after a successful board write.

    The function never edits the board. It reports shadow failures as structured
    DEGRADED/FAIL results so the canonical publication remains unaffected.
    """

    path = Path(board_path)
    board_ref = safe_path_reference(path, context.repository_root)
    if not path.is_file():
        context.terminal = True
        report = ReconciliationReport(
            prediction_run_id=context.prediction_run_id,
            operating_date=context.prediction_date,
            status=ReconciliationStatus.FAIL.value,
            board_published=False,
            board_path=board_ref,
            board_sha256=None,
            expected_row_count=0,
            committed_event_count=0,
            matched_row_count=0,
            unresolved_identity_count=0,
            mismatches=(),
            errors=("canonical actionable board was not successfully published",),
            verified_at_utc=context.clock.now(),
        )
        reconciliation_path = _best_effort_write_report(context, report)
        return ShadowPublicationResult(
            status=report.status,
            prediction_run_id=context.prediction_run_id,
            commit_status=None,
            segment_directory=None,
            reconciliation_path=reconciliation_path,
            reconciliation=report,
            message="canonical board missing; no PREDICTION_PUBLISHED event was committed",
        )
    board_hash: str | None = None
    rows: Sequence[Mapping[str, str]] = ()
    commit = None
    try:
        board_hash = file_sha256(path)
        rows = read_canonical_board_rows(path)
        _validate_board_security(path)
        completed_manifest, events, evidence = _prepare_successful_publication(
            context,
            board_path=path,
            board_reference=board_ref,
            board_sha256=board_hash,
            rows=rows,
            observations_enabled=observations_enabled,
            observation_batch=observation_batch,
            observation_capture_error=observation_capture_error,
        )
        writer = LifecycleWriter(context.lifecycle_root, clock=context.clock)
        commit = writer.commit_segment(
            completed_manifest,
            events,
            evidence_objects=evidence,
            command="courtvision_ai.py shadow publication",
        )
        committed_events = read_segment_events(commit.segment_directory)
        report = reconcile_board_with_events(
            prediction_run_id=context.prediction_run_id,
            operating_date=context.prediction_date,
            board_path=path,
            board_path_reference=board_ref,
            events=committed_events,
            expected_board_sha256=board_hash,
            clock=context.clock,
        )
        reconciliation_path = writer.write_reconciliation(report)
        context.run_manifest = completed_manifest
        context.terminal = True
        return ShadowPublicationResult(
            status=report.status,
            prediction_run_id=context.prediction_run_id,
            commit_status=commit.status,
            segment_directory=commit.segment_directory,
            reconciliation_path=reconciliation_path,
            reconciliation=report,
            message=(
                "shadow publication reconciled"
                if report.status == ReconciliationStatus.PASS.value
                else "shadow publication committed with reconciliation findings"
            ),
        )
    except Exception as exc:
        context.terminal = True
        integrity_failure = isinstance(
            exc, (IdempotencyConflictError, LifecycleIntegrityError)
        )
        status = (
            ReconciliationStatus.FAIL.value
            if integrity_failure
            else ReconciliationStatus.DEGRADED.value
        )
        report = degraded_reconciliation(
            prediction_run_id=context.prediction_run_id,
            operating_date=context.prediction_date,
            board_path=board_ref,
            board_sha256=board_hash,
            expected_row_count=len(rows),
            error=f"{type(exc).__name__}: {_safe_error_text(exc)}",
            status=status,
            clock=context.clock,
        )
        reconciliation_path = _best_effort_write_report(context, report)
        report_suffix = (
            ""
            if reconciliation_path is not None
            else "; reconciliation report persistence also failed"
        )
        return ShadowPublicationResult(
            status=report.status,
            prediction_run_id=context.prediction_run_id,
            commit_status=commit.status if commit is not None else None,
            segment_directory=(
                commit.segment_directory if commit is not None else None
            ),
            reconciliation_path=reconciliation_path,
            reconciliation=report,
            message=(
                "shadow integrity conflict" + report_suffix
                if integrity_failure
                else (
                    "canonical board published; shadow lifecycle is degraded"
                    + report_suffix
                )
            ),
        )


def record_failed_shadow_run(
    context: ShadowRunContext,
    error: BaseException,
) -> str | None:
    """Record a canonical run failure without creating publication evidence."""

    if context.terminal:
        return None
    completed_at = context.clock.now()
    failed_manifest = replace(context.run_manifest, completed_at_utc=completed_at)
    started = EventEnvelope.create(
        event_type=EventType.RUN_STARTED,
        payload=_run_event_payload(failed_manifest, status="STARTED"),
        payload_schema_version=1,
        prediction_run_id=context.prediction_run_id,
        event_sequence=1,
        occurred_at_utc=failed_manifest.started_at_utc,
        recorded_at_utc=completed_at,
        operating_date=context.prediction_date,
        operating_timezone=OPERATING_TIMEZONE,
        actor_type="SYSTEM",
        actor_id="courtvision_ai.py",
        correlation_id=context.prediction_run_id,
        idempotency_key=f"RUN_STARTED:{context.prediction_run_id}",
        code_sha=failed_manifest.git_commit_sha,
        config_hash=failed_manifest.config_hash,
        model_id=failed_manifest.model_id,
        model_version=failed_manifest.model_version,
    )
    failed = EventEnvelope.create(
        event_type=EventType.RUN_FAILED,
        payload={
            "failure_schema_version": 1,
            "error_type": type(error).__name__,
            "error_message": _safe_error_text(error),
            "publication_committed": False,
        },
        payload_schema_version=1,
        prediction_run_id=context.prediction_run_id,
        event_sequence=2,
        occurred_at_utc=completed_at,
        recorded_at_utc=completed_at,
        operating_date=context.prediction_date,
        operating_timezone=OPERATING_TIMEZONE,
        actor_type="SYSTEM",
        actor_id="courtvision_ai.py",
        correlation_id=context.prediction_run_id,
        idempotency_key=f"RUN_FAILED:{context.prediction_run_id}",
        code_sha=failed_manifest.git_commit_sha,
        config_hash=failed_manifest.config_hash,
        model_id=failed_manifest.model_id,
        model_version=failed_manifest.model_version,
        previous_event_hash=started.event_hash,
    )
    evidence = _provenance_evidence(context, failed_manifest)
    writer = LifecycleWriter(context.lifecycle_root, clock=context.clock)
    commit = writer.commit_segment(
        failed_manifest,
        (started, failed),
        evidence_objects=evidence,
        command="courtvision_ai.py shadow failed run",
    )
    context.run_manifest = failed_manifest
    context.terminal = True
    return commit.status


def _prepare_successful_publication(
    context: ShadowRunContext,
    *,
    board_path: Path,
    board_reference: str,
    board_sha256: str,
    rows: Sequence[Mapping[str, str]],
    observations_enabled: bool = False,
    observation_batch: Any = None,
    observation_capture_error: str | None = None,
) -> tuple[RunManifest, tuple[EventEnvelope, ...], tuple[PreparedEvidenceObject, ...]]:
    published_at = context.clock.now()
    completed_manifest = replace(
        context.run_manifest,
        completed_at_utc=published_at,
    )
    evidence_by_hash: dict[str, PreparedEvidenceObject] = {}

    def add_evidence(item: PreparedEvidenceObject) -> PreparedEvidenceObject:
        prior = evidence_by_hash.get(item.sha256)
        if prior is not None and prior.data != item.data:
            raise LifecycleIntegrityError(
                "same evidence hash prepared with different content"
            )
        evidence_by_hash[item.sha256] = item
        return item

    provenance_objects = _provenance_evidence(context, completed_manifest)
    for item in provenance_objects:
        add_evidence(item)
    provenance_object = provenance_objects[0]
    board_object = add_evidence(
        prepare_evidence_object(
            "board_artifact",
            {
                "prediction_run_id": context.prediction_run_id,
                "board_path": board_reference,
                "board_sha256": board_sha256,
                "file_size_bytes": board_path.stat().st_size,
                "publication_time_utc": published_at,
                "row_count": len(rows),
            },
        )
    )
    events: list[EventEnvelope] = []
    started = EventEnvelope.create(
        event_type=EventType.RUN_STARTED,
        payload=_run_event_payload(completed_manifest, status="STARTED"),
        payload_schema_version=1,
        prediction_run_id=context.prediction_run_id,
        event_sequence=1,
        occurred_at_utc=completed_manifest.started_at_utc,
        recorded_at_utc=published_at,
        operating_date=context.prediction_date,
        operating_timezone=OPERATING_TIMEZONE,
        actor_type="SYSTEM",
        actor_id="courtvision_ai.py",
        correlation_id=context.prediction_run_id,
        idempotency_key=f"RUN_STARTED:{context.prediction_run_id}",
        source_refs={
            "model_config_manifest": _evidence_ref(provenance_object)
        },
        source_hashes={"model_config_manifest": provenance_object.sha256},
        code_sha=completed_manifest.git_commit_sha,
        config_hash=completed_manifest.config_hash,
        model_id=completed_manifest.model_id,
        model_version=completed_manifest.model_version,
    )
    events.append(started)
    previous_hash = started.event_hash
    observation_events: tuple[EventEnvelope, ...] = ()
    observation_capture_errors: list[str] = []
    if observation_capture_error:
        observation_capture_errors.append(
            _safe_error_text(RuntimeError(str(observation_capture_error)))
        )
    if observations_enabled:
        if observation_batch is None:
            if not observation_capture_errors:
                observation_capture_errors.append(
                    "observation batch was unavailable"
                )
        else:
            observation_capture_errors.extend(
                _safe_error_text(RuntimeError(str(item)))
                for item in getattr(observation_batch, "capture_errors", ())
            )
            from courtvision.lifecycle.observations import (
                materialize_observation_events,
            )

            observation_events, observation_evidence = (
                materialize_observation_events(
                    observation_batch,
                    run_manifest=completed_manifest,
                    recorded_at_utc=published_at,
                    starting_sequence=len(events) + 1,
                    previous_event_hash=previous_hash,
                )
            )
            for item in observation_evidence:
                add_evidence(item)
            events.extend(observation_events)
            if observation_events:
                previous_hash = observation_events[-1].event_hash
    for row_index, raw_row in enumerate(rows):
        row = dict(raw_row)
        inputs = identity_inputs_from_board_row(row)
        identity = derive_publication_identity(
            sport="basketball",
            league="NBA",
            prediction_run_id=context.prediction_run_id,
            **inputs,
        )
        feature_object = add_evidence(
            prepare_evidence_object(
                "feature_snapshot",
                {
                    "prediction_run_id": context.prediction_run_id,
                    "board_row_index": row_index,
                    "canonical_board_row": row,
                },
            )
        )
        market_object = add_evidence(
            prepare_evidence_object(
                "market_snapshot",
                _market_snapshot(row, row_index, context.prediction_run_id),
            )
        )
        schedule_object = add_evidence(
            prepare_evidence_object(
                "schedule_snapshot",
                _schedule_snapshot(row, row_index, context.prediction_run_id),
            )
        )
        availability_object = add_evidence(
            prepare_evidence_object(
                "availability_snapshot",
                _availability_snapshot(row, row_index, context.prediction_run_id),
            )
        )
        evidence_refs = {
            "availability_snapshot": _evidence_ref(availability_object),
            "board_artifact": _evidence_ref(board_object),
            "feature_snapshot": _evidence_ref(feature_object),
            "market_snapshot": _evidence_ref(market_object),
            "model_config_manifest": _evidence_ref(provenance_object),
            "schedule_snapshot": _evidence_ref(schedule_object),
        }
        evidence_hashes = {
            "availability_snapshot": availability_object.sha256,
            "board_artifact_evidence": board_object.sha256,
            "board_artifact_sha256": board_sha256,
            "feature_snapshot": feature_object.sha256,
            "market_snapshot": market_object.sha256,
            "model_config_manifest": provenance_object.sha256,
            "schedule_snapshot": schedule_object.sha256,
        }
        identity_payload = asdict(identity)
        payload_schema_version = (
            PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION_V2
            if observations_enabled
            else PREDICTION_PUBLISHED_PAYLOAD_SCHEMA_VERSION
        )
        payload = {
            "payload_schema_version": payload_schema_version,
            "publication_authority": "SHADOW_ONLY",
            "canonical_runtime_authority": "CSV_RUNTIME_PIPELINE",
            "prediction_run_id": context.prediction_run_id,
            "published_at_utc": published_at,
            "operating_date": context.prediction_date,
            "operating_timezone": OPERATING_TIMEZONE,
            "board_path": board_reference,
            "board_artifact_sha256": board_sha256,
            "board_file_size_bytes": board_path.stat().st_size,
            "board_row_index": row_index,
            "canonical_board_row": row,
            "published_prediction": _published_prediction(row),
            "identity": identity_payload,
            "entry_market_state": _market_snapshot(
                row, row_index, context.prediction_run_id
            ),
            "schedule_state": _schedule_snapshot(
                row, row_index, context.prediction_run_id
            ),
            "availability_state": _availability_snapshot(
                row, row_index, context.prediction_run_id
            ),
            "model_config_provenance": {
                "model_id": completed_manifest.model_id,
                "model_version": completed_manifest.model_version,
                "model_bundle_hash": completed_manifest.model_bundle_hash,
                "calibration_id": completed_manifest.calibration_id,
                "calibration_version": completed_manifest.calibration_version,
                "calibration_hash": completed_manifest.calibration_hash,
                "strategy_version": completed_manifest.strategy_version,
                "config_hash": completed_manifest.config_hash,
                "git_commit_sha": completed_manifest.git_commit_sha,
                "git_dirty": completed_manifest.git_dirty,
                "working_tree_hash": completed_manifest.working_tree_hash,
            },
            "feature_snapshot_reference": _evidence_ref(feature_object),
            "feature_snapshot_sha256": feature_object.sha256,
            "evidence_refs": evidence_refs,
        }
        if observations_enabled:
            if observation_events:
                from courtvision.lifecycle.observations import (
                    link_publication_observations,
                )

                observation_links = link_publication_observations(
                    row,
                    observation_events,
                    capture_errors=observation_capture_errors,
                )
            else:
                observation_links = _missing_observation_links(
                    observation_capture_errors
                )
            payload["observation_links"] = observation_links
            linked_event_ids = [
                observation_links.get("schedule_observation_event_id"),
                observation_links.get("market_quote_observation_event_id"),
                *observation_links.get(
                    "availability_observation_event_ids", []
                ),
            ]
            linked_event_ids = [
                str(item) for item in linked_event_ids if item
            ]
            linked_by_id = {
                event.event_id: event
                for event in observation_events
                if event.event_id in linked_event_ids
            }
            evidence_refs["observation_event_ids"] = linked_event_ids
            evidence_hashes["observation_event_hashes"] = {
                event_id: linked_by_id[event_id].event_hash
                for event_id in linked_event_ids
                if event_id in linked_by_id
            }
        prediction_id = identity.prediction_id
        idempotency_key = (
            f"PREDICTION_PUBLISHED:{prediction_id}"
            if prediction_id
            else (
                "PREDICTION_PUBLISHED_UNRESOLVED:"
                f"{context.prediction_run_id}:{row_index}:"
                f"{payload_sha256(row)}"
            )
        )
        event = EventEnvelope.create(
            event_type=EventType.PREDICTION_PUBLISHED,
            payload=payload,
            payload_schema_version=payload_schema_version,
            prediction_run_id=context.prediction_run_id,
            prediction_id=identity.prediction_id,
            prediction_key=identity.prediction_key,
            market_subject_key=identity.market_subject_key,
            event_sequence=len(events) + 1,
            occurred_at_utc=published_at,
            recorded_at_utc=published_at,
            provider_reported_at_utc=_provider_reported_timestamp(row),
            operating_date=context.prediction_date,
            operating_timezone=OPERATING_TIMEZONE,
            actor_type="SYSTEM",
            actor_id="courtvision_ai.py",
            correlation_id=context.prediction_run_id,
            idempotency_key=idempotency_key,
            source_refs=evidence_refs,
            source_hashes=evidence_hashes,
            code_sha=completed_manifest.git_commit_sha,
            config_hash=completed_manifest.config_hash,
            model_id=completed_manifest.model_id,
            model_version=completed_manifest.model_version,
            previous_event_hash=previous_hash,
        )
        events.append(event)
        previous_hash = event.event_hash
    completed_payload = {
        **_run_event_payload(completed_manifest, status="COMPLETED"),
        "canonical_board_published": True,
        "publication_event_count": len(rows),
        "board_path": board_reference,
        "board_artifact_sha256": board_sha256,
    }
    if observations_enabled:
        completed_payload["observation_capture"] = {
            "enabled": True,
            "schedule_observation_count": sum(
                item.event_type == EventType.SCHEDULE_OBSERVED.value
                for item in observation_events
            ),
            "market_quote_observation_count": sum(
                item.event_type == EventType.MARKET_QUOTE_OBSERVED.value
                for item in observation_events
            ),
            "player_availability_observation_count": sum(
                item.event_type
                == EventType.PLAYER_AVAILABILITY_OBSERVED.value
                for item in observation_events
            ),
            "source_counts": (
                dict(getattr(observation_batch, "source_counts", {}))
                if observation_batch is not None
                else {}
            ),
            "capture_errors": observation_capture_errors,
        }
    completed_event = EventEnvelope.create(
        event_type=EventType.RUN_COMPLETED,
        payload=completed_payload,
        payload_schema_version=1,
        prediction_run_id=context.prediction_run_id,
        event_sequence=len(events) + 1,
        occurred_at_utc=published_at,
        recorded_at_utc=published_at,
        operating_date=context.prediction_date,
        operating_timezone=OPERATING_TIMEZONE,
        actor_type="SYSTEM",
        actor_id="courtvision_ai.py",
        correlation_id=context.prediction_run_id,
        idempotency_key=f"RUN_COMPLETED:{context.prediction_run_id}",
        source_refs={"board_artifact": _evidence_ref(board_object)},
        source_hashes={
            "board_artifact_evidence": board_object.sha256,
            "board_artifact_sha256": board_sha256,
        },
        code_sha=completed_manifest.git_commit_sha,
        config_hash=completed_manifest.config_hash,
        model_id=completed_manifest.model_id,
        model_version=completed_manifest.model_version,
        previous_event_hash=previous_hash,
    )
    events.append(completed_event)
    return completed_manifest, tuple(events), tuple(evidence_by_hash.values())


def _missing_observation_links(
    capture_errors: Sequence[str],
) -> dict[str, Any]:
    reasons = [
        "SCHEDULE_OBSERVATION_UNAVAILABLE",
        "MARKET_QUOTE_OBSERVATION_UNAVAILABLE",
        "AVAILABILITY_OBSERVATION_UNAVAILABLE",
    ]
    if capture_errors:
        reasons.append("OBSERVATION_CAPTURE_DEGRADED")
    return {
        "observation_link_schema_version": 1,
        "link_status": "DEGRADED",
        "schedule_observation_event_id": None,
        "market_quote_observation_event_id": None,
        "availability_observation_event_ids": [],
        "missing_or_unavailable_reasons": reasons,
        "capture_errors": [str(item)[:500] for item in capture_errors],
    }


def _provenance_evidence(
    context: ShadowRunContext,
    run_manifest: RunManifest,
) -> tuple[PreparedEvidenceObject, ...]:
    return (
        prepare_evidence_object(
            "model_config_manifest",
            {
                "prediction_run_id": context.prediction_run_id,
                "run_manifest": run_manifest.to_dict(),
                "runtime_config": context.config_snapshot,
                "model_artifacts": context.model_manifest,
                "git": context.git_provenance,
            },
        ),
    )


def _run_event_payload(manifest: RunManifest, *, status: str) -> dict[str, Any]:
    return {
        "run_event_schema_version": 1,
        "status": status,
        "prediction_run_id": manifest.prediction_run_id,
        "run_mode": manifest.run_mode,
        "canonical_runtime_mode": manifest.canonical_runtime_mode,
        "lifecycle_authority": manifest.lifecycle_authority,
        "run_reason": manifest.run_reason,
        "parent_run_id": manifest.parent_run_id,
        "started_at_utc": manifest.started_at_utc,
        "completed_at_utc": (
            None if status == "STARTED" else manifest.completed_at_utc
        ),
        "reproducibility_level": manifest.reproducibility_level,
    }


def _published_prediction(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "canonical_event_id": _value(row, "canonical_event_id", "game_id"),
        "provider_event_references": {
            "game_id": _value(row, "game_id"),
            "source_lane": _value(row, "source_lane", "final_selection_source_lane"),
        },
        "canonical_participant_id": _value(
            row, "canonical_player_id", "canonical_participant_id", "player_id"
        ),
        "display_entity_name": _value(
            row, "canonical_player_name", "player_name", "entity_name", "player"
        ),
        "team": _value(row, "canonical_team_abbr", "team_abbr", "team"),
        "opponent": _value(row, "opponent"),
        "canonical_market_id": _value(row, "canonical_market_id", "market_type", "market"),
        "raw_market_id": _value(row, "raw_market_type", "raw_prop_type", "raw_stat_key"),
        "selection": _value(row, "selection", "side"),
        "line": _value(row, "sportsbook_line", "line"),
        "odds": _value(row, "odds", "entry_odds"),
        "bookmaker": _value(row, "canonical_bookmaker_id", "bookmaker", "sportsbook", "vendor"),
        "line_source": _value(row, "line_source"),
        "model_projection": _value(
            row, "model_projection", "projection", "recalibrated_projection"
        ),
        "probability": _value(row, "model_probability", "probability"),
        "implied_probability": _value(row, "implied_probability"),
        "edge": _value(row, "edge", "edge_pct", "recalibrated_edge"),
        "confidence": _value(row, "confidence"),
        "quality_score": _value(row, "quality_score"),
        "selection_score": _value(row, "selection_score"),
        "qualification_reason": _value(row, "qualification_reason"),
        "context_gate_metadata": _selected_fields(
            row,
            (
                "context_pick_alignment",
                "context_caution_level",
                "context_conflict_cause",
                "qualification_gate_mode",
                "kelly_eligible",
                "kelly_projected_skip_reason",
                "recommended_stake",
                "recommended_units",
                "stake_fraction",
                "is_elite",
            ),
        ),
        "availability_state": _availability_snapshot(row, None, None),
        "scheduled_start_state": _schedule_snapshot(row, None, None),
        "provider_timestamps": {
            "odds_updated_at": _value(row, "odds_updated_at", "updated_at"),
            "game_datetime": _value(row, "game_datetime"),
        },
    }


def _market_snapshot(
    row: Mapping[str, str],
    row_index: int | None,
    run_id: str | None,
) -> dict[str, Any]:
    return {
        "prediction_run_id": run_id,
        "board_row_index": row_index,
        **_selected_fields(
            row,
            (
                "canonical_market_id",
                "market_type",
                "market",
                "prop_type",
                "raw_market_type",
                "raw_prop_type",
                "selection",
                "side",
                "sportsbook_line",
                "line",
                "odds",
                "entry_odds",
                "canonical_bookmaker_id",
                "bookmaker",
                "sportsbook",
                "vendor",
                "line_source",
                "source_lane",
                "odds_updated_at",
                "updated_at",
                "is_live_market",
                "synthetic_line",
            ),
        ),
    }


def _schedule_snapshot(
    row: Mapping[str, str],
    row_index: int | None,
    run_id: str | None,
) -> dict[str, Any]:
    return {
        "prediction_run_id": run_id,
        "board_row_index": row_index,
        **_selected_fields(
            row,
            (
                "canonical_event_id",
                "game_id",
                "game_datetime",
                "game_date",
                "game_status",
                "game_status_bucket",
                "home_away",
                "team",
                "team_abbr",
                "opponent",
                "game_home_team_abbr",
                "game_away_team_abbr",
            ),
        ),
    }


def _availability_snapshot(
    row: Mapping[str, str],
    row_index: int | None,
    run_id: str | None,
) -> dict[str, Any]:
    return {
        "prediction_run_id": run_id,
        "board_row_index": row_index,
        **_selected_fields(
            row,
            (
                "injury_status",
                "injury_impact_score",
                "team_injury_impact",
                "opponent_injury_impact",
                "injury_notes",
                "manual_status",
                "manual_minutes_limit",
                "manual_projection_adjustment",
                "manual_confidence_adjustment",
                "manual_context_reason",
                "manual_context_applied",
                "player_identity_valid",
                "player_identity_status",
                "row_identity_valid",
                "row_identity_quarantined",
                "row_identity_quarantine_reason",
            ),
        ),
    }


def _selected_fields(
    row: Mapping[str, str], fields: Sequence[str]
) -> dict[str, Any]:
    return {field: _nullable(row.get(field)) for field in fields if field in row}


def _value(row: Mapping[str, str], *names: str) -> Any:
    for name in names:
        value = _nullable(row.get(name))
        if value is not None:
            return value
    return None


def _nullable(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if not text.strip() or text.strip().lower() in {"nan", "none", "null", "<na>"}:
        return None
    return text


def _provider_reported_timestamp(row: Mapping[str, str]) -> datetime | None:
    for name in ("odds_updated_at", "updated_at", "provider_reported_at_utc"):
        value = _nullable(row.get(name))
        if value is None:
            continue
        try:
            return parse_utc_datetime(value)
        except (TypeError, ValueError):
            # Unknown or naive source times stay null. They are never inferred.
            continue
    return None


def _evidence_ref(item: PreparedEvidenceObject) -> str:
    return f"evidence://sha256/{item.sha256}"


def safe_path_reference(path: Path, repository_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return f"<external>/{resolved.name}"


def _run_reason(environ: Mapping[str, str] | None) -> str | None:
    source = os.environ if environ is None else environ
    candidate = str(source.get("COURTVISION_RUN_REASON", "")).strip().upper()
    return candidate if candidate in {item.value for item in RunReason} else None


def _safe_error_text(error: BaseException) -> str:
    sanitized = sanitize_evidence({"error": str(error)})
    text = str(sanitized.get("error") or type(error).__name__)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret|cookie)"
        r"\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    return text[:1000]


def _validate_board_security(path: Path) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        fieldnames = next(csv.reader(handle), [])
    unsafe = sorted(
        str(field).strip()
        for field in fieldnames
        if str(field).strip() and is_sensitive_key(str(field))
    )
    if unsafe:
        raise UnsafeBoardEvidenceError(
            "canonical board contains prohibited secret-bearing field names: "
            + ", ".join(unsafe)
        )


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _best_effort_write_report(
    context: ShadowRunContext,
    report: ReconciliationReport,
) -> Path | None:
    try:
        return LifecycleWriter(
            context.lifecycle_root, clock=context.clock
        ).write_reconciliation(report)
    except Exception:
        return None


__all__ = [
    "CanonicalPublicationError",
    "LIFECYCLE_SHADOW_ENV",
    "ShadowPublicationResult",
    "ShadowRunContext",
    "UnsafeBoardEvidenceError",
    "begin_shadow_run",
    "lifecycle_shadow_enabled",
    "publish_shadow_after_board",
    "record_failed_shadow_run",
    "safe_path_reference",
]
