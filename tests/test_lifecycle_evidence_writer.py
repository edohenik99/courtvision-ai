from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import socket

import pytest

from courtvision.lifecycle.canonical import canonical_payload_bytes
from courtvision.lifecycle.clock import FixedClock
from courtvision.lifecycle.evidence import (
    EvidenceIntegrityError,
    commit_prepared_evidence,
    prepare_evidence_object,
    sanitize_evidence,
)
from courtvision.lifecycle.models import (
    EventEnvelope,
    EventType,
    ReproducibilityLevel,
    RunManifest,
    RunMode,
)
from courtvision.lifecycle.writer import (
    IdempotencyConflictError,
    LifecycleWriter,
    LifecycleWriterBusyError,
    LifecycleWriterLock,
    completed_segment_directories,
    verify_segment,
)


NOW = datetime(2026, 7, 25, 16, 0, tzinfo=UTC)


def _manifest(run_id: str = "run-a") -> RunManifest:
    return RunManifest(
        prediction_run_id=run_id,
        run_mode=RunMode.SHADOW.value,
        run_reason=None,
        parent_run_id=None,
        started_at_utc=NOW,
        completed_at_utc=NOW,
        operating_date="2026-07-25",
        operating_timezone="America/Toronto",
        git_commit_sha="a" * 40,
        git_dirty=True,
        working_tree_hash="b" * 64,
        config_hash="c" * 64,
        model_id="CourtVisionAI",
        model_version=None,
        model_bundle_hash=None,
        calibration_id=None,
        calibration_version=None,
        calibration_hash=None,
        strategy_version=None,
        pipeline_version=None,
        python_version="3.13",
        dependency_fingerprint="d" * 64,
        input_manifest_hash="e" * 64,
        reproducibility_level=ReproducibilityLevel.PARTIAL.value,
    )


def _event(
    *,
    run_id: str = "run-a",
    payload: dict[str, object] | None = None,
    idempotency_key: str = "RUN_COMPLETED:run-a",
) -> EventEnvelope:
    return EventEnvelope.create(
        event_type=EventType.RUN_COMPLETED,
        payload=payload or {"status": "COMPLETED"},
        payload_schema_version=1,
        prediction_run_id=run_id,
        event_sequence=1,
        occurred_at_utc=NOW,
        recorded_at_utc=NOW,
        operating_date="2026-07-25",
        operating_timezone="America/Toronto",
        actor_type="SYSTEM",
        actor_id="test",
        correlation_id=run_id,
        idempotency_key=idempotency_key,
    )


def test_same_evidence_content_deduplicates_safely(tmp_path: Path) -> None:
    evidence = prepare_evidence_object("market_snapshot", {"line": "24.5"})
    first = commit_prepared_evidence(tmp_path, evidence)
    second = commit_prepared_evidence(tmp_path, evidence)
    assert first == second
    assert first.read_bytes() == evidence.data


def test_evidence_hash_collision_or_path_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    fixed = lambda _: "f" * 64
    first = prepare_evidence_object("market_snapshot", {"line": "24.5"}, digest_func=fixed)
    second = prepare_evidence_object("market_snapshot", {"line": "25.5"}, digest_func=fixed)
    commit_prepared_evidence(tmp_path, first)
    with pytest.raises(EvidenceIntegrityError, match="different content"):
        commit_prepared_evidence(tmp_path, second)


def test_secret_bearing_fields_are_sanitized() -> None:
    secret = "do-not-persist"
    sanitized = sanitize_evidence(
        {
            "authorization": f"Bearer {secret}",
            "nested": {
                "api_key": secret,
                "access_token": secret,
                "password": secret,
                "cookie": secret,
            },
            "safe": "kept",
        }
    )
    encoded = json.dumps(sanitized)
    assert secret not in encoded
    assert sanitized["safe"] == "kept"


def test_writer_lock_prevents_competing_writer_and_live_owner_is_not_stale(
    tmp_path: Path,
) -> None:
    clock = FixedClock(NOW)
    with LifecycleWriterLock(
        tmp_path,
        prediction_run_id="run-a",
        command="first",
        clock=clock,
        timeout_seconds=0,
    ):
        with pytest.raises(LifecycleWriterBusyError, match="already held"):
            with LifecycleWriterLock(
                tmp_path,
                prediction_run_id="run-b",
                command="second",
                clock=clock,
                timeout_seconds=0,
                process_checker=lambda pid: True,
            ):
                pass


def test_dead_stale_lock_can_be_recovered_after_owner_death_verification(
    tmp_path: Path,
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path = tmp_path / ".writer.lock"
    lock_path.write_bytes(
        canonical_payload_bytes(
            {
                "lock_id": "dead",
                "pid": 99999999,
                "hostname": socket.gethostname(),
                "root": str(tmp_path.resolve()),
                "prediction_run_id": "old",
                "command": "old",
                "acquired_at_utc": "2026-07-25T15:00:00.000000Z",
            }
        )
    )
    with LifecycleWriterLock(
        tmp_path,
        prediction_run_id="new",
        command="recover",
        clock=FixedClock(NOW),
        timeout_seconds=0,
        process_checker=lambda pid: False,
    ):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["prediction_run_id"] == "new"
    assert not lock_path.exists()


def test_non_object_writer_lock_payload_fails_with_documented_busy_error(
    tmp_path: Path,
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path = tmp_path / ".writer.lock"
    lock_path.write_text("[]", encoding="utf-8")
    with pytest.raises(LifecycleWriterBusyError, match="metadata is unreadable"):
        with LifecycleWriterLock(
            tmp_path,
            prediction_run_id="new",
            command="contender",
            clock=FixedClock(NOW),
            timeout_seconds=0,
        ):
            pass
    assert lock_path.read_text(encoding="utf-8") == "[]"


def test_segment_commits_atomically_and_completed_segment_verifies(
    tmp_path: Path,
) -> None:
    writer = LifecycleWriter(tmp_path, clock=FixedClock(NOW))
    evidence = prepare_evidence_object("feature_snapshot", {"minutes": "31"})
    result = writer.commit_segment(
        _manifest(),
        (_event(),),
        evidence_objects=(evidence,),
    )
    assert result.status == "COMMITTED"
    verification = verify_segment(result.segment_directory, lifecycle_root=tmp_path)
    assert verification.ok
    assert verification.event_count == 1
    assert len(completed_segment_directories(tmp_path)) == 1


def test_incomplete_temporary_segment_is_ignored(tmp_path: Path) -> None:
    incomplete = (
        tmp_path
        / "ledger"
        / "2026"
        / "07"
        / "25"
        / ".run-a.tmp-incomplete"
    )
    incomplete.mkdir(parents=True)
    (incomplete / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert completed_segment_directories(tmp_path) == ()


def test_changed_committed_content_fails_hash_verification(tmp_path: Path) -> None:
    result = LifecycleWriter(tmp_path, clock=FixedClock(NOW)).commit_segment(
        _manifest(), (_event(),)
    )
    events_path = result.segment_directory / "events.jsonl"
    events_path.write_bytes(events_path.read_bytes() + b" ")
    verification = verify_segment(result.segment_directory, lifecycle_root=tmp_path)
    assert not verification.ok
    assert any("SHA-256 mismatch" in item for item in verification.violations)


def test_duplicate_identical_publication_is_idempotent(tmp_path: Path) -> None:
    writer = LifecycleWriter(tmp_path, clock=FixedClock(NOW))
    first = writer.commit_segment(_manifest(), (_event(),))
    second = writer.commit_segment(_manifest(), (_event(),))
    assert first.status == "COMMITTED"
    assert second.status == "ALREADY_COMMITTED"
    assert second.segment_directory == first.segment_directory


def test_duplicate_conflicting_publication_fails_without_overwrite(
    tmp_path: Path,
) -> None:
    writer = LifecycleWriter(tmp_path, clock=FixedClock(NOW))
    first = writer.commit_segment(_manifest(), (_event(payload={"value": 1}),))
    before = (first.segment_directory / "events.jsonl").read_bytes()
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        writer.commit_segment(_manifest(), (_event(payload={"value": 2}),))
    assert (first.segment_directory / "events.jsonl").read_bytes() == before


def test_failed_segment_construction_leaves_no_committed_segment(
    tmp_path: Path,
) -> None:
    writer = LifecycleWriter(tmp_path, clock=FixedClock(NOW))

    def fail(stage: str) -> None:
        if stage == "before_atomic_rename":
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected"):
        writer.commit_segment(_manifest(), (_event(),), failure_hook=fail)
    assert completed_segment_directories(tmp_path) == ()
    day = tmp_path / "ledger" / "2026" / "07" / "25"
    assert not day.exists() or not any(day.iterdir())


def test_run_manifest_captures_code_config_and_dirty_provenance() -> None:
    manifest = _manifest()
    payload = manifest.to_dict()
    assert payload["git_commit_sha"] == "a" * 40
    assert payload["git_dirty"] is True
    assert payload["working_tree_hash"] == "b" * 64
    assert payload["config_hash"] == "c" * 64
    assert payload["reproducibility_level"] == "PARTIAL"
